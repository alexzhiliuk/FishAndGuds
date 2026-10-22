import asyncio
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Collection

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    Mailing,
    MailingDelivery,
    MailingRun,
    MailingStatus,
    MailingType,
    RunStatus,
)
from app.repositories import MailingRepository, UserRepository
from app.services.mailing_content import (
    MailingContent,
    normalize_mailing_content,
    validate_mailing_content,
)
from app.services.mailing_lock import MailingBusy, mailing_operation

logger = logging.getLogger(__name__)
DELETE_WINDOW = timedelta(hours=48)


def utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def deletion_available(delivery: MailingDelivery, now: datetime) -> bool:
    return (
        delivery.message_id is not None
        and delivery.chat_id is not None
        and delivery.sent_at is not None
        and utc(delivery.sent_at) + DELETE_WINDOW > now
        and delivery.deletion_status not in {"deleted", "missing", "expired"}
    )


class MailingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, name: str, text: str, image_file_id: str | None = None, entities=None
    ):
        content = normalize_mailing_content(text, entities, image_file_id)
        validate_mailing_content(content)
        if not name or len(name) > 200:
            raise ValueError("Название должно содержать от 1 до 200 символов")
        item = await MailingRepository(self.session).create(
            name=name,
            text=content.text,
            entities=content.entities,
            image_file_id=image_file_id,
            type=MailingType.manual,
            status=MailingStatus.draft,
        )
        await self.session.commit()
        return item

    async def _item(self, item_id: int):
        item = await self.session.scalar(
            select(Mailing)
            .where(Mailing.id == item_id)
            .execution_options(populate_existing=True)
        )
        if item is None:
            raise ValueError("Рассылка не найдена")
        return item

    async def _recover(self, item: Mailing):
        """The operation lock proves that no sender/deleter is still running."""
        runs = (
            await self.session.scalars(
                select(MailingRun)
                .where(MailingRun.mailing_id == item.id)
                .execution_options(populate_existing=True)
            )
        ).all()
        changed = False
        for run in runs:
            if run.status == RunStatus.sending:
                deliveries = await self.deliveries(run.id)
                if run.snapshot_text is not None:
                    run.sent_count = sum(d.status == "sent" for d in deliveries)
                    run.failed_count = sum(d.status == "failed" for d in deliveries)
                run.status = RunStatus.failed
                run.finished_at = datetime.now(timezone.utc)
                run.error = "Отправка прервана. Сообщения с неизвестным результатом автоматически не повторяются."
                changed = True
            if run.deletion_status == "deleting":
                run.deletion_status = "interrupted"
                changed = True
        if item.status == MailingStatus.sending:
            item.status = MailingStatus.failed
            changed = True
        if changed:
            await self.session.commit()
        return changed

    async def update(self, item_id: int, **changes):
        async with mailing_operation(self.session, item_id):
            item = await self._item(item_id)
            await self._recover(item)
            if item.archived_at is not None:
                raise ValueError("Рассылка в архиве: изменение и отправка недоступны")
            name = changes.get("name", item.name)
            if not name or len(name) > 200:
                raise ValueError("Название должно содержать от 1 до 200 символов")
            # Replacing text without entities must clear the previous formatting.
            entities = changes.get(
                "entities", [] if "text" in changes else item.entities
            )
            content = normalize_mailing_content(
                changes.get("text", item.text),
                entities,
                changes.get("image_file_id", item.image_file_id),
            )
            validate_mailing_content(content)
            reset = (
                item.status
                in (MailingStatus.sent, MailingStatus.cancelled, MailingStatus.failed)
                and "status" not in changes
            )
            for key, value in changes.items():
                setattr(item, key, value)
            item.text, item.entities = content.text, content.entities
            if reset:
                item.status, item.scheduled_at = MailingStatus.draft, None
            await self.session.commit()
            return item

    async def schedule(self, item_id: int, when: datetime):
        return await self.update(
            item_id,
            scheduled_at=when,
            type=MailingType.scheduled,
            status=MailingStatus.scheduled,
        )

    async def cancel(self, item_id: int):
        return await self.update(
            item_id, scheduled_at=None, status=MailingStatus.cancelled
        )

    async def archive(self, item_id: int):
        async with mailing_operation(self.session, item_id):
            item = await self._item(item_id)
            await self._recover(item)
            if item.archived_at is None:
                item.archived_at = datetime.now(timezone.utc)
                item.scheduled_at = None
                if item.status == MailingStatus.scheduled:
                    item.status = MailingStatus.cancelled
                await self.session.commit()

    async def purge_archived(self, item_id: int) -> bool:
        """Erase the archived template and delivery IDs without calling Telegram."""
        async with mailing_operation(self.session, item_id):
            item = await self.session.get(Mailing, item_id, populate_existing=True)
            if item is None:
                return False
            if item.archived_at is None:
                raise ValueError("Окончательное удаление доступно только из архива")
            await self._recover(item)
            # ORM cascades remove runs and deliveries even in SQLite test databases
            # where foreign-key enforcement may be disabled.
            await MailingRepository(self.session).delete(item)
            await self.session.commit()
            return True

    async def page(self, page: int, size: int = 6, archived: bool = False):
        return await MailingRepository(self.session).page(max(page, 0), size, archived)

    async def get(self, item_id: int):
        return await MailingRepository(self.session).get(item_id)

    async def runs_page(self, item_id: int, page: int = 0, size: int = 6):
        rows = list(
            (
                await self.session.scalars(
                    select(MailingRun)
                    .where(MailingRun.mailing_id == item_id)
                    .order_by(MailingRun.id.desc())
                    .offset(max(page, 0) * size)
                    .limit(size + 1)
                )
            ).all()
        )
        return rows[:size], len(rows) > size

    async def get_run(self, run_id: int):
        run = await self.session.get(MailingRun, run_id, populate_existing=True)
        if run is None:
            raise ValueError("Запуск не найден")
        return run

    async def refresh_run(self, run_id: int):
        run = await self.get_run(run_id)
        try:
            async with mailing_operation(self.session, run.mailing_id):
                await self._recover(await self._item(run.mailing_id))
        except MailingBusy:
            pass
        return await self.get_run(run_id)

    async def deliveries(self, run_id: int):
        return list(
            (
                await self.session.scalars(
                    select(MailingDelivery)
                    .where(MailingDelivery.run_id == run_id)
                    .order_by(MailingDelivery.id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

    async def deletion_summary(self, run_id: int):
        deliveries = await self.deliveries(run_id)
        now = datetime.now(timezone.utc)
        counts: Counter[str] = Counter()
        for delivery in deliveries:
            if delivery.message_id is None:
                continue
            status = delivery.deletion_status
            if (
                status not in {"deleted", "missing"}
                and delivery.sent_at
                and utc(delivery.sent_at) + DELETE_WINDOW <= now
            ):
                status = "expired"
            counts[status] += 1
        counts["unknown_sent"] = sum(d.status == "unknown" for d in deliveries)
        counts["available"] = sum(deletion_available(d, now) for d in deliveries)
        # The deadline can differ for individual recipients of a long mailing.
        deadlines = [
            utc(d.sent_at) + DELETE_WINDOW
            for d in deliveries
            if d.sent_at and deletion_available(d, now)
        ]
        return counts, min(deadlines) if deadlines else None

    async def send(
        self,
        item_id: int,
        sender: Callable[[int, MailingContent], Awaitable[Message]],
        include_telegram_ids: Collection[int] = (),
        *,
        expected_run_id: int | None = None,
        scheduled: bool = False,
    ):
        async with mailing_operation(self.session, item_id):
            item = await self._item(item_id)
            recovered = await self._recover(item)
            if item.archived_at is not None:
                raise ValueError("Рассылка в архиве: отправка недоступна")
            latest = (
                await self.session.scalar(
                    select(func.max(MailingRun.id)).where(
                        MailingRun.mailing_id == item_id
                    )
                )
                or 0
            )
            if expected_run_id is not None and latest != expected_run_id:
                raise ValueError(
                    "Эта кнопка уже использована. Откройте рассылку заново."
                )
            if recovered:
                raise ValueError(
                    "Предыдущая операция прервана. Проверьте историю и откройте рассылку заново."
                )
            if scheduled and (
                item.status != MailingStatus.scheduled
                or item.scheduled_at is None
                or utc(item.scheduled_at) > datetime.now(timezone.utc)
            ):
                raise ValueError("Запланированная отправка уже выполнена или отменена")
            content = normalize_mailing_content(
                item.text, item.entities, item.image_file_id
            )
            validate_mailing_content(content)
            audience = await UserRepository(self.session).mailing_audience()
            admins = set(include_telegram_ids)
            recipients = list(
                dict.fromkeys(
                    u.telegram_id
                    for u in audience
                    if u.is_active or u.telegram_id in admins
                )
            )
            recipients.extend(sorted(admins - set(recipients)))
            run = await MailingRepository(self.session).add_run(
                mailing_id=item.id,
                total_count=len(recipients),
                audience_summary=dict(
                    total_users=len(audience),
                    included_admins=len(admins),
                    excluded_inactive=sum(
                        not u.is_active and u.telegram_id not in admins
                        for u in audience
                    ),
                ),
                snapshot_name=item.name,
                snapshot_text=content.text,
                snapshot_entities=content.entities,
                snapshot_image_file_id=content.image_file_id,
                started_at=datetime.now(timezone.utc),
                status=RunStatus.sending,
                sent_count=0,
                failed_count=0,
            )
            deliveries = [
                MailingDelivery(run_id=run.id, telegram_id=uid) for uid in recipients
            ]
            self.session.add_all(deliveries)
            item.status = MailingStatus.sending
            item.scheduled_at = None
            await self.session.commit()
            try:
                for delivery in deliveries:
                    # Persist uncertainty BEFORE the API request, including process crashes.
                    delivery.status = "unknown"
                    await self.session.commit()
                    try:
                        message = await sender(delivery.telegram_id, content)
                    except (
                        TelegramNetworkError,
                        TelegramServerError,
                        TimeoutError,
                    ) as exc:
                        delivery.error = str(exc)[:2000]
                        logger.warning(
                            "Mailing result unknown run=%s recipient=%s",
                            run.id,
                            delivery.telegram_id,
                        )
                    except Exception as exc:
                        delivery.status, delivery.error = "failed", str(exc)[:2000]
                        run.failed_count += 1
                        logger.warning(
                            "Mailing delivery failed run=%s recipient=%s",
                            run.id,
                            delivery.telegram_id,
                        )
                    else:
                        photos = getattr(message, "photo", None)
                        if photos and content.image_file_id:
                            # Persist Telegram's immutable file reference, including
                            # mailings originally backed by a local asset. Reuse it
                            # for subsequent recipients instead of uploading again.
                            image_id = photos[-1].file_id
                            run.snapshot_image_file_id = image_id
                            content = MailingContent(
                                content.text, content.entities, image_id
                            )
                        elif (
                            content.image_file_id
                            and getattr(message, "text", None) is not None
                        ):
                            # The transport falls back to text for a missing asset.
                            run.snapshot_image_file_id = None
                            content = MailingContent(content.text, content.entities)
                        delivery.chat_id = message.chat.id
                        delivery.message_id = message.message_id
                        delivery.sent_at = utc(message.date)
                        delivery.status = "sent"
                        run.sent_count += 1
                    await self.session.commit()
                uncertain = any(d.status == "unknown" for d in deliveries)
                run.status = RunStatus.failed if uncertain else RunStatus.sent
                item.status = MailingStatus.failed if uncertain else MailingStatus.sent
                if uncertain:
                    run.error = "Есть отправки с неизвестным результатом. Они автоматически не повторяются."
                run.finished_at = datetime.now(timezone.utc)
                await self.session.commit()
            except Exception:
                # A failed DB write must never be mistaken for a failed Telegram send.
                await self.session.rollback()
                await self._recover(await self._item(item_id))
                raise
            return run

    async def delete_messages(
        self, run_id: int, deleter: Callable[[int, int], Awaitable[object]]
    ):
        run = await self.get_run(run_id)
        mailing_id = run.mailing_id
        async with mailing_operation(self.session, mailing_id):
            await self._recover(await self._item(mailing_id))
            run = await self.get_run(run_id)
            if run.snapshot_text is None:
                raise ValueError(
                    "Для этой отправки идентификаторы сообщений не сохранялись"
                )
            run.deletion_status = "deleting"
            await self.session.commit()
            deliveries = await self.deliveries(run_id)
            try:
                for delivery in deliveries:
                    if (
                        delivery.message_id is None
                        or delivery.chat_id is None
                        or delivery.sent_at is None
                    ):
                        continue
                    if delivery.deletion_status in {"deleted", "missing", "expired"}:
                        continue
                    if not deletion_available(delivery, datetime.now(timezone.utc)):
                        delivery.deletion_status = "expired"
                        await self.session.commit()
                        continue
                    delivery.deletion_status, delivery.deletion_error = "unknown", None
                    await self.session.commit()
                    for attempt in range(4):
                        if not deletion_available(delivery, datetime.now(timezone.utc)):
                            delivery.deletion_status = "expired"
                            break
                        delivery.deletion_status = "unknown"
                        await self.session.commit()
                        try:
                            await deleter(delivery.chat_id, delivery.message_id)
                        except TelegramRetryAfter as exc:
                            delivery.deletion_error = str(exc)[:2000]
                            delivery.deletion_status = "error"
                            if attempt < 3:
                                # Honor the server deadline; report progress remains persisted.
                                await self.session.commit()
                                await asyncio.sleep(exc.retry_after)
                                continue
                        except (
                            TelegramNetworkError,
                            TelegramServerError,
                            TimeoutError,
                        ) as exc:
                            delivery.deletion_error = str(exc)[:2000]
                            delivery.deletion_status = "unknown"
                            if attempt < 3:
                                await self.session.commit()
                                await asyncio.sleep(2**attempt)
                                continue
                        except TelegramBadRequest as exc:
                            detail = str(exc).lower()
                            delivery.deletion_status = (
                                "missing"
                                if "message to delete not found" in detail
                                else "error"
                            )
                            delivery.deletion_error = str(exc)[:2000]
                        except TelegramForbiddenError as exc:
                            delivery.deletion_status, delivery.deletion_error = (
                                "error",
                                str(exc)[:2000],
                            )
                        except Exception as exc:
                            delivery.deletion_status, delivery.deletion_error = (
                                "unknown",
                                str(exc)[:2000],
                            )
                        else:
                            delivery.deletion_status = "deleted"
                            delivery.deleted_at = datetime.now(timezone.utc)
                        break
                    await self.session.commit()
                run.deletion_status = "finished"
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                await self._recover(await self._item(mailing_id))
                raise
            return run

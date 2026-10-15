import io
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Collection, Literal

import qrcode
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.iiko.client import IikoClient
from app.integrations.iiko.exceptions import IikoError
from app.models.entities import MailingStatus, MailingType, RunStatus
from app.repositories import (
    ApplicationSettingRepository,
    LoyaltyAccountRepository,
    MailingRepository,
    PurchaseRepository,
    RestaurantRepository,
    UserRepository,
)
from app.services.iiko import (
    CardService,
    CustomerService,
    LoyaltySyncService,
    RestaurantSyncService,
)
from app.services.registration import RegistrationService
from app.services.user_cache import user_data_is_fresh, user_refresh_lock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegistrationDocument:
    key: str
    file_id: str
    file_name: str


@dataclass(frozen=True)
class SyncResult:
    total_users: int
    processed_users: int
    failed_users: int
    skipped_users: int
    imported: int


class ApplicationSettingsService:
    PRIVACY_POLICY = "privacy_policy"
    LOYALTY_RULES = "loyalty_rules"
    DOCUMENT_KEYS = (PRIVACY_POLICY, LOYALTY_RULES)

    def __init__(self, session: AsyncSession):
        self.session = session

    @classmethod
    def _setting_keys(cls, key: str) -> tuple[str, str]:
        if key not in cls.DOCUMENT_KEYS:
            raise ValueError("Неизвестный документ")
        return f"{key}_file_id", f"{key}_file_name"

    async def registration_documents(self):
        setting_keys = tuple(
            setting_key
            for key in self.DOCUMENT_KEYS
            for setting_key in self._setting_keys(key)
        )
        values = await ApplicationSettingRepository(self.session).values(setting_keys)
        documents = {}
        for key in self.DOCUMENT_KEYS:
            file_id_key, file_name_key = self._setting_keys(key)
            file_id = values.get(file_id_key)
            documents[key] = (
                RegistrationDocument(
                    key=key,
                    file_id=file_id,
                    file_name=values.get(file_name_key) or f"{key}.pdf",
                )
                if file_id
                else None
            )
        return documents

    async def get_document(self, key: str):
        return (await self.registration_documents())[key]

    async def update_document(self, key: str, file_id: str, file_name: str):
        file_id_key, file_name_key = self._setting_keys(key)
        repo = ApplicationSettingRepository(self.session)
        await repo.set(file_id_key, file_id)
        await repo.set(file_name_key, file_name)
        await self.session.commit()
        return RegistrationDocument(key=key, file_id=file_id, file_name=file_name)

    async def clear_document(self, key: str):
        file_id_key, file_name_key = self._setting_keys(key)
        repo = ApplicationSettingRepository(self.session)
        await repo.set(file_id_key, None)
        await repo.set(file_name_key, None)
        await self.session.commit()


class UserDataRefreshService:
    def __init__(
        self,
        session: AsyncSession,
        iiko: IikoClient | None,
        default_organization_id: str | None,
        *,
        history_days: int = 365,
        page_size: int = 100,
    ):
        self.session, self.iiko, self.default_organization_id = (
            session,
            iiko,
            default_organization_id,
        )
        self.history_days, self.page_size = history_days, page_size

    async def refresh(self, user, kind: Literal["profile", "transactions"]):
        account = user.loyalty_account
        if (
            not self.iiko
            or not self.default_organization_id
            or not account
            or not account.iiko_customer_id
        ):
            return user
        timestamp = (
            "balance_synced_at" if kind == "profile" else "transactions_synced_at"
        )
        if user_data_is_fresh(getattr(account, timestamp)):
            return user
        user_id = user.id
        async with user_refresh_lock(self.session, user_id):
            account = await LoyaltyAccountRepository(self.session).lock_by_user_id(
                user_id
            )
            repository = UserRepository(self.session)
            user = await repository.by_id(user_id, refresh=True)
            if user is None or account is None or not account.iiko_customer_id:
                return user
            # Another request may have refreshed the account while we waited.
            if user_data_is_fresh(getattr(account, timestamp)):
                return user
            try:
                if kind == "profile":
                    customer = await self.iiko.get_customer_info(
                        organization_id=self.default_organization_id,
                        customer_id=account.iiko_customer_id,
                    )
                    if customer is None:
                        raise IikoError("Customer data could not be refreshed")
                    await CustomerService(
                        self.session, self.iiko, self.default_organization_id
                    ).sync_customer(user, customer)
                    await CardService(
                        self.session, self.iiko, self.default_organization_id
                    ).sync_card(user, customer)
                    await self.session.commit()
                else:
                    await LoyaltySyncService(
                        self.session,
                        self.iiko,
                        default_organization_id=self.default_organization_id,
                        history_days=self.history_days,
                        page_size=self.page_size,
                    ).sync_transactions_by_revision(user)
            except IikoError:
                await self.session.rollback()
                logger.warning(
                    "User request uses cached iiko data user_id=%s kind=%s",
                    user_id,
                    kind,
                )
                # Rollback expires ORM objects; reload the last committed data.
                user = await repository.by_id(user_id, refresh=True)
            return user


class LoyaltyService:
    def __init__(
        self,
        session: AsyncSession,
        iiko: IikoClient | None = None,
        default_organization_id: str | None = None,
    ):
        self.session, self.iiko, self.default_organization_id = (
            session,
            iiko,
            default_organization_id,
        )

    async def get_profile(self, telegram_id: int):
        user = await UserRepository(self.session).by_telegram_id(telegram_id)
        if not user or not user.loyalty_account:
            return None
        user = await UserDataRefreshService(
            self.session, self.iiko, self.default_organization_id
        ).refresh(user, "profile")
        if user is None or user.loyalty_account is None:
            return None
        account = user.loyalty_account
        return {"user": user, "card": account, "balance": account.last_known_balance}

    @staticmethod
    def generate_qr(payload: str) -> bytes:
        image = qrcode.make(payload)
        stream = io.BytesIO()
        # qrcode's runtime accepts the Pillow-compatible format argument, but
        # types-qrcode also models the fallback PyPNG implementation here.
        image.save(stream, format="PNG")  # type: ignore[call-arg]
        return stream.getvalue()


class PurchaseService:
    def __init__(
        self,
        session: AsyncSession,
        iiko: IikoClient | None = None,
        default_organization_id: str | None = None,
        *,
        history_days: int = 365,
        page_size: int = 100,
    ):
        self.session = session
        self.refresh_service = UserDataRefreshService(
            session,
            iiko,
            default_organization_id,
            history_days=history_days,
            page_size=page_size,
        )

    async def page(self, user_id: int, page: int):
        return await PurchaseRepository(self.session).page(user_id, page)

    async def page_for_telegram(self, telegram_id: int, page: int):
        user = await UserRepository(self.session).by_telegram_id(telegram_id)
        if user is not None:
            user = await self.refresh_service.refresh(user, "transactions")
        return None if user is None else await self.page(user.id, page)


class RestaurantService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_active(self):
        return await RestaurantRepository(self.session).active()

    async def list_all(self):
        return await RestaurantRepository(self.session).list_all()

    async def get(self, item_id: int):
        return await RestaurantRepository(self.session).by_id(item_id)

    async def delivery_url(self):
        items = await self.list_active()
        return next(
            (
                item.delivery_url
                for item in items
                if item.delivery_configurable and item.delivery_url
            ),
            None,
        )

    async def update_local_link(self, item_id: int, field: str, value: str | None):
        item = await RestaurantRepository(self.session).update_local_link(
            item_id, field, value
        )
        await self.session.commit()
        return item


class NotificationService:
    FIELDS = {
        "promotions": "promotions_enabled",
        "news": "news_enabled",
        "holidays": "holidays_enabled",
        "sms": "sms_enabled",
        "push": "push_enabled",
        "email": "email_enabled",
    }

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_settings(self, telegram_id: int):
        user = await UserRepository(self.session).by_telegram_id(telegram_id)
        return user.notification_settings if user else None

    async def toggle(self, telegram_id: int, key: str):
        user = await UserRepository(self.session).by_telegram_id(telegram_id)
        field = self.FIELDS[key]
        setattr(
            user.notification_settings,
            field,
            not getattr(user.notification_settings, field),
        )
        await self.session.commit()
        return user.notification_settings


class SyncService:
    def __init__(
        self,
        session: AsyncSession,
        iiko: IikoClient,
        *,
        default_organization_id: str,
        history_days: int = 365,
        page_size: int = 100,
        card_number_prefix: str = "9898",
        card_number_length: int = 8,
        card_generation_attempts: int = 10,
    ):
        self.session, self.iiko = session, iiko
        self.default_organization_id, self.history_days, self.page_size = (
            default_organization_id,
            history_days,
            page_size,
        )
        self.card_number_prefix, self.card_number_length = (
            card_number_prefix,
            card_number_length,
        )
        self.card_generation_attempts = card_generation_attempts

    async def sync_restaurants(self):
        return await RestaurantSyncService(self.session, self.iiko).sync_organizations()

    def _loyalty(self):
        return LoyaltySyncService(
            self.session,
            self.iiko,
            default_organization_id=self.default_organization_id,
            history_days=self.history_days,
            page_size=self.page_size,
        )

    def _cards(self):
        return CardService(
            self.session,
            self.iiko,
            self.default_organization_id,
            number_prefix=self.card_number_prefix,
            number_length=self.card_number_length,
            generation_attempts=self.card_generation_attempts,
        )

    def _registration(self):
        return RegistrationService(
            self.session,
            self.iiko,
            default_organization_id=self.default_organization_id,
            history_days=self.history_days,
            page_size=self.page_size,
            card_number_prefix=self.card_number_prefix,
            card_number_length=self.card_number_length,
            card_generation_attempts=self.card_generation_attempts,
        )

    async def sync_user(self, user):
        if not user.loyalty_account.iiko_customer_id:
            if not await self._registration().sync_pending_user(user):
                raise IikoError("Pending user sync deferred")
            return 1
        customer = await self.iiko.get_customer_info(
            organization_id=self.default_organization_id,
            customer_id=user.loyalty_account.iiko_customer_id,
        )
        if customer:
            await CustomerService(
                self.session, self.iiko, self.default_organization_id
            ).sync_customer(user, customer)
            await self._cards().ensure_card(user, customer)
        return await self._loyalty().sync_transactions_by_revision(user)

    async def retry_pending(self):
        service = self._registration()
        return sum(
            [
                int(await service.sync_pending_user(user))
                for user in await UserRepository(self.session).pending_iiko()
            ]
        )

    async def sync_all(self):
        repository = UserRepository(self.session)
        user_ids = await repository.active_ids()
        total = processed = failed = skipped = 0
        for user_id in user_ids:
            try:
                # Reload after each rollback: SQLAlchemy expires existing objects.
                user = await repository.by_id(user_id)
                if user is None or not user.is_active or not user.loyalty_account:
                    skipped += 1
                    continue
                total += await self.sync_user(user)
                processed += 1
            except Exception:
                await self.session.rollback()
                failed += 1
                logger.exception("User transaction sync failed user_id=%s", user_id)
        result = SyncResult(len(user_ids), processed, failed, skipped, total)
        log = logger.warning if failed else logger.info
        log(
            "Transaction sync summary total=%s processed=%s failed=%s skipped=%s imported=%s",
            result.total_users,
            result.processed_users,
            result.failed_users,
            result.skipped_users,
            result.imported,
        )
        return result


class MailingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, name: str, text: str, image_file_id: str | None = None):
        item = await MailingRepository(self.session).create(
            name=name,
            text=text,
            image_file_id=image_file_id,
            type=MailingType.manual,
            status=MailingStatus.draft,
        )
        await self.session.commit()
        return item

    async def update(self, item_id: int, **changes):
        item = await MailingRepository(self.session).get(item_id)
        if not item:
            raise ValueError("Рассылка не найдена")
        if item.status == MailingStatus.sending:
            raise ValueError("Нельзя редактировать рассылку во время отправки")
        reset_to_draft = (
            item.status
            in (MailingStatus.sent, MailingStatus.cancelled, MailingStatus.failed)
            and "status" not in changes
        )
        for key, value in changes.items():
            setattr(item, key, value)
        if reset_to_draft:
            item.status = MailingStatus.draft
            item.scheduled_at = None
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
        return await self.update(item_id, status=MailingStatus.cancelled)

    async def delete(self, item_id: int):
        repo = MailingRepository(self.session)
        item = await repo.get(item_id)
        if not item:
            raise ValueError("Рассылка не найдена")
        if item.status == MailingStatus.sending:
            raise ValueError("Нельзя удалить рассылку во время отправки")
        await repo.delete(item)
        await self.session.commit()

    async def list(self):
        return await MailingRepository(self.session).list()

    async def page(self, page: int, size: int = 6):
        return await MailingRepository(self.session).page(page, size)

    async def get(self, item_id: int):
        return await MailingRepository(self.session).get(item_id)

    async def send(
        self,
        item_id: int,
        sender: Callable[[int, str, str | None], Awaitable[None]],
        excluded_telegram_ids: Collection[int] = (),
    ):
        repo = MailingRepository(self.session)
        item = await repo.get(item_id)
        if not item:
            raise ValueError("Рассылка не найдена")
        excluded = set(excluded_telegram_ids)
        audience = await UserRepository(self.session).mailing_audience()
        summary = dict(
            total_users=len(audience),
            excluded_admins=0,
            excluded_inactive=0,
            excluded_push=0,
        )
        users = []
        for user in audience:
            # Exclusive categories: every user appears exactly once in the report.
            if user.telegram_id in excluded:
                summary["excluded_admins"] += 1
            elif not user.is_active:
                summary["excluded_inactive"] += 1
            elif (
                user.notification_settings
                and not user.notification_settings.push_enabled
            ):
                summary["excluded_push"] += 1
            else:
                users.append(user)
        now = datetime.now(timezone.utc)
        item.status = MailingStatus.sending
        run = await repo.add_run(
            mailing_id=item.id,
            total_count=len(users),
            audience_summary=summary,
            sent_count=0,
            failed_count=0,
            status=RunStatus.sending,
            started_at=now,
        )
        await self.session.commit()
        try:
            for user in users:
                try:
                    await sender(user.telegram_id, item.text, item.image_file_id)
                    run.sent_count += 1
                except Exception:
                    run.failed_count += 1
                    logger.exception("Mailing delivery failed user_id=%s", user.id)
            run.status = RunStatus.sent
            item.status = MailingStatus.sent
        except Exception as exc:
            run.status = RunStatus.failed
            item.status = MailingStatus.failed
            run.error = str(exc)[:2000]
            logger.exception("Mailing failed id=%s", item.id)
        run.finished_at = datetime.now(timezone.utc)
        await self.session.commit()
        return run

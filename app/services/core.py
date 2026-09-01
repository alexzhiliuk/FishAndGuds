import io
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable, Collection

import qrcode
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.iiko.client import IikoClient
from app.integrations.iiko.exceptions import IikoError
from app.models.entities import MailingStatus, MailingType, RunStatus
from app.repositories import (
    ApplicationSettingRepository,
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

logger = logging.getLogger(__name__)


class ApplicationSettingsService:
    PRIVACY_POLICY_URL = "privacy_policy_url"
    LOYALTY_RULES_URL = "loyalty_rules_url"
    LINK_KEYS = (PRIVACY_POLICY_URL, LOYALTY_RULES_URL)

    def __init__(self, session: AsyncSession):
        self.session = session

    async def registration_links(self):
        values = await ApplicationSettingRepository(self.session).values(self.LINK_KEYS)
        return {key: values.get(key) for key in self.LINK_KEYS}

    async def update_link(self, key: str, value: str | None):
        if key not in self.LINK_KEYS:
            raise ValueError("Неизвестная настройка")
        item = await ApplicationSettingRepository(self.session).set(key, value)
        await self.session.commit()
        return item


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
        account = user.loyalty_account
        if self.iiko and self.default_organization_id and account.iiko_customer_id:
            try:
                customer = await self.iiko.get_customer_info(
                    organization_id=self.default_organization_id,
                    customer_id=account.iiko_customer_id,
                )
                if customer:
                    await CustomerService(
                        self.session, self.iiko, self.default_organization_id
                    ).sync_customer(user, customer)
                    await CardService(
                        self.session, self.iiko, self.default_organization_id
                    ).sync_card(user, customer)
                    await self.session.commit()
            except IikoError:
                logger.warning("Profile uses cached iiko data user_id=%s", user.id)
        return {"user": user, "card": account, "balance": account.last_known_balance}

    @staticmethod
    def generate_qr(payload: str) -> bytes:
        image = qrcode.make(payload)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()


class PurchaseService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def page(self, user_id: int, page: int):
        return await PurchaseRepository(self.session).page(user_id, page)

    async def page_for_telegram(self, telegram_id: int, page: int):
        user = await UserRepository(self.session).by_telegram_id(telegram_id)
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
            return int(await self._registration().sync_pending_user(user))
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
        total = 0
        for user in await UserRepository(self.session).active():
            if user.loyalty_account:
                total += await self.sync_user(user)
        logger.info("Sync completed, imported=%s", total)
        return total


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
        users = [
            user
            for user in await UserRepository(self.session).active()
            if user.telegram_id not in excluded
            and (
                user.notification_settings is None
                or user.notification_settings.push_enabled
            )
        ]
        now = datetime.now(timezone.utc)
        item.status = MailingStatus.sending
        run = await repo.add_run(
            mailing_id=item.id,
            total_count=len(users),
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

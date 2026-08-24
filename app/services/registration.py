from dataclasses import dataclass
from datetime import date, datetime, timezone
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.iiko.client import IikoClient
from app.integrations.iiko.exceptions import IikoError
from app.models.entities import IikoSyncStatus
from app.repositories import UserRepository
from app.services.iiko import CardService, CustomerService, LoyaltySyncService
from app.services.phone import PhoneNormalizationService

logger = logging.getLogger(__name__)


class RegistrationSubmission(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    middle_name: str | None = Field(default=None, max_length=100)
    birthday: date
    gender: Literal["male", "female"]
    email: str | None = Field(default=None, max_length=254)
    sms_enabled: bool = True
    push_enabled: bool = True
    email_enabled: bool = True
    consent: bool

    @field_validator("first_name", "last_name", "middle_name", mode="before")
    @classmethod
    def clean_name(cls, value):
        if value is None: return None
        cleaned = str(value).strip()
        return cleaned or None

    @field_validator("birthday")
    @classmethod
    def validate_birthday(cls, value: date):
        if value >= datetime.now(timezone.utc).date() or value.year < 1900:
            raise ValueError("Некорректная дата рождения")
        return value

    @field_validator("email", mode="before")
    @classmethod
    def validate_email(cls, value):
        cleaned = str(value or "").strip()
        if not cleaned: return None
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cleaned):
            raise ValueError("Некорректный email")
        return cleaned

    @field_validator("consent")
    @classmethod
    def require_consent(cls, value: bool):
        if value is not True: raise ValueError("Нужно согласие на обработку персональных данных")
        return value


@dataclass
class RegistrationStart:
    user: object | None = None
    needs_form: bool = False
    iiko_available: bool = True


class RegistrationService:
    def __init__(self, session: AsyncSession, iiko: IikoClient, *, default_organization_id: str, history_days: int = 365, page_size: int = 100, card_number_prefix: str = "9898", card_number_length: int = 8, card_generation_attempts: int = 10):
        self.session, self.iiko = session, iiko
        self.default_organization_id, self.history_days, self.page_size = default_organization_id, history_days, page_size
        self.card_number_prefix, self.card_number_length = card_number_prefix, card_number_length
        self.card_generation_attempts = card_generation_attempts

    def _customer_service(self): return CustomerService(self.session, self.iiko, self.default_organization_id)
    def _card_service(self):
        return CardService(
            self.session,
            self.iiko,
            self.default_organization_id,
            number_prefix=self.card_number_prefix,
            number_length=self.card_number_length,
            generation_attempts=self.card_generation_attempts,
        )

    async def get_local_user(self, telegram_id: int):
        return await UserRepository(self.session).by_telegram_id(telegram_id)

    async def start(self, telegram_id: int, phone: str):
        phone = PhoneNormalizationService.normalize(phone); repo = UserRepository(self.session)
        if user := await repo.by_telegram_id(telegram_id): return RegistrationStart(user=user)
        if await repo.by_phone(phone): raise ValueError("Этот номер уже связан с другим Telegram-пользователем")
        try: customer = await self._customer_service().get_by_phone(phone)
        except IikoError: return RegistrationStart(needs_form=True, iiko_available=False)
        if customer is None: return RegistrationStart(needs_form=True)
        birthday = customer.birthday.date() if isinstance(customer.birthday, datetime) else customer.birthday
        user = await repo.create_local(telegram_id=telegram_id, phone=phone, first_name=customer.name or "Гость", last_name=customer.surname, middle_name=customer.middle_name, birthday=birthday, email=customer.email, sync_status=IikoSyncStatus.synced)
        await self._customer_service().sync_customer(user, customer)
        customer = await self._card_service().ensure_card(user, customer)
        await self.session.commit()
        await LoyaltySyncService(self.session, self.iiko, default_organization_id=self.default_organization_id, history_days=self.history_days, page_size=self.page_size).initial_sync_transactions(user)
        return RegistrationStart(user=user)

    async def complete(self, *, telegram_id: int, phone: str, first_name: str, last_name: str, middle_name: str | None, birthday: date, email: str | None, consent: bool, gender: str | None = None, sms_enabled: bool = True, push_enabled: bool = True, email_enabled: bool = True):
        if not consent: raise ValueError("Нужно согласие на обработку персональных данных")
        phone = PhoneNormalizationService.normalize(phone); repo = UserRepository(self.session)
        if existing := await repo.by_telegram_id(telegram_id): return existing
        if await repo.by_phone(phone): raise ValueError("Этот номер уже связан с другим Telegram-пользователем")
        user = await repo.create_local(telegram_id=telegram_id, phone=phone, first_name=first_name, last_name=last_name, middle_name=middle_name, birthday=birthday, gender=gender, email=email, consent_at=datetime.now(timezone.utc), sync_status=IikoSyncStatus.pending, sms_enabled=sms_enabled, push_enabled=push_enabled, email_enabled=email_enabled)
        await self.session.commit()
        await self.sync_pending_user(user)
        return user

    async def sync_pending_user(self, user):
        try:
            customer = await self._customer_service().find_or_create_customer(user)
            customer = await self._card_service().ensure_card(user, customer)
            await self._customer_service().sync_customer(user, customer)
            await self.session.commit()
            await LoyaltySyncService(self.session, self.iiko, default_organization_id=self.default_organization_id, history_days=self.history_days, page_size=self.page_size).initial_sync_transactions(user)
            logger.info("Pending iiko registration synced user_id=%s iiko_customer_id=%s organizationId=%s", user.id, user.loyalty_account.iiko_customer_id, self.default_organization_id)
            return True
        except IikoError as exc:
            user.loyalty_account.iiko_sync_status = IikoSyncStatus.pending
            logger.warning("Pending iiko registration deferred user_id=%s organizationId=%s error=%s", user.id, self.default_organization_id, type(exc).__name__)
            await self.session.commit(); return False
        except Exception:
            user.loyalty_account.iiko_sync_status = IikoSyncStatus.error
            logger.exception("Pending iiko registration failed user_id=%s organizationId=%s", user.id, self.default_organization_id)
            await self.session.commit(); return False

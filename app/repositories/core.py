from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (LoyaltyAccount, LoyaltyTransaction, Mailing, MailingRun, NotificationSettings,
                        Purchase, Restaurant, User)
from app.models.entities import IikoSyncStatus, MailingStatus


class UserRepository:
    def __init__(self, session: AsyncSession): self.session = session

    @staticmethod
    def _loaded(): return (selectinload(User.loyalty_account), selectinload(User.notification_settings))

    async def by_telegram_id(self, telegram_id: int):
        return await self.session.scalar(select(User).where(User.telegram_id == telegram_id).options(*self._loaded()))

    async def by_phone(self, phone: str):
        return await self.session.scalar(select(User).where(User.phone == phone).options(*self._loaded()))

    async def create_local(self, *, telegram_id: int, phone: str, first_name: str, last_name: str | None, middle_name: str | None = None, birthday: date | None = None, email: str | None = None, consent_at: datetime | None = None, sync_status: IikoSyncStatus = IikoSyncStatus.pending):
        user = User(telegram_id=telegram_id, phone=phone, first_name=first_name, last_name=last_name, middle_name=middle_name, birthday=birthday, email=email, personal_data_consent_at=consent_at)
        user.loyalty_account = LoyaltyAccount(last_known_balance=0, iiko_sync_status=sync_status, categories=[])
        user.notification_settings = NotificationSettings()
        self.session.add(user); await self.session.flush(); return user

    async def active(self):
        stmt = select(User).where(User.is_active.is_(True)).options(*self._loaded())
        return list((await self.session.scalars(stmt)).all())

    async def pending_iiko(self):
        stmt = select(User).join(LoyaltyAccount).where(User.is_active.is_(True), LoyaltyAccount.iiko_sync_status.in_([IikoSyncStatus.pending, IikoSyncStatus.error])).options(*self._loaded())
        return list((await self.session.scalars(stmt)).all())

    async def birthdays(self, day: int, month: int):
        stmt = select(User).join(NotificationSettings).where(User.is_active.is_(True), NotificationSettings.holidays_enabled.is_(True), func.extract("day", User.birthday) == day, func.extract("month", User.birthday) == month)
        return list((await self.session.scalars(stmt)).all())


class LoyaltyAccountRepository:
    def __init__(self, session: AsyncSession): self.session = session
    async def by_user_id(self, user_id: int): return await self.session.scalar(select(LoyaltyAccount).where(LoyaltyAccount.user_id == user_id))
    async def by_customer_id(self, customer_id: str): return await self.session.scalar(select(LoyaltyAccount).where(LoyaltyAccount.iiko_customer_id == customer_id))
    async def by_card_number(self, card_number: str): return await self.session.scalar(select(LoyaltyAccount).where(LoyaltyAccount.card_number == card_number))


class RestaurantRepository:
    def __init__(self, session: AsyncSession): self.session = session
    async def active(self): return list((await self.session.scalars(select(Restaurant).where(Restaurant.is_active.is_(True)).order_by(Restaurant.id))).all())
    async def list_all(self): return list((await self.session.scalars(select(Restaurant).order_by(Restaurant.name, Restaurant.id))).all())
    async def by_id(self, restaurant_id: int): return await self.session.get(Restaurant, restaurant_id)
    async def by_iiko_id(self, organization_id: str): return await self.session.scalar(select(Restaurant).where(Restaurant.iiko_organization_id == organization_id))
    async def upsert_organization(self, organization):
        item = await self.by_iiko_id(organization.id)
        api_values = {"name": organization.name, "address": organization.restaurant_address, "latitude": organization.latitude, "longitude": organization.longitude, "code": organization.code, "inn": organization.inn, "website_url": organization.website_url, "is_active": organization.is_active}
        if item is None:
            item = Restaurant(iiko_organization_id=organization.id, image_name="gallery_10.jpeg", **api_values); self.session.add(item)
        else:
            for key, value in api_values.items():
                if value is not None or key in {"is_active", "name"}: setattr(item, key, value)
        await self.session.flush(); return item
    async def update_local_link(self, restaurant_id: int, field: str, value: str | None):
        allowed_fields = {"delivery_url", "reviews_url"}
        if field not in allowed_fields: raise ValueError("Недопустимое поле ссылки")
        item = await self.by_id(restaurant_id)
        if item is None: raise ValueError("Ресторан не найден")
        setattr(item, field, value)
        await self.session.flush(); return item


class LoyaltyTransactionRepository:
    def __init__(self, session: AsyncSession): self.session = session
    async def by_external_id(self, external_id: str): return await self.session.scalar(select(LoyaltyTransaction).where(LoyaltyTransaction.external_id == external_id))
    async def by_order(self, user_id: int, pos_order_id: str):
        stmt = select(LoyaltyTransaction).where(LoyaltyTransaction.user_id == user_id, LoyaltyTransaction.pos_order_id == pos_order_id)
        return list((await self.session.scalars(stmt)).all())
    async def upsert(self, user_id: int, transaction):
        item = await self.by_external_id(transaction.id)
        values = {"user_id": user_id, "wallet_id": transaction.wallet_id, "program_id": transaction.program_id, "type": str(transaction.type), "type_name": transaction.type_name, "amount": transaction.amount, "comment": transaction.comment, "balance_before": transaction.balance_before, "balance_after": transaction.balance_after, "order_number": transaction.order_number, "order_sum": transaction.order_sum, "pos_order_id": transaction.pos_order_id, "organization_id": transaction.organization_id, "terminal_group_id": transaction.terminal_group_id, "is_delivery": transaction.is_delivery, "is_ignored": transaction.is_ignored, "created_at_iiko": transaction.when_created, "when_created_order": transaction.when_created_order, "revision": transaction.revision}
        created = item is None
        if created: item = LoyaltyTransaction(external_id=transaction.id, **values); self.session.add(item)
        else:
            for key, value in values.items(): setattr(item, key, value)
        await self.session.flush(); return item, created


class PurchaseRepository:
    def __init__(self, session: AsyncSession): self.session = session
    async def by_pos_order_id(self, pos_order_id: str): return await self.session.scalar(select(Purchase).where(Purchase.pos_order_id == pos_order_id))
    async def upsert_summary(self, user_id: int, transaction, restaurant_id: int | None, *, bonus_earned=0, bonus_spent=0):
        item = await self.by_pos_order_id(transaction.pos_order_id)
        values = {
            "user_id": user_id,
            "restaurant_id": restaurant_id,
            "order_number": transaction.order_number,
            "amount": transaction.order_sum if transaction.order_sum is not None else transaction.amount,
            "bonus_earned": bonus_earned,
            "bonus_spent": bonus_spent,
            "purchased_at": transaction.when_created_order or transaction.created_at_iiko,
            "is_delivery": bool(transaction.is_delivery),
        }
        if item is None:
            item = Purchase(pos_order_id=transaction.pos_order_id, **values)
            self.session.add(item)
        else:
            for key, value in values.items():
                if value is not None or key in {"bonus_earned", "bonus_spent", "is_delivery"}:
                    setattr(item, key, value)
        await self.session.flush(); return item
    async def page(self, user_id: int, page: int, size: int = 10):
        stmt = select(Purchase).where(Purchase.user_id == user_id).options(selectinload(Purchase.restaurant)).order_by(Purchase.purchased_at.desc()).offset(page * size).limit(size + 1)
        rows = list((await self.session.scalars(stmt)).all()); return rows[:size], len(rows) > size


class MailingRepository:
    def __init__(self, session: AsyncSession): self.session = session
    async def create(self, **data): item = Mailing(**data); self.session.add(item); await self.session.flush(); return item
    async def get(self, item_id: int): return await self.session.get(Mailing, item_id, options=[selectinload(Mailing.runs)])
    async def list(self): return list((await self.session.scalars(select(Mailing).options(selectinload(Mailing.runs)).order_by(Mailing.created_at.desc()))).all())
    async def page(self, page: int, size: int):
        stmt = select(Mailing).order_by(Mailing.created_at.desc(), Mailing.id.desc()).offset(page * size).limit(size + 1)
        rows = list((await self.session.scalars(stmt)).all())
        return rows[:size], len(rows) > size
    async def due(self, now): return list((await self.session.scalars(select(Mailing).where(Mailing.status == MailingStatus.scheduled, Mailing.scheduled_at <= now))).all())
    async def delete(self, item): await self.session.delete(item)
    async def add_run(self, **data): run = MailingRun(**data); self.session.add(run); await self.session.flush(); return run

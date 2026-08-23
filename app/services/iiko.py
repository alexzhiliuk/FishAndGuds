import logging
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from secrets import randbelow

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.iiko.client import IikoClient
from app.integrations.iiko.dto import CustomerInfo, LoyaltyTransaction
from app.integrations.iiko.exceptions import IikoError
from app.models.entities import IikoSyncStatus
from app.repositories import LoyaltyAccountRepository, LoyaltyTransactionRepository, PurchaseRepository, RestaurantRepository
from app.services.phone import PhoneNormalizationService

logger = logging.getLogger(__name__)


class CustomerService:
    def __init__(self, session: AsyncSession, iiko: IikoClient, default_organization_id: str):
        self.session, self.iiko, self.default_organization_id = session, iiko, default_organization_id

    async def get_by_phone(self, phone: str):
        organization_ids = [self.default_organization_id]
        try:
            organization_ids += [item.id for item in await self.iiko.get_organizations() if item.id != self.default_organization_id]
        except IikoError:
            raise
        for organization_id in organization_ids:
            if customer := await self.iiko.get_customer_info(organization_id=organization_id, phone=phone): return customer
        return None

    async def sync_customer(self, user, customer: CustomerInfo):
        account = user.loyalty_account
        account.iiko_customer_id = customer.id
        account.last_known_balance = customer.bonus_balance()
        account.balance_synced_at = datetime.now(timezone.utc)
        account.categories = [item.model_dump(mode="json") for item in customer.categories]
        account.iiko_sync_status = IikoSyncStatus.synced
        account.iiko_synced_at = datetime.now(timezone.utc)
        if customer.name: user.first_name = customer.name
        if customer.surname: user.last_name = customer.surname
        if customer.middle_name: user.middle_name = customer.middle_name
        if customer.birthday: user.birthday = customer.birthday.date() if isinstance(customer.birthday, datetime) else customer.birthday
        if customer.email: user.email = customer.email
        if customer.phone:
            try: user.phone = PhoneNormalizationService.normalize(customer.phone)
            except ValueError: logger.warning("iiko returned invalid phone user_id=%s", user.id)
        await self.session.flush(); return customer

    async def find_or_create_customer(self, user):
        customer = await self.get_by_phone(user.phone)
        if customer is None:
            raise IikoError("iiko customer creation is temporarily disabled")
        return await self.sync_customer(user, customer)


class CardService:
    def __init__(self, session: AsyncSession, iiko: IikoClient, default_organization_id: str): self.session, self.iiko, self.default_organization_id = session, iiko, default_organization_id

    @staticmethod
    def _new_card_number(): return str(10**15 + randbelow(9 * 10**15))

    async def sync_card(self, user, customer: CustomerInfo):
        account = user.loyalty_account
        if customer.cards:
            card = customer.cards[0]
            account.iiko_card_id, account.card_number, account.card_track = card.id, card.number, card.track
            account.qr_payload = card.track or card.number
        await self.session.flush()

    async def ensure_card(self, user, customer: CustomerInfo):
        if customer.cards:
            await self.sync_card(user, customer); return customer
        account_repo = LoyaltyAccountRepository(self.session)
        for _ in range(10):
            number = self._new_card_number()
            if await account_repo.by_card_number(number) is None: break
        else: raise RuntimeError("Could not generate a unique loyalty card number")
        track = number
        await self.iiko.add_card(customer_id=customer.id, card_track=track, card_number=number, organization_id=self.default_organization_id)
        refreshed = await self.iiko.get_customer_info(organization_id=self.default_organization_id, customer_id=customer.id)
        if refreshed: await self.sync_card(user, refreshed); return refreshed
        return customer


class RestaurantSyncService:
    def __init__(self, session: AsyncSession, iiko: IikoClient): self.session, self.iiko = session, iiko
    async def sync_organizations(self):
        repo = RestaurantRepository(self.session); organizations = await self.iiko.get_organizations()
        for organization in organizations: await repo.upsert_organization(organization)
        await self.session.commit(); logger.info("iiko organizations sync count=%s", len(organizations)); return organizations


class PurchaseService:
    def __init__(self, session: AsyncSession, iiko: IikoClient | None = None): self.session, self.iiko = session, iiko
    async def sync_purchase_from_transactions(self, user, pos_order_id: str):
        transactions = await LoyaltyTransactionRepository(self.session).by_order(user.id, pos_order_id)
        usable = [item for item in transactions if not item.is_ignored]
        close_transactions = [item for item in usable if item.type == "5" or (item.type_name or "").lower() == "closeorder"]
        if not close_transactions:
            close_transactions = [item for item in usable if item.order_sum is not None]
        if not close_transactions:
            return False
        close_transaction = max(close_transactions, key=lambda item: item.created_at_iiko)
        earned = sum((item.amount for item in usable if item.amount > 0 and (
            item.type == "10" or (item.type_name or "").lower() == "refillwalletfromorder" or
            item.balance_before is not None and item.balance_after is not None and item.balance_after > item.balance_before
        )), start=Decimal("0"))
        spent = sum((-item.amount for item in usable if item.amount < 0 and (
            item.type == "8" or (item.type_name or "").lower() == "payfromwallet" or
            item.balance_before is not None and item.balance_after is not None and item.balance_after < item.balance_before
        )), start=Decimal("0"))
        organization_id = close_transaction.organization_id
        restaurant = await RestaurantRepository(self.session).by_iiko_id(organization_id) if organization_id else None
        repo = PurchaseRepository(self.session)
        await repo.upsert_summary(user.id, close_transaction, restaurant.id if restaurant else None, bonus_earned=earned, bonus_spent=spent)
        return True
    async def page(self, user_id: int, page: int): return await PurchaseRepository(self.session).page(user_id, page)


class LoyaltySyncService:
    def __init__(self, session: AsyncSession, iiko: IikoClient, *, default_organization_id: str, history_days: int = 365, page_size: int = 100):
        self.session, self.iiko = session, iiko
        self.default_organization_id, self.history_days, self.page_size = default_organization_id, history_days, page_size

    async def _store(self, user, transactions):
        repo = LoyaltyTransactionRepository(self.session); purchase_service = PurchaseService(self.session, self.iiko); created = 0
        order_ids = set()
        for transaction in transactions:
            _, is_new = await repo.upsert(user.id, transaction); created += int(is_new)
            if transaction.pos_order_id: order_ids.add(transaction.pos_order_id)
        for pos_order_id in order_ids:
            await purchase_service.sync_purchase_from_transactions(user, pos_order_id)
        return created

    async def initial_sync_transactions(self, user):
        now = datetime.now(timezone.utc); start = now - timedelta(days=self.history_days); total = 0; highest = None
        organizations = await self.iiko.get_organizations()
        for organization in organizations:
            page = 0
            while True:
                rows = await self.iiko.get_transactions_by_date(customer_id=user.loyalty_account.iiko_customer_id, date_from=start, date_to=now, page_number=page, page_size=self.page_size, organization_id=organization.id)
                total += await self._store(user, rows)
                if rows:
                    candidate = max(rows, key=lambda item: (item.revision, item.id))
                    if highest is None or (candidate.revision, candidate.id) > (highest.revision, highest.id): highest = candidate
                if len(rows) < self.page_size: break
                page += 1
        if highest:
            user.loyalty_account.last_transaction_revision = highest.revision
            user.loyalty_account.last_transaction_id = highest.id
        await self.session.commit()
        logger.info("Initial loyalty sync user_id=%s iiko_customer_id=%s imported=%s", user.id, user.loyalty_account.iiko_customer_id, total)
        return total

    async def sync_transactions_by_revision(self, user):
        account = user.loyalty_account
        if account.last_transaction_revision is None: return await self.initial_sync_transactions(user)
        created = 0
        while True:
            rows, revision, last_id = await self.iiko.get_transactions_by_revision(customer_id=account.iiko_customer_id, revision=account.last_transaction_revision, last_transaction_id=account.last_transaction_id, page_size=self.page_size, organization_id=self.default_organization_id)
            created += await self._store(user, rows)
            old_cursor = (account.last_transaction_revision, account.last_transaction_id)
            account.last_transaction_revision, account.last_transaction_id = revision, last_id
            if len(rows) < self.page_size or old_cursor == (revision, last_id): break
        await self.session.commit()
        logger.info("Revision loyalty sync user_id=%s iiko_customer_id=%s imported=%s revision=%s", user.id, account.iiko_customer_id, created, account.last_transaction_revision)
        return created

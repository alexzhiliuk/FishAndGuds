import logging
from decimal import Decimal
from datetime import datetime, time, timedelta, timezone
from secrets import randbelow

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.iiko.client import IikoClient
from app.integrations.iiko.dto import CustomerCreate, CustomerInfo
from app.integrations.iiko.exceptions import IikoError, IikoRequestError
from app.models.entities import IikoSyncStatus
from app.repositories import (
    LoyaltyAccountRepository,
    LoyaltyTransactionRepository,
    PurchaseRepository,
    RestaurantRepository,
)
from app.services.phone import PhoneNormalizationService

logger = logging.getLogger(__name__)


class CustomerService:
    def __init__(
        self, session: AsyncSession, iiko: IikoClient, default_organization_id: str
    ):
        self.session, self.iiko, self.default_organization_id = (
            session,
            iiko,
            default_organization_id,
        )

    async def get_by_phone(self, phone: str):
        organization_ids = [self.default_organization_id]
        try:
            organization_ids += [
                item.id
                for item in await self.iiko.get_organizations()
                if item.id != self.default_organization_id
            ]
        except IikoError:
            raise
        for organization_id in organization_ids:
            if customer := await self.iiko.get_customer_info(
                organization_id=organization_id, phone=phone
            ):
                return customer
        return None

    async def sync_customer(self, user, customer: CustomerInfo):
        account = user.loyalty_account
        account.iiko_customer_id = customer.id
        account.last_known_balance = customer.bonus_balance()
        account.balance_synced_at = datetime.now(timezone.utc)
        account.categories = [
            item.model_dump(mode="json") for item in customer.categories
        ]
        account.iiko_sync_status = IikoSyncStatus.synced
        account.iiko_synced_at = datetime.now(timezone.utc)
        if customer.name:
            user.first_name = customer.name
        if customer.surname:
            user.last_name = customer.surname
        if customer.middle_name:
            user.middle_name = customer.middle_name
        if customer.birthday:
            user.birthday = (
                customer.birthday.date()
                if isinstance(customer.birthday, datetime)
                else customer.birthday
            )
        if customer.email:
            user.email = customer.email
        if customer.phone:
            try:
                user.phone = PhoneNormalizationService.normalize(customer.phone)
            except ValueError:
                logger.warning("iiko returned invalid phone user_id=%s", user.id)
        await self.session.flush()
        return customer

    async def find_or_create_customer(self, user):
        customer = await self.get_by_phone(user.phone)
        if customer is None:
            notifications = user.notification_settings
            receives_external_messages = bool(
                notifications is None
                or notifications.sms_enabled
                or notifications.email_enabled
            )
            customer_id = await self.iiko.create_or_update_customer(
                organization_id=self.default_organization_id,
                customer=CustomerCreate(
                    phone=user.phone,
                    name=user.first_name,
                    surName=user.last_name,
                    middleName=user.middle_name,
                    birthday=datetime.combine(user.birthday, time.min)
                    if user.birthday
                    else None,
                    email=user.email,
                    sex={"male": 1, "female": 2}.get(user.gender, 0),
                    consentStatus=1,
                    shouldReceiveLoyaltyInfo=receives_external_messages,
                    shouldReceivePromoActionsInfo=receives_external_messages,
                ),
            )
            customer = await self.iiko.get_customer_info(
                organization_id=self.default_organization_id,
                customer_id=customer_id,
            )
            if customer is None:
                raise IikoError("iiko customer was created but could not be read back")
        return await self.sync_customer(user, customer)


class CardService:
    def __init__(
        self,
        session: AsyncSession,
        iiko: IikoClient,
        default_organization_id: str,
        *,
        number_prefix: str = "9898",
        number_length: int = 8,
        generation_attempts: int = 10,
    ):
        if not number_prefix.isdigit() or number_length <= len(number_prefix):
            raise ValueError("Invalid loyalty card number format")
        if generation_attempts < 1:
            raise ValueError("Card generation attempts must be positive")
        self.session, self.iiko, self.default_organization_id = (
            session,
            iiko,
            default_organization_id,
        )
        self.number_prefix, self.number_length, self.generation_attempts = (
            number_prefix,
            number_length,
            generation_attempts,
        )

    def _new_card_number(self):
        suffix_length = self.number_length - len(self.number_prefix)
        return f"{self.number_prefix}{randbelow(10**suffix_length):0{suffix_length}d}"

    async def _organization_ids(self):
        ids = [self.default_organization_id]
        for organization in await self.iiko.get_organizations():
            if organization.id not in ids:
                ids.append(organization.id)
        return ids

    async def _find_card_owner(self, number: str):
        for organization_id in await self._organization_ids():
            customer = await self.iiko.get_customer_info(
                organization_id=organization_id,
                card_number=number,
            )
            if customer is not None:
                return customer
        return None

    async def sync_card(self, user, customer: CustomerInfo):
        account = user.loyalty_account
        if customer.cards:
            card = customer.cards[0]
            account.iiko_card_id, account.card_number, account.card_track = (
                card.id,
                card.number,
                card.track,
            )
            account.qr_payload = card.track or card.number
        await self.session.flush()

    async def ensure_card(self, user, customer: CustomerInfo):
        if customer.cards:
            await self.sync_card(user, customer)
            return customer
        account_repo = LoyaltyAccountRepository(self.session)
        for _ in range(self.generation_attempts):
            number = self._new_card_number()
            if await account_repo.by_card_number(number) is not None:
                continue
            owner = await self._find_card_owner(number)
            if owner is not None:
                if owner.id == customer.id:
                    await self.sync_card(user, owner)
                    return owner
                continue
            try:
                await self.iiko.add_card(
                    customer_id=customer.id,
                    card_track=number,
                    card_number=number,
                    organization_id=self.default_organization_id,
                )
            except IikoRequestError as exc:
                if exc.status_code not in (400, 409):
                    raise
                owner = await self._find_card_owner(number)
                if owner is None:
                    raise
                if owner.id != customer.id:
                    continue
            refreshed = await self.iiko.get_customer_info(
                organization_id=self.default_organization_id, customer_id=customer.id
            )
            if refreshed and refreshed.cards:
                await self.sync_card(user, refreshed)
                return refreshed
            raise IikoError("iiko card was added but could not be read back")
        raise IikoError("Could not generate a globally unique iiko loyalty card number")


class RestaurantSyncService:
    def __init__(self, session: AsyncSession, iiko: IikoClient):
        self.session, self.iiko = session, iiko

    async def sync_organizations(self):
        repo = RestaurantRepository(self.session)
        organizations = await self.iiko.get_organizations()
        for organization in organizations:
            await repo.upsert_organization(organization)
        await self.session.commit()
        logger.info("iiko organizations sync count=%s", len(organizations))
        return organizations


class PurchaseService:
    def __init__(self, session: AsyncSession, iiko: IikoClient | None = None):
        self.session, self.iiko = session, iiko

    async def sync_purchase_from_transactions(self, user, pos_order_id: str):
        transactions = await LoyaltyTransactionRepository(self.session).by_order(
            user.id, pos_order_id
        )
        usable = [item for item in transactions if not item.is_ignored]
        close_transactions = [
            item
            for item in usable
            if item.type == "5" or (item.type_name or "").lower() == "closeorder"
        ]
        if not close_transactions:
            close_transactions = [item for item in usable if item.order_sum is not None]
        if not close_transactions:
            return False
        close_transaction = max(
            close_transactions, key=lambda item: item.created_at_iiko
        )
        earned = sum(
            (
                item.amount
                for item in usable
                if item.amount > 0
                and (
                    item.type == "10"
                    or (item.type_name or "").lower() == "refillwalletfromorder"
                    or item.balance_before is not None
                    and item.balance_after is not None
                    and item.balance_after > item.balance_before
                )
            ),
            start=Decimal("0"),
        )
        spent = sum(
            (
                -item.amount
                for item in usable
                if item.amount < 0
                and (
                    item.type == "8"
                    or (item.type_name or "").lower() == "payfromwallet"
                    or item.balance_before is not None
                    and item.balance_after is not None
                    and item.balance_after < item.balance_before
                )
            ),
            start=Decimal("0"),
        )
        organization_id = close_transaction.organization_id
        restaurant = (
            await RestaurantRepository(self.session).by_iiko_id(organization_id)
            if organization_id
            else None
        )
        repo = PurchaseRepository(self.session)
        await repo.upsert_summary(
            user.id,
            close_transaction,
            restaurant.id if restaurant else None,
            bonus_earned=earned,
            bonus_spent=spent,
        )
        return True

    async def page(self, user_id: int, page: int):
        return await PurchaseRepository(self.session).page(user_id, page)


class LoyaltySyncService:
    def __init__(
        self,
        session: AsyncSession,
        iiko: IikoClient,
        *,
        default_organization_id: str,
        history_days: int = 365,
        page_size: int = 100,
    ):
        self.session, self.iiko = session, iiko
        self.default_organization_id, self.history_days, self.page_size = (
            default_organization_id,
            history_days,
            page_size,
        )

    async def _store(self, user, transactions):
        repo = LoyaltyTransactionRepository(self.session)
        purchase_service = PurchaseService(self.session, self.iiko)
        created = 0
        order_ids = set()
        for transaction in transactions:
            _, is_new = await repo.upsert(user.id, transaction)
            created += int(is_new)
            if transaction.pos_order_id:
                order_ids.add(transaction.pos_order_id)
        for pos_order_id in order_ids:
            await purchase_service.sync_purchase_from_transactions(user, pos_order_id)
        return created

    async def initial_sync_transactions(self, user):
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=self.history_days)
        total = 0
        highest = None
        organizations = await self.iiko.get_organizations()
        for organization in organizations:
            page = 0
            while True:
                rows = await self.iiko.get_transactions_by_date(
                    customer_id=user.loyalty_account.iiko_customer_id,
                    date_from=start,
                    date_to=now,
                    page_number=page,
                    page_size=self.page_size,
                    organization_id=organization.id,
                )
                total += await self._store(user, rows)
                if rows:
                    candidate = max(rows, key=lambda item: (item.revision, item.id))
                    if highest is None or (candidate.revision, candidate.id) > (
                        highest.revision,
                        highest.id,
                    ):
                        highest = candidate
                if len(rows) < self.page_size:
                    break
                page += 1
        if highest:
            user.loyalty_account.last_transaction_revision = highest.revision
            user.loyalty_account.last_transaction_id = highest.id
        await self.session.commit()
        logger.info(
            "Initial loyalty sync user_id=%s iiko_customer_id=%s imported=%s",
            user.id,
            user.loyalty_account.iiko_customer_id,
            total,
        )
        return total

    async def sync_transactions_by_revision(self, user):
        account = user.loyalty_account
        if account.last_transaction_revision is None:
            return await self.initial_sync_transactions(user)
        created = 0
        while True:
            rows, revision, last_id = await self.iiko.get_transactions_by_revision(
                customer_id=account.iiko_customer_id,
                revision=account.last_transaction_revision,
                last_transaction_id=account.last_transaction_id,
                page_size=self.page_size,
                organization_id=self.default_organization_id,
            )
            created += await self._store(user, rows)
            old_cursor = (
                account.last_transaction_revision,
                account.last_transaction_id,
            )
            account.last_transaction_revision, account.last_transaction_id = (
                revision,
                last_id,
            )
            if len(rows) < self.page_size or old_cursor == (revision, last_id):
                break
        await self.session.commit()
        logger.info(
            "Revision loyalty sync user_id=%s iiko_customer_id=%s imported=%s revision=%s",
            user.id,
            account.iiko_customer_id,
            created,
            account.last_transaction_revision,
        )
        return created

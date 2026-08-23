from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

from app.integrations.iiko.client import IikoClient
from app.integrations.iiko.dto import CustomerCard, CustomerInfo, IikoOrganization, LoyaltyTransaction, WalletBalance
from app.integrations.iiko.exceptions import IikoUnavailableError


class IikoTestDouble(IikoClient):
    """Deterministic iiko substitute available only to unit tests."""

    def __init__(self, *, default_organization_id: str = "926c9ebc-27a9-4297-a970-a692f1af7f37", not_found_phones: set[str] | None = None, unavailable: bool = False):
        self.default_organization_id = default_organization_id
        self.not_found_phones = not_found_phones or set()
        self.unavailable = unavailable
        self.customers: dict[str, CustomerInfo] = {}
        self.add_card_calls = 0

    def _check(self):
        if self.unavailable:
            raise IikoUnavailableError("Test iiko is unavailable")

    async def get_access_token(self):
        self._check(); return "test-access-token"

    async def get_organizations(self):
        self._check()
        return [IikoOrganization(id="65b9dc6a-7bb3-47f6-8f31-807d4006186d", name="Бистро Рыба и гады", websiteUrl="https://bistro.example.com"), IikoOrganization(id=self.default_organization_id, name="Рыба и гады", additionalInfo={"website": "https://restaurant.example.com"})]

    @staticmethod
    def _fingerprint(phone: str):
        return sha256("".join(character for character in phone if character.isdigit()).encode()).hexdigest()[:12]

    def seed_customer(self, phone: str, *, balance: Decimal | None = Decimal("300"), with_card: bool = True):
        fingerprint = self._fingerprint(phone)
        customer = CustomerInfo(id=f"test-customer-{fingerprint}", name="Иван", surname="Иванов", phone=phone, birthday="2000-04-01", cards=[CustomerCard(id=f"card-{fingerprint}", track=fingerprint, number=str(int(fingerprint, 16) % 1_000_000_000_000).zfill(12))] if with_card else [], walletBalances=[] if balance is None else [WalletBalance(id="bonus-wallet", name="Бонусная программа", type=1, balance=balance)], whenRegistered="2026-01-01T00:00:00Z")
        self.customers[phone] = customer
        return customer

    async def get_customer_info(self, *, organization_id: str, phone: str | None = None, customer_id: str | None = None):
        self._check()
        if phone:
            if phone in self.not_found_phones and phone not in self.customers: return None
            return self.customers.get(phone) or self.seed_customer(phone)
        return next((item for item in self.customers.values() if item.id == customer_id), None)

    async def add_card(self, *, customer_id: str, card_track: str, card_number: str, organization_id: str):
        self._check(); self.add_card_calls += 1
        customer = next(item for item in self.customers.values() if item.id == customer_id)
        if not customer.cards: customer.cards.append(CustomerCard(id=str(uuid4()), track=card_track, number=card_number))

    def _transactions(self, customer_id: str):
        suffix = self._fingerprint(customer_id)
        return [LoyaltyTransaction(id=f"welcome-{suffix}", revision=1, type=0, typeName="WelcomeBonus", sum=300, organizationId=self.default_organization_id, isIgnored=False, whenCreated="2026-01-01T10:00:00Z"), LoyaltyTransaction(id=f"category-{suffix}", revision=2, type=0, typeName="SetGuestCategory", sum=0, organizationId=self.default_organization_id, isIgnored=False, whenCreated="2026-01-01T10:01:00Z"), LoyaltyTransaction(id=f"purchase-{suffix}", revision=3, type=1, typeName="Order", sum=15, balanceBefore=300, balanceAfter=315, orderNumber=42, orderSum=150, posOrderId=f"pos-{suffix}", organizationId=self.default_organization_id, terminalGroupId="terminal-1", isDelivery=False, isIgnored=False, whenCreated="2026-08-15T18:30:00Z", whenCreatedOrder="2026-08-15T18:00:00Z")]

    async def get_transactions_by_date(self, *, customer_id: str, date_from: datetime, date_to: datetime, page_number: int, page_size: int, organization_id: str):
        self._check(); rows = self._transactions(customer_id); start = page_number * page_size; return rows[start:start + page_size]

    async def get_transactions_by_revision(self, *, customer_id: str, revision: int, last_transaction_id: str | None, page_size: int, organization_id: str):
        self._check(); rows = [item for item in self._transactions(customer_id) if item.revision >= revision]
        if last_transaction_id: rows = [item for item in rows if item.id != last_transaction_id]
        rows = rows[:page_size]; last = rows[-1] if rows else None
        return rows, last.revision if last else revision, last.id if last else last_transaction_id

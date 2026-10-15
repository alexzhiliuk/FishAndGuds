import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.integrations.iiko.exceptions import IikoUnavailableError
from app.models import LoyaltyTransaction
from app.repositories import UserRepository
from app.services import LoyaltyService, PurchaseService, RegistrationService
from app.services import user_cache
from tests.iiko_double import IikoTestDouble

ORG = "926c9ebc-27a9-4297-a970-a692f1af7f37"


class CountingClient(IikoTestDouble):
    def __init__(self):
        super().__init__(default_organization_id=ORG)
        self.info_ids = []
        self.date_ids = []
        self.revision_ids = []

    async def get_customer_info(self, **kwargs):
        self.info_ids.append(kwargs.get("customer_id") or kwargs.get("phone"))
        await asyncio.sleep(0)
        return await super().get_customer_info(**kwargs)

    async def get_transactions_by_date(self, **kwargs):
        self.date_ids.append(kwargs["customer_id"])
        await asyncio.sleep(0)
        return await super().get_transactions_by_date(**kwargs)

    async def get_transactions_by_revision(self, **kwargs):
        self.revision_ids.append(kwargs["customer_id"])
        await asyncio.sleep(0)
        return await super().get_transactions_by_revision(**kwargs)


async def local_user(session, client, telegram_id=42):
    phone = f"+3752912345{telegram_id:02d}"
    customer = client.seed_customer(phone)
    user = await UserRepository(session).create_local(
        telegram_id=telegram_id,
        phone=phone,
        first_name="Cached name",
        last_name=None,
    )
    user.loyalty_account.iiko_customer_id = customer.id
    user.loyalty_account.last_known_balance = 10
    await session.commit()
    return user


async def test_profile_refreshes_only_requested_user_then_reuses_cache_for_five_minutes(
    session,
):
    client = CountingClient()
    user = await local_user(session, client)
    other = await local_user(session, client, 43)
    profile = LoyaltyService(session, client, ORG)
    assert (await profile.get_profile(42))["balance"] == Decimal("300")
    client.customers[user.phone].wallet_balances[0].balance = Decimal("700")
    assert (await profile.get_profile(42))["balance"] == Decimal("300")
    assert client.info_ids == [user.loyalty_account.iiko_customer_id]
    assert other.loyalty_account.balance_synced_at is None
    assert not client.date_ids and not client.revision_ids
    user.loyalty_account.balance_synced_at -= timedelta(seconds=301)
    await session.commit()
    assert (await profile.get_profile(42))["balance"] == Decimal("700")
    assert len(client.info_ids) == 2


async def test_history_refreshes_on_open_and_pagination_uses_cache(session):
    client = CountingClient()
    user = await local_user(session, client)
    history = PurchaseService(session, client, ORG)
    rows, has_next = await history.page_for_telegram(42, 0)
    assert len(rows) == 1 and not has_next
    assert client.date_ids == [user.loyalty_account.iiko_customer_id] * 2
    await history.page_for_telegram(42, 1)
    await history.page_for_telegram(42, 0)
    assert len(client.date_ids) == 2 and not client.revision_ids
    assert user.loyalty_account.balance_synced_at is None
    user.loyalty_account.transactions_synced_at -= timedelta(seconds=301)
    await session.commit()
    await history.page_for_telegram(42, 0)
    assert client.revision_ids == [user.loyalty_account.iiko_customer_id]
    assert len(client.date_ids) == 2  # Existing cursor imports only changes.
    assert not client.info_ids


async def test_cache_is_independent_for_different_users_and_data_types(session):
    client = CountingClient()
    first = await local_user(session, client)
    second = await local_user(session, client, 43)
    await LoyaltyService(session, client, ORG).get_profile(42)
    await PurchaseService(session, client, ORG).page_for_telegram(42, 0)
    assert client.info_ids == [first.loyalty_account.iiko_customer_id]
    assert client.date_ids == [first.loyalty_account.iiko_customer_id] * 2
    assert second.loyalty_account.transactions_synced_at is None
    await PurchaseService(session, client, ORG).page_for_telegram(43, 0)
    assert client.date_ids[-2:] == [second.loyalty_account.iiko_customer_id] * 2


async def test_empty_history_is_also_cached(session):
    class EmptyClient(CountingClient):
        def _transactions(self, customer_id):
            return []

    client = EmptyClient()
    user = await local_user(session, client)
    history = PurchaseService(session, client, ORG)
    assert await history.page_for_telegram(42, 0) == ([], False)
    assert user.loyalty_account.transactions_synced_at is not None
    assert await history.page_for_telegram(42, 0) == ([], False)
    assert len(client.date_ids) == 2


async def test_zero_balance_is_cached(session):
    client = CountingClient()
    user = await local_user(session, client)
    client.customers[user.phone].wallet_balances[0].balance = Decimal("0")
    profile = LoyaltyService(session, client, ORG)
    assert (await profile.get_profile(42))["balance"] == 0
    assert (await profile.get_profile(42))["balance"] == 0
    assert len(client.info_ids) == 1


async def test_failed_profile_refresh_keeps_old_data_and_does_not_mark_cache_fresh(
    session,
):
    client = CountingClient()
    await local_user(session, client)
    client.unavailable = True
    data = await LoyaltyService(session, client, ORG).get_profile(42)
    assert data["balance"] == 10
    assert data["card"].balance_synced_at is None
    client.unavailable = False
    assert (await LoyaltyService(session, client, ORG).get_profile(42))[
        "balance"
    ] == 300
    assert len(client.info_ids) == 2


async def test_failed_history_refresh_rolls_back_partial_import_and_retries_on_next_request(
    session,
):
    class PartialFailureClient(CountingClient):
        fail = True

        async def get_transactions_by_date(self, **kwargs):
            if self.fail and kwargs["page_number"] == 1:
                raise IikoUnavailableError("Controlled outage after first page")
            return await super().get_transactions_by_date(**kwargs)

    client = PartialFailureClient()
    await local_user(session, client)
    history = PurchaseService(session, client, ORG, page_size=1)
    assert await history.page_for_telegram(42, 0) == ([], False)
    user = await UserRepository(session).by_telegram_id(42)
    assert user.loyalty_account.transactions_synced_at is None
    assert user.loyalty_account.last_transaction_revision is None
    assert await session.scalar(select(func.count(LoyaltyTransaction.id))) == 0
    client.fail = False
    assert len((await history.page_for_telegram(42, 0))[0]) == 1
    assert await session.scalar(select(func.count(LoyaltyTransaction.id))) == 3


async def test_registration_and_pending_recovery_do_not_import_history(session):
    client = CountingClient()
    registration = RegistrationService(session, client, default_organization_id=ORG)
    result = await registration.start(42, "+375291234567")
    assert result.user.loyalty_account.transactions_synced_at is None
    assert not client.date_ids
    assert await registration.sync_pending_user(result.user)
    assert not client.date_ids and not client.revision_ids


@pytest.mark.parametrize("kind", ["profile", "transactions"])
async def test_parallel_user_requests_coalesce_refresh(tmp_path, kind):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'cache.db').as_posix()}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    client = CountingClient()
    try:
        async with sessions() as session:
            await local_user(session, client)

        async def request():
            async with sessions() as session:
                if kind == "profile":
                    return (await LoyaltyService(session, client, ORG).get_profile(42))[
                        "balance"
                    ]
                return len(
                    (
                        await PurchaseService(session, client, ORG).page_for_telegram(
                            42, 0
                        )
                    )[0]
                )

        results = await asyncio.gather(request(), request(), request())
        assert results == ([Decimal("300")] * 3 if kind == "profile" else [1] * 3)
        assert len(client.info_ids) == (1 if kind == "profile" else 0)
        assert len(client.date_ids) == (2 if kind == "transactions" else 0)
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "age,expected", [(0, True), (299, True), (300, False), (301, False), (-1, False)]
)
def test_cache_expires_exactly_at_five_minutes(monkeypatch, age, expected):
    now = datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(user_cache, "datetime", Clock)
    timestamp = now - timedelta(seconds=age)
    assert user_cache.user_data_is_fresh(timestamp) is expected
    assert user_cache.user_data_is_fresh(timestamp.replace(tzinfo=None)) is expected

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from decimal import Decimal
import json
from types import SimpleNamespace

import httpx
import pytest

from app.integrations.iiko.dto import CustomerCreate, CustomerInfo, WalletBalance
from app.integrations.iiko.real import RealIikoClient
from app.integrations.iiko import real
from app.integrations.iiko.exceptions import IikoRequestError


@pytest.fixture(autouse=True)
def request_clock(monkeypatch):
    clock = SimpleNamespace(now=100.0, sleeps=[])

    async def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(real, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(
        real, "asyncio", SimpleNamespace(Lock=asyncio.Lock, sleep=sleep)
    )
    return clock


def mock_client(handler, **kwargs):
    return RealIikoClient(
        base_url="https://example.test",
        api_key="key",
        app_id="app",
        client_secret="secret",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


async def test_read_retries_429_and_honors_retry_after(request_clock):
    times = []

    def handler(request):
        if request.url.path == "/api/v2/access_token":
            return httpx.Response(200, json={"token": "token"})
        times.append(request_clock.now)
        if len(times) == 1:
            return httpx.Response(429, headers={"Retry-After": "5"}, json={})
        return httpx.Response(200, json={"organizations": []})

    client = mock_client(handler)
    try:
        assert await client.get_organizations() == []
        assert times == [101.0, 106.0]
        assert request_clock.sleeps == [1.0, 5.0]
    finally:
        await client.close()


async def test_retry_budget_and_shared_cooldown(request_clock):
    calls = []

    def handler(request):
        if request.url.path == "/api/v2/access_token":
            return httpx.Response(200, json={"token": "token"})
        calls.append(request_clock.now)
        return httpx.Response(429, json={})

    client = mock_client(handler, rate_limit_retries=2)
    try:
        with pytest.raises(IikoRequestError) as error:
            await client.get_organizations()
        assert error.value.status_code == 429
        assert calls == [101.0, 102.0, 104.0]
        with pytest.raises(IikoRequestError):
            await client.get_customer_info(organization_id="org", customer_id="u")
        # The next endpoint honors the previous endpoint's cooldown too.
        assert calls[3] == 108.0
    finally:
        await client.close()


async def test_long_retry_after_fails_fast_without_early_requests(request_clock):
    calls = 0

    def handler(request):
        nonlocal calls
        if request.url.path == "/api/v2/access_token":
            return httpx.Response(200, json={"token": "token"})
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "120"}, json={})
        return httpx.Response(200, json={"organizations": []})

    client = mock_client(handler)
    try:
        with pytest.raises(IikoRequestError):
            await client.get_organizations()
        with pytest.raises(IikoRequestError):
            await client.get_organizations()
        assert calls == 1
        assert request_clock.sleeps == [1.0]
        request_clock.now += 120
        assert await client.get_organizations() == []
        assert calls == 2
    finally:
        await client.close()


@pytest.mark.parametrize("endpoint", ["create_or_update", "card/add"])
async def test_writes_are_not_replayed_after_429(endpoint):
    calls = 0

    def handler(request):
        nonlocal calls
        if request.url.path == "/api/v2/access_token":
            return httpx.Response(200, json={"token": "token"})
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, json={})

    client = mock_client(handler)
    try:
        with pytest.raises(IikoRequestError):
            await client._request(f"/api/1/loyalty/iiko/customer/{endpoint}", {})
        assert calls == 1
    finally:
        await client.close()


async def test_parallel_requests_share_spacing_and_one_token(request_clock):
    times = []
    auth_calls = 0

    def handler(request):
        nonlocal auth_calls
        if request.url.path == "/api/v2/access_token":
            auth_calls += 1
            return httpx.Response(200, json={"token": "token"})
        times.append(request_clock.now)
        return httpx.Response(200, json={"organizations": []})

    client = mock_client(handler)
    try:
        await asyncio.gather(*(client.get_organizations() for _ in range(3)))
        assert auth_calls == 1
        assert times == [101.0, 102.0, 103.0]
    finally:
        await client.close()


async def test_token_endpoint_also_retries_rate_limits():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json={"token": "token"})

    client = mock_client(handler)
    try:
        assert await client.get_access_token() == "token"
        assert calls == 2
    finally:
        await client.close()


@pytest.mark.parametrize("header", ["invalid", "nan", "inf", "-1", "0"])
def test_invalid_retry_after_uses_backoff(header):
    response = httpx.Response(429, headers={"Retry-After": header})
    assert RealIikoClient._retry_after(response, 2) == (
        0.0 if header in {"-1", "0"} else 4.0
    )


def test_http_date_retry_after():
    date = datetime.now(timezone.utc) + timedelta(seconds=10)
    response = httpx.Response(
        429, headers={"Retry-After": format_datetime(date, usegmt=True)}
    )
    assert 8 <= RealIikoClient._retry_after(response, 0) <= 10


@pytest.mark.asyncio
async def test_access_token_is_cached():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/v2/access_token"
        return httpx.Response(200, json={"token": "not-a-jwt", "correlationId": "c1"})

    client = RealIikoClient(
        base_url="https://example.test",
        api_key="key",
        app_id="app",
        client_secret="secret",
        transport=httpx.MockTransport(handler),
    )
    assert await client.get_access_token() == await client.get_access_token()
    assert calls == 1
    await client.close()


@pytest.mark.asyncio
async def test_unauthorized_refreshes_token_once():
    auth_calls = 0
    organization_calls = 0

    def handler(request):
        nonlocal auth_calls, organization_calls
        if request.url.path == "/api/v2/access_token":
            auth_calls += 1
            return httpx.Response(200, json={"token": f"token-{auth_calls}"})
        organization_calls += 1
        if organization_calls == 1:
            return httpx.Response(401, json={"correlationId": "expired"})
        return httpx.Response(200, json={"organizations": []})

    client = RealIikoClient(
        base_url="https://example.test",
        api_key="key",
        app_id="app",
        client_secret="secret",
        transport=httpx.MockTransport(handler),
    )
    assert await client.get_organizations() == []
    assert (auth_calls, organization_calls) == (2, 2)
    await client.close()


@pytest.mark.asyncio
async def test_customer_404_is_not_found():
    def handler(request):
        if request.url.path == "/api/v2/access_token":
            return httpx.Response(200, json={"token": "token"})
        return httpx.Response(404, json={"correlationId": "missing"})

    client = RealIikoClient(
        base_url="https://example.test",
        api_key="key",
        app_id="app",
        client_secret="secret",
        transport=httpx.MockTransport(handler),
    )
    assert (
        await client.get_customer_info(organization_id="org", phone="+375291111111")
        is None
    )
    await client.close()


@pytest.mark.asyncio
async def test_customer_lookup_by_card_number_uses_official_discriminator():
    payloads = []

    def handler(request):
        if request.url.path == "/api/v2/access_token":
            return httpx.Response(200, json={"token": "token"})
        payloads.append(json.loads(request.content))
        return httpx.Response(404, json={"correlationId": "missing"})

    client = RealIikoClient(
        base_url="https://example.test",
        api_key="key",
        app_id="app",
        client_secret="secret",
        transport=httpx.MockTransport(handler),
    )
    assert (
        await client.get_customer_info(organization_id="org", card_number="98981234")
        is None
    )
    assert payloads == [
        {"organizationId": "org", "type": "cardNumber", "cardNumber": "98981234"}
    ]
    await client.close()


@pytest.mark.asyncio
async def test_create_customer_sends_registration_fields_to_iiko():
    payloads = []

    def handler(request):
        if request.url.path == "/api/v2/access_token":
            return httpx.Response(200, json={"token": "token"})
        assert request.url.path == "/api/1/loyalty/iiko/customer/create_or_update"
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "customer-id"})

    client = RealIikoClient(
        base_url="https://example.test",
        api_key="key",
        app_id="app",
        client_secret="secret",
        transport=httpx.MockTransport(handler),
    )
    customer_id = await client.create_or_update_customer(
        organization_id="org",
        customer=CustomerCreate(
            phone="+375291111111",
            name="Иван",
            surName="Иванов",
            birthday=datetime(2000, 1, 2),
            sex=1,
            consentStatus=1,
            shouldReceiveLoyaltyInfo=False,
            shouldReceivePromoActionsInfo=False,
        ),
    )
    assert customer_id == "customer-id"
    assert payloads == [
        {
            "phone": "+375291111111",
            "name": "Иван",
            "surName": "Иванов",
            "birthday": "2000-01-02 00:00:00.000",
            "sex": 1,
            "consentStatus": 1,
            "shouldReceiveLoyaltyInfo": False,
            "shouldReceivePromoActionsInfo": False,
            "organizationId": "org",
        }
    ]
    await client.close()


def test_bonus_balance_uses_only_type_one_wallet():
    customer = CustomerInfo(
        id="1",
        walletBalances=[
            WalletBalance(type=2, balance=Decimal("999")),
            WalletBalance(type=1, balance=Decimal("12.50")),
        ],
    )
    assert customer.bonus_balance() == Decimal("12.50")


def test_missing_bonus_wallet_means_zero_balance():
    customer = CustomerInfo(
        id="1", walletBalances=[WalletBalance(type=2, balance=Decimal("999"))]
    )
    assert customer.bonus_balance() == Decimal("0")

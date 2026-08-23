from decimal import Decimal

import httpx
import pytest

from app.integrations.iiko.dto import CustomerInfo, WalletBalance
from app.integrations.iiko.real import RealIikoClient


@pytest.mark.asyncio
async def test_access_token_is_cached():
    calls = 0
    def handler(request):
        nonlocal calls; calls += 1
        assert request.url.path == "/api/v2/access_token"
        return httpx.Response(200, json={"token": "not-a-jwt", "correlationId": "c1"})
    client = RealIikoClient(base_url="https://example.test", api_key="key", app_id="app", client_secret="secret", transport=httpx.MockTransport(handler))
    assert await client.get_access_token() == await client.get_access_token()
    assert calls == 1
    await client.close()


@pytest.mark.asyncio
async def test_unauthorized_refreshes_token_once():
    auth_calls = 0; organization_calls = 0
    def handler(request):
        nonlocal auth_calls, organization_calls
        if request.url.path == "/api/v2/access_token":
            auth_calls += 1; return httpx.Response(200, json={"token": f"token-{auth_calls}"})
        organization_calls += 1
        if organization_calls == 1: return httpx.Response(401, json={"correlationId": "expired"})
        return httpx.Response(200, json={"organizations": []})
    client = RealIikoClient(base_url="https://example.test", api_key="key", app_id="app", client_secret="secret", transport=httpx.MockTransport(handler))
    assert await client.get_organizations() == []
    assert (auth_calls, organization_calls) == (2, 2)
    await client.close()


@pytest.mark.asyncio
async def test_customer_404_is_not_found():
    def handler(request):
        if request.url.path == "/api/v2/access_token": return httpx.Response(200, json={"token": "token"})
        return httpx.Response(404, json={"correlationId": "missing"})
    client = RealIikoClient(base_url="https://example.test", api_key="key", app_id="app", client_secret="secret", transport=httpx.MockTransport(handler))
    assert await client.get_customer_info(organization_id="org", phone="+375291111111") is None
    await client.close()


def test_bonus_balance_uses_only_type_one_wallet():
    customer = CustomerInfo(id="1", walletBalances=[WalletBalance(type=2, balance=Decimal("999")), WalletBalance(type=1, balance=Decimal("12.50"))])
    assert customer.bonus_balance() == Decimal("12.50")


def test_missing_bonus_wallet_means_zero_balance():
    customer = CustomerInfo(id="1", walletBalances=[WalletBalance(type=2, balance=Decimal("999"))])
    assert customer.bonus_balance() == Decimal("0")

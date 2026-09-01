from app.integrations.iiko.cached import CachedIikoClient
from tests.iiko_double import IikoTestDouble


class MemoryCache:
    def __init__(self):
        self.values = {}
        self.writes = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, ttl_seconds):
        self.values[key] = value
        self.writes.append((key, ttl_seconds))


class CountingIikoTestDouble(IikoTestDouble):
    def __init__(self):
        super().__init__()
        self.organization_calls = 0
        self.customer_calls = 0

    async def get_organizations(self):
        self.organization_calls += 1
        return await super().get_organizations()

    async def get_customer_info(self, **kwargs):
        self.customer_calls += 1
        return await super().get_customer_info(**kwargs)


def cached_client(backend, cache):
    return CachedIikoClient(backend, cache, organizations_ttl=1800)


async def test_organizations_are_cached_with_ttl():
    backend = CountingIikoTestDouble()
    cache = MemoryCache()
    client = cached_client(backend, cache)

    first = await client.get_organizations()
    second = await client.get_organizations()

    assert first == second
    assert backend.organization_calls == 1
    assert cache.writes == [(CachedIikoClient.ORGANIZATIONS_KEY, 1800)]


async def test_customer_balance_is_never_cached():
    backend = CountingIikoTestDouble()
    cache = MemoryCache()
    client = cached_client(backend, cache)

    await client.get_customer_info(
        organization_id=backend.default_organization_id, phone="+375291111111"
    )
    await client.get_customer_info(
        organization_id=backend.default_organization_id, phone="+375291111111"
    )

    assert backend.customer_calls == 2
    assert cache.writes == []

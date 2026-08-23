from app.config import Settings
from app.cache import RedisJsonCache
from app.integrations.iiko.cached import CachedIikoClient
from app.integrations.iiko.client import IikoClient
from app.integrations.iiko.real import RealIikoClient


def create_iiko_client(settings: Settings, cache: RedisJsonCache | None = None) -> IikoClient:
    backend = RealIikoClient(base_url=settings.iiko_base_url, api_key=settings.iiko_api_key, app_id=settings.iiko_app_id, client_secret=settings.iiko_client_secret, timeout=settings.iiko_timeout_seconds)
    if cache is None:
        return backend
    return CachedIikoClient(backend, cache, organizations_ttl=settings.iiko_organizations_cache_ttl_seconds)

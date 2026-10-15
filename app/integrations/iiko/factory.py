from app.config import Settings
from app.cache import RedisJsonCache
from app.integrations.iiko.cached import CachedIikoClient
from app.integrations.iiko.client import IikoClient
from app.integrations.iiko.real import RealIikoClient


def create_iiko_client(
    settings: Settings, cache: RedisJsonCache | None = None
) -> IikoClient:
    backend = RealIikoClient(
        base_url=settings.iiko_base_url,
        api_key=settings.iiko_api_key,
        app_id=settings.iiko_app_id,
        client_secret=settings.iiko_client_secret,
        timeout=settings.iiko_timeout_seconds,
        request_interval=settings.iiko_request_interval_seconds,
        rate_limit_retries=settings.iiko_rate_limit_retries,
        max_rate_limit_wait=settings.iiko_max_rate_limit_wait_seconds,
    )
    if cache is None:
        return backend
    return CachedIikoClient(
        backend, cache, organizations_ttl=settings.iiko_organizations_cache_ttl_seconds
    )

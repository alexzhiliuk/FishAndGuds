import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from weakref import WeakValueDictionary


USER_CACHE_TTL_SECONDS = 300
_refresh_locks: WeakValueDictionary[tuple[str, int], asyncio.Lock] = (
    WeakValueDictionary()
)


def user_data_is_fresh(updated_at: datetime | None) -> bool:
    if updated_at is None:
        return False
    if updated_at.tzinfo is None:
        # SQLite returns naive timestamps; PostgreSQL stores them with timezone.
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - updated_at).total_seconds()
    return 0 <= age < USER_CACHE_TTL_SECONDS


@asynccontextmanager
async def user_refresh_lock(session, user_id: int):
    # Coalesce requests in this process (including local SQLite test instances).
    # A database row lock inside the service also covers multiple PG workers.
    key = (str(session.get_bind().engine.url), user_id)
    lock = _refresh_locks.setdefault(key, asyncio.Lock())
    async with lock:
        yield

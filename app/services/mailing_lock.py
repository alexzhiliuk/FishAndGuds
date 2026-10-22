import asyncio
from contextlib import asynccontextmanager
from weakref import WeakValueDictionary

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class MailingBusy(ValueError):
    pass


_locks: WeakValueDictionary[tuple[str, int], asyncio.Lock] = WeakValueDictionary()


@asynccontextmanager
async def mailing_operation(session, mailing_id: int):
    """Keep a nonblocking lock across the per-recipient transaction commits.

    PostgreSQL uses a dedicated connection: a pooled session connection must never
    be returned to the pool with a session-level advisory lock still held.
    """
    engine = session.bind
    assert isinstance(engine, AsyncEngine)
    key = (str(engine.url), mailing_id)
    lock = _locks.setdefault(key, asyncio.Lock())
    if lock.locked():
        raise MailingBusy(
            "С рассылкой уже выполняется операция. Обновите статус позже."
        )
    async with lock:
        if engine.dialect.name != "postgresql":
            yield
            return
        async with engine.connect() as connection:
            try:
                acquired = await connection.scalar(
                    text("SELECT pg_try_advisory_lock(1296124236, :id)"),
                    {"id": mailing_id},
                )
                await connection.commit()
            except BaseException:
                # Even acquisition can lose its reply after the server took the
                # lock. Never return that connection to the pool in this case.
                await connection.invalidate()
                raise
            if not acquired:
                raise MailingBusy(
                    "С рассылкой уже выполняется операция. Обновите статус позже."
                )
            try:
                yield
            finally:
                try:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(1296124236, :id)"),
                        {"id": mailing_id},
                    )
                    await connection.commit()
                except BaseException:
                    # Invalidate rather than leak an advisory lock into the pool.
                    await connection.invalidate()
                    raise

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.mailing_lock import MailingBusy, mailing_operation


def postgres_session(acquired=True):
    engine = MagicMock(spec=AsyncEngine)
    engine.url = "postgresql+asyncpg://isolated-lock-test"
    engine.dialect = SimpleNamespace(name="postgresql")
    connection = MagicMock()
    connection.__aenter__ = AsyncMock(return_value=connection)
    connection.__aexit__ = AsyncMock(return_value=False)
    connection.scalar = AsyncMock(return_value=acquired)
    connection.commit = AsyncMock()
    connection.execute = AsyncMock()
    connection.invalidate = AsyncMock()
    engine.connect.return_value = connection
    return SimpleNamespace(bind=engine), connection


async def test_postgres_lock_is_retained_until_operation_finishes():
    session, connection = postgres_session()
    async with mailing_operation(session, 1):
        assert "pg_try_advisory_lock" in str(connection.scalar.await_args.args[0])
        assert connection.scalar.await_args.args[1] == {"id": 1}
        connection.execute.assert_not_awaited()
        with pytest.raises(MailingBusy):
            async with mailing_operation(session, 1):
                pytest.fail("Concurrent operation entered")
    assert "pg_advisory_unlock" in str(connection.execute.await_args.args[0])
    assert connection.commit.await_count == 2


async def test_busy_postgres_lock_does_not_unlock_another_workers_lock():
    session, connection = postgres_session(acquired=False)
    with pytest.raises(MailingBusy):
        async with mailing_operation(session, 2):
            pytest.fail("Busy lock entered")
    connection.execute.assert_not_awaited()


async def test_uncertain_lock_acquisition_invalidates_its_connection():
    session, connection = postgres_session()
    connection.commit.side_effect = RuntimeError("commit response lost")
    with pytest.raises(RuntimeError, match="commit response lost"):
        async with mailing_operation(session, 4):
            pytest.fail("Uncertain acquisition entered")
    connection.invalidate.assert_awaited_once()


async def test_lock_cleanup_on_failure_invalidates_connection_if_unlock_fails():
    session, connection = postgres_session()
    with pytest.raises(RuntimeError, match="operation failed"):
        async with mailing_operation(session, 3):
            raise RuntimeError("operation failed")
    connection.execute.assert_awaited_once()
    connection.execute.side_effect = RuntimeError("connection lost")
    with pytest.raises(RuntimeError, match="connection lost"):
        async with mailing_operation(session, 3):
            pass
    connection.invalidate.assert_awaited_once()

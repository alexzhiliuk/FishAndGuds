from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.scheduler import jobs


def scheduler_settings():
    return SimpleNamespace(
        timezone="Europe/Moscow",
        iiko_default_organization_id="org",
        iiko_transaction_history_days=365,
        iiko_transaction_page_size=100,
        iiko_card_number_prefix="9898",
        iiko_card_number_length=8,
        iiko_card_generation_attempts=10,
        admin_ids=(42,),
        assets_dir=Path("."),
    )


async def test_scheduler_has_no_background_user_transaction_sync():
    @asynccontextmanager
    async def factory():
        yield object()

    scheduler = jobs.configure_scheduler(
        bot=object(),
        session_factory=factory,
        iiko=object(),
        settings=scheduler_settings(),
    )
    assert scheduler.get_job("transactions") is None
    assert {job.id for job in scheduler.get_jobs()} == {
        "organizations",
        "pending_iiko",
        "mailings",
        "birthdays",
    }


async def test_scheduled_mailings_include_admins(monkeypatch):
    session = object()

    @asynccontextmanager
    async def factory():
        yield session

    repository = SimpleNamespace(due=AsyncMock(return_value=[SimpleNamespace(id=7)]))
    service = SimpleNamespace(send=AsyncMock())
    monkeypatch.setattr(jobs, "MailingRepository", lambda session: repository)
    monkeypatch.setattr(jobs, "MailingService", lambda session: service)
    scheduler = jobs.configure_scheduler(
        bot=object(),
        session_factory=factory,
        iiko=object(),
        settings=scheduler_settings(),
    )
    await scheduler.get_job("mailings").func()
    service.send.assert_awaited_once()
    assert service.send.await_args.args[0] == 7
    assert service.send.await_args.kwargs["include_telegram_ids"] == (42,)

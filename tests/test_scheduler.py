from contextlib import asynccontextmanager
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.entities import User
from app.scheduler import jobs
from app.services.birthdays import BirthdayGreetingService


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


async def test_birthday_schedule_reloads_updates_and_pauses(session):
    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    settings = scheduler_settings()
    service = BirthdayGreetingService(session)
    await service.set_send_time(time(9, 30))
    scheduler = jobs.configure_scheduler(
        bot=object(), session_factory=factory, iiko=object(), settings=settings
    )
    refresh = jobs.make_birthday_schedule_refresher(scheduler, factory)

    await refresh()
    scheduler.start(paused=True)
    try:
        job = scheduler.get_job("birthdays")
        starts_at = job.next_run_time
        assert starts_at.time() == time(9, 30)
        assert str(starts_at.tzinfo) == settings.timezone
        assert (
            job.trigger.get_next_fire_time(starts_at, starts_at) - starts_at
        ).days == 1

        await service.set_send_time(time(17, 45))
        await refresh()
        assert scheduler.get_job("birthdays").next_run_time.time() == time(17, 45)

        await service.set_enabled(False)
        await refresh()
        assert scheduler.get_job("birthdays").next_run_time is None

        await service.set_send_time(time(8, 10))
        await refresh()
        assert scheduler.get_job("birthdays").next_run_time is None

        await service.set_enabled(True)
        await refresh()
        assert scheduler.get_job("birthdays").next_run_time.time() == time(8, 10)
    finally:
        scheduler.shutdown(wait=False)


@pytest.mark.parametrize(("month", "day"), [(1, 1), (3, 8)])
async def test_birthday_greetings_without_holiday_broadcast(
    session, monkeypatch, month, day
):
    @asynccontextmanager
    async def factory():
        yield session

    birthday_user = SimpleNamespace(id=1, telegram_id=123, first_name="Анна")
    repository = SimpleNamespace(
        birthdays=AsyncMock(return_value=[birthday_user]),
        active=AsyncMock(return_value=[birthday_user]),
    )
    bot = SimpleNamespace(send_message=AsyncMock())
    monkeypatch.setattr(jobs, "UserRepository", lambda session: repository)
    monkeypatch.setattr(
        jobs,
        "datetime",
        SimpleNamespace(now=lambda tz: datetime(2026, month, day, 10, tzinfo=tz)),
    )
    scheduler = jobs.configure_scheduler(
        bot=bot,
        session_factory=factory,
        iiko=object(),
        settings=scheduler_settings(),
    )

    await scheduler.get_job("birthdays").func()

    repository.birthdays.assert_awaited_once_with(day, month)
    bot.send_message.assert_awaited_once_with(
        123,
        "🎉 Анна, поздравляем с днём рождения! Ждём вас в «Рыба и гады».",
        entities=[],
        parse_mode=None,
    )


async def test_birthdays_use_saved_settings_and_only_send_to_active_recipients(
    session, monkeypatch
):
    session.add_all(
        [
            User(
                telegram_id=123,
                phone="+123",
                first_name="Анна🐟",
                birthday=date(1990, 7, 2),
            ),
            User(
                telegram_id=124,
                phone="+124",
                first_name="Иван",
                birthday=date(1980, 7, 2),
                is_active=False,
            ),
            User(
                telegram_id=125,
                phone="+125",
                first_name="Елена",
                birthday=date(1995, 7, 3),
            ),
        ]
    )
    await session.commit()
    service = BirthdayGreetingService(session)
    await service.set_text("{name}, с праздником! [Меню](https://example.com/menu)")
    await service.set_image("telegram-photo")
    await service.set_send_time(time(9, 30))
    await service.set_enabled(False)

    current_time = datetime(2026, 7, 2, 9, 30)
    monkeypatch.setattr(
        jobs,
        "datetime",
        SimpleNamespace(now=lambda tz: current_time.replace(tzinfo=tz)),
    )
    bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
    scheduler = jobs.configure_scheduler(
        bot=bot,
        session_factory=async_sessionmaker(session.bind, expire_on_commit=False),
        iiko=object(),
        settings=scheduler_settings(),
    )
    job = scheduler.get_job("birthdays").func

    await job()
    bot.send_message.assert_not_awaited()
    bot.send_photo.assert_not_awaited()

    await service.set_enabled(True)
    current_time = current_time.replace(minute=29)
    await job()
    bot.send_photo.assert_not_awaited()

    current_time = current_time.replace(minute=30, second=3)
    await job()
    bot.send_photo.assert_awaited_once()
    sent = bot.send_photo.await_args
    assert sent.args == (123, "telegram-photo")
    assert sent.kwargs["caption"] == "Анна🐟, с праздником! Меню"
    assert sent.kwargs["caption_entities"][0].url == "https://example.com/menu"
    assert sent.kwargs["caption_entities"][0].offset == len("Анна, с праздником! ") + 2
    bot.send_message.assert_not_awaited()

    await service.set_send_time(time(11, 30))
    current_time = current_time.replace(hour=11)
    await job()
    bot.send_photo.assert_awaited_once()

    current_time = current_time.replace(day=3)
    await job()
    assert bot.send_photo.await_count == 2
    assert bot.send_photo.await_args.args == (125, "telegram-photo")

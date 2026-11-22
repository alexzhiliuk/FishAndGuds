import logging
from asyncio import Lock
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.mailing_transport import send_mailing
from app.repositories import MailingRepository, UserRepository
from app.services import MailingService, SyncService
from app.services.birthdays import BirthdayGreetingService

logger = logging.getLogger(__name__)
BIRTHDAY_JOB_ID = "birthdays"


def make_birthday_schedule_refresher(
    scheduler: AsyncIOScheduler, session_factory
) -> Callable[[], Awaitable[None]]:
    refresh_lock = Lock()

    async def refresh() -> None:
        async with refresh_lock, session_factory() as session:
            greeting = await BirthdayGreetingService(session).get()
            if greeting.enabled:
                scheduler.reschedule_job(
                    BIRTHDAY_JOB_ID,
                    trigger="cron",
                    hour=greeting.send_time.hour,
                    minute=greeting.send_time.minute,
                )
            else:
                scheduler.pause_job(BIRTHDAY_JOB_ID)

    return refresh


def configure_scheduler(*, bot: Bot, session_factory, iiko, settings):
    scheduler = AsyncIOScheduler(timezone=settings.timezone)

    def sync_service(session):
        return SyncService(
            session,
            iiko,
            default_organization_id=settings.iiko_default_organization_id,
            history_days=settings.iiko_transaction_history_days,
            page_size=settings.iiko_transaction_page_size,
            card_number_prefix=settings.iiko_card_number_prefix,
            card_number_length=settings.iiko_card_number_length,
            card_generation_attempts=settings.iiko_card_generation_attempts,
        )

    async def organizations():
        async with session_factory() as session:
            try:
                await sync_service(session).sync_restaurants()
            except Exception:
                logger.exception("Scheduled organization sync failed")

    async def pending_users():
        async with session_factory() as session:
            try:
                await sync_service(session).retry_pending()
            except Exception:
                logger.exception("Scheduled pending-user sync failed")

    async def due_mailings():
        async with session_factory() as session:
            due_ids = [
                item.id
                for item in await MailingRepository(session).due(
                    datetime.now(timezone.utc)
                )
            ]
            for item_id in due_ids:
                try:
                    await MailingService(session).send(
                        item_id,
                        lambda uid, content: send_mailing(bot, settings, uid, content),
                        include_telegram_ids=settings.admin_ids,
                        scheduled=True,
                    )
                except ValueError:
                    logger.info(
                        "Scheduled mailing skipped id=%s: no longer available", item_id
                    )
                except Exception:
                    await session.rollback()
                    logger.exception("Scheduled mailing failed")

    async def birthdays():
        now = datetime.now(ZoneInfo(settings.timezone))
        async with session_factory() as session:
            greeting = await BirthdayGreetingService(session).claim_for_delivery(now)
            if greeting is None:
                return
            for user in await UserRepository(session).birthdays(now.day, now.month):
                try:
                    await send_mailing(
                        bot,
                        settings,
                        user.telegram_id,
                        greeting.for_recipient(user.first_name),
                    )
                except Exception:
                    logger.exception("Birthday delivery failed user_id=%s", user.id)

    scheduler.add_job(
        due_mailings,
        "interval",
        minutes=1,
        id="mailings",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        organizations,
        "interval",
        minutes=30,
        id="organizations",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        pending_users,
        "interval",
        minutes=5,
        id="pending_iiko",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        birthdays,
        id=BIRTHDAY_JOB_ID,
        next_run_time=None,
        max_instances=1,
        coalesce=True,
    )
    return scheduler

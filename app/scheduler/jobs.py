import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.mailing_transport import send_mailing
from app.repositories import MailingRepository, UserRepository
from app.services import MailingService, SyncService

logger = logging.getLogger(__name__)
HOLIDAYS = {
    (1, 1): "С Новым годом! Желаем ярких вкусов и тёплых встреч!",
    (8, 3): "С праздником весны! Ждём вас за красивым ужином!",
}


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
            for user in await UserRepository(session).birthdays(now.day, now.month):
                try:
                    await bot.send_message(
                        user.telegram_id,
                        f"🎉 {user.first_name}, поздравляем с днём рождения! Ждём вас в «Рыба и гады».",
                    )
                except Exception:
                    logger.exception("Birthday delivery failed user_id=%s", user.id)
            if holiday_text := HOLIDAYS.get((now.day, now.month)):
                active_users = await UserRepository(session).active()
                notified_ids = set()
                for user in active_users:
                    notified_ids.add(user.telegram_id)
                    try:
                        await bot.send_message(
                            user.telegram_id,
                            f"🎉 {user.first_name}, {holiday_text}",
                        )
                    except Exception:
                        logger.exception("Holiday delivery failed user_id=%s", user.id)
                for telegram_id in sorted(set(settings.admin_ids) - notified_ids):
                    try:
                        await bot.send_message(telegram_id, f"🎉 {holiday_text}")
                    except Exception:
                        logger.exception(
                            "Holiday delivery failed telegram_id=%s", telegram_id
                        )

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
        birthdays, "cron", hour=10, minute=0, id="birthdays", max_instances=1
    )
    return scheduler

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.media import resolve_mailing_photo
from app.repositories import MailingRepository, UserRepository
from app.services import MailingService, SyncService

logger = logging.getLogger(__name__)
HOLIDAYS = {
    (1, 1): "С Новым годом! Желаем ярких вкусов и тёплых встреч!",
    (8, 3): "С праздником весны! Ждём вас за красивым ужином!",
}


async def send_message(bot, settings, uid, text, image=None):
    photo = resolve_mailing_photo(image, settings.assets_dir)
    if photo:
        await bot.send_photo(uid, photo, caption=text)
    else:
        await bot.send_message(uid, text)


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

    async def transactions():
        async with session_factory() as session:
            try:
                await sync_service(session).sync_all()
            except Exception:
                logger.exception("Scheduled transaction sync failed")

    async def due_mailings():
        async with session_factory() as session:
            for item in await MailingRepository(session).due(
                datetime.now(timezone.utc)
            ):
                await MailingService(session).send(
                    item.id,
                    lambda uid, text, image: send_message(
                        bot, settings, uid, text, image
                    ),
                    excluded_telegram_ids=settings.admin_ids,
                )

    async def birthdays():
        now = datetime.now(ZoneInfo(settings.timezone))
        async with session_factory() as session:
            for user in await UserRepository(session).birthdays(now.day, now.month):
                if (
                    user.notification_settings
                    and not user.notification_settings.push_enabled
                ):
                    continue
                try:
                    await bot.send_message(
                        user.telegram_id,
                        f"🎉 {user.first_name}, поздравляем с днём рождения! Ждём вас в «Рыба и гады».",
                    )
                except Exception:
                    logger.exception("Birthday delivery failed user_id=%s", user.id)
            if holiday_text := HOLIDAYS.get((now.day, now.month)):
                for user in await UserRepository(session).active():
                    if (
                        user.notification_settings
                        and user.notification_settings.holidays_enabled
                        and user.notification_settings.push_enabled
                    ):
                        try:
                            await bot.send_message(
                                user.telegram_id,
                                f"🎉 {user.first_name}, {holiday_text}",
                            )
                        except Exception:
                            logger.exception(
                                "Holiday delivery failed user_id=%s", user.id
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
        transactions,
        "interval",
        minutes=settings.purchase_sync_interval_minutes,
        id="transactions",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        birthdays, "cron", hour=10, minute=0, id="birthdays", max_instances=1
    )
    return scheduler

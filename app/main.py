import asyncio
import logging
import re
from urllib.parse import urlparse

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage
from redis.asyncio import Redis

from app.api.app import create_api
from app.bot.handlers import admin_router, user_router
from app.bot.middlewares import DatabaseMiddleware
from app.cache import RedisJsonCache
from app.config import get_settings
from app.db.session import create_session_factory
from app.integrations.iiko.factory import create_iiko_client
from app.scheduler.jobs import configure_scheduler
from app.services import RestaurantSyncService


def validate_webhook_settings(url: str, path: str, secret: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RuntimeError("TELEGRAM_WEBHOOK_URL must be a public HTTPS URL")
    if parsed.path.rstrip("/") != path.rstrip("/"):
        raise RuntimeError("TELEGRAM_WEBHOOK_URL path must match TELEGRAM_WEBHOOK_PATH")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
        raise RuntimeError("TELEGRAM_WEBHOOK_SECRET must contain 1-256 characters: A-Z, a-z, 0-9, _ or -")


async def wait_until_server_started(server: uvicorn.Server, task: asyncio.Task, timeout: float = 10.0) -> None:
    async with asyncio.timeout(timeout):
        while not server.started:
            if task.done():
                await task
            await asyncio.sleep(0.05)


async def main():
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")
    validate_webhook_settings(settings.telegram_webhook_url, settings.telegram_webhook_path, settings.telegram_webhook_secret)
    if not all((settings.iiko_api_key, settings.iiko_app_id, settings.iiko_client_secret, settings.iiko_default_organization_id)):
        raise RuntimeError("IIKO_API_KEY, IIKO_APP_ID, IIKO_CLIENT_SECRET and IIKO_DEFAULT_ORGANIZATION_ID are required in production")

    fsm_redis = Redis.from_url(settings.redis_url)
    cache_redis = Redis.from_url(settings.redis_url)
    cache = RedisJsonCache(cache_redis)
    storage = RedisStorage(redis=fsm_redis)
    bot = Bot(settings.bot_token)
    dp = Dispatcher(storage=storage, events_isolation=RedisEventIsolation(redis=fsm_redis))
    factory = create_session_factory(settings)
    iiko = create_iiko_client(settings, cache)
    scheduler = None
    server = None
    api_task = None

    try:
        if not await cache.ping():
            raise RuntimeError("Redis is required for Telegram state storage")
        async with factory() as session:
            try:
                await RestaurantSyncService(session, iiko).sync_organizations()
            except Exception:
                logging.exception("Initial iiko organization sync failed; scheduler will retry")

        middleware = DatabaseMiddleware(factory)
        dp.update.outer_middleware(middleware)
        dp.include_router(admin_router)
        dp.include_router(user_router)
        dp["settings"] = settings
        dp["iiko"] = iiko

        scheduler = configure_scheduler(bot=bot, session_factory=factory, iiko=iiko, settings=settings)
        scheduler.start()
        api = create_api(
            bot=bot,
            dispatcher=dp,
            webhook_path=settings.telegram_webhook_path,
            webhook_secret=settings.telegram_webhook_secret,
            cache=cache,
            session_factory=factory,
        )
        server = uvicorn.Server(uvicorn.Config(api, host=settings.api_host, port=settings.api_port, log_level=settings.log_level.lower()))
        api_task = asyncio.create_task(server.serve())
        await wait_until_server_started(server, api_task)
        await bot.set_webhook(
            url=settings.telegram_webhook_url,
            secret_token=settings.telegram_webhook_secret,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=False,
        )
        logging.info("Application started in webhook mode: %s", settings.telegram_webhook_url)
        await api_task
    finally:
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=False)
        if server is not None:
            server.should_exit = True
        if api_task is not None and not api_task.done():
            await api_task
        close = getattr(iiko, "close", None)
        if close:
            await close()
        await storage.close()
        await cache.close()
        await bot.session.close()
        logging.info("Application stopped")


if __name__ == "__main__":
    asyncio.run(main())

import hmac
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

from app.cache import RedisJsonCache
from app.services import ApplicationSettingsService

logger = logging.getLogger(__name__)
TELEGRAM_UPDATE_DEDUP_TTL_SECONDS = 86_400


def create_api(
    *,
    bot: Bot | None = None,
    dispatcher: Dispatcher | None = None,
    webhook_path: str = "/telegram/webhook",
    webhook_secret: str | None = None,
    cache: RedisJsonCache | None = None,
    session_factory=None,
) -> FastAPI:
    api = FastAPI(title="Restaurant Loyalty Bot", version="1.0.0")

    @api.get("/healthz")
    async def health():
        return {"status": "ok"}

    @api.get("/registration", include_in_schema=False)
    async def registration_page():
        return FileResponse(
            Path(__file__).resolve().parent.parent / "web" / "registration.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    if session_factory is not None:

        @api.get("/registration/config", include_in_schema=False)
        async def registration_config():
            async with session_factory() as session:
                links = await ApplicationSettingsService(session).registration_links()
            return {
                "privacy_policy_url": links[
                    ApplicationSettingsService.PRIVACY_POLICY_URL
                ],
                "loyalty_rules_url": links[
                    ApplicationSettingsService.LOYALTY_RULES_URL
                ],
            }

    if cache is not None:

        @api.get("/readyz")
        async def readiness():
            if not await cache.ping():
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Redis is unavailable",
                )
            return {"status": "ready"}

    if bot is not None and dispatcher is not None and webhook_secret:

        @api.post(webhook_path, include_in_schema=False)
        async def telegram_webhook(
            request: Request,
            x_telegram_bot_api_secret_token: str | None = Header(default=None),
        ):
            received_secret = x_telegram_bot_api_secret_token or ""
            if not hmac.compare_digest(received_secret, webhook_secret):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Invalid webhook secret",
                )
            try:
                payload = await request.json()
                update = Update.model_validate(payload, context={"bot": bot})
            except (ValueError, ValidationError):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid Telegram update",
                ) from None
            if cache is not None:
                claimed = await cache.claim(
                    f"telegram:update:{update.update_id}",
                    TELEGRAM_UPDATE_DEDUP_TTL_SECONDS,
                )
                if not claimed:
                    logger.info(
                        "Duplicate Telegram update ignored update_id=%s",
                        update.update_id,
                    )
                    return {"ok": True}
            try:
                await dispatcher.feed_update(bot, update)
            except TelegramBadRequest as exc:
                if "query is too old" not in str(exc).lower():
                    logger.exception(
                        "Telegram update failed update_id=%s", update.update_id
                    )
                else:
                    logger.warning(
                        "Expired Telegram callback ignored update_id=%s",
                        update.update_id,
                    )
            except Exception:
                # Telegram retries every non-2xx response. Since handlers can already
                # have committed side effects, acknowledge and log instead of
                # executing the same update repeatedly.
                logger.exception(
                    "Telegram update failed and will not be retried update_id=%s",
                    update.update_id,
                )
            return {"ok": True}

    return api

import httpx
import pytest

from app.api.app import create_api


@pytest.mark.asyncio
async def test_healthcheck_without_bot_does_not_expose_webhook():
    app = create_api()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    business_paths = {
        route.path
        for route in app.routes
        if not route.path.startswith("/docs")
        and route.path != "/openapi.json"
        and route.path != "/redoc"
    }
    assert business_paths == {"/healthz", "/registration"}


@pytest.mark.asyncio
async def test_registration_page_is_served():
    app = create_api()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/registration")
    assert response.status_code == 200
    assert "Анкета участника" in response.text
    assert 'type="date"' not in response.text
    assert 'aria-haspopup="dialog"' in response.text
    assert 'aria-label="Месяц"' in response.text
    assert 'aria-label="Год"' in response.text
    assert '<button id="submit" type="submit">' in response.text
    assert '<span class="required-star">*</span>' in response.text


class DispatcherStub:
    def __init__(self):
        self.updates = []

    async def feed_update(self, bot, update):
        self.updates.append((bot, update))


class CacheStub:
    def __init__(self):
        self.keys = set()

    async def ping(self):
        return True

    async def claim(self, key, ttl_seconds):
        if key in self.keys:
            return False
        self.keys.add(key)
        return True


@pytest.mark.asyncio
async def test_telegram_webhook_checks_secret_and_feeds_update():
    bot = object()
    dispatcher = DispatcherStub()
    app = create_api(bot=bot, dispatcher=dispatcher, webhook_secret="test_secret")
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        forbidden = await client.post("/telegram/webhook", json={"update_id": 1})
        accepted = await client.post(
            "/telegram/webhook",
            json={"update_id": 2},
            headers={"X-Telegram-Bot-Api-Secret-Token": "test_secret"},
        )

    assert forbidden.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json() == {"ok": True}
    assert len(dispatcher.updates) == 1
    assert dispatcher.updates[0][0] is bot
    assert dispatcher.updates[0][1].update_id == 2


@pytest.mark.asyncio
async def test_telegram_webhook_rejects_invalid_update():
    app = create_api(
        bot=object(), dispatcher=DispatcherStub(), webhook_secret="test_secret"
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/telegram/webhook",
            content=b"not-json",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test_secret"},
        )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_telegram_webhook_deduplicates_update_id():
    dispatcher = DispatcherStub()
    app = create_api(
        bot=object(),
        dispatcher=dispatcher,
        webhook_secret="test_secret",
        cache=CacheStub(),
    )
    transport = httpx.ASGITransport(app=app)
    headers = {"X-Telegram-Bot-Api-Secret-Token": "test_secret"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/telegram/webhook", json={"update_id": 10}, headers=headers
        )
        duplicate = await client.post(
            "/telegram/webhook", json={"update_id": 10}, headers=headers
        )

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert len(dispatcher.updates) == 1


@pytest.mark.asyncio
async def test_telegram_webhook_acknowledges_handler_error_to_prevent_retries():
    class FailingDispatcher:
        async def feed_update(self, bot, update):
            raise RuntimeError("handler failed after a side effect")

    app = create_api(
        bot=object(),
        dispatcher=FailingDispatcher(),
        webhook_secret="test_secret",
        cache=CacheStub(),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/telegram/webhook",
            json={"update_id": 11},
            headers={"X-Telegram-Bot-Api-Secret-Token": "test_secret"},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

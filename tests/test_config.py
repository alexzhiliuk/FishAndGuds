import pytest

from app.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123", (123,)),
        ("123, 456", (123, 456)),
        ("[123,456]", (123, 456)),
        (123, (123,)),
    ],
)
def test_admin_ids_accept_common_env_formats(raw, expected):
    assert Settings(admin_ids=raw).admin_ids == expected


def test_registration_web_app_uses_public_webhook_origin():
    settings = Settings(telegram_webhook_url="https://bot.example.com/telegram/webhook")
    assert settings.registration_web_app_url == "https://bot.example.com/registration"


def test_bot_display_name_has_expected_default():
    settings = Settings()

    assert settings.bot_display_name == "Клуб «Рыба и Гады»"


@pytest.mark.parametrize(
    "values",
    [
        {"iiko_request_interval_seconds": 0},
        {"iiko_request_interval_seconds": float("nan")},
        {"iiko_rate_limit_retries": -1},
        {"iiko_rate_limit_retries": 11},
        {"iiko_max_rate_limit_wait_seconds": -1},
        {"iiko_max_rate_limit_wait_seconds": 61},
    ],
)
def test_iiko_retry_settings_are_bounded(values):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(**values)

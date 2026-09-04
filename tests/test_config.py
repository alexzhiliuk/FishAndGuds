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


def test_public_restaurant_channels_have_expected_defaults():
    settings = Settings()

    assert settings.bot_display_name == "Клуб «Рыба и Гады»"
    assert settings.privacy_policy_url == "https://rybagady.ru/privacy"
    assert settings.booking_url == "https://473113.restoplace.ws/"
    assert "ryba_i_gady" in settings.delivery_url
    assert settings.contact_phone == "+79818977766"

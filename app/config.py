from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = ""
    bot_display_name: str = "Клуб «Рыба и Гады»"
    database_url: str = "sqlite+aiosqlite:///./loyalty.db"
    admin_ids: Annotated[tuple[int, ...], NoDecode] = ()
    telegram_webhook_url: str = ""
    telegram_webhook_path: str = "/telegram/webhook"
    telegram_webhook_secret: str = ""
    privacy_policy_url: str = "https://rybagady.ru/privacy"
    booking_url: str = "https://473113.restoplace.ws/"
    delivery_url: str = "https://eda.yandex.ru/restaurant/ryba_i_gady"
    reviews_url: str = "https://yandex.ru/maps/org/ryba_i_gady/88547327027/reviews/"
    contact_phone: str = "+79818977766"
    redis_url: str = "redis://redis:6379/0"
    iiko_organizations_cache_ttl_seconds: int = 1800
    iiko_base_url: str = "https://api-ru.iiko.services"
    iiko_api_key: str = ""
    iiko_app_id: str = ""
    iiko_client_secret: str = ""
    iiko_default_organization_id: str = "926c9ebc-27a9-4297-a970-a692f1af7f37"
    iiko_timeout_seconds: float = 15.0
    iiko_transaction_history_days: int = 365
    iiko_transaction_page_size: int = 100
    iiko_card_number_prefix: str = "9898"
    iiko_card_number_length: int = 8
    iiko_card_generation_attempts: int = 10
    purchase_sync_interval_minutes: int = 5
    timezone: str = "Europe/Minsk"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    assets_dir: Path = Path(__file__).parent / "assets"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value):
        if isinstance(value, str):
            cleaned = value.strip().removeprefix("[").removesuffix("]")
            return tuple(
                int(item.strip()) for item in cleaned.split(",") if item.strip()
            )
        if isinstance(value, int):
            return (value,)
        return tuple(value) if value else ()

    @field_validator("telegram_webhook_path")
    @classmethod
    def validate_webhook_path(cls, value: str):
        if not value.startswith("/"):
            raise ValueError("TELEGRAM_WEBHOOK_PATH must start with /")
        return value

    @field_validator("iiko_card_number_prefix")
    @classmethod
    def validate_card_prefix(cls, value: str):
        if not value.isdigit():
            raise ValueError("IIKO_CARD_NUMBER_PREFIX must contain digits only")
        return value

    @model_validator(mode="after")
    def validate_card_format(self):
        if self.iiko_card_number_length <= len(self.iiko_card_number_prefix):
            raise ValueError(
                "IIKO_CARD_NUMBER_LENGTH must be greater than the prefix length"
            )
        if self.iiko_card_generation_attempts < 1:
            raise ValueError("IIKO_CARD_GENERATION_ATTEMPTS must be positive")
        return self

    @property
    def registration_web_app_url(self) -> str:
        parsed = urlsplit(self.telegram_webhook_url)
        return urlunsplit((parsed.scheme, parsed.netloc, "/registration", "", ""))


@lru_cache
def get_settings() -> Settings:
    return Settings()

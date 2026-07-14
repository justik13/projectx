from pydantic_settings import BaseSettings
from typing import List
from pydantic import field_validator


class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS: List[int]
    DB_PATH: str = "./bot_data.db"
    DB_ENCRYPTION_KEY: str = ""
    DEFAULT_DEVICE_LIMIT: int = 2
    REFERRAL_BONUS_DAYS: int = 3
    SUPPORT_USERNAME: str = "@support_username"

    # Platega.io (СБП)
    PLATEGA_MERCHANT_ID: str = ""
    PLATEGA_SECRET: str = ""
    PLATEGA_BASE_URL: str = "https://app.platega.io"
    PLATEGA_CALLBACK_URL: str = ""
    PLATEGA_WEBHOOK_PORT: int = 8080
    PLATEGA_PAYMENT_METHOD: int = 2
    PLATEGA_RETURN_URL: str = "https://t.me/{bot_username}"
    PLATEGA_FAILED_URL: str = "https://t.me/{bot_username}"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore"
    }

    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def parse_admins(cls, v: str | list | int) -> list[int]:
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        elif isinstance(v, int):
            return [v]
        elif isinstance(v, list):
            return [int(x) for x in v]
        return v


_settings = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
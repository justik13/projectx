from pydantic_settings import BaseSettings
from typing import List
import logging

class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS: List[int]
    DB_PATH: str = "./bot_data.db"
    DB_ENCRYPTION_KEY: str = ""
    DEFAULT_DEVICE_LIMIT: int = 3
    REFERRAL_BONUS_DAYS: int = 3
    SUPPORT_USERNAME: str = "@support_username"
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8"
    }
    
    class Config:
        case_sensitive = False

_settings = None

def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

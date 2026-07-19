from config.settings import get_settings


def is_admin(telegram_id: int) -> bool:
    return telegram_id in get_settings().ADMIN_IDS
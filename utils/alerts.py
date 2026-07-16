"""
Утилита для отправки алертов администраторам через Telegram Bot API напрямую.
Используется из сервисов, где нет доступа к экземпляру Bot (PaymentService, Workers).
🔥 ИСПРАВЛЕНО #22: Chargeback alerts и критические системные уведомления.
"""
import aiohttp
import logging
from config.settings import get_settings

logger = logging.getLogger(__name__)

# Timeout для HTTP-запросов к Telegram API
_ALERT_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def send_admin_alert(text: str, parse_mode: str = "HTML") -> None:
    """
    Отправляет сообщение всем администраторам через Bot API напрямую.
    Используется когда:
    - PaymentService получил CHARGEBACK
    - Worker обнаружил критическую проблему
    - CircuitBreaker перешёл в OPEN (fallback если bot_ref недоступен)
    
    Args:
        text: Текст сообщения (HTML по умолчанию)
        parse_mode: Режим парсинга (HTML или Markdown)
    """
    settings = get_settings()
    bot_token = settings.BOT_TOKEN
    admin_ids = settings.ADMIN_IDS
    
    if not bot_token:
        logger.warning("send_admin_alert: BOT_TOKEN not configured")
        return
    
    if not admin_ids:
        logger.warning("send_admin_alert: ADMIN_IDS is empty")
        return
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    
    async with aiohttp.ClientSession() as session:
        for admin_id in admin_ids:
            data = {
                "chat_id": admin_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            try:
                async with session.post(
                    url, json=data, timeout=_ALERT_TIMEOUT
                ) as response:
                    if response.status == 200:
                        logger.debug(f"Admin alert sent to {admin_id}")
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"Admin alert failed for {admin_id}: "
                            f"{response.status} - {error_text[:200]}"
                        )
            except aiohttp.ClientError as e:
                logger.error(f"Admin alert network error for {admin_id}: {e}")
            except Exception as e:
                logger.error(f"Admin alert unexpected error for {admin_id}: {e}")


async def send_admin_alert_with_keyboard(
    text: str, 
    inline_keyboard: list,
    parse_mode: str = "HTML"
) -> None:
    """
    Отправляет сообщение с inline-клавиатурой всем администраторам.
    Используется для интерактивных алертов (например, с кнопкой "Профиль").
    
    Args:
        text: Текст сообщения
        inline_keyboard: Список списков кнопок в формате Telegram API
        parse_mode: Режим парсинга
    """
    settings = get_settings()
    bot_token = settings.BOT_TOKEN
    admin_ids = settings.ADMIN_IDS
    
    if not bot_token or not admin_ids:
        return
    
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    
    async with aiohttp.ClientSession() as session:
        for admin_id in admin_ids:
            data = {
                "chat_id": admin_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": {"inline_keyboard": inline_keyboard},
                "disable_web_page_preview": True,
            }
            try:
                async with session.post(url, json=data, timeout=_ALERT_TIMEOUT) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(
                            f"Admin alert with KB failed for {admin_id}: "
                            f"{response.status} - {error_text[:200]}"
                        )
            except Exception as e:
                logger.error(f"Admin alert with KB error for {admin_id}: {e}")
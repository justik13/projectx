"""
Heartbeat worker — обновление timestamp для мониторинга живости polling process.

🔥 ИСПРАВЛЕНО #17: Bot health check.
🔥 ИСПРАВЛЕНО #7: Алерт админу при падении Amnezia API (CircuitBreaker OPEN).
🔥 ИСПРАВЛЕНО #22: Экспорт get_bot_ref() для использования в PaymentService (chargeback alerts).

Проблема:
Webhook server имеет /health endpoint (для UptimeRobot/Healthchecks.io).
Но сам polling process (bot/main.py) не имеет health check.
Если polling завис, а webhook server жив — мониторинг этого не заметит.

Решение:
Фоновый worker пишет текущий timestamp в файл `.heartbeat` каждые 60 секунд.
Внешний скрипт (systemd, крон, monitoring) проверяет mtime файла:
- Если файл обновлён < 5 минут назад → бот жив
- Если файл устарел → бот завис, нужно перезапустить

🔥 ИСПРАВЛЕНО #7: Дополнительно проверяет CircuitBreaker для каждого сервера.
Если CB перешёл в OPEN — шлёт алерт админу в Telegram.
"""

import asyncio
import logging
import os
import time
from pathlib import Path

from services.amnezia_client import _circuit_breakers
from config.settings import get_settings

logger = logging.getLogger("BackgroundWorker")

# Путь к heartbeat файлу
HEARTBEAT_FILE = Path("./.heartbeat")
HEARTBEAT_INTERVAL = 60.0  # Обновлять раз в 60 секунд

# 🔥 ИСПРАВЛЕНО #7: Трекинг уже отправленных алертов (не спамить)
# {api_url: last_alert_timestamp}
_api_alert_sent: dict[str, float] = {}
_API_ALERT_COOLDOWN = 1800.0  # Повторный алерт не ранее чем через 30 минут


async def heartbeat_loop(shutdown_event: asyncio.Event):
    """
    Фоновый worker обновления heartbeat timestamp + мониторинг CircuitBreaker.
    """
    logger.info(f"Heartbeat worker started, file={HEARTBEAT_FILE}")
    # Первая запись сразу после старта
    _write_heartbeat()

    while not shutdown_event.is_set():
        try:
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=HEARTBEAT_INTERVAL,
                )
                break
            except asyncio.TimeoutError:
                pass

            _write_heartbeat()
            # 🔥 ИСПРАВЛЕНО #7: Проверка CircuitBreaker
            await _check_circuit_breakers()

        except asyncio.CancelledError:
            logger.info("Heartbeat worker cancelled")
            break
        except Exception as e:
            logger.error(f"Heartbeat worker error: {e}", exc_info=True)
            if shutdown_event.is_set():
                break
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    # Финальная запись перед остановкой
    _write_heartbeat(final=True)
    logger.info("Heartbeat worker stopped gracefully")


async def _check_circuit_breakers():
    """
    🔥 ИСПРАВЛЕНО #7: Проверяет состояние CircuitBreaker для каждого сервера.
    Если CB в состоянии OPEN — шлёт алерт админу (не чаще раза в 30 минут на сервер).
    """
    from database.connection import get_session
    from database.repositories.servers_repo import get_server_by_api_url

    settings = get_settings()
    now = time.monotonic()

    for api_url, cb in list(_circuit_breakers.items()):
        if not cb.is_open:
            # CB в норме — очищаем запись об алерте (если была)
            _api_alert_sent.pop(api_url, None)
            continue

        # CB в OPEN — проверяем, не отправляли ли уже алерт недавно
        last_alert = _api_alert_sent.get(api_url, 0)
        if now - last_alert < _API_ALERT_COOLDOWN:
            continue  # Уже отправляли недавно

        # Получаем имя сервера для понятного алерта
        server_name = api_url  # fallback
        try:
            session = await get_session()
            try:
                server = await get_server_by_api_url(session, api_url)
                if server:
                    server_name = server.name
            finally:
                await session.close()
        except Exception:
            pass

        # Формируем алерт
        alert_msg = (
            f"⚠️ <b>Сервер Amnezia недоступен!</b>\n"
            f"🌍 <b>{server_name}</b>\n"
            f"🔗 <code>{api_url}</code>\n"
            f"❌ CircuitBreaker перешёл в OPEN\n"
            f"🔄 Попытки восстановления каждые {cb.recovery_timeout:.0f}с\n"
            f"💡 Проверьте сервер вручную"
        )

        # 🔥 ИСПРАВЛЕНО: Отправляем алерт через _bot_ref
        if _bot_ref is not None:
            for admin_id in settings.ADMIN_IDS:
                try:
                    await _bot_ref.send_message(admin_id, alert_msg, parse_mode="HTML")
                    logger.info(f"CircuitBreaker alert sent to admin {admin_id} for {server_name}")
                except Exception as e:
                    logger.warning(f"Failed to send CB alert to admin {admin_id}: {e}")
        else:
            logger.warning(
                "🚨 CircuitBreaker OPEN for server '%s' (%s). "
                "bot_ref is None, cannot send alert.",
                server_name, api_url,
            )

        _api_alert_sent[api_url] = now


# 🔥 Глобальная ссылка на bot для отправки алертов
_bot_ref = None


def set_bot_ref(bot):
    """Устанавливает ссылку на bot для отправки алертов."""
    global _bot_ref
    _bot_ref = bot


def get_bot_ref():
    """
    🔥 ИСПРАВЛЕНО #22: Возвращает ссылку на bot для использования в других сервисах.
    Используется в PaymentService для отправки chargeback alerts.

    Returns:
        Bot instance или None если бот ещё не инициализирован
    """
    return _bot_ref


def _write_heartbeat(final: bool = False):
    """
    Записывает текущий Unix timestamp в heartbeat файл.
    """
    try:
        temp_file = HEARTBEAT_FILE.with_suffix(".tmp")
        if final:
            content = f"STOPPED {int(time.time())}\n"
        else:
            content = f"{int(time.time())}\n"

        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, HEARTBEAT_FILE)

        try:
            os.chmod(HEARTBEAT_FILE, 0o644)
        except PermissionError:
            pass

        if final:
            logger.debug("Heartbeat: written STOPPED marker")
        else:
            logger.debug("Heartbeat: timestamp updated")
    except Exception as e:
        logger.warning(f"Failed to write heartbeat file: {e}")

def get_bot_ref():
    """Возвращает ссылку на bot для использования в других сервисах."""
    return _bot_ref
import asyncio
import logging
import os
import time
from pathlib import Path

from services.amnezia_client import _circuit_breakers
from config.settings import get_settings

logger = logging.getLogger("BackgroundWorker")

#
# ИСПРАВЛЕНО: абсолютный путь вместо относительного.
#
# Раньше HEARTBEAT_FILE = Path("./.heartbeat").
# Если working directory менялся, healthcheck-скрипт
# не находил файл.
#
# Теперь путь определяется через переменную окружения
# PROJECTX_DIR или через расположение самого файла.
#
_PROJECT_DIR = os.environ.get(
    "PROJECTX_DIR",
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
    ),
)
HEARTBEAT_FILE = Path(_PROJECT_DIR) / ".heartbeat"

HEARTBEAT_INTERVAL = 60.0

_api_alert_sent: dict[str, float] = {}
_API_ALERT_COOLDOWN = 1800.0

_bot_ref = None


async def heartbeat_loop(shutdown_event: asyncio.Event):
    logger.info(f"Heartbeat worker started, file={HEARTBEAT_FILE}")
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
            await _check_circuit_breakers()

        except asyncio.CancelledError:
            logger.info("Heartbeat worker cancelled")
            break
        except Exception as e:
            logger.error(f"Heartbeat worker error: {e}", exc_info=True)
            if shutdown_event.is_set():
                break
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    _write_heartbeat(final=True)
    logger.info("Heartbeat worker stopped gracefully")


async def _check_circuit_breakers():
    from database.connection import session_scope
    from database.repositories.servers_repo import get_server_by_api_url

    settings = get_settings()
    now = time.monotonic()

    for api_url, cb in list(_circuit_breakers.items()):
        if not cb.is_open:
            continue

        last_alert = _api_alert_sent.get(api_url, 0)
        if now - last_alert < _API_ALERT_COOLDOWN:
            continue

        server_name = api_url
        try:
            async with session_scope() as session:
                server = await get_server_by_api_url(session, api_url)
                if server:
                    server_name = server.name
        except Exception:
            pass

        alert_msg = (
            f"⚠️ <b>Сервер Amnezia недоступен!</b>\n"
            f"🌍 <b>{server_name}</b>\n"
            f"🔗 <code>{api_url}</code>\n"
            f"❌ CircuitBreaker перешёл в OPEN\n"
            f"🔄 Попытки восстановления каждые {cb.recovery_timeout:.0f}с\n"
            f"💡 Проверьте сервер вручную"
        )

        if _bot_ref is not None:
            for admin_id in settings.ADMIN_IDS:
                try:
                    await _bot_ref.send_message(
                        admin_id,
                        alert_msg,
                        parse_mode="HTML",
                    )
                    logger.info(
                        "CircuitBreaker alert sent to admin %s for %s",
                        admin_id,
                        server_name,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to send CB alert to admin %s: %s",
                        admin_id,
                        e,
                    )
        else:
            logger.warning(
                "🚨 CircuitBreaker OPEN for server '%s' (%s). "
                "bot_ref is None.",
                server_name,
                api_url,
            )

        _api_alert_sent[api_url] = now


def set_bot_ref(bot):
    global _bot_ref
    _bot_ref = bot


def get_bot_ref():
    return _bot_ref


def _write_heartbeat(final: bool = False):
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
    except Exception as e:
        logger.warning(f"Failed to write heartbeat file: {e}")
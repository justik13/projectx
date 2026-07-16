"""
Heartbeat worker — обновление timestamp для мониторинга живости polling process.

🔥 ИСПРАВЛЕНО #17: Bot health check.

Проблема:
Webhook server имеет /health endpoint (для UptimeRobot/Healthchecks.io).
Но сам polling process (bot/main.py) не имеет health check.
Если polling завис, а webhook server жив — мониторинг этого не заметит.

Решение:
Фоновый worker пишет текущий timestamp в файл `.heartbeat` каждые 60 секунд.
Внешний скрипт (systemd, крон, monitoring) проверяет mtime файла:
- Если файл обновлён < 5 минут назад → бот жив
- Если файл устарел → бот завис, нужно перезапустить

Файл пишется в PROJECT_DIR (обычно /opt/projectx-bot/.heartbeat).
Файл защищён правами 644 (читать может monitoring user, писать — только projectx).

Почему не PID файл:
- PID может быть жив, но event loop завис (deadlock)
- PID не учитывает состояние asyncio workers
- Timestamp в файле — более точный индикатор "живости"
"""
import asyncio
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("BackgroundWorker")

# Путь к heartbeat файлу (относительно CWD бота, обычно /opt/projectx-bot)
HEARTBEAT_FILE = Path("./.heartbeat")
HEARTBEAT_INTERVAL = 60.0  # Обновлять раз в 60 секунд


async def heartbeat_loop(shutdown_event: asyncio.Event):
    """
    Фоновый worker обновления heartbeat timestamp.
    🔥 ИСПРАВЛЕНО #17: Graceful shutdown через shutdown_event.
    
    Логика:
    1. Каждые 60 секунд пишем Unix timestamp в .heartbeat файл
    2. Если shutdown_event установлен — выходим
    3. При ошибке записи — логируем, но не падаем (это не критично)
    
    Использование:
    Внешний скрипт может проверить "живость" бота:
    
    ```bash
    #!/bin/bash
    FILE="/opt/projectx-bot/.heartbeat"
    MAX_AGE=300  # 5 минут
    if [ -f "$FILE" ]; then
        AGE=$(( $(date +%s) - $(stat -c %Y "$FILE") ))
        if [ "$AGE" -gt "$MAX_AGE" ]; then
            echo "Bot is stale (last heartbeat: ${AGE}s ago)"
            systemctl restart projectx-bot
        else
            echo "Bot is alive (heartbeat: ${AGE}s ago)"
        fi
    else
        echo "Heartbeat file not found"
    fi
    ```
    """
    logger.info(f"Heartbeat worker started, file={HEARTBEAT_FILE}")
    
    # Первая запись сразу после старта
    _write_heartbeat()
    
    while not shutdown_event.is_set():
        try:
            # 🔥 ИСПРАВЛЕНО #5 (из Части 3): wait_for для быстрого реагирования на shutdown
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=HEARTBEAT_INTERVAL,
                )
                # Shutdown запрошен — выходим
                break
            except asyncio.TimeoutError:
                # Timeout — продолжаем работу, обновляем heartbeat
                pass
            
            _write_heartbeat()
        
        except asyncio.CancelledError:
            logger.info("Heartbeat worker cancelled")
            break
        except Exception as e:
            logger.error(f"Heartbeat worker error: {e}", exc_info=True)
            # Не критично — пробуем снова через interval
            if shutdown_event.is_set():
                break
            await asyncio.sleep(HEARTBEAT_INTERVAL)
    
    # Финальная запись перед остановкой (для отладки)
    _write_heartbeat(final=True)
    logger.info("Heartbeat worker stopped gracefully")


def _write_heartbeat(final: bool = False):
    """
    Записывает текущий Unix timestamp в heartbeat файл.
    
    Args:
        final: Если True — пишет "STOPPED" вместо timestamp (для отладки)
    """
    try:
        # Атомарная запись через временный файл + rename
        # (защита от чтения частично записанного файла)
        temp_file = HEARTBEAT_FILE.with_suffix(".tmp")
        
        if final:
            content = f"STOPPED {int(time.time())}\n"
        else:
            content = f"{int(time.time())}\n"
        
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())  # Форсим запись на диск
        
        # Атомарный rename (не прерывается SIGTERM)
        os.replace(temp_file, HEARTBEAT_FILE)
        
        # Устанавливаем права 644 (read для monitoring user)
        try:
            os.chmod(HEARTBEAT_FILE, 0o644)
        except PermissionError:
            pass  # Не критично, если chmod не удался
        
        if final:
            logger.debug("Heartbeat: written STOPPED marker")
        else:
            logger.debug("Heartbeat: timestamp updated")
    
    except Exception as e:
        logger.warning(f"Failed to write heartbeat file: {e}")
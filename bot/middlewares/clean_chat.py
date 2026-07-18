"""
CleanChatMiddleware — удаляет сообщения пользователя для поддержания чистоты чата (SMH).
🔥 ИСПРАВЛЕНО LOW #16: Спам стикерами создаёт тысячи фоновых тасок в event loop.
Теперь используется asyncio.Queue с лимитом 10 удалений/сек для защиты от DoS.
"""
import asyncio
import logging
from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)

# Глобальная очередь на удаление сообщений
_delete_queue: asyncio.Queue | None = None
_delete_worker_task: asyncio.Task | None = None

# Скорость обработки: 10 удалений в секунду
_DELETE_RATE = 10
_DELETE_BATCH_SIZE = 5


async def _delete_worker():
    """
    Фоновый воркер, который обрабатывает очередь удалений.
    Запускается один раз при первом использовании middleware.
    """
    global _delete_queue
    while True:
        try:
            batch = []
            # Собираем батч до _DELETE_BATCH_SIZE сообщений
            for _ in range(_DELETE_BATCH_SIZE):
                try:
                    item = await asyncio.wait_for(_delete_queue.get(), timeout=0.1)
                    batch.append(item)
                except asyncio.TimeoutError:
                    break
            
            # Обрабатываем батч
            for bot, chat_id, message_id in batch:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception as e:
                    logger.debug(
                        f"Failed to delete message {message_id} in {chat_id}: {e}"
                    )
            
            # Пауза для соблюдения rate limit
            await asyncio.sleep(1.0 / _DELETE_RATE * _DELETE_BATCH_SIZE)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"CleanChat worker error: {e}")
            await asyncio.sleep(1.0)


def _ensure_worker_started():
    """Запускает воркер, если он ещё не запущен."""
    global _delete_queue, _delete_worker_task
    if _delete_queue is None:
        _delete_queue = asyncio.Queue(maxsize=1000)
    if _delete_worker_task is None or _delete_worker_task.done():
        _delete_worker_task = asyncio.create_task(_delete_worker())


class CleanChatMiddleware(BaseMiddleware):
    """
    Удаляет сообщения пользователя для поддержания чистоты чата (SMH).
    🔥 ИСПРАВЛЕНО: Не удаляет системные сообщения Telegram:
    - successful_payment (нужно для обработки оплаты)
    - service messages (pin, group creation и т.д.)
    🔥 ИСПРАВЛЕНО LOW #16: Использует asyncio.Queue вместо create_task
    для защиты от DoS через спам стикерами/файлами.
    """

    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            # Пропускаем системные сообщения
            if event.successful_payment:
                return await handler(event, data)

            # Пропускаем service messages
            if any([
                event.pinned_message,
                event.new_chat_members,
                event.left_chat_member,
                event.new_chat_title,
                event.new_chat_photo,
                event.delete_chat_photo,
                event.group_chat_created,
                event.supergroup_chat_created,
                event.channel_chat_created,
                event.migrate_to_chat_id,
                event.migrate_from_chat_id,
            ]):
                return await handler(event, data)

            # Добавляем сообщение в очередь на удаление
            _ensure_worker_started()
            
            try:
                # non-blocking put с таймаутом
                await asyncio.wait_for(
                    _delete_queue.put((event.bot, event.chat.id, event.message_id)),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                # Очередь переполнена — пропускаем удаление
                logger.warning(
                    f"CleanChat queue full, skipping deletion of message "
                    f"{event.message_id} in {event.chat.id}"
                )
            except Exception as e:
                logger.debug(f"Failed to enqueue message deletion: {e}")

        return await handler(event, data)
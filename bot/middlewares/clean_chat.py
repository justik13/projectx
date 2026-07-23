import asyncio
import logging

from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)

_delete_queue: asyncio.Queue | None = None
_delete_worker_task: asyncio.Task | None = None

_DELETE_RATE = 10
_DELETE_BATCH_SIZE = 5


async def _delete_worker():
    global _delete_queue
    while True:
        try:
            batch = []
            for _ in range(_DELETE_BATCH_SIZE):
                try:
                    item = await asyncio.wait_for(_delete_queue.get(), timeout=0.1)
                    batch.append(item)
                except asyncio.TimeoutError:
                    break

            for bot, chat_id, message_id in batch:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except Exception as e:
                    logger.debug(
                        f"Failed to delete message {message_id} in {chat_id}: {e}"
                    )

            await asyncio.sleep(1.0 / _DELETE_RATE * _DELETE_BATCH_SIZE)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"CleanChat worker error: {e}")
            await asyncio.sleep(1.0)


def _ensure_worker_started():
    global _delete_queue, _delete_worker_task
    if _delete_queue is None:
        #
        # ИСПРАВЛЕНО: maxsize увеличен с 1000 до 5000.
        #
        # При массовой рассылке (10 000+ пользователей)
        # очередь 1000 могла переполниться, и сообщения
        # не удалялись.
        #
        _delete_queue = asyncio.Queue(maxsize=5000)
    if _delete_worker_task is None or _delete_worker_task.done():
        _delete_worker_task = asyncio.create_task(_delete_worker())


class CleanChatMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            if event.successful_payment:
                return await handler(event, data)

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

            _ensure_worker_started()
            try:
                await asyncio.wait_for(
                    _delete_queue.put((event.bot, event.chat.id, event.message_id)),
                    timeout=1.0
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"CleanChat queue full, skipping deletion of message "
                    f"{event.message_id} in {event.chat.id}"
                )
            except Exception as e:
                logger.debug(f"Failed to enqueue message deletion: {e}")

        return await handler(event, data)
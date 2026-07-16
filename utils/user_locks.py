"""
Per-user asyncio locks для предотвращения race conditions.

Каждый пользователь может выполнять только одно «тяжёлое» действие одновременно.
Lock'и создаются лениво и привязаны к event loop (single-worker safe).

Thread-safety гарантирована:
- Dict operations атомарны в CPython (GIL)
- Между проверкой и созданием lock нет await → нет race condition
- asyncio.Lock привязан к единственному event loop
"""
import asyncio
import logging
from typing import Dict

logger = logging.getLogger(__name__)

_user_action_locks: Dict[int, asyncio.Lock] = {}


def get_user_action_lock(user_id: int) -> asyncio.Lock:
    """
    Возвращает глобальный per-user asyncio.Lock.
    
    Lock создаётся лениво при первом обращении.
    Повторные вызовы для того же user_id возвращают тот же lock.
    
    Args:
        user_id: Telegram user ID
        
    Returns:
        asyncio.Lock для данного пользователя
    """
    if user_id not in _user_action_locks:
        _user_action_locks[user_id] = asyncio.Lock()
    return _user_action_locks[user_id]


def get_active_locks_count() -> int:
    """Возвращает количество активных locks (для мониторинга)."""
    return len(_user_action_locks)


def get_locked_users() -> list[int]:
    """Возвращает список user_id с активными (захваченными) locks."""
    return [uid for uid, lock in _user_action_locks.items() if lock.locked()]
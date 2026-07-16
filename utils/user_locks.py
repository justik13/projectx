"""
Per-user asyncio locks для предотвращения race conditions.
Каждый пользователь может выполнять только одно «тяжёлое» действие одновременно.
Lock'и создаются лениво и привязаны к event loop (single-worker safe).

🔥 ИСПРАВЛЕНО #4: Memory Leak
Было: _user_action_locks рос бесконечно (1 lock на каждого пользователя навсегда).
Стало: Периодический cleanup (раз в час) удаляет locks не использовавшиеся > 1 часа.
При 10 000 пользователей это предотвращает утечку 10 000 asyncio.Lock объектов.

Thread-safety гарантирована:
- Dict operations атомарны в CPython (GIL)
- Между проверкой и созданием lock нет await → нет race condition
- asyncio.Lock привязан к единственному event loop
"""
import asyncio
import time
import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# (lock, last_used_timestamp)
_user_action_locks: Dict[int, Tuple[asyncio.Lock, float]] = {}

_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 3600.0  # Проверять cleanup раз в час
_LOCK_TTL = 3600.0  # Удалять locks не использовавшиеся > 1 часа


def get_user_action_lock(user_id: int) -> asyncio.Lock:
    """
    Возвращает глобальный per-user asyncio.Lock.
    Lock создаётся лениво при первом обращении.
    Повторные вызовы для того же user_id возвращают тот же lock.
    
    🔥 ИСПРАВЛЕНО #4: Автоматический cleanup неактивных locks.
    """
    global _last_cleanup_time
    
    now = time.monotonic()
    
    # Периодический cleanup (раз в час)
    if now - _last_cleanup_time > _CLEANUP_INTERVAL:
        _cleanup_old_locks(now)
        _last_cleanup_time = now
    
    if user_id not in _user_action_locks:
        # Создаём новый lock с текущим timestamp
        _user_action_locks[user_id] = (asyncio.Lock(), now)
    else:
        # Обновляем last_used timestamp
        lock, _ = _user_action_locks[user_id]
        _user_action_locks[user_id] = (lock, now)
    
    return _user_action_locks[user_id][0]


def _cleanup_old_locks(now: float):
    """
    Удаляет locks не использовавшиеся > 1 часа и не захваченные в данный момент.
    🔥 ИСПРАВЛЕНО #4: Предотвращает утечку памяти при 10 000+ пользователей.
    """
    old_users = [
        uid for uid, (lock, last_used) in _user_action_locks.items()
        if now - last_used > _LOCK_TTL and not lock.locked()
    ]
    for uid in old_users:
        del _user_action_locks[uid]
    
    if old_users:
        logger.debug(
            f"User locks cleanup: removed {len(old_users)} old locks, "
            f"{len(_user_action_locks)} remaining"
        )


def get_active_locks_count() -> int:
    """Возвращает количество активных locks (для мониторинга)."""
    return len(_user_action_locks)


def get_locked_users() -> list[int]:
    """Возвращает список user_id с активными (захваченными) locks."""
    return [uid for uid, (lock, _) in _user_action_locks.items() if lock.locked()]
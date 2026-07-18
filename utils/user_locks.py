import asyncio
import time
import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)
_user_action_locks: Dict[int, Tuple[asyncio.Lock, float]] = {}

_last_cleanup_time: float = 0.0
_CLEANUP_INTERVAL = 3600.0  # Проверять cleanup раз в час
_LOCK_TTL = 3600.0  # Удалять locks не использовавшиеся > 1 часа


def get_user_action_lock(user_id: int) -> asyncio.Lock:
    global _last_cleanup_time
    
    now = time.monotonic()
    if now - _last_cleanup_time > _CLEANUP_INTERVAL:
        _cleanup_old_locks(now)
        _last_cleanup_time = now
    
    if user_id not in _user_action_locks:
        _user_action_locks[user_id] = (asyncio.Lock(), now)
    else:
        lock, _ = _user_action_locks[user_id]
        _user_action_locks[user_id] = (lock, now)
    
    return _user_action_locks[user_id][0]


def _cleanup_old_locks(now: float):
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
    return len(_user_action_locks)


def get_locked_users() -> list[int]:
    return [uid for uid, (lock, _) in _user_action_locks.items() if lock.locked()]
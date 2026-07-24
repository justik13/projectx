import asyncio
import time


class GlobalRateLimiter:
    """
    Единый token-bucket limiter на все исходящие сообщения бота.
    Telegram hard limit = 30 msg/s globally.
    Ставим 25 msg/s с запасом.
    """

    def __init__(self, rate: float = 25.0):
        self.rate = rate
        self.tokens = rate
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(
                    self.rate,
                    self.tokens + elapsed * self.rate,
                )
                self.last_refill = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait_time = (1.0 - self.tokens) / self.rate
            await asyncio.sleep(wait_time)


_global_limiter = GlobalRateLimiter(rate=25.0)


async def acquire_global_rate() -> None:
    await _global_limiter.acquire()
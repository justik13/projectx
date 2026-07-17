import aiohttp
import asyncio
import logging
import time
from typing import Optional, List
from pydantic import BaseModel, Field
from bot.constants import AMNEZIA_PROTOCOL, API_TIMEOUT, API_CONCURRENCY_LIMIT, API_RETRY_COUNT

logger = logging.getLogger(__name__)

_http_session: Optional[aiohttp.ClientSession] = None


# ============================================================
# 🔥 CIRCUIT BREAKER
# ============================================================
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 60.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.state = "CLOSED"
        self.last_failure_time = 0.0
        self._lock = asyncio.Lock()

    async def is_available(self) -> bool:
        async with self._lock:
            if self.state == "OPEN":
                elapsed = time.monotonic() - self.last_failure_time
                if elapsed > self.recovery_timeout:
                    logger.info(
                        f"Circuit breaker: half-open, attempting recovery "
                        f"(was OPEN for {elapsed:.0f}s)"
                    )
                    self.state = "CLOSED"
                    self.failure_count = 0
                    return True
                return False
            return True

    async def record_success(self):
        async with self._lock:
            if self.failure_count > 0:
                logger.info(
                    f"Circuit breaker: request succeeded, resetting failure count "
                    f"({self.failure_count} -> 0)"
                )
                self.failure_count = 0
                self.state = "CLOSED"

    async def record_failure(self):
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.monotonic()
            if self.failure_count >= self.failure_threshold:
                if self.state != "OPEN":
                    logger.warning(
                        f"Circuit breaker: OPEN after {self.failure_count} failures. "
                        f"Will retry in {self.recovery_timeout}s."
                    )
                    self.state = "OPEN"

    @property
    def is_open(self) -> bool:
        return self.state == "OPEN"


_circuit_breakers: dict[str, CircuitBreaker] = {}


def _get_circuit_breaker(api_url: str) -> CircuitBreaker:
    if api_url not in _circuit_breakers:
        _circuit_breakers[api_url] = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60.0,
        )
    return _circuit_breakers[api_url]


# ============================================================
# 🔥 TOKEN BUCKET RATE LIMITER
# ============================================================
class TokenBucketRateLimiter:
    def __init__(self, rate: float = 10.0, burst: int = 20):
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
                self.last_refill = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(1.0 / self.rate, remaining))


_rate_limiters: dict[str, TokenBucketRateLimiter] = {}


def _get_rate_limiter(api_url: str) -> TokenBucketRateLimiter:
    if api_url not in _rate_limiters:
        _rate_limiters[api_url] = TokenBucketRateLimiter(rate=10.0, burst=20)
    return _rate_limiters[api_url]


# ============================================================
# DTO MODELS
# ============================================================
class AmneziaClientCreateResponse(BaseModel):
    id: str
    config: str
    protocol: str = AMNEZIA_PROTOCOL


class AmneziaClientTraffic(BaseModel):
    totalDownload: int = 0
    totalUpload: int = 0
    received: int = 0
    sent: int = 0


class AmneziaClientListItem(BaseModel):
    id: str
    username: str = ""
    peer_name: str = ""
    status: str = "active"
    traffics: AmneziaClientTraffic = Field(default_factory=AmneziaClientTraffic)
    lastHandshake: Optional[float] = None
    lastSeen: Optional[float] = None
    updatedAt: Optional[float] = None

    @property
    def clientName(self) -> str:
        return self.username

    @property
    def name(self) -> str:
        return self.peer_name


class AmneziaServerInfo(BaseModel):
    name: str = ""
    protocols: List[str] = Field(default_factory=list)
    maxPeers: int = 0
    serverMaxPeers: int = 0
    SERVER_MAX_PEERS: int = 250

    def get_effective_max_peers(self) -> int:
        return self.maxPeers or self.serverMaxPeers or self.SERVER_MAX_PEERS


# ============================================================
# HTTP SESSION
# ============================================================
async def get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None:
        connector = aiohttp.TCPConnector(
            limit=100, limit_per_host=API_CONCURRENCY_LIMIT
        )
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        _http_session = aiohttp.ClientSession(
            connector=connector, timeout=timeout
        )
    return _http_session


async def close_http_session():
    global _http_session
    if _http_session:
        await _http_session.close()
        _http_session = None


# ============================================================
# CLIENT
# ============================================================
class AmneziaClient:
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self._headers = {
            "x-api-key": api_key, "Content-Type": "application/json"
        }

    async def _request(self, method: str, path: str, **kwargs) -> Optional[dict]:
        url = f"{self.api_url}{path}"

        cb = _get_circuit_breaker(self.api_url)
        if not await cb.is_available():
            logger.debug(
                f"Circuit breaker OPEN for {self.api_url}{path}, skipping request"
            )
            return None

        limiter = _get_rate_limiter(self.api_url)

        for attempt in range(API_RETRY_COUNT + 1):
            if not await limiter.acquire(timeout=30.0):
                logger.warning(
                    f"Rate limit timeout for {self.api_url}{path} "
                    f"(attempt {attempt + 1}/{API_RETRY_COUNT + 1})"
                )
                return None

            session = await get_http_session()
            try:
                async with session.request(
                    method, url, headers=self._headers, **kwargs
                ) as response:
                    if response.status == 204:
                        await cb.record_success()
                        return {}
                    elif 200 <= response.status < 300:
                        await cb.record_success()
                        try:
                            return await response.json()
                        except aiohttp.ContentTypeError:
                            return None
                    elif 400 <= response.status < 500:
                        # 🔥 ИСПРАВЛЕНО: 4xx НЕ открывает Circuit Breaker
                        # 404 Not Found, 400 Bad Request — это клиентские ошибки,
                        # сервер работает нормально
                        if response.status == 429:
                            # 429 Too Many Requests — считаем failure
                            if attempt < API_RETRY_COUNT:
                                backoff = 2 ** attempt
                                logger.warning(
                                    f"API {self.api_url}{path} returned 429, "
                                    f"retrying in {backoff}s"
                                )
                                await asyncio.sleep(backoff)
                                continue
                            await cb.record_failure()
                            return None
                        else:
                            # 400, 401, 403, 404, 409 — логируем, но НЕ считаем failure
                            error_text = await response.text()
                            logger.warning(
                                f"API {self.api_url}{path} returned {response.status} "
                                f"(client error, not counting as failure): {error_text[:200]}"
                            )
                            return None
                    else:
                        # 5xx — серверная ошибка, retry + record_failure
                        if attempt < API_RETRY_COUNT and response.status >= 500:
                            backoff = 2 ** attempt
                            logger.warning(
                                f"API {self.api_url}{path} returned {response.status}, "
                                f"retrying in {backoff}s (attempt {attempt + 1})"
                            )
                            await asyncio.sleep(backoff)
                            continue
                        await cb.record_failure()
                        return None

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt < API_RETRY_COUNT:
                    backoff = 2 ** attempt
                    logger.warning(
                        f"Network error for {self.api_url}{path}: {type(e).__name__}, "
                        f"retrying in {backoff}s (attempt {attempt + 1})"
                    )
                    await asyncio.sleep(backoff)
                else:
                    await cb.record_failure()
                    logger.error(
                        f"All retries exhausted for {self.api_url}{path}: "
                        f"{type(e).__name__}"
                    )
                    return None
            except Exception as e:
                await cb.record_failure()
                logger.error(
                    f"Unexpected error for {self.api_url}{path}: "
                    f"{type(e).__name__}: {e}"
                )
                return None

        return None

    async def create_user(
        self, client_name: str, expires_at: Optional[int] = None
    ) -> Optional[AmneziaClientCreateResponse]:
        data = {
            "clientName": client_name,
            "protocol": AMNEZIA_PROTOCOL,
            "expiresAt": expires_at
        }
        result = await self._request("POST", "/clients", json=data)
        if result and "client" in result:
            try:
                return AmneziaClientCreateResponse(**result["client"])
            except Exception as e:
                logger.error(f"Failed to parse create_user response: {e}")
                return None
        return None

    async def delete_user(self, client_id: str) -> bool:
        data = {"clientId": client_id, "protocol": AMNEZIA_PROTOCOL}
        result = await self._request("DELETE", "/clients", json=data)
        return result is not None

    async def update_client(
        self,
        client_id: str,
        status: Optional[str] = None,
        expires_at: Optional[int] = None
    ) -> bool:
        data = {"clientId": client_id, "protocol": AMNEZIA_PROTOCOL}
        if status is not None:
            data["status"] = status
        if expires_at is not None:
            data["expiresAt"] = expires_at
        result = await self._request("PATCH", "/clients", json=data)
        return result is not None

    async def get_server_stats(self) -> Optional[dict]:
        return await self._request("GET", "/server/load")

    async def get_server_info(self) -> Optional[AmneziaServerInfo]:
        result = await self._request("GET", "/server")
        if result:
            try:
                return AmneziaServerInfo(**result)
            except Exception as e:
                logger.error(f"Failed to parse get_server_info response: {e}")
                return None
        return None

    async def healthcheck(self) -> bool:
        return (await self._request("GET", "/healthz")) is not None

    async def get_all_clients(self) -> Optional[List[AmneziaClientListItem]]:
        all_clients: List[AmneziaClientListItem] = []
        page_size = 100
        max_pages = 10
        page_count = 0

        while page_count < max_pages:
            result = await self._request(
                "GET", "/clients",
                params={"skip": page_count * page_size, "limit": page_size}
            )
            if result is None:
                if page_count == 0:
                    return None
                logger.warning(
                    f"get_all_clients: API failed on page {page_count}, "
                    f"returning partial result ({len(all_clients)} clients)"
                )
                break

            items_raw = result.get("items", [])
            if not items_raw:
                break

            page_clients = self._parse_clients_page(items_raw)
            all_clients.extend(page_clients)

            if len(page_clients) < page_size:
                break

            page_count += 1

        if page_count >= max_pages:
            logger.warning(
                f"get_all_clients: reached safety limit "
                f"({max_pages * page_size} clients)."
            )

        logger.info(
            f"get_all_clients: parsed {len(all_clients)} peers "
            f"across {page_count + 1} page(s)"
        )
        return all_clients

    @staticmethod
    def _parse_clients_page(items_raw: list) -> List[AmneziaClientListItem]:
        clients: List[AmneziaClientListItem] = []
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            username = item.get("username", "")
            peers = item.get("peers", [])
            if not isinstance(peers, list):
                continue
            for peer in peers:
                if not isinstance(peer, dict):
                    continue
                peer_id = peer.get("id", "")
                if not peer_id:
                    continue

                traffic_raw = peer.get("traffic", {})
                if isinstance(traffic_raw, dict):
                    traffic = AmneziaClientTraffic(
                        totalDownload=traffic_raw.get("received", 0) or 0,
                        totalUpload=traffic_raw.get("sent", 0) or 0,
                        received=traffic_raw.get("received", 0) or 0,
                        sent=traffic_raw.get("sent", 0) or 0,
                    )
                else:
                    traffic = AmneziaClientTraffic()

                try:
                    client_item = AmneziaClientListItem(
                        id=peer_id,
                        username=username,
                        peer_name=peer.get("name") or "",
                        status=peer.get("status", "active"),
                        traffics=traffic,
                        lastHandshake=peer.get("lastHandshake"),
                        lastSeen=peer.get("lastSeen"),
                        updatedAt=peer.get("updatedAt"),
                    )
                    clients.append(client_item)
                except Exception as e:
                    logger.warning(
                        f"Failed to parse peer item: {e}, peer={peer}"
                    )
                    continue
        return clients

    async def delete_client(self, client_id: str) -> bool:
        data = {"clientId": client_id, "protocol": AMNEZIA_PROTOCOL}
        result = await self._request("DELETE", "/clients", json=data)
        return result is not None
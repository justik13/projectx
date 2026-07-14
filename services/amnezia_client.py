import aiohttp
import asyncio
import logging
from typing import Optional, List
from pydantic import BaseModel, Field
from bot.constants import AMNEZIA_PROTOCOL, API_TIMEOUT, API_CONCURRENCY_LIMIT

logger = logging.getLogger(__name__)

_http_session: Optional[aiohttp.ClientSession] = None


# ============================================================
# DTO MODELS (Pydantic)
# ============================================================

class AmneziaClientCreateResponse(BaseModel):
    """Ответ API при создании клиента."""
    id: str
    config: str
    protocol: str = AMNEZIA_PROTOCOL


class AmneziaClientTraffic(BaseModel):
    """Трафик клиента."""
    totalDownload: int = 0
    totalUpload: int = 0


class AmneziaClientListItem(BaseModel):
    """Элемент списка клиентов из GET /clients."""
    id: str
    clientName: str = ""
    name: str = ""
    status: str = "active"
    traffics: AmneziaClientTraffic = Field(default_factory=AmneziaClientTraffic)
    lastHandshake: Optional[float] = None
    lastSeen: Optional[float] = None
    updatedAt: Optional[float] = None


class AmneziaServerInfo(BaseModel):
    """Информация о сервере из GET /server."""
    name: str = ""
    protocols: List[str] = Field(default_factory=list)
    maxPeers: int = 0
    serverMaxPeers: int = 0
    SERVER_MAX_PEERS: int = 250

    def get_effective_max_peers(self) -> int:
        """Возвращает максимальное количество пиров."""
        return self.maxPeers or self.serverMaxPeers or self.SERVER_MAX_PEERS


# ============================================================
# HTTP SESSION
# ============================================================

async def get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None:
        connector = aiohttp.TCPConnector(limit=100, limit_per_host=API_CONCURRENCY_LIMIT)
        timeout = aiohttp.ClientTimeout(total=API_TIMEOUT)
        _http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
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
        self._headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, **kwargs) -> Optional[dict]:
        url = f"{self.api_url}{path}"
        for attempt in range(2):
            session = await get_http_session()
            try:
                async with session.request(method, url, headers=self._headers, **kwargs) as response:
                    if response.status == 204:
                        return {}
                    elif 200 <= response.status < 300:
                        try:
                            return await response.json()
                        except aiohttp.ContentTypeError:
                            return None
                    else:
                        if attempt == 0 and response.status >= 500:
                            await asyncio.sleep(1)
                            continue
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == 0:
                    await asyncio.sleep(1)
                else:
                    return None
            except Exception:
                return None
        return None

    async def create_user(
        self, client_name: str, expires_at: Optional[int] = None
    ) -> Optional[AmneziaClientCreateResponse]:
        """Создаёт клиента. Возвращает DTO или None."""
        data = {"clientName": client_name, "protocol": AMNEZIA_PROTOCOL, "expiresAt": expires_at}
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
        """Возвращает информацию о сервере как DTO."""
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

    async def get_all_clients(
        self, skip: int = 0, limit: int = 100
    ) -> Optional[List[AmneziaClientListItem]]:
        """Возвращает список клиентов как список DTO."""
        all_clients: List[AmneziaClientListItem] = []
        current_skip = skip
        while True:
            result = await self._request(
                "GET", "/clients",
                params={"skip": current_skip, "limit": min(limit, 100)}
            )
            if result is None:
                return None
            clients_raw = result.get("clients", [])
            if not clients_raw:
                break
            for raw in clients_raw:
                try:
                    all_clients.append(AmneziaClientListItem(**raw))
                except Exception as e:
                    logger.warning(f"Failed to parse client item: {e}")
                    continue
            if len(clients_raw) < min(limit, 100):
                break
            current_skip += len(clients_raw)
        return all_clients

    async def delete_client(self, client_id: str) -> bool:
        """Удаляет клиента с сервера (используется при массовом удалении)."""
        data = {"clientId": client_id, "protocol": AMNEZIA_PROTOCOL}
        result = await self._request("DELETE", "/clients", json=data)
        return result is not None
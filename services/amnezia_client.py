import aiohttp
import asyncio
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

_http_session: Optional[aiohttp.ClientSession] = None

async def get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None:
        connector = aiohttp.TCPConnector(limit=100, limit_per_host=20)
        timeout = aiohttp.ClientTimeout(total=15)
        _http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)
    return _http_session

async def close_http_session():
    global _http_session
    if _http_session:
        await _http_session.close()
        _http_session = None

PROTOCOL = "amneziawg2"

class AmneziaClient:
    def __init__(self, api_url: str, api_key: str):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self._headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, **kwargs) -> Optional[Dict]:
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
                            logger.error(f"API returned non-JSON on 2xx from {url}")
                            return None
                    else:
                        error_text = await response.text()
                        logger.warning(f"API error {response.status} (attempt {attempt+1}): {error_text}")
                        if attempt == 0 and response.status >= 500:
                            await asyncio.sleep(1)
                            continue
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Network error to {url} (attempt {attempt+1}): {e}")
                if attempt == 0:
                    await asyncio.sleep(1)
                else:
                    return None
            except Exception as e:
                logger.error(f"Unexpected error to {url}: {e}", exc_info=True)
                return None
        return None

    async def create_user(self, client_name: str, expires_at: Optional[int] = None) -> Optional[Dict]:
        data = {"clientName": client_name, "protocol": PROTOCOL, "expiresAt": expires_at}
        result = await self._request("POST", "/clients", json=data)
        if result and "client" in result:
            client = result["client"]
            logger.info(f"Created client: id={client.get('id')}, name={client_name}")
            return client
        logger.error(f"Failed to create client {client_name}")
        return None

    async def delete_user(self, client_id: str) -> bool:
        data = {"clientId": client_id, "protocol": PROTOCOL}
        result = await self._request("DELETE", "/clients", json=data)
        if result is not None:
            logger.info(f"Deleted client {client_id}")
            return True
        logger.error(f"Failed to delete client {client_id}")
        return False

    async def update_client(self, client_id: str, status: Optional[str] = None, expires_at: Optional[int] = None) -> bool:
        data = {"clientId": client_id, "protocol": PROTOCOL}
        if status is not None:
            data["status"] = status
        if expires_at is not None:
            data["expiresAt"] = expires_at
        result = await self._request("PATCH", "/clients", json=data)
        if result is not None:
            logger.info(f"Updated client {client_id}: status={status}")
            return True
        return False

    async def get_server_stats(self) -> Optional[Dict]:
        result = await self._request("GET", "/server/load")
        if result:
            return {
                "cpu": result.get("cpu", {}).get("usage", 0),
                "memory": result.get("memory", {}).get("usage", 0),
                "disk": result.get("disk", {}).get("usage", 0),
                "active_clients": result.get("activeClients", 0)
            }
        return None

    async def get_server_info(self) -> Optional[Dict]:
        return await self._request("GET", "/server")

    async def healthcheck(self) -> bool:
        return (await self._request("GET", "/healthz")) is not None

    async def get_all_clients(self, skip: int = 0, limit: int = 100) -> Optional[List[Dict]]:
        """🔥 P0 FIX: При сбое на ЛЮБОЙ странице возвращаем None (не частичный список)"""
        all_clients = []
        current_skip = skip
        while True:
            result = await self._request("GET", "/clients", params={"skip": current_skip, "limit": min(limit, 100)})
            if result is None:
                logger.error(f"Pagination aborted at skip={current_skip}. Discarding {len(all_clients)} partial results.")
                return None  # 🔥 Всегда None, чтобы воркер не удалил реальные профили
            clients = result.get("clients", [])
            if not clients:
                break
            all_clients.extend(clients)
            if len(clients) < min(limit, 100):
                break
            current_skip += len(clients)
        return all_clients

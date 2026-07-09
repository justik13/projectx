import aiohttp
import asyncio
import logging
from typing import Optional, Dict, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Глобальная сессия для HTTP-запросов
_http_session: Optional[aiohttp.ClientSession] = None


async def get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None:
        connector = aiohttp.TCPConnector(limit=100, limit_per_host=20)
        timeout = aiohttp.ClientTimeout(total=15)
        _http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        )
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
        self._headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }

    async def _request(self, method: str, path: str, **kwargs) -> Optional[Dict]:
        url = f"{self.api_url}{path}"
        
        for attempt in range(2):  # 1 повторная попытка
            session = await get_http_session()
            try:
                async with session.request(
                    method, url, headers=self._headers, **kwargs
                ) as response:
                    if response.status == 204:
                        return {}
                    elif 200 <= response.status < 300:
                        data = await response.json()
                        return data
                    else:
                        error_text = await response.text()
                        logger.warning(f"API error {response.status}: {error_text}")
                        return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Network error during request to {url} (attempt {attempt+1}): {e}")
                if attempt == 0:
                    # Пересоздаём сессию при первой неудаче
                    await close_http_session()
                    await asyncio.sleep(1)
                else:
                    return None
            except Exception as e:
                logger.error(f"Unexpected error during request to {url}: {e}", exc_info=True)
                return None

    async def create_user(
        self, 
        client_name: str, 
        expires_at: Optional[int] = None
    ) -> Optional[Dict]:
        """
        Создать нового клиента.
        Возвращает {"id": "...", "config": "...", "protocol": "..."}
        """
        data = {
            "clientName": client_name,
            "protocol": PROTOCOL,
            "expiresAt": expires_at  # None = бессрочно
        }
        result = await self._request("POST", "/clients", json=data)
        if result and "client" in result:
            client = result["client"]
            logger.info(f"Created client: id={client.get('id')}, name={client_name}")
            return client  # {"id": "...", "config": "...", "protocol": "..."}
        else:
            logger.error(f"Failed to create client with name {client_name}")
            return None

    async def delete_user(self, client_id: str) -> bool:
        """Удалить клиента (DELETE с JSON body)"""
        data = {
            "clientId": client_id,
            "protocol": PROTOCOL
        }
        result = await self._request("DELETE", "/clients", json=data)
        if result is not None:
            logger.info(f"Deleted client with ID {client_id}")
            return True
        else:
            logger.error(f"Failed to delete client with ID {client_id}")
            return False

    async def update_client(
        self,
        client_id: str,
        status: Optional[str] = None,
        expires_at: Optional[int] = None
    ) -> bool:
        """
        Обновить клиента (поставить на паузу / возобновить / задать срок).
        status: "active" | "disabled"
        """
        data = {
            "clientId": client_id,
            "protocol": PROTOCOL
        }
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
        """Получить метрики сервера (CPU, RAM, диск)"""
        result = await self._request("GET", "/server/load")
        if result:
            logger.info("Retrieved server stats")
            return {
                "cpu": result.get("cpu", {}).get("usage", 0),
                "memory": result.get("memory", {}).get("usage", 0),
                "disk": result.get("disk", {}).get("usage", 0),
                "active_clients": result.get("activeClients", 0)
            }
        else:
            logger.error("Failed to retrieve server stats")
            return None

    async def get_server_info(self) -> Optional[Dict]:
        """Получить информацию о сервере"""
        result = await self._request("GET", "/server")
        if result:
            logger.info("Retrieved server info")
            return result
        return None

    async def healthcheck(self) -> bool:
        """Проверить доступность API"""
        result = await self._request("GET", "/healthz")
        return result is not None

    async def get_all_clients(self, skip: int = 0, limit: int = 1000) -> Optional[List[Dict]]:
        """Получить список всех клиентов с сервера"""
        result = await self._request("GET", "/clients", params={"skip": skip, "limit": limit})
        if result and "clients" in result:
            return result["clients"]
        return []

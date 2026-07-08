# services/amnezia_client.py

import aiohttp
import asyncio
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)


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
        try:
            async with aiohttp.ClientSession() as session:
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
            logger.error(f"Network error during request to {url}: {e}")
            return None

    async def create_user(
        self, 
        client_name: str, 
        protocol: str = "amneziawg2",
        expires_at: Optional[int] = None
    ) -> Optional[Dict]:
        """
        Создать нового клиента.
        Возвращает {"id": "...", "config": "...", "protocol": "..."}
        """
        data = {
            "clientName": client_name,
            "protocol": protocol,
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

    async def delete_user(self, client_id: str, protocol: str = "amneziawg2") -> bool:
        """Удалить клиента (DELETE с JSON body)"""
        data = {
            "clientId": client_id,
            "protocol": protocol
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
        protocol: str = "amneziawg2",
        status: Optional[str] = None,
        expires_at: Optional[int] = None
    ) -> bool:
        """
        Обновить клиента (поставить на паузу / возобновить / задать срок).
        status: "active" | "disabled"
        """
        data = {
            "clientId": client_id,
            "protocol": protocol
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

    async def get_client_config(self, client_id: str, protocol: str = "amneziawg2") -> Optional[str]:
        """
        Получить конфиг клиента.
        NOTE: В текущей версии API нет отдельного эндпоинта для получения конфига.
        Конфиг возвращается только при создании клиента.
        Эта функция ищет клиента в списке и возвращает его данные.
        """
        # Получаем список всех клиентов
        result = await self._request("GET", "/clients", params={"skip": 0, "limit": 1000})
        if not result:
            logger.error(f"Failed to get clients list")
            return None
        
        # Ищем нужного клиента
        clients = result.get("clients", [])
        for client in clients:
            if client.get("id") == client_id and client.get("protocol") == protocol:
                # В списке клиентов может не быть поля config, только статистика
                # Поэтому эта функция возвращает None, если config не сохранён в БД
                logger.warning(
                    f"Config for client {client_id} not available via API. "
                    f"Use raw_config stored in DB."
                )
                return None
        
        logger.error(f"Client {client_id} not found in list")
        return None

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
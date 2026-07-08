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
        self._headers = {"X-API-Key": api_key}

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
                        logger.warning(f"API error {response.status}: {await response.text()}")
                        return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.error(f"Network error during request to {url}: {e}")
            return None

    async def create_user(self, client_id: str, protocol: str = "amneziawg2") -> Optional[Dict]:
        data = {"peer_id": client_id, "protocol": protocol}
        result = await self._request("POST", "/api/v1/client", json=data)
        if result:
            logger.info(f"Created user with ID {client_id}")
        else:
            logger.error(f"Failed to create user with ID {client_id}")
        return result

    async def delete_user(self, client_id: str, protocol: str = "amneziawg2") -> bool:
        params = {"peer_id": client_id, "protocol": protocol}
        result = await self._request("DELETE", "/api/v1/client", params=params)
        if result is not None:
            logger.info(f"Deleted user with ID {client_id}")
            return True
        else:
            logger.error(f"Failed to delete user with ID {client_id}")
            return False

    async def get_client_config(self, client_id: str, protocol: str = "amneziawg2") -> Optional[str]:
        params = {"peer_id": client_id, "protocol": protocol}
        result = await self._request("GET", "/api/v1/client/config", params=params)
        if result and "config" in result:
            logger.info(f"Retrieved config for user {client_id}")
            return result["config"]
        else:
            logger.error(f"Failed to retrieve config for user {client_id}")
            return None

    async def get_server_stats(self) -> Optional[Dict]:
        result = await self._request("GET", "/api/v1/system/stats")
        if result:
            logger.info("Retrieved server stats")
            return {
                "cpu": result.get("cpu"),
                "memory": result.get("memory"),
                "disk": result.get("disk"),
                "active_clients": result.get("active_clients", 0)
            }
        else:
            logger.error("Failed to retrieve server stats")
            return None

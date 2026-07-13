"""Integration тесты для AmneziaClient с mocking HTTP."""
import pytest
from aioresponses import aioresponses
import json


class TestAmneziaClient:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_healthcheck_success(self):
        from services.amnezia_client import AmneziaClient
        
        client = AmneziaClient("http://test.server:4001", "test_key")
        
        with aioresponses() as mocked:
            mocked.get("http://test.server:4001/healthz", status=200, payload={})
            
            result = await client.healthcheck()
            assert result is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_healthcheck_failure(self):
        from services.amnezia_client import AmneziaClient
        
        client = AmneziaClient("http://test.server:4001", "test_key")
        
        with aioresponses() as mocked:
            mocked.get("http://test.server:4001/healthz", status=500)
            
            result = await client.healthcheck()
            assert result is False

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_create_user_success(self):
        from services.amnezia_client import AmneziaClient
        
        client = AmneziaClient("http://test.server:4001", "test_key")
        
        with aioresponses() as mocked:
            mocked.post(
                "http://test.server:4001/clients",
                status=200,
                payload={
                    "client": {
                        "id": "test_peer_id_123",
                        "config": "vpn://test_config_data"
                    }
                }
            )
            
            result = await client.create_user("test_client")
            
            assert result is not None
            assert result["id"] == "test_peer_id_123"
            assert result["config"] == "vpn://test_config_data"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_delete_user_success(self):
        from services.amnezia_client import AmneziaClient
        
        client = AmneziaClient("http://test.server:4001", "test_key")
        
        with aioresponses() as mocked:
            mocked.delete("http://test.server:4001/clients", status=200, payload={})
            
            result = await client.delete_user("test_peer_id")
            assert result is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_server_info_success(self):
        from services.amnezia_client import AmneziaClient
        
        client = AmneziaClient("http://test.server:4001", "test_key")
        
        with aioresponses() as mocked:
            mocked.get(
                "http://test.server:4001/server",
                status=200,
                payload={
                    "name": "Test Server",
                    "protocols": ["amneziawg2"],
                    "maxPeers": 50
                }
            )
            
            result = await client.get_server_info()
            
            assert result is not None
            assert result["name"] == "Test Server"
            assert "amneziawg2" in result["protocols"]

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_update_client_status(self):
        from services.amnezia_client import AmneziaClient
        
        client = AmneziaClient("http://test.server:4001", "test_key")
        
        with aioresponses() as mocked:
            mocked.patch("http://test.server:4001/clients", status=200, payload={})
            
            result = await client.update_client(
                client_id="test_peer",
                status="disabled"
            )
            assert result is True

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_retry_on_5xx_error(self):
        from services.amnezia_client import AmneziaClient
        
        client = AmneziaClient("http://test.server:4001", "test_key")
        
        with aioresponses() as mocked:
            # Первый запрос — 500 ошибка
            mocked.get("http://test.server:4001/healthz", status=500)
            # Второй запрос (retry) — успех
            mocked.get("http://test.server:4001/healthz", status=200, payload={})
            
            result = await client.healthcheck()
            assert result is True

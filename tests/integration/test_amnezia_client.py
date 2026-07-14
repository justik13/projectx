"""Integration тесты для AmneziaClient с mocking HTTP."""
import pytest
from aioresponses import aioresponses
from services.amnezia_client import AmneziaClientCreateResponse, AmneziaServerInfo


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
        """Тест создания клиента — API возвращает DTO AmneziaClientCreateResponse."""
        from services.amnezia_client import AmneziaClient
        client = AmneziaClient("http://test.server:4001", "test_key")
        with aioresponses() as mocked:
            mocked.post(
                "http://test.server:4001/clients",
                status=200,
                payload={
                    "client": {
                        "id": "test_peer_id_123",
                        "config": "vpn://test_config_data",
                        "protocol": "amneziawg2"
                    }
                }
            )
            result = await client.create_user("test_client")
            # 🔥 ИСПРАВЛЕНО: результат — DTO, а не dict
            assert result is not None
            assert isinstance(result, AmneziaClientCreateResponse)
            assert result.id == "test_peer_id_123"
            assert result.config == "vpn://test_config_data"
            assert result.protocol == "amneziawg2"

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
        """Тест получения информации о сервере — API возвращает DTO AmneziaServerInfo."""
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
            # 🔥 ИСПРАВЛЕНО: результат — DTO, а не dict
            assert result is not None
            assert isinstance(result, AmneziaServerInfo)
            assert result.name == "Test Server"
            assert "amneziawg2" in result.protocols
            assert result.maxPeers == 50
            assert result.get_effective_max_peers() == 50

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

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_get_all_clients_returns_dto_list(self):
        """Тест получения списка клиентов — возвращает List[AmneziaClientListItem]."""
        from services.amnezia_client import AmneziaClient, AmneziaClientListItem
        client = AmneziaClient("http://test.server:4001", "test_key")
        with aioresponses() as mocked:
            mocked.get(
                "http://test.server:4001/clients",
                status=200,
                payload={
                    "clients": [
                        {
                            "id": "peer_1",
                            "clientName": "tg_111_Device_abc",
                            "status": "active",
                            "traffics": {"totalDownload": 1000, "totalUpload": 500}
                        },
                        {
                            "id": "peer_2",
                            "clientName": "tg_222_Device_xyz",
                            "status": "disabled",
                            "traffics": {"totalDownload": 2000, "totalUpload": 1000}
                        }
                    ]
                }
            )
            result = await client.get_all_clients()
            assert result is not None
            assert len(result) == 2
            assert all(isinstance(c, AmneziaClientListItem) for c in result)
            assert result[0].id == "peer_1"
            assert result[0].clientName == "tg_111_Device_abc"
            assert result[0].status == "active"
            assert result[0].traffics.totalDownload == 1000
            assert result[1].id == "peer_2"
            assert result[1].status == "disabled"
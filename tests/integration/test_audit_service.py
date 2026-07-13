"""Integration тесты для сервиса аудита."""
import pytest


class TestAuditService:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_log_action_basic(self, test_db_session):
        from services.audit_service import AuditService
        from database.repositories.audit_repo import get_recent_audit_logs

        await AuditService.log_action(
            test_db_session,
            admin_id=123456789,
            action="TEST_ACTION",
            target_type="User",
            target_id=999,
            details="Test details"
        )

        logs = await get_recent_audit_logs(test_db_session, limit=10)
        assert len(logs) >= 1
        
        last_log = logs[0]
        assert last_log.admin_id == 123456789
        assert last_log.action == "TEST_ACTION"
        assert last_log.target_type == "User"
        assert last_log.target_id == 999
        assert last_log.details == "Test details"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_log_action_minimal(self, test_db_session):
        from services.audit_service import AuditService

        # Без опциональных параметров
        await AuditService.log_action(
            test_db_session,
            admin_id=987654321,
            action="SIMPLE_ACTION"
        )

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_log_action_error_handling(self, test_db_session):
        from services.audit_service import AuditService

        # Не должно упасть даже при ошибке
        await AuditService.log_action(
            None,  # Невалидная сессия
            admin_id=123456789,
            action="SHOULD_NOT_CRASH"
        )

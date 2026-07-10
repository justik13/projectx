from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.audit_repo import create_audit_log
import logging

logger = logging.getLogger(__name__)


class AuditService:
    @staticmethod
    async def log_action(
        session: AsyncSession,
        admin_id: int,
        action: str,
        target_type: str = None,
        target_id: int = None,
        details: str = None
    ):
        try:
            await create_audit_log(
                session=session,
                admin_id=admin_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                details=details
            )
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from database.repositories.maintenance_repo import (
    get_maintenance_mode,
    is_maintenance_enabled,
    set_maintenance_mode,
)
from utils.admin import is_admin

logger = logging.getLogger(__name__)

DEFAULT_MAINTENANCE_MESSAGE = (
    "⚠️ Ведутся технические работы. "
    "Некоторые действия временно недоступны. "
    "Попробуйте позже."
)


class MaintenanceService:
    """
    Сервис режима технических работ.

    Правила:
    - обычные пользователи не могут создавать новые устройства;
    - обычные пользователи не могут создавать новые платежи;
    - существующие подключения продолжают работать;
    - админка доступна;
    - админы могут обходить режим;
    - webhook и фоновые задачи продолжают работать,
      чтобы не терять уже оплаченные платежи.
    """

    @staticmethod
    async def is_enabled(session: AsyncSession) -> bool:
        return await is_maintenance_enabled(session)

    @staticmethod
    async def get_message(session: AsyncSession) -> str:
        maintenance = await get_maintenance_mode(session)

        if maintenance is None:
            return DEFAULT_MAINTENANCE_MESSAGE

        if maintenance.message:
            return maintenance.message

        return DEFAULT_MAINTENANCE_MESSAGE

    @staticmethod
    async def can_user_perform_action(
        session: AsyncSession,
        telegram_id: int,
    ) -> bool:
        """
        Возвращает True, если пользователю разрешено выполнять
        ограниченные действия.

        Админы всегда могут выполнять действия.
        Обычные пользователи — только если режим выключен.
        """
        if is_admin(telegram_id):
            return True

        return not await is_maintenance_enabled(session)

    @staticmethod
    async def enable(
        session: AsyncSession,
        admin_id: int,
        message: Optional[str] = None,
    ) -> None:
        await set_maintenance_mode(
            session,
            is_enabled=True,
            message=message or DEFAULT_MAINTENANCE_MESSAGE,
            updated_by=admin_id,
        )

        logger.info(
            "Maintenance mode enabled by admin %s",
            admin_id,
        )

    @staticmethod
    async def disable(
        session: AsyncSession,
        admin_id: int,
    ) -> None:
        await set_maintenance_mode(
            session,
            is_enabled=False,
            updated_by=admin_id,
        )

        logger.info(
            "Maintenance mode disabled by admin %s",
            admin_id,
        )

    @staticmethod
    async def toggle(
        session: AsyncSession,
        admin_id: int,
        message: Optional[str] = None,
    ) -> bool:
        """
        Переключает режим технических работ.

        Возвращает новое состояние:
        - True — режим включён;
        - False — режим выключен.
        """
        current = await is_maintenance_enabled(session)

        if current:
            await MaintenanceService.disable(session, admin_id)
            return False

        await MaintenanceService.enable(
            session,
            admin_id,
            message=message,
        )

        return True
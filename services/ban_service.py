import logging

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.middlewares.user_context import invalidate_user_cache
from database.models import Payment
from database.repositories.users_repo import (
    get_user_by_telegram_id,
    update_user,
)
from services.audit_service import AuditService
from services.profile_deletion_service import ProfileDeletionService

logger = logging.getLogger(__name__)


class BanService:
    """
    Сервис бана и разбана пользователей.

    Принятая продуктовая логика:
    - бан сразу удаляет все устройства пользователя;
    - устройства не восстанавливаются после разбана;
    - ожидающие платежи банящегося пользователя отменяются;
    - если пользователь оплатит после бана, платёж должен попасть
      в ручную проверку, а не выдавать доступ автоматически.
    """

    @staticmethod
    async def toggle_ban(
        session: AsyncSession,
        admin_id: int,
        telegram_id: int,
    ) -> tuple:
        user = await get_user_by_telegram_id(session, telegram_id)

        if not user:
            return False, "Пользователь не найден"

        new_status = not user.is_banned

        if new_status:
            return await BanService._ban_user(
                session=session,
                admin_id=admin_id,
                user=user,
                telegram_id=telegram_id,
            )

        return await BanService._unban_user(
            session=session,
            admin_id=admin_id,
            user=user,
            telegram_id=telegram_id,
        )

    @staticmethod
    async def _ban_user(
        session: AsyncSession,
        admin_id: int,
        user,
        telegram_id: int,
    ) -> tuple:
        # 1. Удаляем все устройства пользователя.
        deleted_profiles = (
            await ProfileDeletionService.delete_profiles_for_user(
                session,
                user.id,
                reason="ban_delete",
                background=True,
            )
        )

        # 2. Отменяем ожидающие платежи.
        await session.execute(
            update(Payment)
            .where(
                Payment.user_id == user.id,
                Payment.status == "pending",
            )
            .values(status="cancelled")
        )

        # 3. Ставим бан.
        await update_user(session, user, is_banned=True)

        # 4. Аудит.
        await AuditService.log_action(
            session,
            admin_id,
            "BAN",
            "User",
            telegram_id,
            f"profiles_deleted={deleted_profiles}",
        )

        # 5. Инвалидация кэша пользователя.
        invalidate_user_cache(telegram_id)

        logger.info(
            "User %s banned by admin %s. Deleted profiles: %s",
            telegram_id,
            admin_id,
            deleted_profiles,
        )

        return True, "забанен"

    @staticmethod
    async def _unban_user(
        session: AsyncSession,
        admin_id: int,
        user,
        telegram_id: int,
    ) -> tuple:
        # При разбане устройства НЕ восстанавливаются.
        # Пользователь должен создать их заново, если подписка активна.

        await update_user(session, user, is_banned=False)

        await AuditService.log_action(
            session,
            admin_id,
            "UNBAN",
            "User",
            telegram_id,
            "devices_not_restored",
        )

        invalidate_user_cache(telegram_id)

        logger.info(
            "User %s unbanned by admin %s. Devices were not restored.",
            telegram_id,
            admin_id,
        )

        return True, "разбанен"
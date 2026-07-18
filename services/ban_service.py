import asyncio
import logging
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.middlewares.user_context import invalidate_user_cache
from database.repositories.users_repo import get_user_by_telegram_id, update_user
from database.repositories.profiles_repo import get_user_profiles
from services.amnezia_client import AmneziaClient
from database.models import Server, VPNProfile
from services.audit_service import AuditService
from utils.datetime_helpers import now_utc, is_expired

logger = logging.getLogger(__name__)


async def _update_api_status_background(
    tasks_info: list, target_status: str, telegram_id: int
):
    """
    Фоновая задача: обновляет статус пиров в Amnezia API.
    Запускается через asyncio.create_task(), не блокирует handler.
    """
    sem = asyncio.Semaphore(20)

    async def _update_peer(info):
        async with sem:
            client = AmneziaClient(info['api_url'], info['api_key'])
            try:
                return await client.update_client(
                    client_id=info['peer_id'], status=target_status
                )
            except Exception as e:
                logger.warning(
                    f"Ban background: failed to update peer "
                    f"{info['peer_id'][:16]}... : {e}"
                )
                return False

    tasks = [_update_peer(info) for info in tasks_info]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    errors = [
        r for r in results
        if isinstance(r, Exception) or r is False
    ]

    if errors:
        logger.warning(
            f"Ban background: {len(errors)}/{len(tasks)} API calls failed "
            f"for user {telegram_id}. Self-healing will retry in 15 min."
        )
    else:
        logger.info(
            f"Ban background: all {len(tasks)} peers updated "
            f"for user {telegram_id}"
        )


class BanService:
    @staticmethod
    async def toggle_ban(
        session: AsyncSession, admin_id: int, telegram_id: int
    ) -> tuple[bool, str]:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            return False, "Пользователь не найден"

        new_status = not user.is_banned
        has_access = user.subscription_end and not is_expired(user.subscription_end)

        target_api_status = (
            "disabled" if new_status
            else ("active" if has_access else "disabled")
        )
        target_db_active = (
            False if new_status
            else (True if has_access else False)
        )
        await update_user(session, user, is_banned=new_status)

        profiles = await get_user_profiles(session, user.id)
        if profiles:
            profile_ids = [p.id for p in profiles]
            await session.execute(
                update(VPNProfile)
                .where(VPNProfile.id.in_(profile_ids))
                .values(is_active=target_db_active)
            )
            await session.flush()

        await AuditService.log_action(
            session, admin_id,
            "BAN" if new_status else "UNBAN",
            "User", telegram_id
        )

        invalidate_user_cache(telegram_id)
        tasks_info = []
        if profiles:
            server_ids = {p.server_id for p in profiles}
            servers_map = {}
            if server_ids:
                stmt = select(Server).where(Server.id.in_(server_ids))
                res = await session.execute(stmt)
                servers_map = {s.id: s for s in res.scalars().all()}

            for profile in profiles:
                server = servers_map.get(profile.server_id)
                if server and server.is_active:
                    tasks_info.append({
                        'api_url': server.api_url,
                        'api_key': server.api_key,
                        'peer_id': profile.peer_id
                    })
        if tasks_info:
            asyncio.create_task(
                _update_api_status_background(
                    tasks_info, target_api_status, telegram_id
                )
            )

        action = "забанен" if new_status else "разбанен"
        return True, action
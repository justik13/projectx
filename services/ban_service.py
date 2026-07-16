import asyncio
import logging
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from bot.middlewares.user_context import invalidate_user_cache
from database.repositories.users_repo import get_user_by_telegram_id, update_user
from database.repositories.profiles_repo import get_user_profiles
from services.amnezia_client import AmneziaClient
from database.models import Server, VPNProfile
from services.audit_service import AuditService

logger = logging.getLogger(__name__)


class BanService:
    @staticmethod
    async def toggle_ban(session: AsyncSession, admin_id: int, telegram_id: int) -> tuple[bool, str]:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            return False, "Пользователь не найден"

        new_status = not user.is_banned
        await update_user(session, user, is_banned=new_status)

        await AuditService.log_action(
            session, admin_id, "BAN" if new_status else "UNBAN", "User", telegram_id
        )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        has_access = user.subscription_end and user.subscription_end > now
        target_api_status = "disabled" if new_status else ("active" if has_access else "disabled")
        target_db_status = False if new_status else (True if has_access else False)

        profiles = await get_user_profiles(session, user.id)
        server_ids = {p.server_id for p in profiles}
        servers_map = {}

        if server_ids:
            stmt = select(Server).where(Server.id.in_(server_ids))
            res = await session.execute(stmt)
            servers_map = {s.id: s for s in res.scalars().all()}

        tasks_info = []
        profile_ids_to_update = []

        for profile in profiles:
            server = servers_map.get(profile.server_id)
            if server and server.is_active:
                tasks_info.append({
                    'api_url': server.api_url, 'api_key': server.api_key, 'peer_id': profile.peer_id
                })
                profile_ids_to_update.append(profile.id)

        network_success = True
        if tasks_info:
            sem = asyncio.Semaphore(20)

            async def _update_peer(info, status):
                async with sem:
                    client = AmneziaClient(info['api_url'], info['api_key'])
                    return await client.update_client(client_id=info['peer_id'], status=status)

            tasks = [_update_peer(info, target_api_status) for info in tasks_info]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            api_errors = [r for r in results if isinstance(r, Exception) or r is False]
            if api_errors:
                network_success = False

        if not network_success:
            return False, "Amnezia API недоступен"

        if profile_ids_to_update:
            await session.execute(
                update(VPNProfile)
                .where(VPNProfile.id.in_(profile_ids_to_update))
                .values(is_active=target_db_status)
            )
            # 🔥 ИСПРАВЛЕНО #8: flush() вместо commit() для работы внутри DBSessionMiddleware
            await session.flush()
            invalidate_user_cache(telegram_id)
        action = "забанен" if new_status else "разбанен"
        return True, action
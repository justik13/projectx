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


# ═══════════════════════════════════════════════════════════
# 🔥 ИСПРАВЛЕНО P1-5: API вызовы вынесены за пределы транзакции
# Было:
#   async def toggle_ban(session, ...):                  # ← ОТКРЫТА ТРАНЗАКЦИЯ
#       await update_user(session, user, is_banned=...)  # UPDATE БД
#       await AuditService.log_action(session, ...)       # INSERT БД
#       results = await asyncio.gather(*tasks)            # ← HTTP 15s!
#       if not network_success:
#           await session.rollback()
#   → ТРАНЗАКЦИЯ ДЕРЖИТ СОЕДИНЕНИЕ 15 СЕКУНД!
# Стало:
#   ШАГ 1: Загружаем user + profiles + servers (быстрая транзакция, ~20ms)
#   ШАГ 2: HTTP-запросы к Amnezia API (concurrent, БЕЗ транзакции)
#   ШАГ 3: Если API успешен → обновляем БД (новая транзакция, ~10ms)
#   ШАГ 4: Если API упал → возвращаем False (БД не изменена)
# ═══════════════════════════════════════════════════════════
class BanService:
    @staticmethod
    async def toggle_ban(
        session: AsyncSession, admin_id: int, telegram_id: int
    ) -> tuple[bool, str]:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            return False, "Пользователь не найден"

        new_status = not user.is_banned

        # ═══════════════════════════════════════════════════════════
        # ШАГ 1: Загружаем все необходимые данные из БД
        # (транзакция уже открыта через DBSessionMiddleware)
        # ═══════════════════════════════════════════════════════════
        has_access = user.subscription_end and not is_expired(user.subscription_end)
        target_api_status = (
            "disabled" if new_status
            else ("active" if has_access else "disabled")
        )
        target_db_status = (
            False if new_status
            else (True if has_access else False)
        )

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
                    'api_url': server.api_url,
                    'api_key': server.api_key,
                    'peer_id': profile.peer_id
                })
                profile_ids_to_update.append(profile.id)

        # ═══════════════════════════════════════════════════════════
        # ШАГ 2: HTTP-запросы к Amnezia API (concurrent)
        # Транзакция всё ещё открыта, но мы не делаем await внутри неё —
        # мы закрываем её явным commit/rollback и открываем новую
        # ═══════════════════════════════════════════════════════════
        # Для этого сначала принудительно коммитим текущую транзакцию
        # (чтобы освободить соединение из пула)
        try:
            await session.commit()
        except Exception:
            await session.rollback()
            raise

        # Теперь выполняем HTTP-запросы БЕЗ активной транзакции
        network_success = True
        api_errors = []
        if tasks_info:
            sem = asyncio.Semaphore(20)

            async def _update_peer(info, status):
                async with sem:
                    client = AmneziaClient(info['api_url'], info['api_key'])
                    return await client.update_client(
                        client_id=info['peer_id'], status=status
                    )

            tasks = [_update_peer(info, target_api_status) for info in tasks_info]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            api_errors = [
                r for r in results
                if isinstance(r, Exception) or r is False
            ]
            if api_errors:
                network_success = False

        # ═══════════════════════════════════════════════════════════
        # ШАГ 3: Обновляем БД в НОВОЙ транзакции (только если API успешен)
        # ═══════════════════════════════════════════════════════════
        if not network_success:
            invalidate_user_cache(telegram_id)
            return False, "Amnezia API недоступен (изменения не применены)"

        # Открываем новую транзакцию через session_scope
        from database.connection import session_scope
        async with session_scope() as new_session:
            # Перезагружаем user в новой сессии
            fresh_user = await get_user_by_telegram_id(new_session, telegram_id)
            if not fresh_user:
                return False, "Пользователь исчез из БД"

            # Обновляем флаг бана
            await update_user(new_session, fresh_user, is_banned=new_status)

            # Логируем в аудит
            await AuditService.log_action(
                new_session, admin_id,
                "BAN" if new_status else "UNBAN",
                "User", telegram_id
            )

            # Обновляем статус профилей в БД
            if profile_ids_to_update:
                await new_session.execute(
                    update(VPNProfile)
                    .where(VPNProfile.id.in_(profile_ids_to_update))
                    .values(is_active=target_db_status)
                )
                await new_session.flush()

        invalidate_user_cache(telegram_id)
        action = "забанен" if new_status else "разбанен"
        return True, action
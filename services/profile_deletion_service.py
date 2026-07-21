import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import session_scope, queue_post_commit_task
from database.models import (
    PendingAPIDeletion,
    Server,
    VPNProfile,
)
from services.amnezia_client import AmneziaClient
from utils.datetime_helpers import now_utc

logger = logging.getLogger(__name__)


class ProfileDeletionService:
    """
    Общий сервис удаления пользовательских устройств.

    Используется в сценариях:
    - бан пользователя;
    - chargeback;
    - grace-период 48 часов после истечения подписки;
    - ручное удаление админом;
    - очистка сиротских профилей.

    Логика:
    1. Профили удаляются из БД.
    2. Удаление на сервере выполняется только после commit.
    3. Если сервер недоступен или удаление не удалось,
       создаётся запись в pending_api_deletions.
    4. Фоновый cleanup позже повторит удаление.
    """

    @staticmethod
    async def delete_profiles_for_user(
        session: AsyncSession,
        user_id: int,
        *,
        reason: str,
        background: bool = True,
    ) -> int:
        stmt = select(VPNProfile).where(VPNProfile.user_id == user_id)
        result = await session.execute(stmt)
        profiles = list(result.scalars().all())

        if not profiles:
            return 0

        return await ProfileDeletionService._delete_profiles(
            session,
            profiles,
            reason=reason,
            background=background,
        )

    @staticmethod
    async def delete_profiles_list(
        session: AsyncSession,
        profiles: list,
        *,
        reason: str,
        background: bool = True,
    ) -> int:
        if not profiles:
            return 0

        return await ProfileDeletionService._delete_profiles(
            session,
            profiles,
            reason=reason,
            background=background,
        )

    @staticmethod
    async def _delete_profiles(
        session: AsyncSession,
        profiles: list,
        *,
        reason: str,
        background: bool,
    ) -> int:
        server_ids = {profile.server_id for profile in profiles}
        servers_stmt = select(Server).where(Server.id.in_(server_ids))
        servers_result = await session.execute(servers_stmt)

        servers_map = {
            server.id: server
            for server in servers_result.scalars().all()
        }

        deletion_tasks = []

        for profile in profiles:
            server = servers_map.get(profile.server_id)

            if server is None:
                logger.warning(
                    "ProfileDeletionService: profile %s references "
                    "missing server %s. DB profile will be removed, "
                    "but API deletion cannot be queued.",
                    profile.id,
                    profile.server_id,
                )
                continue

            if not server.api_url or not server.api_key:
                logger.critical(
                    "ProfileDeletionService: server %s has invalid "
                    "API URL or API key. Profile %s cannot be queued "
                    "for API deletion.",
                    server.id,
                    profile.id,
                )
                continue

            deletion_tasks.append(
                {
                    "api_url": server.api_url,
                    "api_key": server.api_key,
                    "server_name": server.name,
                    "peer_id": profile.peer_id,
                    "client_name": f"tg_{profile.user_id}_{profile.id}",
                    "profile_id": profile.id,
                    "reason": reason,
                }
            )

        for profile in profiles:
            await session.delete(profile)

        await session.flush()

        logger.info(
            "ProfileDeletionService: removed %s profiles from DB, "
            "reason=%s",
            len(profiles),
            reason,
        )

        if deletion_tasks:
            queue_post_commit_task(
                session,
                lambda tasks=deletion_tasks: (
                    ProfileDeletionService._delete_peers_on_api_background(
                        deletion_tasks=tasks,
                    )
                ),
            )

        return len(profiles)

    @staticmethod
    async def _delete_peers_on_api_background(
        deletion_tasks: list,
    ) -> None:
        if not deletion_tasks:
            return

        import asyncio

        semaphore = asyncio.Semaphore(20)
        success_count = 0
        failed_tasks = []

        async def _delete_one(task_info: dict):
            nonlocal success_count

            async with semaphore:
                client = AmneziaClient(
                    task_info["api_url"],
                    task_info["api_key"],
                )

                try:
                    deleted = await client.delete_user(
                        client_id=task_info["peer_id"],
                    )

                    if deleted:
                        success_count += 1
                    else:
                        failed_tasks.append(task_info)

                except Exception as e:
                    logger.warning(
                        "ProfileDeletionService background: failed to "
                        "delete peer %s on server %s: %s",
                        task_info["peer_id"][:16],
                        task_info["server_name"],
                        e,
                    )
                    failed_tasks.append(task_info)

        await asyncio.gather(
            *[_delete_one(task_info) for task_info in deletion_tasks],
            return_exceptions=True,
        )

        logger.info(
            "ProfileDeletionService background: "
            "%s/%s peers deleted on API",
            success_count,
            len(deletion_tasks),
        )

        if failed_tasks:
            await ProfileDeletionService._queue_failed_api_deletions(
                failed_tasks=failed_tasks,
            )

    @staticmethod
    async def _queue_failed_api_deletions(
        failed_tasks: list,
    ) -> None:
        try:
            async with session_scope() as session:
                current_time = now_utc()

                for task_info in failed_tasks:
                    pending = PendingAPIDeletion(
                        server_name=task_info["server_name"],
                        api_url=task_info["api_url"],
                        api_key=task_info["api_key"],
                        peer_id=task_info["peer_id"],
                        client_name=task_info.get("client_name"),
                        reason=task_info.get("reason"),
                        attempts=1,
                        last_attempt_at=current_time,
                        last_error="Background API deletion failed",
                    )
                    session.add(pending)

                await session.flush()

                logger.warning(
                    "ProfileDeletionService: queued %s failed API deletions "
                    "for cleanup",
                    len(failed_tasks),
                )

        except Exception as e:
            logger.error(
                "ProfileDeletionService: failed to queue pending API "
                "deletions: %s",
                e,
                exc_info=True,
            )
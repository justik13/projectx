import asyncio
import logging
from datetime import timedelta
from typing import Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import (
    PERMANENT_SUBSCRIPTION_DAYS,
    PERMANENT_END_DATE,
)
from bot.middlewares.user_context import invalidate_user_cache
from database.connection import queue_post_commit_task
from database.models import User, VPNProfile
from database.repositories.profiles_repo import (
    get_user_profiles,
    get_user_profiles_count,
)
from database.repositories.servers_repo import get_server_by_id
from database.repositories.users_repo import (
    create_user,
    get_user_by_telegram_id,
    get_user_by_telegram_id_any,
)
from services.amnezia_client import AmneziaClient
from utils.datetime_helpers import is_expired, now_utc

logger = logging.getLogger(__name__)


class SubscriptionService:
    @staticmethod
    async def sync_access_state(
        session: AsyncSession,
        user: User,
    ) -> None:
        """
        Публичная обёртка для админских действий.
        """
        await SubscriptionService._sync_access_state(session, user)

    @staticmethod
    async def check_access(
        session: AsyncSession,
        telegram_id: int,
    ) -> bool:
        user = await get_user_by_telegram_id(session, telegram_id)

        if not user or user.is_banned or not user.subscription_end:
            return False

        return not is_expired(user.subscription_end)

    @staticmethod
    async def _validate_referral(
        session: AsyncSession,
        telegram_id: int,
        ref_id: int,
    ) -> bool:
        if ref_id == telegram_id:
            logger.warning(
                "Referral: self-referral attempt by %s",
                telegram_id,
            )
            return False

        ref_user = await get_user_by_telegram_id(session, ref_id)
        if not ref_user:
            logger.warning(
                "Referral: referrer %s not found in DB",
                ref_id,
            )
            return False

        current_id = ref_id
        chain_visited = {telegram_id, ref_id}

        for _ in range(5):
            if not current_id:
                break

            current_user = await get_user_by_telegram_id(
                session,
                current_id,
            )

            if not current_user or not current_user.referred_by:
                break

            if current_user.referred_by in chain_visited:
                logger.warning(
                    "Circular referral chain detected for user %s, "
                    "ref_id %s",
                    telegram_id,
                    ref_id,
                )
                return False

            chain_visited.add(current_user.referred_by)
            current_id = current_user.referred_by

        return True

    @staticmethod
    async def process_onboarding(
        session: AsyncSession,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        ref_id: int | None = None,
    ) -> Optional[User]:
        #
        # Ищем пользователя включая soft-deleted.
        #
        # Это нужно, чтобы:
        # - не ловить unique constraint при повторном входе;
        # - безопасно восстанавливать soft-deleted пользователя;
        # - не создавать дубликат.
        #
        user = await get_user_by_telegram_id_any(
            session,
            telegram_id,
        )

        #
        # Если пользователь был soft-deleted, восстанавливаем его.
        #
        # В проекте пока нет полноценного пользовательского удаления,
        # поэтому восстановление при /start безопасно и защищает
        # от unique constraint error.
        #
        if user is not None and user.is_deleted:
            user.is_deleted = False
            user.deleted_at = None
            await session.flush()

            invalidate_user_cache(telegram_id)

            logger.info(
                "Restored soft-deleted user %s on onboarding",
                telegram_id,
            )

        if user is not None:
            changed = False

            if username is not None and user.username != username:
                user.username = username
                changed = True

            if first_name is not None and user.first_name != first_name:
                user.first_name = first_name
                changed = True

            #
            # Поздняя привязка реферала.
            #
            # Если пользователь уже существует, но пришёл по реферальной
            # ссылке и ещё не был привязан к рефереру, привязываем.
            #
            if ref_id is not None and user.referred_by is None:
                is_valid = await SubscriptionService._validate_referral(
                    session,
                    telegram_id,
                    ref_id,
                )

                if is_valid:
                    user.referred_by = ref_id
                    changed = True

                    logger.info(
                        "Late referral binding: user %s bound to "
                        "referrer %s",
                        telegram_id,
                        ref_id,
                    )

            if changed:
                await session.flush()
                invalidate_user_cache(telegram_id)

            return user

        referred_by = None

        if ref_id is not None:
            is_valid = await SubscriptionService._validate_referral(
                session,
                telegram_id,
                ref_id,
            )

            if is_valid:
                referred_by = ref_id

                logger.info(
                    "New user %s referred by %s",
                    telegram_id,
                    ref_id,
                )

        user = await create_user(
            session,
            telegram_id,
            username,
            first_name,
            referred_by,
        )

        #
        # Критично: иначе UserContextMiddleware может ещё 15 секунд
        # отдавать None для только что созданного пользователя.
        #
        invalidate_user_cache(telegram_id)

        return user

    @staticmethod
    async def extend_subscription(
        session: AsyncSession,
        telegram_id: int,
        days: int,
        new_device_limit: Optional[int] = None,
        new_tariff_id: Optional[int] = None,
    ) -> Optional[User]:
        user = await get_user_by_telegram_id(session, telegram_id)

        if not user:
            return None

        if new_device_limit is not None:
            profiles_count = await get_user_profiles_count(
                session,
                user.id,
            )

            if profiles_count > new_device_limit:
                raise ValueError(
                    f"Cannot downgrade: {profiles_count} devices > "
                    f"{new_device_limit} limit. "
                    f"User must delete devices first."
                )

        now = now_utc()

        had_active_subscription = bool(
            user.subscription_end and user.subscription_end > now
        )

        #
        # Если days == 0 и активной подписки нет,
        # не делаем подписку "активной до сейчас".
        #
        # Это нужно для сценария админской смены тарифа без продления:
        # тариф/лимит можно поменять, но доступ не должен внезапно
        # стать активным.
        #
        if days == 0 and not had_active_subscription:
            new_end = user.subscription_end
        else:
            current_end = (
                user.subscription_end
                if had_active_subscription
                else now
            )

            new_end = (
                PERMANENT_END_DATE
                if days >= PERMANENT_SUBSCRIPTION_DAYS
                else current_end + timedelta(days=days)
            )

        user.subscription_end = new_end

        user.notified_3d = False
        user.notified_1d = False
        user.notified_2h = False

        #
        # Сбрасываем grace-уведомления, чтобы после продления
        # пользователь не получал старые уведомления об истечении.
        #
        user.notified_expired = False
        user.notified_grace_12h = False

        #
        # Сбрасываем notification retry state.
        #
        # Иначе старый счётчик ошибок может задержать новые уведомления.
        #
        user.notification_retry_count = 0
        user.last_notification_attempt = None

        if new_device_limit is not None:
            old_device_limit = user.device_limit
            user.device_limit = new_device_limit

            if new_device_limit > old_device_limit:
                user.device_creations_today = 0
                user.last_creation_date = None

                logger.info(
                    "extend_subscription: user %s upgraded from "
                    "%s to %s devices. Daily creations counter "
                    "reset to 0.",
                    telegram_id,
                    old_device_limit,
                    new_device_limit,
                )

        if new_tariff_id is not None:
            user.current_tariff_id = new_tariff_id

        await session.flush()

        invalidate_user_cache(telegram_id)

        #
        # После изменения подписки синхронизируем статус устройств:
        # - если доступ активен и пользователь не забанен —
        #   устройства активны;
        # - если доступ неактивен или пользователь забанен —
        #   устройства неактивны.
        #
        await SubscriptionService._sync_access_state(session, user)

        return user

    @staticmethod
    async def _sync_access_state(
        session: AsyncSession,
        user: User,
    ) -> None:
        """
        Синхронизирует is_active у профилей в БД и статус на сервере.

        Правила:
        - если подписка активна и пользователь не забанен:
          профили активны;
        - если подписка истекла или пользователь забанен:
          профили неактивны.

        Важно:
        - API-синхронизация выполняется только после commit.
        """
        target_active = bool(
            user.subscription_end
            and not is_expired(user.subscription_end)
            and not user.is_banned
        )

        profiles = await get_user_profiles(session, user.id)
        if not profiles:
            return

        profile_ids = [profile.id for profile in profiles]

        await session.execute(
            update(VPNProfile)
            .where(VPNProfile.id.in_(profile_ids))
            .values(is_active=target_active)
        )

        await session.flush()

        expires_ts = (
            await SubscriptionService.get_expires_timestamp(user)
            if target_active
            else None
        )

        target_status = "active" if target_active else "disabled"

        queue_post_commit_task(
            session,
            lambda uid=user.id, ts=expires_ts, st=target_status: (
                SubscriptionService._sync_expires_to_servers(
                    uid,
                    ts,
                    st,
                )
            ),
        )

    @staticmethod
    async def _sync_expires_to_servers(
        user_id: int,
        expires_ts: Optional[int],
        target_status: str = "active",
    ):
        from database.connection import session_scope

        try:
            async with session_scope() as session:
                profiles = await get_user_profiles(
                    session,
                    user_id,
                )

                if not profiles:
                    return

                server_ids = {p.server_id for p in profiles}
                servers_map = {}

                for sid in server_ids:
                    server = await get_server_by_id(session, sid)
                    if server and server.api_url and server.api_key:
                        servers_map[sid] = server

                tasks = []

                for profile in profiles:
                    server = servers_map.get(profile.server_id)
                    if not server:
                        continue

                    client = AmneziaClient(
                        server.api_url,
                        server.api_key,
                    )

                    #
                    # Для вечной подписки expires_ts=None.
                    #
                    # Если целевой статус active и expires_ts=None,
                    # нужно явно очистить expiresAt на сервере.
                    # Иначе API может сохранить старый срок истечения.
                    #
                    clear_expires_at = (
                        target_status == "active"
                        and expires_ts is None
                    )

                    tasks.append(
                        client.update_client(
                            client_id=profile.peer_id,
                            expires_at=(
                                expires_ts
                                if target_status == "active"
                                else None
                            ),
                            status=target_status,
                            clear_expires_at=clear_expires_at,
                        )
                    )

                if tasks:
                    results = await asyncio.gather(
                        *tasks,
                        return_exceptions=True,
                    )

                    success = sum(
                        1 for r in results if r is True
                    )

                    logger.info(
                        "Access state sync: %s/%s servers updated "
                        "for user_id=%s, status=%s",
                        success,
                        len(tasks),
                        user_id,
                        target_status,
                    )

        except Exception as e:
            logger.error(
                "Access state sync failed for user_id=%s: %s",
                user_id,
                e,
                exc_info=True,
            )

    @staticmethod
    async def get_expires_timestamp(
        user: User,
    ) -> Optional[int]:
        if (
            not user.subscription_end
            or user.subscription_end.year >= 2100
        ):
            logger.info(
                "get_expires_timestamp: user %s has permanent "
                "subscription, sending expiresAt=null to API",
                user.telegram_id,
            )
            return None

        expires_ts = int(user.subscription_end.timestamp())
        return expires_ts
from sqlalchemy.ext.asyncio import AsyncSession
from database.repositories.users_repo import get_user_by_telegram_id, create_user, update_user
from database.repositories.profiles_repo import get_user_profiles, get_user_profiles_count
from database.repositories.servers_repo import get_server_by_id
from database.models import User
from datetime import timedelta
from typing import Optional
from bot.constants import PERMANENT_SUBSCRIPTION_DAYS, PERMANENT_END_DATE
from bot.middlewares.user_context import invalidate_user_cache
from services.amnezia_client import AmneziaClient
from utils.datetime_helpers import now_utc, is_expired
import asyncio
import logging

logger = logging.getLogger(__name__)


class SubscriptionService:

    @staticmethod
    async def check_access(session: AsyncSession, telegram_id: int) -> bool:
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
                f"Referral: self-referral attempt by {telegram_id}"
            )
            return False

        ref_user = await get_user_by_telegram_id(session, ref_id)
        if not ref_user:
            logger.warning(
                f"Referral: referrer {ref_id} not found in DB"
            )
            return False

        # Проверка циклической цепочки (до 5 уровней вглубь)
        current_id = ref_id
        chain_visited = {telegram_id, ref_id}
        for _ in range(5):
            if not current_id:
                break
            current_user = await get_user_by_telegram_id(session, current_id)
            if not current_user or not current_user.referred_by:
                break
            if current_user.referred_by in chain_visited:
                logger.warning(
                    f"Circular referral chain detected for user "
                    f"{telegram_id}, ref_id {ref_id}"
                )
                return False
            chain_visited.add(current_user.referred_by)
            current_id = current_user.referred_by

        return True

    @staticmethod
    async def process_onboarding(
        session: AsyncSession, telegram_id: int,
        username: str | None, first_name: str | None,
        ref_id: int | None = None
    ) -> User:
        user = await get_user_by_telegram_id(session, telegram_id)

        if user:
            if (
                ref_id is not None
                and user.referred_by is None
            ):
                is_valid = await SubscriptionService._validate_referral(
                    session, telegram_id, ref_id
                )
                if is_valid:
                    user.referred_by = ref_id
                    await session.flush()
                    invalidate_user_cache(telegram_id)
                    logger.info(
                        f"Late referral binding: user {telegram_id} "
                        f"bound to referrer {ref_id} "
                        f"(was unbound on first /start)"
                    )
            return user

        referred_by = None
        if ref_id is not None:
            is_valid = await SubscriptionService._validate_referral(
                session, telegram_id, ref_id
            )
            if is_valid:
                referred_by = ref_id
                logger.info(
                    f"New user {telegram_id} referred by {ref_id}"
                )

        return await create_user(
            session, telegram_id, username, first_name, referred_by
        )

    @staticmethod
    async def extend_subscription(
        session: AsyncSession, telegram_id: int, days: int,
        new_device_limit: Optional[int] = None,
        new_tariff_id: Optional[int] = None,
    ) -> Optional[User]:
        user = await get_user_by_telegram_id(session, telegram_id)
        if not user:
            return None

        if new_device_limit is not None:
            profiles_count = await get_user_profiles_count(session, user.id)
            if profiles_count > new_device_limit:
                raise ValueError(
                    f"Cannot downgrade: {profiles_count} devices > "
                    f"{new_device_limit} limit. "
                    f"User must delete devices first."
                )

        now = now_utc()
        current_end = user.subscription_end if (
            user.subscription_end and user.subscription_end > now
        ) else now

        new_end = (
            PERMANENT_END_DATE
            if days >= PERMANENT_SUBSCRIPTION_DAYS
            else current_end + timedelta(days=days)
        )

        user.subscription_end = new_end
        user.notified_3d = False
        user.notified_1d = False
        user.notified_2h = False

        if new_device_limit is not None:
            old_device_limit = user.device_limit
            user.device_limit = new_device_limit
            if new_device_limit > old_device_limit:
                user.device_creations_today = 0
                user.last_creation_date = None
                logger.info(
                    f"extend_subscription: user {telegram_id} upgraded from "
                    f"{old_device_limit} to {new_device_limit} devices. "
                    f"Daily creations counter reset to 0."
                )

        if new_tariff_id is not None:
            user.current_tariff_id = new_tariff_id

        await session.flush()
        invalidate_user_cache(telegram_id)

        expires_ts = await SubscriptionService.get_expires_timestamp(user)
        asyncio.create_task(
            SubscriptionService._sync_expires_to_servers(
                user.id, expires_ts
            )
        )

        return user

    @staticmethod
    async def _sync_expires_to_servers(
        user_id: int, expires_ts: Optional[int]
    ):
        from database.connection import session_scope
        try:
            async with session_scope() as session:
                profiles = await get_user_profiles(session, user_id)
                if not profiles:
                    return

                server_ids = {p.server_id for p in profiles}
                servers_map = {}
                for sid in server_ids:
                    s = await get_server_by_id(session, sid)
                    if s:
                        servers_map[sid] = s

                tasks = []
                for profile in profiles:
                    server = servers_map.get(profile.server_id)
                    if server and server.is_active:
                        client = AmneziaClient(
                            server.api_url, server.api_key
                        )
                        tasks.append(
                            client.update_client(
                                client_id=profile.peer_id,
                                expires_at=expires_ts,
                                status="active"
                            )
                        )

                if tasks:
                    results = await asyncio.gather(
                        *tasks, return_exceptions=True
                    )
                    success = sum(
                        1 for r in results if r is True
                    )
                    logger.info(
                        f"expiresAt sync: {success}/{len(tasks)} "
                        f"servers updated for user_id={user_id}"
                    )
        except Exception as e:
            logger.error(
                f"expiresAt sync failed for user_id={user_id}: {e}",
                exc_info=True
            )

    @staticmethod
    async def get_expires_timestamp(user: User) -> Optional[int]:
        if not user.subscription_end or user.subscription_end.year >= 2100:
            logger.info(
                f"get_expires_timestamp: user {user.telegram_id} "
                f"has permanent subscription, "
                f"sending expiresAt=null to API"
            )
            return None
        expires_ts = int(user.subscription_end.timestamp())
        return expires_ts
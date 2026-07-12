import logging
from typing import Any, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from database.models import User
from database.repositories.users_repo import get_user_by_telegram_id

logger = logging.getLogger(__name__)


class UserContextMiddleware(BaseMiddleware):
    """
    Загружает пользователя в контекст запроса.
    ✅ ИСПРАВЛЕНО: использует selectinload для eager loading полей,
    чтобы избежать DetachedInstanceError при доступе к атрибутам после закрытия сессии.
    """
    async def __call__(
        self,
        handler: Callable,
        event: Message | CallbackQuery,
        data: Dict[str, Any],
    ) -> Any:
        session: AsyncSession = data.get("session")
        if not session:
            return await handler(event, data)
            
        # Определяем telegram_id из разных типов событий
        telegram_id = None
        if isinstance(event, Message):
            telegram_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            telegram_id = event.from_user.id
            
        if not telegram_id:
            return await handler(event, data)
        
        # ✅ ИСПРАВЛЕНО: eager loading полей, которые могут понадобиться позже
        # Это предотвращает DetachedInstanceError при доступе к user.is_banned и другим полям
        stmt = (
            select(User)
            .where(User.telegram_id == telegram_id)
            .options(
                selectinload(User.tariff),
                selectinload(User.profiles),
                selectinload(User.payments),
            )
        )
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()
        
        if user:
            # ✅ ИСПРАВЛЕНО: явная загрузка всех полей, которые могут быть lazy
            # Это гарантирует, что они доступны даже после закрытия сессии
            await session.refresh(user, attribute_names=[
                'is_banned', 'is_bot_blocked', 'current_tariff_id',
                'subscription_end', 'device_limit', 'referral_days'
            ])
            data["db_user"] = user
            
            # Проверка бана — теперь работает без ошибок
            if user.is_banned:
                if isinstance(event, CallbackQuery):
                    await event.answer("⛔️ Доступ заблокирован", show_alert=True)
                    return
                elif isinstance(event, Message):
                    await event.answer("⛔️ У вас заблокирован доступ к сервису.")
                    return
        
        return await handler(event, data)
"""
Сервис обработки платежей.

🔥 ИСПРАВЛЕНО (Часть 2):
- #2: Сверка amount/payload в Platega webhook
- #3: Убран session.commit() из handle_successful_payment
"""

import logging
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from bot.middlewares.user_context import invalidate_user_cache
from database.repositories.payments_repo import get_user_payments, get_payment_by_id
from database.models import Payment
from services.subscription import SubscriptionService
from services.referral_service import ReferralService
from services.platega_client import PlategaClient
from services.audit_service import AuditService
from config.settings import get_settings

logger = logging.getLogger(__name__)


class PaymentService:
    @staticmethod
    async def handle_successful_payment(
        session: AsyncSession, payment_id: int
    ) -> bool:
        """
        Обрабатывает успешную оплату. Возвращает True при успехе.
        
        🔥 ИСПРАВЛЕНО (Часть 2):
        - Убран session.commit() — DBSessionMiddleware сделает финальный commit
        """
        try:
            async with session.begin_nested() as savepoint:
                stmt = (
                    update(Payment)
                    .where(Payment.id == payment_id, Payment.status == 'pending')
                    .values(
                        status='completed',
                        paid_at=datetime.now(timezone.utc).replace(tzinfo=None)
                    )
                )
                result = await session.execute(stmt)
                
                if result.rowcount == 0:
                    await savepoint.commit()
                    return True
                
                result = await session.execute(
                    select(Payment)
                    .options(selectinload(Payment.user), selectinload(Payment.tariff))
                    .where(Payment.id == payment_id)
                )
                payment = result.scalar_one()
                tariff = payment.tariff
                user = payment.user
                
                if not tariff or not user:
                    logger.error(f"Payment {payment_id}: missing tariff or user")
                    await savepoint.rollback()
                    return False
                
                new_device_limit = getattr(tariff, 'device_limit', user.device_limit)
                
                try:
                    await SubscriptionService.extend_subscription(
                        session, user.telegram_id, tariff.duration_days,
                        new_device_limit=new_device_limit,
                        new_tariff_id=tariff.id,
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to extend subscription for payment {payment_id}: {e}",
                        exc_info=True
                    )
                    await savepoint.rollback()
                    return False
                
                # Проверка по paid_at IS NOT NULL для реферального бонуса
                payments = await get_user_payments(session, user.id)
                successful_payments = [p for p in payments if p.paid_at is not None]
                is_first_payment = len(successful_payments) == 1
                
                if is_first_payment and user.referred_by:
                    try:
                        await ReferralService.process_bonus(
                            session, user.telegram_id, user.referred_by
                        )
                    except Exception as e:
                        logger.warning(
                            f"Referral bonus failed for payment {payment_id}: {e}"
                        )
                
                user.last_payment_at = datetime.now(timezone.utc).replace(tzinfo=None)
                
                await savepoint.commit()
                # 🔥 ИСПРАВЛЕНО: Убран session.commit() — DBSessionMiddleware сделает финальный commit
                # await session.commit()  # ← УДАЛЕНО
                
                invalidate_user_cache(user.telegram_id)
                
                try:
                    await AuditService.log_action(
                        session, admin_id=0, action="PAYMENT_SUCCESS",
                        target_type="Payment", target_id=payment_id,
                        details=f"user={user.telegram_id}, amount={payment.amount} {payment.currency}"
                    )
                except Exception as e:
                    logger.error(f"Failed to log payment success to audit: {e}")
                
                logger.info(
                    f"Payment {payment_id} processed successfully for user {user.telegram_id}"
                )
                
                return True
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to process payment {payment_id}: {e}", exc_info=True)
            return False
    
    @staticmethod
    async def create_platega_payment(
        session: AsyncSession, user_id: int, tariff_id: int,
        amount: float, telegram_id: int, bot_username: str
    ) -> tuple:
        from database.repositories.payments_repo import create_payment
        
        settings = get_settings()
        payment = await create_payment(
            session=session, user_id=user_id, tariff_id=tariff_id,
            amount=int(amount), currency="RUB"
        )
        
        description = f"Оплата подписки. TgId:{telegram_id} UserId:{user_id}"
        clean_username = bot_username.lstrip("@")
        return_url = settings.PLATEGA_RETURN_URL.format(bot_username=clean_username)
        failed_url = settings.PLATEGA_FAILED_URL.format(bot_username=clean_username)
        payload = f"payment_{payment.id}"
        
        client = PlategaClient()
        transaction = await client.create_transaction(
            amount=amount, currency="RUB", description=description,
            return_url=return_url, failed_url=failed_url, payload=payload
        )
        
        if not transaction:
            payment.status = "failed"
            await session.commit()
            
            try:
                await AuditService.log_action(
                    session, admin_id=0, action="PAYMENT_FAILED",
                    target_type="Payment", target_id=payment.id,
                    details=f"user={user_id}, amount={amount} RUB, Platega create_transaction failed"
                )
            except Exception as e:
                logger.error(f"Failed to log payment failure to audit: {e}")
            
            return payment, None
        
        payment.external_id = transaction.get("transactionId")
        payment.payment_url = transaction.get("redirect")
        payment.payment_method = transaction.get("paymentMethod", "SBPQR")
        await session.commit()
        
        return payment, None
    
    @staticmethod
    async def handle_platega_callback(
        session: AsyncSession, transaction_id: str,
        status: str, payload: str,
        callback_amount: float | None = None,
        callback_payload: str | None = None
    ) -> tuple[bool, str]:
        """
        Обрабатывает callback от Platega.io.
        
        🔥 ИСПРАВЛЕНО (Часть 2):
        - Добавлена сверка amount и payload из callback с записью в БД
        """
        stmt = (
            select(Payment)
            .options(selectinload(Payment.user), selectinload(Payment.tariff))
            .where(Payment.external_id == transaction_id)
        )
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()
        
        if not payment:
            logger.warning(f"Platega callback: payment not found for {transaction_id}")
            return False, "not_found"
        
        logger.info(f"Platega callback: payment {payment.id} status={status}")
        
        if status == "CONFIRMED":
            # 🔥 ИСПРАВЛЕНО: Сверка amount из callback с суммой в БД
            if callback_amount is not None and payment.amount != int(callback_amount):
                logger.error(
                    f"Platega amount mismatch: DB={payment.amount}, "
                    f"callback={callback_amount}, payment_id={payment.id}, "
                    f"transaction={transaction_id}"
                )
                # Не выдаём подписку при несовпадении суммы
                await AuditService.log_action(
                    session, admin_id=0, action="PAYMENT_AMOUNT_MISMATCH",
                    target_type="Payment", target_id=payment.id,
                    details=(
                        f"DB amount={payment.amount}, callback amount={callback_amount}, "
                        f"transaction={transaction_id}. Subscription NOT granted."
                    ),
                )
                return False, "amount_mismatch"
            
            # 🔥 ИСПРАВЛЕНО: Сверка payload
            expected_payload = f"payment_{payment.id}"
            if callback_payload is not None and callback_payload != expected_payload:
                logger.error(
                    f"Platega payload mismatch: expected={expected_payload}, "
                    f"callback={callback_payload}, payment_id={payment.id}"
                )
                await AuditService.log_action(
                    session, admin_id=0, action="PAYMENT_PAYLOAD_MISMATCH",
                    target_type="Payment", target_id=payment.id,
                    details=(
                        f"Expected payload={expected_payload}, "
                        f"callback payload={callback_payload}, "
                        f"transaction={transaction_id}. Subscription NOT granted."
                    ),
                )
                return False, "payload_mismatch"
            
            success = await PaymentService.handle_successful_payment(session, payment.id)
            return success, "success" if success else "error"
        
        elif status == "CANCELED":
            if payment.status == "cancelled":
                logger.info(f"Platega callback: payment {payment.id} already cancelled")
                return True, "already_processed"
            
            payment.status = "cancelled"
            await session.commit()
            
            try:
                await AuditService.log_action(
                    session, admin_id=0, action="PAYMENT_CANCELLED",
                    target_type="Payment", target_id=payment.id,
                    details=f"Platega callback: transaction={transaction_id}, user={payment.user_id}"
                )
            except Exception as e:
                logger.error(f"Failed to log payment cancellation to audit: {e}")
            
            return True, "success"
        
        elif status == "CHARGEBACKED":
            if payment.status == "refunded":
                logger.info(f"Platega callback: payment {payment.id} already refunded")
                return True, "already_processed"
            
            payment.status = "refunded"
            
            user = payment.user
            if user:
                from database.repositories.profiles_repo import get_user_profiles
                from database.repositories.servers_repo import get_server_by_id
                from services.amnezia_client import AmneziaClient
                
                now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                user.subscription_end = now_utc
                user.current_tariff_id = None
                
                profiles = await get_user_profiles(session, user.id)
                if profiles:
                    for profile in profiles:
                        profile.is_active = False
                        try:
                            server = await get_server_by_id(session, profile.server_id)
                            if server and server.api_url:
                                api = AmneziaClient(server.api_url, server.api_key)
                                await api.update_client(
                                    client_id=profile.peer_id,
                                    status="disabled",
                                )
                        except Exception as e:
                            logger.warning(
                                "Chargeback: failed to disable profile %s on server %s: %s",
                                profile.id, profile.server_id, e,
                            )
                
                invalidate_user_cache(user.telegram_id)
                
                logger.warning(
                    "CHARGEBACK processed: user %s, payment %s. Access revoked.",
                    user.telegram_id, payment.id,
                )
            
            await session.commit()
            logger.warning(f"Chargeback for payment {payment.id}")
            
            try:
                await AuditService.log_action(
                    session, admin_id=0, action="PAYMENT_CHARGEBACK",
                    target_type="Payment", target_id=payment.id,
                    details=f"Platega chargeback: transaction={transaction_id}, user={payment.user_id}"
                )
            except Exception as e:
                logger.error(f"Failed to log chargeback to audit: {e}")
            
            await _send_chargeback_alert(payment, transaction_id)
            
            return True, "success"
        
        logger.warning(f"Unknown Platega status: {status}")
        return False, "error"
    
    @staticmethod
    async def check_platega_payment(session: AsyncSession, payment_id: int) -> bool:
        payment = await get_payment_by_id(session, payment_id)
        if not payment or not payment.external_id:
            return False
        
        if payment.status != "pending":
            return payment.status == "completed"
        
        client = PlategaClient()
        status_data = await client.check_status(payment.external_id)
        if not status_data:
            return False
        
        status = status_data.get("status")
        
        if status == "CONFIRMED":
            return await PaymentService.handle_successful_payment(session, payment.id)
        elif status == "CANCELED":
            payment.status = "cancelled"
            await session.commit()
            
            try:
                await AuditService.log_action(
                    session, admin_id=0, action="PAYMENT_CANCELLED",
                    target_type="Payment", target_id=payment.id,
                    details=f"check_platega_payment: status=CANCELED, user={payment.user_id}"
                )
            except Exception as e:
                logger.error(f"Failed to log payment cancellation to audit: {e}")
            
            return False
        
        return False


async def _send_chargeback_alert(payment: Payment, transaction_id: str) -> None:
    """Отправляет алерт админам о chargeback."""
    from services.workers.heartbeat import get_bot_ref
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from aiogram.types import LinkPreviewOptions
    
    bot = get_bot_ref()
    if bot is None:
        logger.error(
            f"Chargeback alert SKIPPED: bot_ref is None. "
            f"Payment {payment.id}, user {payment.user_id}, "
            f"transaction={transaction_id}"
        )
        return
    
    settings = get_settings()
    admin_ids = settings.ADMIN_IDS
    if not admin_ids:
        logger.warning("Chargeback alert skipped: ADMIN_IDS is empty")
        return
    
    user = payment.user
    tariff = payment.tariff
    username = f"@{user.username}" if user and user.username else "—"
    tariff_name = f"{tariff.duration_days} дн. / {tariff.device_limit} устр." if tariff else "—"
    
    builder = InlineKeyboardBuilder()
    builder.button(
        text="👤 Профиль пользователя",
        callback_data=f"admin_user_card:{user.telegram_id}" if user else "admin_menu"
    )
    builder.adjust(1)
    keyboard = builder.as_markup()
    
    alert_msg = (
        f"🚨 <b>CHARGEBACK (Возврат средств)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Платёж ID:</b> <code>{payment.id}</code>\n"
        f"👤 <b>Пользователь:</b> <code>{user.telegram_id if user else '—'}</code> ({username})\n"
        f"💰 <b>Сумма:</b> <b>{payment.amount} {payment.currency}</b>\n"
        f"📦 <b>Тариф:</b> {tariff_name}\n"
        f"🔗 <b>Transaction ID:</b> <code>{transaction_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Средства возвращены плательщику через банк.\n"
        f"<i>Проверьте профиль пользователя и примите решение о блокировке.</i>"
    )
    
    for admin_id in admin_ids:
        try:
            await bot.send_message(
                admin_id, alert_msg,
                reply_markup=keyboard, parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
            logger.info(f"Chargeback alert sent to admin {admin_id} for payment {payment.id}")
        except Exception as e:
            logger.error(
                f"Failed to send chargeback alert to admin {admin_id} "
                f"for payment {payment.id}: {e}"
                                                                   )

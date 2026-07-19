import logging
import asyncio
from sqlalchemy import select, update, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from bot.middlewares.user_context import invalidate_user_cache
from database.repositories.payments_repo import get_user_payments, get_payment_by_id
from database.models import Payment, VPNProfile
from services.subscription import SubscriptionService
from services.referral_service import ReferralService
from services.platega_client import PlategaClient
from services.audit_service import AuditService
from config.settings import get_settings
from utils.datetime_helpers import now_utc
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_alerted_paid_after_cancel: set[int] = set()
_notified_paid_after_cancel: set[int] = set()

_redis_client: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_timeout=5.0,
        )
    return _redis_client


class PaymentService:

    @staticmethod
    async def handle_successful_payment(
        session: AsyncSession, payment_id: int
    ) -> tuple[bool, str]:
        redis = await _get_redis()
        lock_key = f"lock:payment_bonus:user"

        payment_obj = await session.get(Payment, payment_id)
        if not payment_obj:
            return False, "not_found"

        user_lock_key = f"lock:payment_bonus:{payment_obj.user_id}"
        redis_lock = redis.lock(user_lock_key, timeout=30, blocking_timeout=15)

        try:
            acquired = await redis_lock.acquire()
            if not acquired:
                logger.warning(
                    f"Payment {payment_id}: failed to acquire Redis bonus lock"
                )
                pass

            try:
                async with session.begin_nested() as savepoint:
                    stmt = (
                        update(Payment)
                        .where(Payment.id == payment_id, Payment.status == 'pending')
                        .values(
                            status='completed',
                            paid_at=now_utc(),
                        )
                    )
                    result = await session.execute(stmt)

                    if result.rowcount == 0:
                        current_payment = await session.get(Payment, payment_id)
                        if current_payment and current_payment.status == 'completed':
                            await savepoint.commit()
                            logger.info(
                                f"Payment {payment_id} already completed (idempotent)"
                            )
                            return True, "already_processed"
                        elif current_payment and current_payment.status == 'cancelled':
                            await savepoint.commit()
                            await _alert_paid_after_cancel(session, payment_id)
                            await _notify_client_paid_after_cancel(session, payment_id)
                            return True, "paid_after_cancel"
                        else:
                            await savepoint.rollback()
                            return False, "error"

                    result = await session.execute(
                        select(Payment)
                        .options(
                            selectinload(Payment.user),
                            selectinload(Payment.tariff),
                        )
                        .where(Payment.id == payment_id)
                    )
                    payment = result.scalar_one()
                    tariff = payment.tariff
                    user = payment.user

                    if not tariff or not user:
                        logger.error(
                            f"Payment {payment_id}: missing tariff or user"
                        )
                        await savepoint.rollback()
                        return False, "error"

                    new_device_limit = getattr(
                        tariff, 'device_limit', user.device_limit
                    )

                    try:
                        await SubscriptionService.extend_subscription(
                            session, user.telegram_id, tariff.duration_days,
                            new_device_limit=new_device_limit,
                            new_tariff_id=tariff.id,
                        )
                    except ValueError as e:
                        logger.error(
                            f"Failed to extend subscription for payment "
                            f"{payment_id}: {e}",
                            exc_info=True,
                        )
                        await savepoint.rollback()
                        return False, "device_limit_exceeded"
                    except Exception as e:
                        logger.error(
                            f"Failed to extend subscription for payment "
                            f"{payment_id}: {e}",
                            exc_info=True,
                        )
                        await savepoint.rollback()
                        return False, "error"

                    payments = await get_user_payments(session, user.id)
                    successful_payments = [
                        p for p in payments if p.status == 'completed'
                    ]
                    is_first_payment = len(successful_payments) == 1

                    if user.referred_by:
                        try:
                            await ReferralService.process_bonus(
                                session,
                                user.telegram_id,
                                user.referred_by,
                                is_first_payment=is_first_payment,
                                duration_days=tariff.duration_days,
                            )
                        except Exception as e:
                            logger.warning(
                                f"Referral bonus failed for payment "
                                f"{payment_id}: {e}"
                            )

                    user.last_payment_at = now_utc()
                    await savepoint.commit()

                invalidate_user_cache(user.telegram_id)

                try:
                    await AuditService.log_action(
                        session, admin_id=0, action="PAYMENT_SUCCESS",
                        target_type="Payment", target_id=payment_id,
                        details=(
                            f"user={user.telegram_id}, "
                            f"amount={payment.amount} {payment.currency}"
                        ),
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to log payment success to audit: {e}"
                    )

                logger.info(
                    f"Payment {payment_id} processed successfully "
                    f"for user {user.telegram_id}"
                )
                return True, "success"

            except Exception as e:
                await session.rollback()
                logger.error(
                    f"Failed to process payment {payment_id}: {e}",
                    exc_info=True,
                )
                return False, "error"

        finally:
            try:
                await redis_lock.release()
            except Exception:
                pass

    @staticmethod
    async def force_grant_payment(
        session: AsyncSession, payment_id: int, admin_id: int
    ) -> tuple[bool, str]:
        try:
            async with session.begin_nested() as savepoint:
                stmt = (
                    update(Payment)
                    .where(
                        Payment.id == payment_id,
                        Payment.status != 'completed'
                    )
                    .values(status='completed', paid_at=now_utc())
                )
                result = await session.execute(stmt)

                if result.rowcount == 0:
                    await savepoint.rollback()
                    current = await session.get(Payment, payment_id)
                    if current and current.status == 'completed':
                        return False, "Платёж уже выдан"
                    return False, "Платёж не найден или не может быть выдан"

                result = await session.execute(
                    select(Payment)
                    .options(
                        selectinload(Payment.user),
                        selectinload(Payment.tariff),
                    )
                    .where(Payment.id == payment_id)
                )
                payment = result.scalar_one_or_none()

                if not payment:
                    await savepoint.rollback()
                    return False, "Платёж не найден"

                tariff = payment.tariff
                user = payment.user

                if not tariff or not user:
                    await savepoint.rollback()
                    return False, "Нет тарифа или пользователя"

                new_device_limit = getattr(
                    tariff, 'device_limit', user.device_limit
                )

                try:
                    await SubscriptionService.extend_subscription(
                        session, user.telegram_id, tariff.duration_days,
                        new_device_limit=new_device_limit,
                        new_tariff_id=tariff.id,
                    )
                except Exception as e:
                    logger.error(
                        f"force_grant: extend failed for {payment_id}: {e}",
                        exc_info=True,
                    )
                    await savepoint.rollback()
                    return False, f"Ошибка продления: {e}"

                payments = await get_user_payments(session, user.id)
                successful_payments = [
                    p for p in payments if p.status == 'completed'
                ]
                is_first_payment = len(successful_payments) == 1

                if user.referred_by:
                    try:
                        await ReferralService.process_bonus(
                            session,
                            user.telegram_id,
                            user.referred_by,
                            is_first_payment=is_first_payment,
                            duration_days=tariff.duration_days,
                        )
                    except Exception as e:
                        logger.warning(
                            f"Referral bonus failed for manual grant "
                            f"{payment_id}: {e}"
                        )

                await savepoint.commit()

            invalidate_user_cache(user.telegram_id)

            try:
                await AuditService.log_action(
                    session, admin_id=admin_id, action="MANUAL_GRANT",
                    target_type="Payment", target_id=payment_id,
                    details=(
                        f"Admin {admin_id} manually granted cancelled "
                        f"payment {payment_id} for user {user.telegram_id}"
                    ),
                )
            except Exception as e:
                logger.error(f"force_grant: audit failed: {e}")

            return True, "ok"

        except Exception as e:
            await session.rollback()
            logger.error(
                f"force_grant_payment failed: {e}", exc_info=True
            )
            return False, f"Ошибка БД: {e}"

    @staticmethod
    async def create_platega_payment(
        session: AsyncSession, user_id: int, tariff_id: int,
        amount: float, telegram_id: int, bot_username: str,
    ) -> tuple:
        from database.repositories.payments_repo import create_payment

        settings = get_settings()

        payment = await create_payment(
            session=session, user_id=user_id, tariff_id=tariff_id,
            amount=int(amount), currency="RUB",
        )

        description = (
            f"Оплата подписки. TgId:{telegram_id} UserId:{user_id}"
        )

        clean_username = bot_username.lstrip("@")
        return_url = settings.PLATEGA_RETURN_URL.format(
            bot_username=clean_username
        )
        failed_url = settings.PLATEGA_FAILED_URL.format(
            bot_username=clean_username
        )
        payload = f"payment_{payment.id}"

        client = PlategaClient()
        transaction = await client.create_transaction(
            amount=amount, currency="RUB", description=description,
            return_url=return_url, failed_url=failed_url, payload=payload,
        )

        if not transaction:
            try:
                await session.execute(
                    delete(Payment).where(Payment.id == payment.id)
                )
                await session.flush()
            except Exception as delete_error:
                logger.error(
                    f"Failed to delete phantom payment {payment.id}: {delete_error}"
                )
                payment.status = "failed"
                try:
                    await session.flush()
                except Exception:
                    pass

            try:
                await AuditService.log_action(
                    session, admin_id=0, action="PAYMENT_FAILED",
                    target_type="Payment", target_id=payment.id,
                    details=(
                        f"user={user_id}, amount={amount} RUB, "
                        f"Platega create_transaction failed"
                    ),
                )
            except Exception as e:
                logger.error(
                    f"Failed to log payment failure to audit: {e}"
                )
            return None, None

        payment.external_id = transaction.get("transactionId")
        payment.payment_url = transaction.get("redirect")
        payment.payment_method = transaction.get("paymentMethod", "SBPQR")

        return payment, None

    @staticmethod
    async def handle_platega_callback(
        session: AsyncSession, transaction_id: str,
        status: str, payload: str,
        callback_amount: float | None = None,
        callback_payload: str | None = None,
    ) -> tuple[bool, str]:
        stmt = (
            select(Payment)
            .options(
                selectinload(Payment.user),
                selectinload(Payment.tariff),
            )
            .where(Payment.external_id == transaction_id)
        )
        result = await session.execute(stmt)
        payment = result.scalar_one_or_none()

        if not payment:
            logger.warning(
                f"Platega callback: payment not found for {transaction_id}"
            )
            return False, "not_found"

        logger.info(
            f"Platega callback: payment {payment.id} status={status}"
        )

        if status == "CONFIRMED":
            if payment.status == "completed":
                logger.info(
                    f"Platega callback: payment {payment.id} already completed, "
                    f"idempotent success for transaction={transaction_id}"
                )
                return True, "already_processed"

            # ── Верификация суммы ──────────────────────────────
            # Если amount не прислан в callback — запрашиваем
            # через Platega API. Если и там нет — отклоняем.
            if callback_amount is None:
                client = PlategaClient()
                status_data = await client.check_status(transaction_id)
                if status_data and status_data.get("amount") is not None:
                    callback_amount = float(status_data["amount"])
                    logger.info(
                        f"Platega callback: amount recovered via API "
                        f"check_status: {callback_amount} "
                        f"for transaction={transaction_id}"
                    )
                else:
                    logger.error(
                        f"Platega callback: amount not provided in callback "
                        f"and API verification failed for "
                        f"transaction={transaction_id}. Rejecting."
                    )
                    try:
                        await AuditService.log_action(
                            session, admin_id=0,
                            action="PAYMENT_AMOUNT_MISSING",
                            target_type="Payment", target_id=payment.id,
                            details=(
                                f"Amount not in callback and API "
                                f"check_status failed. "
                                f"transaction={transaction_id}. "
                                f"Subscription NOT granted."
                            ),
                        )
                    except Exception:
                        pass
                    return False, "amount_mismatch"

            if payment.amount != int(callback_amount):
                logger.error(
                    f"Platega amount mismatch: DB={payment.amount}, "
                    f"callback={callback_amount}, payment_id={payment.id}, "
                    f"transaction={transaction_id}"
                )
                try:
                    await AuditService.log_action(
                        session, admin_id=0,
                        action="PAYMENT_AMOUNT_MISMATCH",
                        target_type="Payment", target_id=payment.id,
                        details=(
                            f"DB amount={payment.amount}, "
                            f"callback amount={callback_amount}, "
                            f"transaction={transaction_id}. "
                            f"Subscription NOT granted."
                        ),
                    )
                except Exception:
                    pass
                return False, "amount_mismatch"

            expected_payload = f"payment_{payment.id}"
            if (
                callback_payload is not None
                and callback_payload != expected_payload
            ):
                logger.error(
                    f"Platega payload mismatch: "
                    f"expected={expected_payload}, "
                    f"callback={callback_payload}, "
                    f"payment_id={payment.id}"
                )
                try:
                    await AuditService.log_action(
                        session, admin_id=0,
                        action="PAYMENT_PAYLOAD_MISMATCH",
                        target_type="Payment", target_id=payment.id,
                        details=(
                            f"Expected payload={expected_payload}, "
                            f"callback payload={callback_payload}, "
                            f"transaction={transaction_id}. "
                            f"Subscription NOT granted."
                        ),
                    )
                except Exception:
                    pass
                return False, "payload_mismatch"

            success, result_code = await PaymentService.handle_successful_payment(
                session, payment.id
            )
            return success, result_code

        elif status == "CANCELED":
            if payment.status == "cancelled":
                logger.info(
                    f"Platega callback: payment {payment.id} "
                    f"already cancelled"
                )
                return True, "already_processed"

            payment.status = "cancelled"
            try:
                await AuditService.log_action(
                    session, admin_id=0, action="PAYMENT_CANCELLED",
                    target_type="Payment", target_id=payment.id,
                    details=(
                        f"Platega callback: transaction={transaction_id}, "
                        f"user={payment.user_id}"
                    ),
                )
            except Exception as e:
                logger.error(
                    f"Failed to log payment cancellation to audit: {e}"
                )
            return True, "success"

        elif status == "CHARGEBACKED":
            if payment.status == "refunded":
                logger.info(
                    f"Platega callback: payment {payment.id} "
                    f"already refunded"
                )
                return True, "already_processed"

            payment.status = "refunded"
            user = payment.user

            if user:
                from database.repositories.profiles_repo import get_user_profiles
                from database.repositories.servers_repo import get_server_by_id

                current_time = now_utc()
                user.subscription_end = current_time
                user.current_tariff_id = None

                if user.referred_by:
                    try:
                        from database.repositories.users_repo import get_user_by_telegram_id
                        referrer = await get_user_by_telegram_id(session, user.referred_by)
                        if referrer:
                            payments = await get_user_payments(session, user.id)
                            successful_payments = [
                                p for p in payments if p.status == 'completed'
                            ]
                            is_first_payment = len(successful_payments) <= 1
                            tariff = payment.tariff

                            if tariff and tariff.duration_days >= 30:
                                if is_first_payment:
                                    bonus_referral = 5
                                    bonus_referrer = 3

                                    if user.referral_days and user.referral_days >= bonus_referral:
                                        user.referral_days -= bonus_referral

                                    if referrer.referral_days and referrer.referral_days >= bonus_referrer:
                                        referrer.referral_days -= bonus_referrer

                                    if referrer.subscription_end and referrer.subscription_end > current_time:
                                        from datetime import timedelta
                                        referrer.subscription_end = referrer.subscription_end - timedelta(days=bonus_referrer)

                                    logger.info(
                                        f"Chargeback: rolled back referral bonuses "
                                        f"for user {user.telegram_id} and referrer {referrer.telegram_id}"
                                    )
                    except Exception as e:
                        logger.error(
                            f"Chargeback: failed to rollback referral bonuses: {e}",
                            exc_info=True,
                        )

                profiles = await get_user_profiles(session, user.id)
                if profiles:
                    profile_ids = [p.id for p in profiles]
                    await session.execute(
                        update(VPNProfile)
                        .where(VPNProfile.id.in_(profile_ids))
                        .values(is_active=False)
                    )
                    await session.flush()

                    tasks_info = []
                    for profile in profiles:
                        try:
                            server = await get_server_by_id(
                                session, profile.server_id
                            )
                            if server and server.api_url:
                                tasks_info.append({
                                    'api_url': server.api_url,
                                    'api_key': server.api_key,
                                    'peer_id': profile.peer_id,
                                    'profile_id': profile.id,
                                })
                        except Exception as e:
                            logger.warning(
                                "Chargeback: failed to get server for "
                                "profile %s: %s",
                                profile.id, e,
                            )

                    if tasks_info:
                        asyncio.create_task(
                            _disable_peers_background(
                                tasks_info,
                                user.telegram_id,
                                payment.id,
                            )
                        )

                invalidate_user_cache(user.telegram_id)

                logger.warning(
                    "CHARGEBACK processed: user %s, payment %s. "
                    "Access revoked (API disable in background).",
                    user.telegram_id if user else "unknown",
                    payment.id,
                )

            logger.warning(f"Chargeback for payment {payment.id}")

            try:
                await AuditService.log_action(
                    session, admin_id=0, action="PAYMENT_CHARGEBACK",
                    target_type="Payment", target_id=payment.id,
                    details=(
                        f"Platega chargeback: "
                        f"transaction={transaction_id}, "
                        f"user={payment.user_id}"
                    ),
                )
            except Exception as e:
                logger.error(
                    f"Failed to log chargeback to audit: {e}"
                )

            await _send_chargeback_alert(payment, transaction_id)
            return True, "success"

        logger.warning(f"Unknown Platega status: {status}")
        return False, "error"

    @staticmethod
    async def check_platega_payment(
        session: AsyncSession, payment_id: int
    ) -> tuple[bool, str]:
        payment = await get_payment_by_id(session, payment_id)
        if not payment or not payment.external_id:
            return False, "not_found"

        if payment.status == "completed":
            return True, "success"

        if payment.status not in ("pending", "cancelled"):
            return False, "invalid_status"

        client = PlategaClient()
        status_data = await client.check_status(payment.external_id)

        if not status_data:
            return False, "api_error"

        status = status_data.get("status")

        if status == "CONFIRMED":
            success, result_code = await PaymentService.handle_successful_payment(
                session, payment.id
            )
            return success, result_code
        elif status == "CANCELED":
            if payment.status != "cancelled":
                payment.status = "cancelled"
                try:
                    await AuditService.log_action(
                        session, admin_id=0, action="PAYMENT_CANCELLED",
                        target_type="Payment", target_id=payment.id,
                        details=(
                            f"check_platega_payment: status=CANCELED, "
                            f"user={payment.user_id}"
                        ),
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to log payment cancellation to audit: {e}"
                    )
            return False, "cancelled"

        return False, "pending"


async def _disable_peers_background(
    tasks_info: list, telegram_id: int, payment_id: int
):
    from services.amnezia_client import AmneziaClient

    sem = asyncio.Semaphore(20)

    async def _disable_peer(info):
        async with sem:
            api = AmneziaClient(info['api_url'], info['api_key'])
            try:
                return await api.update_client(
                    client_id=info['peer_id'],
                    status="disabled",
                )
            except Exception as e:
                logger.warning(
                    "Chargeback background: failed to disable "
                    "profile %s: %s",
                    info['profile_id'], e,
                )
                return False

    results = await asyncio.gather(
        *[_disable_peer(info) for info in tasks_info],
        return_exceptions=True,
    )

    api_errors = [
        r for r in results
        if isinstance(r, Exception) or r is False
    ]

    if api_errors:
        logger.warning(
            "Chargeback background: %d/%d API calls failed for user %s",
            len(api_errors), len(tasks_info), telegram_id,
        )
    else:
        logger.info(
            "Chargeback background: all %d peers disabled for user %s "
            "(payment %s)",
            len(tasks_info), telegram_id, payment_id,
        )


async def _alert_paid_after_cancel(
    session, payment_id: int
) -> None:
    global _alerted_paid_after_cancel

    if payment_id in _alerted_paid_after_cancel:
        return
    _alerted_paid_after_cancel.add(payment_id)

    from services.workers.heartbeat import get_bot_ref
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from utils.tariff_names import get_tariff_display_name

    bot = get_bot_ref()
    if bot is None:
        logger.error(
            f"Paid-after-cancel alert SKIPPED: bot_ref is None. "
            f"Payment {payment_id}"
        )
        return

    try:
        result = await session.execute(
            select(Payment)
            .options(selectinload(Payment.user), selectinload(Payment.tariff))
            .where(Payment.id == payment_id)
        )
        payment = result.scalar_one_or_none()
    except Exception:
        payment = None

    if not payment:
        return

    settings = get_settings()
    admin_ids = settings.ADMIN_IDS
    if not admin_ids:
        return

    user = payment.user
    tariff = payment.tariff

    username = (
        f"@{user.username}" if user and user.username else "—"
    )

    tariff_name = "—"
    if tariff:
        tariff_name = get_tariff_display_name(
            getattr(tariff, 'device_limit', 2)
        )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Выдать подписку",
        callback_data=f"admin_manual_grant:{payment.id}"
    )
    if payment.payment_method == "SBPQR" and payment.external_id:
        builder.button(
            text="💸 Вернуть средства (Platega)",
            url=f"https://app.platega.io/transactions/{payment.external_id}"
        )
    builder.button(
        text="👤 Профиль клиента",
        callback_data=(
            f"admin_user_card:{user.telegram_id}" if user else "admin_menu"
        ),
    )
    builder.adjust(1, 1, 1)
    keyboard = builder.as_markup()

    alert_msg = (
        f"⚠️ <b>Оплата после отмены!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Платёж ID:</b> <code>{payment.id}</code>\n"
        f"👤 <b>Клиент:</b> "
        f"<code>{user.telegram_id if user else '—'}</code> ({username})\n"
        f"💎 <b>Тариф:</b> {tariff_name}\n"
        f"💰 <b>Сумма:</b> {payment.amount} {payment.currency}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Деньги поступили, но платёж был ранее отменён клиентом.\n"
        f"Клиент уведомлён автоматически.\n"
        f"Выберите действие:</i>"
    )

    for admin_id in admin_ids:
        try:
            await bot.send_message(
                admin_id, alert_msg,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            logger.info(
                f"Paid-after-cancel alert sent to admin {admin_id} "
                f"for payment {payment.id}"
            )
        except Exception as e:
            logger.error(
                f"Failed to send paid-after-cancel alert to admin "
                f"{admin_id}: {e}"
            )

    try:
        await AuditService.log_action(
            session, admin_id=0, action="PAID_AFTER_CANCEL",
            target_type="Payment", target_id=payment_id,
            details=(
                f"user={user.telegram_id if user else '—'}, "
                f"amount={payment.amount} {payment.currency}"
            ),
        )
    except Exception as e:
        logger.error(f"Failed to log PAID_AFTER_CANCEL: {e}")


async def _notify_client_paid_after_cancel(
    session, payment_id: int
) -> None:
    global _notified_paid_after_cancel

    if payment_id in _notified_paid_after_cancel:
        return
    _notified_paid_after_cancel.add(payment_id)

    from services.workers.heartbeat import get_bot_ref
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from config.settings import get_settings
    from utils.tariff_names import get_tariff_display_name

    bot = get_bot_ref()
    if bot is None:
        logger.error(
            f"Client notification SKIPPED: bot_ref is None. "
            f"Payment {payment_id}"
        )
        return

    try:
        result = await session.execute(
            select(Payment)
            .options(selectinload(Payment.user), selectinload(Payment.tariff))
            .where(Payment.id == payment_id)
        )
        payment = result.scalar_one_or_none()
    except Exception:
        payment = None

    if not payment or not payment.user:
        return

    user = payment.user
    tariff = payment.tariff

    if user.is_banned:
        logger.info(
            f"Client notification skipped: user {user.telegram_id} is banned"
        )
        return

    settings = get_settings()
    support_username = settings.SUPPORT_USERNAME.lstrip("@")

    tariff_name = "—"
    if tariff:
        tariff_name = get_tariff_display_name(
            getattr(tariff, 'device_limit', 2)
        )

    msg = (
        f"💳 <b>Мы получили вашу оплату</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Сумма:</b> {payment.amount} {payment.currency}\n"
        f"💎 <b>Тариф:</b> {tariff_name}\n"
        f"🆔 <b>Платёж:</b> <code>{payment.id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Ранее в боте была нажата кнопка «Отменить», "
        f"поэтому доступ не активировался автоматически.\n"
        f"Напишите нам — решим за 2 минуты."
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="💬 Написать в поддержку",
        url=f"https://t.me/{support_username}"
    )
    builder.adjust(1)

    try:
        await bot.send_message(
            user.telegram_id, msg,
            reply_markup=builder.as_markup(),
            parse_mode="HTML"
        )
        logger.info(
            f"Paid-after-cancel notification sent to user {user.telegram_id} "
            f"for payment {payment.id}"
        )
    except TelegramForbiddenError:
        logger.info(
            f"Paid-after-cancel notification: user {user.telegram_id} "
            f"blocked the bot"
        )
        try:
            from database.repositories.users_repo import mark_user_bot_blocked
            await mark_user_bot_blocked(session, user.telegram_id)
        except Exception:
            pass
    except Exception as e:
        logger.error(
            f"Failed to send paid-after-cancel notification to "
            f"user {user.telegram_id}: {e}"
        )

    try:
        await AuditService.log_action(
            session, admin_id=0, action="CLIENT_NOTIFIED_PAID_AFTER_CANCEL",
            target_type="Payment", target_id=payment_id,
            details=f"user={user.telegram_id}, support=@{support_username}",
        )
    except Exception as e:
        logger.error(f"Failed to log CLIENT_NOTIFIED: {e}")


async def _send_chargeback_alert(
    payment: Payment, transaction_id: str
) -> None:
    from services.workers.heartbeat import get_bot_ref
    from aiogram.utils.keyboard import InlineKeyboardBuilder

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

    username = (
        f"@{user.username}" if user and user.username else "—"
    )

    tariff_name = (
        f"{tariff.duration_days} дн. / {tariff.device_limit} устр."
        if tariff else "—"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text="👤 Профиль пользователя",
        callback_data=(
            f"admin_user_card:{user.telegram_id}" if user else "admin_menu"
        ),
    )
    builder.adjust(1)
    keyboard = builder.as_markup()

    alert_msg = (
        f"🚨 <b>CHARGEBACK (Возврат средств)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>Платёж ID:</b> <code>{payment.id}</code>\n"
        f"👤 <b>Пользователь:</b> "
        f"<code>{user.telegram_id if user else '—'}</code> ({username})\n"
        f"💎 <b>Тариф:</b> {tariff_name}\n"
        f"💰 <b>Сумма:</b> <b>{payment.amount} {payment.currency}</b>\n"
        f"🔗 <b>Transaction:</b> <code>{transaction_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Доступ отозван. Все устройства отключаются в фоне.\n"
        f"Реферальные бонусы откатаны.</i>"
    )

    for admin_id in admin_ids:
        try:
            await bot.send_message(
                admin_id, alert_msg,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(
                f"Failed to send chargeback alert to admin {admin_id}: {e}"
            )
import asyncio
import logging
import html
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from config.settings import get_settings
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from sqlalchemy import select, or_, update
from database.connection import get_session
from database.repositories.servers_repo import get_active_servers
from database.repositories.tariffs_repo import get_active_tariffs
from services.amnezia_client import AmneziaClient
from database.models import User, VPNProfile, Server, Payment
from database.repositories.users_repo import mark_user_bot_blocked
from bot.keyboards import get_payment_method_keyboard

logger = logging.getLogger("BackgroundWorker")


async def start_background_worker(bot: Bot):
    # 🔥 ИСПРАВЛЕНО: subscription_expiry_checker_loop удалён.
    # API сам отключает пиров по expiresAt, боту не нужно спамить PATCH запросами.
    asyncio.create_task(traffic_sync_loop())
    asyncio.create_task(cleanup_dangling_peers_loop())
    asyncio.create_task(stale_payments_checker_loop(bot))
    asyncio.create_task(subscription_notifications_loop(bot))
    logger.info("Фоновые воркеры успешно запущены.")


async def subscription_notifications_loop(bot: Bot):
    """Умные уведомления: за 3 дня, 1 день и 2 часа до конца подписки."""
    while True:
        try:
            await asyncio.sleep(1800)  # каждые 30 минут
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            session = await get_session()
            try:
                # 🔥 ИСПРАВЛЕНО: исключаем пользователей, заблокировавших бота
                stmt = select(User).where(
                    User.subscription_end > now,
                    User.subscription_end <= now + timedelta(days=3),
                    User.is_banned == False,
                    User.is_bot_blocked == False,
                    or_(
                        User.notified_3d == False,
                        User.notified_1d == False,
                        User.notified_2h == False
                    )
                )
                users = (await session.execute(stmt)).scalars().all()
                if not users:
                    continue

                tariffs = await get_active_tariffs(session)
                tariff_id = tariffs[0].id if tariffs else None

                for user in users:
                    time_left = user.subscription_end - now
                    msg = None
                    if time_left <= timedelta(hours=2) and not user.notified_2h:
                        msg = (
                            "🔴 <b>Ваш доступ отключится через 2 часа!</b>\n"
                            "Не оставайтесь без защищённой сети.\n"
                            "Нажмите кнопку ниже, чтобы продлить подписку в один клик."
                        )
                        user.notified_2h = True
                    elif time_left <= timedelta(days=1) and not user.notified_1d:
                        msg = (
                            "🟡 <b>Ваш доступ отключится через 1 день.</b>\n"
                            "Рекомендуем продлить подписку заранее, чтобы не потерять связь.\n"
                            "Нажмите кнопку ниже для быстрого продления."
                        )
                        user.notified_1d = True
                    elif time_left <= timedelta(days=3) and not user.notified_3d:
                        msg = (
                            "🟢 <b>Ваш доступ отключится через 3 дня.</b>\n"
                            "Успейте продлить подписку и продолжайте пользоваться сервисом без перебоев.\n"
                            "Нажмите кнопку ниже для оплаты."
                        )
                        user.notified_3d = True

                    if msg:
                        try:
                            kb = get_payment_method_keyboard(tariff_id) if tariff_id else None
                            await bot.send_message(
                                user.telegram_id,
                                msg,
                                reply_markup=kb,
                                parse_mode="HTML"
                            )
                            await session.commit()
                            logger.info(f"Sent notification to user {user.telegram_id}")
                        except TelegramForbiddenError:
                            # 🔥 ИСПРАВЛЕНО: пользователь заблокировал бота
                            logger.info(f"User {user.telegram_id} blocked the bot")
                            try:
                                await mark_user_bot_blocked(session, user.telegram_id)
                            except Exception as e:
                                logger.error(f"Failed to mark user {user.telegram_id} as bot_blocked: {e}")
                            await session.rollback()
                        except Exception as e:
                            logger.warning(f"Failed to send notification to {user.telegram_id}: {e}")
                            await session.rollback()
            finally:
                await session.close()
        except Exception as e:
            logger.error(f"Ошибка в цикле уведомлений: {e}", exc_info=True)
            await asyncio.sleep(60)


async def traffic_sync_loop():
    """🔥 ИСПРАВЛЕНО: теперь синхронизирует is_active статус из API в БД"""
    while True:
        try:
            logger.info("Запуск синхронизации трафика и статусов...")
            session = await get_session()
            try:
                stmt = (
                    select(
                        VPNProfile.id, VPNProfile.peer_id, VPNProfile.server_id,
                        VPNProfile.traffic_down, VPNProfile.traffic_up, VPNProfile.last_connected,
                        VPNProfile.is_active,
                        Server.api_url, Server.api_key, Server.name
                    )
                    .join(Server, VPNProfile.server_id == Server.id)
                    .where(Server.is_active == True)
                )
                result = await session.execute(stmt)
                rows = result.all()

                by_server = defaultdict(list)
                servers_map = {}
                for row in rows:
                    p_id, peer_id, s_id, t_down, t_up, last_conn, is_active, api_url, api_key, s_name = row
                    by_server[s_id].append({
                        'id': p_id, 'peer_id': peer_id,
                        'traffic_down': t_down, 'traffic_up': t_up,
                        'last_connected': last_conn,
                        'is_active': is_active
                    })
                    servers_map[s_id] = {'api_url': api_url, 'api_key': api_key, 'name': s_name}
            finally:
                await session.close()

            if not servers_map:
                await asyncio.sleep(900)
                continue

            async def _fetch_server_traffic(server_id, server_info):
                client = AmneziaClient(server_info['api_url'], server_info['api_key'])
                try:
                    api_clients_list = await client.get_all_clients()
                    if api_clients_list is None:
                        return server_id, None
                    return server_id, {c["id"]: c for c in api_clients_list}
                except Exception as e:
                    logger.error(f"Ошибка трафика с {server_info['name']}: {e}")
                    return server_id, None

            tasks = [_fetch_server_traffic(s_id, servers_map[s_id]) for s_id in servers_map]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            api_data_by_server = {
                r[0]: r[1] for r in results
                if not isinstance(r, Exception) and r is not None and r[1] is not None
            }

            updates_data = {}
            for server_id, api_clients in api_data_by_server.items():
                for p_dict in by_server[server_id]:
                    if p_dict['peer_id'] in api_clients:
                        api_data = api_clients[p_dict['peer_id']]
                        stats = api_data.get("traffics", {})
                        t_down = stats.get("totalDownload", p_dict['traffic_down'])
                        t_up = stats.get("totalUpload", p_dict['traffic_up'])
                        last_conn_raw = api_data.get("updatedAt")
                        last_connected = p_dict['last_connected']
                        if last_conn_raw:
                            try:
                                last_connected = datetime.fromtimestamp(
                                    int(float(str(last_conn_raw))),
                                    tz=timezone.utc
                                ).replace(tzinfo=None)
                            except (ValueError, TypeError):
                                pass

                        # 🔥 НОВОЕ: синхронизация is_active статуса из API
                        api_status = api_data.get("status", "active")
                        api_is_active = (api_status == "active")
                        db_is_active = p_dict['is_active']

                        if (p_dict['traffic_down'] != t_down or
                            p_dict['traffic_up'] != t_up or
                            p_dict['last_connected'] != last_connected or
                            db_is_active != api_is_active):
                            updates_data[p_dict['id']] = {
                                'traffic_down': t_down,
                                'traffic_up': t_up,
                                'last_connected': last_connected,
                                'is_active': api_is_active
                            }

            if updates_data:
                session = await get_session()
                try:
                    for p_id, data in updates_data.items():
                        await session.execute(
                            update(VPNProfile).where(VPNProfile.id == p_id).values(
                                traffic_down=data['traffic_down'],
                                traffic_up=data['traffic_up'],
                                last_connected=data['last_connected'],
                                is_active=data['is_active']
                            )
                        )
                    await session.commit()
                    logger.info(f"Трафик и статусы синхронизированы для {len(updates_data)} устройств.")
                finally:
                    await session.close()
        except Exception as e:
            logger.error(f"Ошибка в цикле трафика: {e}", exc_info=True)
        await asyncio.sleep(900)


async def cleanup_dangling_peers_loop():
    await asyncio.sleep(600)
    while True:
        try:
            logger.info("Запуск очистки 'призраков'...")
            session = await get_session()
            try:
                servers = await get_active_servers(session)
                result = await session.execute(select(VPNProfile.id, VPNProfile.peer_id))
                db_peer_ids = {row[1] for row in result.all()}
                servers_data = [
                    {'api_url': s.api_url, 'api_key': s.api_key, 'name': s.name}
                    for s in servers
                ]
            finally:
                await session.close()

            if not db_peer_ids or all(p is None for p in db_peer_ids):
                if servers_data:
                    logger.critical("🛑 DB returned empty/invalid peer IDs. Aborting cleanup!")
                await asyncio.sleep(86400)
                continue

            async def _clean_server_dangling_peers(server_info, db_peer_ids_set):
                client = AmneziaClient(server_info['api_url'], server_info['api_key'])
                try:
                    api_clients_list = await client.get_all_clients()
                    if api_clients_list is None:
                        logger.warning(
                            f"Skipping ghost cleanup on {server_info['name']}: API failed"
                        )
                        return
                    for api_client in api_clients_list:
                        client_id = api_client.get("id")
                        client_name = api_client.get(
                            "clientName", api_client.get("name", "")
                        )
                        if client_name.startswith("tg_") and client_id not in db_peer_ids_set:
                            session2 = await get_session()
                            try:
                                fresh_result = await session2.execute(
                                    select(VPNProfile.id, VPNProfile.created_at)
                                    .where(VPNProfile.peer_id == client_id)
                                )
                                fresh_row = fresh_result.first()
                            finally:
                                await session2.close()
                            if fresh_row:
                                logger.info(f"Race Condition caught: {client_name}")
                                continue
                            logger.warning(
                                f"Обнаружен 'призрак' на {server_info['name']}: {client_name}. Удаляю..."
                            )
                            await client.delete_user(client_id=client_id)
                except Exception as e:
                    logger.error(
                        f"Ошибка очистки призраков на {server_info['name']}: {e}"
                    )

            tasks = [
                _clean_server_dangling_peers(s, db_peer_ids)
                for s in servers_data
            ]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Ошибка в цикле призраков: {e}", exc_info=True)
        await asyncio.sleep(86400)


async def stale_payments_checker_loop(bot: Bot):
    settings = get_settings()
    while True:
        try:
            await asyncio.sleep(3600)
            logger.info("Проверка зависших платежей...")
            session = await get_session()
            try:
                threshold = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
                stmt = (
                    select(Payment)
                    .where(Payment.status == 'pending', Payment.created_at < threshold)
                    .order_by(Payment.created_at.desc())
                )
                result = await session.execute(stmt)
                stale_payments = result.scalars().all()
                if not stale_payments:
                    continue
                msg = f"⚠️ <b>{len(stale_payments)} зависших платежей (pending > 1ч)</b>\n"
                msg += "Возможно, Stars списались, но БД не обновилась.\n\n"
                for p in stale_payments[:10]:
                    msg += (
                        f"ID: <code>{p.id}</code> · "
                        f"User: <code>{p.user_id}</code> · "
                        f"{p.amount} {p.currency}\n"
                    )
                if len(stale_payments) > 10:
                    msg += f"\n<i>... и ещё {len(stale_payments) - 10}</i>"

                for admin_id in settings.ADMIN_IDS:
                    try:
                        await bot.send_message(admin_id, msg, parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Stale alert failed to {admin_id}: {e}")
                logger.warning(f"Stale payments alert: {len(stale_payments)}")
            finally:
                await session.close()
        except Exception as e:
            logger.error(f"Ошибка в stale_payments_checker: {e}", exc_info=True)
# services/background_worker.py
import asyncio
import logging
import sys
from datetime import datetime, timezone
from database.connection import get_session
from database.repositories.users_repo import get_all_users
from database.repositories.profiles_repo import get_user_profiles, update_profile
from database.repositories.servers_repo import get_server_by_id, get_active_servers
from services.amnezia_client import AmneziaClient, close_http_session

logger = logging.getLogger("BackgroundWorker")

async def start_background_worker():
    """Запуск бесконечных циклов фоновых задач синхронизации"""
    asyncio.create_task(subscription_expiry_checker_loop())
    asyncio.create_task(traffic_sync_loop())
    asyncio.create_task(cleanup_dangling_peers_loop())
    logger.info("Фоновые воркеры успешно запущены.")

async def subscription_expiry_checker_loop():
    """Фоновый цикл проверки и управления статусом клиентов на VPS в зависимости от подписки"""
    while True:
        try:
            try:
                logger.info("Запуск проверки истечения подписок...")
                session = await get_session()
                try:
                    users = await get_all_users(session)
                    
                    for user in users:
                        has_access = user.subscription_end and user.subscription_end > datetime.now(timezone.utc) and not user.is_banned
                        profiles = await get_user_profiles(session, user.id)
                        
                        for profile in profiles:
                            server = await get_server_by_id(session, profile.server_id)
                            if not server:
                                continue
                            
                            client = AmneziaClient(server.api_url, server.api_key)
                            
                            # Сценарий 1: Подписка закончилась, но профиль всё еще активен на VPS
                            if not has_access and profile.is_active:
                                logger.info(f"Блокировка устройства {profile.device_name} для пользователя {user.telegram_id}")
                                success = await client.update_client(client_id=profile.peer_id, status="disabled")
                                if success:
                                    await update_profile(session, profile, is_active=False)
                            
                            # Сценарий 2: Подписка активна (продлена), но профиль был выключен
                            elif has_access and not profile.is_active:
                                logger.info(f"Активация устройства {profile.device_name} для пользователя {user.telegram_id}")
                                success = await client.update_client(client_id=profile.peer_id, status="active")
                                if success:
                                    await update_profile(session, profile, is_active=True)
                finally:
                    await session.close()
            except Exception as e:
                logger.error(f"Ошибка в цикле проверки подписок: {e}", exc_info=True)
        except Exception as fatal:
            print(f"FATAL in subscription_checker: {fatal}", file=sys.stderr)
        
        await asyncio.sleep(1800)  # Проверка каждые 30 минут


async def traffic_sync_loop():
    """Фоновый цикл сбора статистики трафика с серверов Amnezia API"""
    while True:
        try:
            try:
                logger.info("Запуск синхронизации метрик трафика...")
                session = await get_session()
                try:
                    servers = await get_active_servers(session)
                    
                    for server in servers:
                        client = AmneziaClient(server.api_url, server.api_key)
                        # Используем новый публичный метод
                        api_clients_list = await client.get_all_clients()
                        if not api_clients_list:
                            continue
                            
                        api_clients = {c["id"]: c for c in api_clients_list}
                        all_users = await get_all_users(session)
                        
                        for user in all_users:
                            profiles = await get_user_profiles(session, user.id)
                            for profile in profiles:
                                if profile.server_id == server.id and profile.peer_id in api_clients:
                                    api_data = api_clients[profile.peer_id]
                                    stats = api_data.get("traffics", {})
                                    traffic_down = stats.get("totalDownload", profile.traffic_down)
                                    traffic_up = stats.get("totalUpload", profile.traffic_up)
                                    
                                    last_conn_raw = api_data.get("updatedAt")
                                    last_connected = profile.last_connected
                                    if last_conn_raw:
                                        try:
                                            last_connected = datetime.fromtimestamp(int(last_conn_raw), tz=timezone.utc)
                                        except (ValueError, TypeError):
                                            pass
                                    
                                    await update_profile(
                                        session, profile,
                                        traffic_down=traffic_down,
                                        traffic_up=traffic_up,
                                        last_connected=last_connected
                                    )
                finally:
                    await session.close()
            except Exception as e:
                logger.error(f"Ошибка в цикле синхронизации трафика: {e}", exc_info=True)
        except Exception as fatal:
            print(f"FATAL in traffic_sync: {fatal}", file=sys.stderr)
            
        await asyncio.sleep(900)  # Синхронизация каждые 15 минут


async def cleanup_dangling_peers_loop():
    """Фоновый цикл очистки 'призраков' (пиров, которых нет в БД, но они есть на сервере)"""
    # Ждем 10 минут после старта бота перед первым запуском
    await asyncio.sleep(600)
    while True:
        try:
            try:
                logger.info("Запуск очистки 'призраков' (Dangling Peers)...")
                session = await get_session()
                try:
                    servers = await get_active_servers(session)
                    
                    # Получаем все peer_id из БД (они автоматически расшифруются благодаря EncryptedString)
                    from database.models import VPNProfile
                    from sqlalchemy import select
                    result = await session.execute(select(VPNProfile.peer_id))
                    db_peer_ids = {row[0] for row in result.all()}
                    
                    for server in servers:
                        client = AmneziaClient(server.api_url, server.api_key)
                        api_clients_list = await client.get_all_clients()
                        if not api_clients_list:
                            continue
                            
                        for api_client in api_clients_list:
                            client_id = api_client.get("id")
                            # В API поле имени может называться clientName или name
                            client_name = api_client.get("clientName", api_client.get("name", ""))
                            
                            # Удаляем только тех, кто создан ботом (имеет префикс tg_) и отсутствует в БД
                            if client_name.startswith("tg_") and client_id not in db_peer_ids:
                                logger.warning(f"Обнаружен 'призрак' на сервере {server.name}: {client_name} ({client_id}). Удаляю...")
                                await client.delete_user(client_id=client_id)
                finally:
                    await session.close()
            except Exception as e:
                logger.error(f"Ошибка в цикле очистки 'призраков': {e}", exc_info=True)
        except Exception as fatal:
            print(f"FATAL in dangling_peers_cleanup: {fatal}", file=sys.stderr)
            
        await asyncio.sleep(86400)  # Повторяем раз в сутки

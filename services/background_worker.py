# services/background_worker.py
import asyncio
import logging
from datetime import datetime
from database.connection import get_session
from database.repositories.users_repo import get_all_users
from database.repositories.profiles_repo import get_user_profiles, update_profile
from database.repositories.servers_repo import get_server_by_id, get_active_servers
from services.amnezia_client import AmneziaClient

logger = logging.getLogger("BackgroundWorker")

async def start_background_worker():
    """Запуск бесконечных циклов фоновых задач синхронизации"""
    asyncio.create_task(subscription_expiry_checker_loop())
    asyncio.create_task(traffic_sync_loop())
    logger.info("Фоновые воркеры успешно запущены.")

async def subscription_expiry_checker_loop():
    """Фоновый цикл проверки и управления статусом клиентов на VPS в зависимости от подписки"""
    while True:
        try:
            logger.info("Запуск проверки истечения подписок...")
            session = await get_session()
            now = datetime.utcnow()
            
            users = await get_all_users(session)
            for user in users:
                has_access = user.subscription_end and user.subscription_end > now and not user.is_banned
                profiles = await get_user_profiles(session, user.id)
                
                for profile in profiles:
                    server = await get_server_by_id(session, profile.server_id)
                    if not server:
                        continue
                        
                    client = AmneziaClient(server.api_url, server.api_key)
                    
                    # Сценарий 1: Подписка закончилась, но профиль всё еще активен на VPS
                    if not has_access and profile.is_active:
                        logger.info(f"Блокировка устройства {profile.device_name} для пользователя {user.telegram_id}")
                        success = await client.update_client(client_id=profile.peer_id, protocol=server.protocol, status="disabled")
                        if success:
                            await update_profile(session, profile, is_active=False)
                            
                    # Сценарий 2: Подписка активна (продлена), но профиль был выключен
                    elif has_access and not profile.is_active:
                        logger.info(f"Активация устройства {profile.device_name} для пользователя {user.telegram_id}")
                        success = await client.update_client(client_id=profile.peer_id, protocol=server.protocol, status="active")
                        if success:
                            await update_profile(session, profile, is_active=True)
            
            await session.close()
        except Exception as e:
            logger.error(f"Ошибка в цикле проверки подписок: {e}", exc_info=True)
            
        await asyncio.sleep(1800)  # Проверка каждые 30 минут


async def traffic_sync_loop():
    """Фоновый цикл сбора статистики трафика с серверов Amnezia API"""
    while True:
        try:
            logger.info("Запуск синхронизации метрик трафика...")
            session = await get_session()
            servers = await get_active_servers(session)
            
            for server in servers:
                client = AmneziaClient(server.api_url, server.api_key)
                # Запрашиваем полный список клиентов с сервера
                result = await client._request("GET", "/clients", params={"skip": 0, "limit": 1000})
                
                if not result or "clients" not in result:
                    continue
                    
                api_clients = {c["id"]: c for c in result["clients"]}
                
                # Обновляем данные в нашей локальной БД
                all_users = await get_all_users(session)
                for user in all_users:
                    profiles = await get_user_profiles(session, user.id)
                    for profile in profiles:
                        if profile.server_id == server.id and profile.peer_id in api_clients:
                            api_data = api_clients[profile.peer_id]
                            
                            # Извлекаем статистику (структура зависит от метрик amneziawg/wireguard внутри API)
                            stats = api_data.get("traffics", {})
                            traffic_down = stats.get("totalDownload", profile.traffic_down)
                            traffic_up = stats.get("totalUpload", profile.traffic_up)
                            
                            # Парсим дату последней активности, если она доступна
                            last_conn_raw = api_data.get("updatedAt")
                            last_connected = profile.last_connected
                            if last_conn_raw:
                                try:
                                    last_connected = datetime.fromtimestamp(int(last_conn_raw))
                                except ValueError:
                                    pass
                                    
                            await update_profile(
                                session, profile, 
                                traffic_down=traffic_down, 
                                traffic_up=traffic_up,
                                last_connected=last_connected
                            )
            await session.close()
        except Exception as e:
            logger.error(f"Ошибка в цикле синхронизации трафика: {e}", exc_info=True)
            
        await asyncio.sleep(900)  # Синхронизация каждые 15 минут

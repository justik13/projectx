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
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from database.models import User, VPNProfile
from collections import defaultdict

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
                    # ОДИН запрос вместо N+1
                    result = await session.execute(
                        select(User).options(
                            selectinload(User.profiles).selectinload(VPNProfile.server)
                        )
                    )
                    users = result.scalars().all()
                    
                    server_profiles = defaultdict(list)
                    now = datetime.now(timezone.utc)
                    
                    for user in users:
                        has_access = user.subscription_end and user.subscription_end > now and not user.is_banned
                        for profile in user.profiles:
                            if profile.server and profile.server.is_active:
                                server_profiles[profile.server.id].append((profile, has_access, user.telegram_id))
                    
                    for server_id, items in server_profiles.items():
                        server = items[0][0].server
                        client = AmneziaClient(server.api_url, server.api_key)
                        for profile, has_access, tg_id in items:
                            if not has_access and profile.is_active:
                                logger.info(f"Блокировка устройства {profile.device_name} для пользователя {tg_id}")
                                success = await client.update_client(client_id=profile.peer_id, status="disabled")
                                if success:
                                    await update_profile(session, profile, is_active=False)
                            elif has_access and not profile.is_active:
                                logger.info(f"Активация устройства {profile.device_name} для пользователя {tg_id}")
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
                    # ОДИН запрос на все активные профили
                    result = await session.execute(
                        select(VPNProfile)
                        .options(selectinload(VPNProfile.server))
                        .where(VPNProfile.is_active == True)
                    )
                    all_profiles = result.scalars().all()
                    
                    by_server = defaultdict(list)
                    for p in all_profiles:
                        if p.server and p.server.is_active:
                            by_server[p.server.id].append(p)
                    
                    for server_id, profiles in by_server.items():
                        server = profiles[0].server
                        client = AmneziaClient(server.api_url, server.api_key)
                        api_clients_list = await client.get_all_clients()
                        if not api_clients_list:
                            continue
                        
                        api_clients = {c["id"]: c for c in api_clients_list}
                        
                        for profile in profiles:
                            if profile.peer_id in api_clients:
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

# services/background_worker.py
import asyncio
import logging
import sys
from datetime import datetime, timezone
from database.connection import get_session
from database.repositories.servers_repo import get_active_servers
from services.amnezia_client import AmneziaClient
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


async def _process_single_server_expiry(server, items):
    """Сетевая задача: конкурентный опрос одного сервера на изменение статусов"""
    client = AmneziaClient(server.api_url, server.api_key)
    updates = []  # Список кортежей: (profile_id, new_status_bool)
    
    for p_dict in items:
        try:
            # Если подписка кончилась, а устройство активно -> выключаем на стороне API
            if not p_dict['has_access'] and p_dict['is_active']:
                success = await client.update_client(client_id=p_dict['peer_id'], status="disabled")
                if success:
                    updates.append((p_dict['id'], False))
            # Если подписка активна, но устройство выключено -> включаем на стороне API
            elif p_dict['has_access'] and not p_dict['is_active']:
                success = await client.update_client(client_id=p_dict['peer_id'], status="active")
                if success:
                    updates.append((p_dict['id'], True))
        except Exception as e:
            logger.error(f"Ошибка изменения статуса профиля {p_dict['id']} на сервере {server.name}: {e}")
            
    return server.id, updates


async def subscription_expiry_checker_loop():
    """Фоновый цикл проверки и управления статусом клиентов в зависимости от подписки"""
    while True:
        try:
            logger.info("Запуск проверки истечения подписок...")
            session = await get_session()
            try:
                # ОДИН запрос к БД: выкачиваем пользователей со связями
                result = await session.execute(
                    select(User).options(
                        selectinload(User.profiles).selectinload(VPNProfile.server)
                    )
                )
                users = result.scalars().all()
                
                server_profiles = defaultdict(list)
                servers_map = {}
                # ✅ Исправлено: Сравнение как naive UTC для совместимости с SQLite
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                
                # Маппим данные в легковесные словари для сетевой обработки
                for user in users:
                    has_access = user.subscription_end and user.subscription_end > now and not user.is_banned
                    for profile in user.profiles:
                        if profile.server and profile.server.is_active:
                            server_profiles[profile.server.id].append({
                                'id': profile.id,
                                'peer_id': profile.peer_id,
                                'is_active': profile.is_active,
                                'has_access': has_access
                            })
                            servers_map[profile.server.id] = profile.server
                            
                if not servers_map:
                    await asyncio.sleep(1800)
                    continue

                # 1. NETWORK I/O: Параллельно отправляем запросы на все серверы Amnezia API
                tasks = [_process_single_server_expiry(s, server_profiles[s.id]) for s in servers_map.values()]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Собираем успешные результаты сетевых вызовов
                updates_map = {}
                for r in results:
                    if not isinstance(r, Exception) and r is not None:
                        server_id, server_updates = r
                        for p_id, new_status in server_updates:
                            updates_map[p_id] = new_status
                            
                # 2. DATABASE I/O: Применяем изменения к объектам в памяти и делаем ОДИН коммит на всю транзакцию
                if updates_map:
                    for user in users:
                        for profile in user.profiles:
                            if profile.id in updates_map:
                                profile.is_active = updates_map[profile.id]
                    
                    await session.commit()
                    logger.info(f"Статусы подписок успешно обновлены для {len(updates_map)} устройств.")
                    
            finally:
                await session.close()
        except Exception as e:
            logger.error(f"Ошибка в цикле проверки подписок: {e}", exc_info=True)
        except Exception as fatal:
            print(f"FATAL in subscription_checker: {fatal}", file=sys.stderr)
        
        await asyncio.sleep(1800)  # Проверка каждые 30 минут


async def _fetch_server_traffic(server):
    """Сетевая задача: сбор метрик с конкретного сервера"""
    client = AmneziaClient(server.api_url, server.api_key)
    try:
        api_clients_list = await client.get_all_clients()
        if not api_clients_list:
            return server.id, {}
        return server.id, {c["id"]: c for c in api_clients_list}
    except Exception as e:
        logger.error(f"Ошибка сети при получении трафика с сервера {server.name}: {e}")
        return server.id, {}


async def traffic_sync_loop():
    """Фоновый цикл сбора статистики трафика с серверов Amnezia API"""
    while True:
        try:
            logger.info("Запуск синхронизации метрик трафика...")
            session = await get_session()
            try:
                # ОДИН запрос: получаем только активные профили на активных локациях
                result = await session.execute(
                    select(VPNProfile)
                    .options(selectinload(VPNProfile.server))
                    .where(VPNProfile.is_active == True)
                )
                all_profiles = result.scalars().all()
                
                by_server = defaultdict(list)
                servers_map = {}
                for p in all_profiles:
                    if p.server and p.server.is_active:
                        by_server[p.server.id].append(p)
                        servers_map[p.server.id] = p.server
                        
                if not servers_map:
                    await asyncio.sleep(900)
                    continue

                # 1. NETWORK I/O: Параллельно собираем дампы клиентов со всех VPS одновременно
                tasks = [_fetch_server_traffic(s) for s in servers_map.values()]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                api_data_by_server = {r[0]: r[1] for r in results if not isinstance(r, Exception) and r is not None}
                
                # 2. DATABASE I/O: Сравниваем данные и изменяем свойства объектов в памяти SQLAlchemy
                changed = False
                for server_id, api_clients in api_data_by_server.items():
                    if not api_clients:
                        continue
                    for profile in by_server[server_id]:
                        if profile.peer_id in api_clients:
                            api_data = api_clients[profile.peer_id]
                            stats = api_data.get("traffics", {})
                            
                            t_down = stats.get("totalDownload", profile.traffic_down)
                            t_up = stats.get("totalUpload", profile.traffic_up)
                            
                            # Безопасный парсинг времени последнего подключения
                            last_conn_raw = api_data.get("updatedAt")
                            last_connected = profile.last_connected
                            if last_conn_raw:
                                try:
                                    # ✅ Исправлено: приведение даты последнего коннекта на Amnezia API к naive UTC
                                    last_connected = datetime.fromtimestamp(int(float(str(last_conn_raw))), tz=timezone.utc).replace(tzinfo=None)
                                except (ValueError, TypeError):
                                    pass
                                    
                            # Если что-то изменилось — обновляем поля
                            if (profile.traffic_down != t_down or 
                                profile.traffic_up != t_up or 
                                profile.last_connected != last_connected):
                                
                                profile.traffic_down = t_down
                                profile.traffic_up = t_up
                                profile.last_connected = last_connected
                                changed = True
                                
                # Фиксируем изменения в базе данных одним единственным коммитом на весь цикл
                if changed:
                    await session.commit()
                    logger.info("Метрики трафика успешно синхронизированы в БД.")
                    
            finally:
                await session.close()
        except Exception as e:
            logger.error(f"Ошибка в цикле синхронизации трафика: {e}", exc_info=True)
        except Exception as fatal:
            print(f"FATAL in traffic_sync: {fatal}", file=sys.stderr)
            
        await asyncio.sleep(900)  # Синхронизация каждые 15 минут


async def _clean_server_dangling_peers(server, db_peer_ids):
    """Сетевая задача: поиск и удаление призраков на конкретном сервере"""
    client = AmneziaClient(server.api_url, server.api_key)
    try:
        api_clients_list = await client.get_all_clients()
        if not api_clients_list:
            return
            
        for api_client in api_clients_list:
            client_id = api_client.get("id")
            client_name = api_client.get("clientName", api_client.get("name", ""))
            
            # Если пир был создан ботом, но запись о нем удалена из локальной БД
            if client_name.startswith("tg_") and client_id not in db_peer_ids:
                logger.warning(f"Обнаружен 'призрак' на сервере {server.name}: {client_name} ({client_id}). Удаляю...")
                await client.delete_user(client_id=client_id)
    except Exception as e:
        logger.error(f"Ошибка очистки 'призраков' на сервере {server.name}: {e}")


async def cleanup_dangling_peers_loop():
    """Фоновый цикл очистки 'призраков' (пиров, которых нет в БД, но они остались на сервере)"""
    await asyncio.sleep(600)  # Ждем 10 минут после старта бота перед первым запуском
    while True:
        try:
            logger.info("Запуск очистки 'призраков' (Dangling Peers)...")
            session = await get_session()
            try:
                servers = await get_active_servers(session)
                
                # Извлекаем все peer_id из БД в виде множества для мгновенного поиска (O(1))
                result = await session.execute(select(VPNProfile.peer_id))
                db_peer_ids = {row[0] for row in result.all()}
                
                # Параллельно запускаем очистку на всех серверах
                tasks = [_clean_server_dangling_peers(s, db_peer_ids) for s in servers]
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                await session.close()
        except Exception as e:
            logger.error(f"Ошибка в цикле очистки 'призраков': {e}", exc_info=True)
        except Exception as fatal:
            print(f"FATAL in dangling_peers_cleanup: {fatal}", file=sys.stderr)
            
        await asyncio.sleep(86400)  # Повторяем раз в сутки
# services/background_worker.py
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from sqlalchemy import select, or_, update
from database.connection import get_session
from database.repositories.servers_repo import get_active_servers
from services.amnezia_client import AmneziaClient
from database.models import User, VPNProfile, Server

logger = logging.getLogger("BackgroundWorker")

async def start_background_worker():
    """Запуск бесконечных циклов фоновых задач синхронизации"""
    asyncio.create_task(subscription_expiry_checker_loop())
    asyncio.create_task(traffic_sync_loop())
    asyncio.create_task(cleanup_dangling_peers_loop())
    logger.info("Фоновые воркеры успешно запущены.")


# ====================================================================
# 1. ПРОВЕРКА ИСТЕЧЕНИЯ ПОДПИСОК + Circuit Breaker
# ====================================================================
async def subscription_expiry_checker_loop():
    while True:
        try:
            logger.info("Запуск проверки истечения подписок...")
            now = datetime.now(timezone.utc).replace(tzinfo=None)

            session = await get_session()
            try:
                stmt_disable = (
                    select(VPNProfile.id, VPNProfile.peer_id, VPNProfile.server_id)
                    .join(User, VPNProfile.user_id == User.id)
                    .join(Server, VPNProfile.server_id == Server.id)
                    .where(
                        VPNProfile.is_active == True,
                        Server.is_active == True,
                        or_(User.subscription_end <= now, User.is_banned == True)
                    )
                )
                res_disable = await session.execute(stmt_disable)
                to_disable = res_disable.all()

                stmt_enable = (
                    select(VPNProfile.id, VPNProfile.peer_id, VPNProfile.server_id)
                    .join(User, VPNProfile.user_id == User.id)
                    .join(Server, VPNProfile.server_id == Server.id)
                    .where(
                        VPNProfile.is_active == False,
                        Server.is_active == True,
                        User.subscription_end > now,
                        User.is_banned == False
                    )
                )
                res_enable = await session.execute(stmt_enable)
                to_enable = res_enable.all()

                tasks_data = defaultdict(lambda: {'disable': [], 'enable': []})
                server_ids = set()

                for p_id, peer_id, s_id in to_disable:
                    server_ids.add(s_id)
                    tasks_data[s_id]['disable'].append((p_id, peer_id))
                for p_id, peer_id, s_id in to_enable:
                    server_ids.add(s_id)
                    tasks_data[s_id]['enable'].append((p_id, peer_id))

                if not server_ids:
                    await asyncio.sleep(1800)
                    continue

                stmt_servers = select(Server).where(Server.id.in_(server_ids))
                servers_res = await session.execute(stmt_servers)
                servers_data = {s.id: {'api_url': s.api_url, 'api_key': s.api_key, 'name': s.name} for s in servers_res.scalars().all()}
            finally:
                await session.close()

            async def _process_server_status(server_id, server_info, data):
                client = AmneziaClient(server_info['api_url'], server_info['api_key'])
                updates = []
                for p_id, peer_id in data['disable']:
                    if await client.update_client(client_id=peer_id, status="disabled"):
                        updates.append((p_id, False))
                for p_id, peer_id in data['enable']:
                    if await client.update_client(client_id=peer_id, status="active"):
                        updates.append((p_id, True))
                return server_id, updates

            tasks = [_process_server_status(s_id, servers_data[s_id], tasks_data[s_id]) for s_id in server_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            ids_to_disable = []
            ids_to_enable = []
            
            for r in results:
                if not isinstance(r, Exception) and r is not None:
                    _, server_updates = r
                    for p_id, new_status in server_updates:
                        (ids_to_disable if not new_status else ids_to_enable).append(p_id)

            if ids_to_disable or ids_to_enable:
                session = await get_session()
                try:
                    if ids_to_disable:
                        await session.execute(update(VPNProfile).where(VPNProfile.id.in_(ids_to_disable)).values(is_active=False, sync_fail_count=0))
                    if ids_to_enable:
                        await session.execute(update(VPNProfile).where(VPNProfile.id.in_(ids_to_enable)).values(is_active=True, sync_fail_count=0))
                    await session.commit()
                    logger.info(f"Обновлено статусов: выключено {len(ids_to_disable)}, включено {len(ids_to_enable)}")
                finally:
                    await session.close()
            
            # 🔥 FIX P1: Circuit Breaker
            failed_profile_ids = []
            for p_id, _, _ in to_disable:
                if p_id not in ids_to_disable:
                    failed_profile_ids.append(p_id)
            
            if failed_profile_ids:
                session = await get_session()
                try:
                    await session.execute(
                        update(VPNProfile)
                        .where(VPNProfile.id.in_(failed_profile_ids))
                        .values(sync_fail_count=VPNProfile.sync_fail_count + 1)
                    )
                    await session.commit()
                    
                    check_stmt = select(VPNProfile.id, VPNProfile.server_id).where(
                        VPNProfile.id.in_(failed_profile_ids),
                        VPNProfile.sync_fail_count >= 3
                    )
                    res = await session.execute(check_stmt)
                    critical_fails = res.all()
                    for p_id, s_id in critical_fails:
                        logger.critical(f"⚠️ Circuit Breaker: Profile {p_id} on server {s_id} failed to sync 3+ times!")
                finally:
                    await session.close()

        except Exception as e:
            logger.error(f"Ошибка в цикле проверки подписок: {e}", exc_info=True)
        
        await asyncio.sleep(1800)


# ====================================================================
# 2. СИНХРОНИЗАЦИЯ ТРАФИКА
# ====================================================================
async def traffic_sync_loop():
    while True:
        try:
            logger.info("Запуск синхронизации метрик трафика...")
            session = await get_session()
            try:
                stmt = (
                    select(
                        VPNProfile.id, VPNProfile.peer_id, VPNProfile.server_id,
                        VPNProfile.traffic_down, VPNProfile.traffic_up, VPNProfile.last_connected,
                        Server.api_url, Server.api_key, Server.name
                    )
                    .join(Server, VPNProfile.server_id == Server.id)
                    .where(VPNProfile.is_active == True, Server.is_active == True)
                )
                result = await session.execute(stmt)
                rows = result.all()

                by_server = defaultdict(list)
                servers_map = {}
                for row in rows:
                    p_id, peer_id, s_id, t_down, t_up, last_conn, api_url, api_key, s_name = row
                    by_server[s_id].append({
                        'id': p_id, 'peer_id': peer_id,
                        'traffic_down': t_down, 'traffic_up': t_up, 'last_connected': last_conn
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
                    return server_id, ({c["id"]: c for c in api_clients_list} if api_clients_list else {})
                except Exception as e:
                    logger.error(f"Ошибка сети при получении трафика с сервера {server_info['name']}: {e}")
                    return server_id, {}

            tasks = [_fetch_server_traffic(s_id, servers_map[s_id]) for s_id in servers_map]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            api_data_by_server = {r[0]: r[1] for r in results if not isinstance(r, Exception) and r is not None}

            updates_data = {}
            for server_id, api_clients in api_data_by_server.items():
                if not api_clients: continue
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
                                last_connected = datetime.fromtimestamp(int(float(str(last_conn_raw))), tz=timezone.utc).replace(tzinfo=None)
                            except (ValueError, TypeError): pass
                        
                        if (p_dict['traffic_down'] != t_down or p_dict['traffic_up'] != t_up or p_dict['last_connected'] != last_connected):
                            updates_data[p_dict['id']] = {'traffic_down': t_down, 'traffic_up': t_up, 'last_connected': last_connected}

            if updates_data:
                session = await get_session()
                try:
                    for p_id, data in updates_data.items():
                        await session.execute(
                            update(VPNProfile).where(VPNProfile.id == p_id).values(
                                traffic_down=data['traffic_down'],
                                traffic_up=data['traffic_up'],
                                last_connected=data['last_connected']
                            )
                        )
                    await session.commit()
                    logger.info(f"Метрики трафика успешно синхронизированы для {len(updates_data)} устройств.")
                finally:
                    await session.close()

        except Exception as e:
            logger.error(f"Ошибка в цикле синхронизации трафика: {e}", exc_info=True)
        
        await asyncio.sleep(900)


# ====================================================================
# 3. ОЧИСТКА "ПРИЗРАКОВ" (Sanity Check + Double-Check Lock)
# ====================================================================
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
                servers_data = [{'api_url': s.api_url, 'api_key': s.api_key, 'name': s.name} for s in servers]
            finally:
                await session.close()

            # 🔥 FIX P0: Sanity Check
            if not db_peer_ids or all(p is None for p in db_peer_ids):
                if servers_data:
                    logger.critical("🛑 DB returned empty/invalid peer IDs. Aborting cleanup to prevent mass deletion!")
                    await asyncio.sleep(86400)
                    continue

            async def _clean_server_dangling_peers(server_info, db_peer_ids_set):
                client = AmneziaClient(server_info['api_url'], server_info['api_key'])
                try:
                    api_clients_list = await client.get_all_clients()
                    if not api_clients_list: return
                    
                    for api_client in api_clients_list:
                        client_id = api_client.get("id")
                        client_name = api_client.get("clientName", api_client.get("name", ""))
                        
                        if client_name.startswith("tg_") and client_id not in db_peer_ids_set:
                            # 🔥 FIX P0: Double-Check перед удалением
                            session2 = await get_session()
                            try:
                                fresh_result = await session2.execute(
                                    select(VPNProfile.id, VPNProfile.created_at).where(VPNProfile.peer_id == client_id)
                                )
                                fresh_row = fresh_result.first()
                            finally:
                                await session2.close()
                            
                            if fresh_row:
                                logger.info(f"Предотвращено ложное удаление (Race Condition caught): {client_name}")
                                continue
                            
                            logger.warning(f"Обнаружен 'призрак' на сервере {server_info['name']}: {client_name}. Удаляю...")
                            await client.delete_user(client_id=client_id)
                except Exception as e:
                    logger.error(f"Ошибка очистки 'призраков' на сервере {server_info['name']}: {e}")

            tasks = [_clean_server_dangling_peers(s, db_peer_ids) for s in servers_data]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"Ошибка в цикле очистки 'призраков': {e}", exc_info=True)
        
        await asyncio.sleep(86400)
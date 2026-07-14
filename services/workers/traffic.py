import asyncio
import logging
from datetime import datetime, timezone
from collections import defaultdict
from sqlalchemy import select, update
from database.connection import get_session
from services.amnezia_client import AmneziaClient
from database.models import VPNProfile, Server
from bot.constants import TRAFFIC_SYNC_INTERVAL, WORKER_ERROR_SLEEP_INTERVAL

logger = logging.getLogger("BackgroundWorker")


async def traffic_sync_loop():
    """
    Фоновый воркер синхронизации трафика.
    🔥 ИСПРАВЛЕНО: Надежная обработка ошибок с автоматическим перезапуском.
    - CancelledError обрабатывается для graceful shutdown
    - Все остальные исключения логируются и воркер продолжает работу
    - Sleep вынесен за try/except для гарантии перезапуска
    """
    while True:
        try:
            session = await get_session()
            try:
                stmt = (
                    select(
                        VPNProfile.id, VPNProfile.peer_id, VPNProfile.server_id,
                        VPNProfile.traffic_down, VPNProfile.traffic_up, VPNProfile.last_connected,
                        VPNProfile.is_active, Server.api_url, Server.api_key, Server.name
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
                        'last_connected': last_conn, 'is_active': is_active
                    })
                    servers_map[s_id] = {'api_url': api_url, 'api_key': api_key, 'name': s_name}
            finally:
                await session.close()
            
            if not servers_map:
                await asyncio.sleep(TRAFFIC_SYNC_INTERVAL)
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
                        
                        last_conn_raw = (
                            api_data.get("lastHandshake")
                            or api_data.get("lastSeen")
                            or api_data.get("updatedAt")
                        )
                        
                        last_connected = p_dict['last_connected']
                        if last_conn_raw:
                            try:
                                if isinstance(last_conn_raw, (int, float)):
                                    ts = int(float(str(last_conn_raw)))
                                    if ts > 1e12:
                                        ts = ts // 1000
                                    last_connected = datetime.fromtimestamp(
                                        ts, tz=timezone.utc
                                    ).replace(tzinfo=None)
                                elif isinstance(last_conn_raw, str):
                                    try:
                                        ts = int(float(last_conn_raw))
                                        if ts > 1e12:
                                            ts = ts // 1000
                                        last_connected = datetime.fromtimestamp(
                                            ts, tz=timezone.utc
                                        ).replace(tzinfo=None)
                                    except (ValueError, TypeError):
                                        try:
                                            last_connected = datetime.fromisoformat(last_conn_raw).replace(tzinfo=None)
                                        except (ValueError, TypeError):
                                            pass
                            except (ValueError, TypeError, OverflowError):
                                pass
                        
                        api_status = api_data.get("status", "active")
                        api_is_active = (api_status == "active")
                        db_is_active = p_dict['is_active']
                        
                        if (p_dict['traffic_down'] != t_down or
                            p_dict['traffic_up'] != t_up or
                            p_dict['last_connected'] != last_connected or
                            db_is_active != api_is_active):
                            updates_data[p_dict['id']] = {
                                'traffic_down': t_down, 'traffic_up': t_up,
                                'last_connected': last_connected, 'is_active': api_is_active
                            }
            
            if updates_data:
                session = await get_session()
                try:
                    for p_id, data in updates_data.items():
                        await session.execute(
                            update(VPNProfile).where(VPNProfile.id == p_id).values(**data)
                        )
                    await session.commit()
                    logger.info(f"Трафик синхронизирован для {len(updates_data)} устройств.")
                finally:
                    await session.close()
        
        except asyncio.CancelledError:
            logger.info("Traffic sync worker cancelled")
            break
        except Exception as e:
            logger.error(f"Критическая ошибка в цикле трафика: {e}", exc_info=True)
            # 🔥 ИСПРАВЛЕНО: Sleep после ошибки для предотвращения flood loop
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)
            continue
        
        # 🔥 ИСПРАВЛЕНО: Sleep вынесен за try/except для гарантии выполнения
        await asyncio.sleep(TRAFFIC_SYNC_INTERVAL)
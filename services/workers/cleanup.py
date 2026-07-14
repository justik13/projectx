import asyncio
import logging
from sqlalchemy import select
from database.connection import get_session
from database.repositories.servers_repo import get_active_servers
from services.amnezia_client import AmneziaClient
from database.models import VPNProfile
from bot.constants import CLEANUP_INTERVAL, WORKER_ERROR_SLEEP_INTERVAL

logger = logging.getLogger("BackgroundWorker")


async def cleanup_dangling_peers_loop():
    """
    Фоновый воркер очистки "призрачных" пиров.
    🔥 ИСПРАВЛЕНО: Надежная обработка ошибок с автоматическим перезапуском.
    """
    # Начальная задержка для стабилизации системы
    await asyncio.sleep(600)
    
    while True:
        try:
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
                await asyncio.sleep(CLEANUP_INTERVAL)
                continue
            
            async def _clean_server_dangling_peers(server_info, db_peer_ids_set):
                client = AmneziaClient(server_info['api_url'], server_info['api_key'])
                try:
                    api_clients_list = await client.get_all_clients()
                    if api_clients_list is None:
                        return
                    
                    for api_client in api_clients_list:
                        client_id = api_client.get("id")
                        client_name = api_client.get("clientName", api_client.get("name", ""))
                        
                        if client_name.startswith("tg_") and client_id not in db_peer_ids_set:
                            # Двойная проверка — peer мог появиться в БД за время запроса
                            session2 = await get_session()
                            try:
                                fresh_result = await session2.execute(
                                    select(VPNProfile.id).where(VPNProfile.peer_id == client_id)
                                )
                                if fresh_result.first():
                                    continue
                            finally:
                                await session2.close()
                            
                            logger.warning(f"Удаляю 'призрака' {client_name} на {server_info['name']}")
                            await client.delete_user(client_id=client_id)
                except Exception as e:
                    logger.error(f"Ошибка очистки призраков на {server_info['name']}: {e}")
            
            tasks = [_clean_server_dangling_peers(s, db_peer_ids) for s in servers_data]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        
        except asyncio.CancelledError:
            logger.info("Cleanup worker cancelled")
            break
        except Exception as e:
            logger.error(f"Критическая ошибка в цикле призраков: {e}", exc_info=True)
            await asyncio.sleep(WORKER_ERROR_SLEEP_INTERVAL)
            continue
        
        await asyncio.sleep(CLEANUP_INTERVAL)
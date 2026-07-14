import logging
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession
from services.platega_client import PlategaClient
from services.payment_service import PaymentService
from database.connection import session_scope

logger = logging.getLogger(__name__)

async def platega_webhook_handler(request: web.Request) -> web.Response:
    """
    Обработчик webhook от Platega.io
    Принимает POST запросы с информацией об изменении статуса транзакции
    """
    try:
        # Валидация заголовков
        merchant_id = request.headers.get("X-MerchantId", "")
        secret = request.headers.get("X-Secret", "")
        
        client = PlategaClient()
        if not client.validate_callback(merchant_id, secret):
            logger.warning(f"Invalid Platega callback credentials: {merchant_id}")
            return web.Response(status=401, text="Unauthorized")
        
        # Парсим JSON
        try:
            data = await request.json()
        except Exception as e:
            logger.error(f"Failed to parse Platega webhook JSON: {e}")
            return web.Response(status=400, text="Invalid JSON")
        
        transaction_id = data.get("id") or data.get("transactionId")
        status = data.get("status")
        payload = data.get("payload", "")
        
        if not transaction_id or not status:
            logger.warning(f"Invalid Platega webhook data: {data}")
            return web.Response(status=400, text="Missing required fields")
        
        logger.info(f"Platega webhook received: transaction={transaction_id}, status={status}")
        
        # Обрабатываем callback
        async with session_scope() as session:
            success = await PaymentService.handle_platega_callback(
                session=session,
                transaction_id=transaction_id,
                status=status,
                payload=payload
            )
        
        if success:
            return web.Response(status=200, text="OK")
        else:
            return web.Response(status=500, text="Processing failed")
            
    except Exception as e:
        logger.error(f"Platega webhook error: {e}", exc_info=True)
        return web.Response(status=500, text="Internal server error")

async def healthcheck_handler(request: web.Request) -> web.Response:
    """Эндпоинт для мониторинга (UptimeRobot, Healthchecks.io)"""
    return web.Response(status=200, text="OK")

def setup_webhook_routes(app: web.Application):
    """Регистрирует webhook маршруты"""
    app.router.add_post("/webhook/platega", platega_webhook_handler)
    app.router.add_get("/health", healthcheck_handler)  # 🔥 ДОБАВИТЬ
    logger.info("Platega webhook route registered: POST /webhook/platega")
    logger.info("Healthcheck endpoint registered: GET /health")
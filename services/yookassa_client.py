import logging
from typing import Optional
from decimal import Decimal

import aiohttp

from config.settings import get_settings

logger = logging.getLogger(__name__)

_http_session: Optional[aiohttp.ClientSession] = None

YOOKASSA_API_BASE = "https://api.yookassa.ru/v3"


async def _get_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None:
        timeout = aiohttp.ClientTimeout(total=30)
        _http_session = aiohttp.ClientSession(timeout=timeout)
    return _http_session


async def close_yookassa_session() -> None:
    global _http_session
    if _http_session is not None:
        await _http_session.close()
        _http_session = None


class YooKassaClient:
    """
    Клиент для работы с YooKassa API v3.

    Аутентификация: HTTP Basic Auth (shopId:secretKey).
    """

    def __init__(self) -> None:
        settings = get_settings()
        self.shop_id = settings.YOOKASSA_SHOP_ID
        self.secret_key = settings.YOOKASSA_SECRET_KEY
        self._auth = aiohttp.BasicAuth(
            login=self.shop_id,
            password=self.secret_key,
        )

    # ──────────────────────────────────────────────────────────
    # Создание платежа
    # ──────────────────────────────────────────────────────────
    async def create_payment(
        self,
        amount: Decimal,
        currency: str = "RUB",
        description: str = "",
        return_url: str = "",
        metadata: Optional[dict] = None,
    ) -> Optional[dict]:
        """
        Создаёт платёж в YooKassa.

        Возвращает dict с полями:
        - id: str (идентификатор платежа в YooKassa)
        - status: str ("pending")
        - confirmation.confirmation_url: str (URL для редиректа)
        - amount.value, amount.currency
        - metadata

        При ошибке возвращает None.
        """
        url = f"{YOOKASSA_API_BASE}/payments"

        body: dict = {
            "amount": {
                "value": str(amount),
                "currency": currency,
            },
            "description": description,
            "confirmation": {
                "type": "redirect",
                "return_url": return_url,
            },
        }
        if metadata:
            body["metadata"] = metadata

        try:
            session = await _get_session()
            async with session.post(
                url,
                json=body,
                auth=self._auth,
                headers={"Content-Type": "application/json"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    logger.info(
                        "YooKassa payment created: id=%s, status=%s",
                        data.get("id"),
                        data.get("status"),
                    )
                    return data
                else:
                    text = await resp.text()
                    logger.error(
                        "YooKassa create_payment failed: "
                        "status=%s, body=%s",
                        resp.status,
                        text[:500],
                    )
                    return None
        except Exception as e:
            logger.error(
                "YooKassa create_payment exception: %s",
                e,
                exc_info=True,
            )
            return None

    # ──────────────────────────────────────────────────────────
    # Проверка статуса платежа
    # ──────────────────────────────────────────────────────────
    async def get_payment(
        self,
        payment_id: str,
    ) -> Optional[dict]:
        """
        Получает информацию о платеже по его ID в YooKassa.

        Возвращает dict:
        - id, status ("pending"|"processing"|"succeeded"|"canceled")
        - amount {value, currency}
        - metadata
        - paid: bool
        - created_at, expires_at

        При ошибке возвращает None.
        """
        url = f"{YOOKASSA_API_BASE}/payments/{payment_id}"

        try:
            session = await _get_session()
            async with session.get(
                url,
                auth=self._auth,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data
                elif resp.status == 404:
                    logger.warning(
                        "YooKassa payment not found: %s",
                        payment_id,
                    )
                    return None
                else:
                    text = await resp.text()
                    logger.error(
                        "YooKassa get_payment failed: "
                        "status=%s, body=%s",
                        resp.status,
                        text[:500],
                    )
                    return None
        except Exception as e:
            logger.error(
                "YooKassa get_payment exception: %s",
                e,
                exc_info=True,
            )
            return None

    # ──────────────────────────────────────────────────────────
    # Валидация webhook-уведомления
    # ──────────────────────────────────────────────────────────
    def validate_webhook(
        self,
        merchant_id: str,
        secret: str,
    ) -> bool:
        """
        Проверяет credentials из заголовков webhook-запроса.

        YooKassa не подписывает webhook'и криптографически,
        поэтому мы защищаем endpoint через Basic Auth
        (shopId:secretKey в заголовках X-ShopId / X-Secret).
        """
        return (
            merchant_id == self.shop_id
            and secret == self.secret_key
        )

    # ──────────────────────────────────────────────────────────
    # Нормализация статуса из webhook
    # ──────────────────────────────────────────────────────────
    @staticmethod
    def normalize_webhook_event(event: str) -> str:
        """
        Приводит event из webhook YooKassa к внутреннему статусу.

        YooKassa events:
        - payment.succeeded  → CONFIRMED
        - payment.canceled   → CANCELED
        - refund.succeeded   → CHARGEBACKED
        """
        mapping = {
            "payment.succeeded": "CONFIRMED",
            "payment.canceled": "CANCELED",
            "refund.succeeded": "CHARGEBACKED",
        }
        return mapping.get(event, event.upper())
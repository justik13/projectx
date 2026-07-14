import aiohttp
import logging
from typing import Optional, Dict
from aiohttp import ClientTimeout
from config.settings import get_settings
from services.amnezia_client import get_http_session

logger = logging.getLogger(__name__)

# 🔥 ИСПРАВЛЕНО: Явный ClientTimeout для aiohttp 3.14+ (убирает deprecation warning)
PLATEGA_TIMEOUT = ClientTimeout(total=30, connect=10)


class PlategaClient:
    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.PLATEGA_BASE_URL.rstrip("/")
        self.merchant_id = self.settings.PLATEGA_MERCHANT_ID
        self.secret = self.settings.PLATEGA_SECRET
        self.payment_method = self.settings.PLATEGA_PAYMENT_METHOD

    def _get_headers(self) -> Dict[str, str]:
        return {
            "X-MerchantId": self.merchant_id,
            "X-Secret": self.secret,
            "Content-Type": "application/json"
        }

    async def create_transaction(
        self,
        amount: float,
        currency: str = "RUB",
        description: str = "",
        return_url: str = "",
        failed_url: str = "",
        payload: str = ""
    ) -> Optional[Dict]:
        url = f"{self.base_url}/transaction/process"
        data = {
            "paymentMethod": self.payment_method,
            "paymentDetails": {
                "amount": amount,
                "currency": currency
            },
            "description": description,
            "return": return_url,
            "failedUrl": failed_url,
            "payload": payload
        }
        
        try:
            session = await get_http_session()
            async with session.post(
                url, json=data, headers=self._get_headers(), timeout=PLATEGA_TIMEOUT
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"Platega transaction created: {result.get('transactionId')}")
                    return result
                else:
                    error_text = await response.text()
                    logger.error(f"Platega API error {response.status}: {error_text}")
                    return None
        except Exception as e:
            logger.error(f"Platega create_transaction failed: {e}", exc_info=True)
            return None

    async def check_status(self, transaction_id: str) -> Optional[Dict]:
        url = f"{self.base_url}/transaction/{transaction_id}"
        try:
            session = await get_http_session()
            async with session.get(
                url, headers=self._get_headers(), timeout=PLATEGA_TIMEOUT
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    # 🔥 ИСПРАВЛЕНО: Нормализация status через .upper()
                    if "status" in result and isinstance(result["status"], str):
                        result["status"] = result["status"].upper()
                    return result
                elif response.status == 404:
                    logger.warning(f"Platega transaction {transaction_id} not found")
                    return None
                else:
                    error_text = await response.text()
                    logger.error(f"Platega status check error {response.status}: {error_text}")
                    return None
        except Exception as e:
            logger.error(f"Platega check_status failed: {e}", exc_info=True)
            return None

    def validate_callback(self, merchant_id: str, secret: str) -> bool:
        return merchant_id == self.merchant_id and secret == self.secret
import hmac
import logging
from typing import Optional, Dict

from aiohttp import ClientTimeout

from config.settings import get_settings
from services.amnezia_client import get_http_session

logger = logging.getLogger(__name__)

PLATEGA_TIMEOUT = ClientTimeout(total=30, connect=10)


def normalize_provider_status(status) -> str:
    """
    Нормализует статус платёжного провайдера к внутреннему формату.

    Platega обычно использует:
    - CONFIRMED
    - CANCELED
    - CHARGEBACKED

    Но на практике могут встретиться синонимы, поэтому безопасно
    приводим их к ожидаемому виду.
    """
    if status is None:
        return ""

    status = str(status).upper()

    mapping = {
        "SUCCESS": "CONFIRMED",
        "PAID": "CONFIRMED",
        "COMPLETED": "CONFIRMED",
        "CANCELLED": "CANCELED",
        "EXPIRED": "CANCELED",
        "FAILED": "CANCELED",
        "REFUND": "CHARGEBACKED",
        "REFUNDED": "CHARGEBACKED",
        "CHARGEBACK": "CHARGEBACKED",
    }

    return mapping.get(status, status)


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
            "Content-Type": "application/json",
        }

    async def create_transaction(
        self,
        amount: float,
        currency: str = "RUB",
        description: str = "",
        return_url: str = "",
        failed_url: str = "",
        payload: str = "",
    ) -> Optional[Dict]:
        url = f"{self.base_url}/transaction/process"

        data = {
            "paymentMethod": self.payment_method,
            "paymentDetails": {
                "amount": amount,
                "currency": currency,
            },
            "description": description,
            "return": return_url,
            "failedUrl": failed_url,
            "payload": payload,
        }

        try:
            session = await get_http_session()

            async with session.post(
                url,
                json=data,
                headers=self._get_headers(),
                timeout=PLATEGA_TIMEOUT,
            ) as response:
                if response.status == 200:
                    result = await response.json()

                    logger.info(
                        "Platega transaction created: %s",
                        result.get("transactionId"),
                    )

                    return result

                error_text = await response.text()

                logger.error(
                    "Platega API error %s: %s",
                    response.status,
                    error_text,
                )

                return None

        except Exception as e:
            logger.error(
                "Platega create_transaction failed: %s",
                e,
                exc_info=True,
            )
            return None

    async def check_status(self, transaction_id: str) -> Optional[Dict]:
        url = f"{self.base_url}/transaction/{transaction_id}"

        try:
            session = await get_http_session()

            async with session.get(
                url,
                headers=self._get_headers(),
                timeout=PLATEGA_TIMEOUT,
            ) as response:
                if response.status == 200:
                    result = await response.json()

                    if (
                        "status" in result
                        and result["status"] is not None
                    ):
                        result["status"] = normalize_provider_status(
                            result["status"]
                        )

                    return result

                elif response.status == 404:
                    logger.warning(
                        "Platega transaction %s not found",
                        transaction_id,
                    )
                    return None

                else:
                    error_text = await response.text()

                    logger.error(
                        "Platega status check error %s: %s",
                        response.status,
                        error_text,
                    )

                    return None

        except Exception as e:
            logger.error(
                "Platega check_status failed: %s",
                e,
                exc_info=True,
            )
            return None

    def validate_callback(self, merchant_id: str, secret: str) -> bool:
        merchant_ok = hmac.compare_digest(
            merchant_id.encode("utf-8"),
            self.merchant_id.encode("utf-8"),
        )

        secret_ok = hmac.compare_digest(
            secret.encode("utf-8"),
            self.secret.encode("utf-8"),
        )

        return merchant_ok and secret_ok
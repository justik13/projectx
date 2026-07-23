from aiogram import Router

from .yookassa_routes import router as yookassa_router
from .showcase_routes import router as showcase_router

router = Router()
router.include_router(showcase_router)
router.include_router(yookassa_router)

__all__ = [
    "router",
]
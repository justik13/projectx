from aiogram import Router

from .sbp_routes import router as sbp_router
from .showcase_routes import router as showcase_router

router = Router()
router.include_router(showcase_router)
router.include_router(sbp_router)

__all__ = [
    "router",
]
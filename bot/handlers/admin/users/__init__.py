from aiogram import Router

from .ban_routes import router as ban_router
from .device_routes import router as device_router
from .list_routes import router as list_router
from .manual_grant_routes import router as manual_grant_router
from .subscription_change_routes import router as subscription_change_router
from .subscription_extend_routes import router as subscription_extend_router
from .subscription_grant_routes import router as subscription_grant_router
from .subscription_menu_routes import router as subscription_menu_router
from .subscription_reduce_routes import router as subscription_reduce_router

router = Router()

router.include_router(list_router)
router.include_router(subscription_menu_router)
router.include_router(subscription_change_router)
router.include_router(subscription_extend_router)
router.include_router(subscription_reduce_router)
router.include_router(subscription_grant_router)
router.include_router(device_router)
router.include_router(ban_router)
router.include_router(manual_grant_router)

__all__ = [
    "router",
]
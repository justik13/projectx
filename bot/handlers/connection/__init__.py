from aiogram import Router

from .device_create_routes import router as device_create_router
from .device_delete_routes import router as device_delete_router
from .device_rename_routes import router as device_rename_router
from .device_view_routes import router as device_view_router
from .list_routes import router as list_router

router = Router()

router.include_router(list_router)
router.include_router(device_view_router)
router.include_router(device_rename_router)
router.include_router(device_delete_router)
router.include_router(device_create_router)

__all__ = [
    "router",
]
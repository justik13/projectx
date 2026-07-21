from aiogram import Router

from .add_routes import router as add_router
from .card_routes import router as card_router
from .delete_routes import router as delete_router
from .edit_routes import router as edit_router
from .list_routes import router as list_router

router = Router()

router.include_router(list_router)
router.include_router(add_router)
router.include_router(card_router)
router.include_router(edit_router)
router.include_router(delete_router)

__all__ = [
    "router",
]
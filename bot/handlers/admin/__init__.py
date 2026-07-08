from .dashboard import router as dashboard_router
from .users import router as users_router
from .servers import router as servers_router
from .tariffs import router as tariffs_router
from .broadcast import router as broadcast_router

__all__ = [
    "dashboard_router",
    "users_router",
    "servers_router",
    "tariffs_router",
    "broadcast_router"
]
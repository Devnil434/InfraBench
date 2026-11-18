"""API Routers package."""

from app.routers.health import router as health_router
from app.routers.metrics import router as metrics_router
from app.routers.orders import router as orders_router

__all__ = ["health_router", "metrics_router", "orders_router"]

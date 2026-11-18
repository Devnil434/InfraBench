"""FastAPI application entrypoint for InfraBench."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import engine, Base, close_db_engine
from app.redis_client import get_redis_client, close_redis_client
from app.routers import health_router, metrics_router, orders_router
from app.telemetry import TelemetryMiddleware

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("infrabench")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan manager handling graceful startup and shutdown."""
    logger.info("Initializing InfraBench services...")

    # Startup: Ensure tables exist
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database schema verified.")
    except Exception as exc:
        logger.warning(f"Could not connect to database on startup (will retry on requests): {exc}")

    # Startup: Verify Redis connectivity
    try:
        redis_client = get_redis_client()
        await redis_client.ping()
        logger.info("Redis cache connection verified.")
    except Exception as exc:
        logger.warning(f"Could not connect to Redis on startup (will retry on requests): {exc}")

    logger.info("InfraBench application started successfully.")
    yield

    # Graceful Shutdown
    logger.info("Gracefully shutting down InfraBench services...")
    await close_redis_client()
    await close_db_engine()
    logger.info("InfraBench shutdown complete.")


app = FastAPI(
    title=settings.app_name,
    description="Autonomous SRE Benchmark Lab - Target Infrastructure",
    version="0.1.0",
    lifespan=lifespan,
)

# Telemetry middleware for latency, traffic volume, and error-rate monitoring
app.add_middleware(TelemetryMiddleware)

# Register routers
app.include_router(health_router)
app.include_router(metrics_router)
app.include_router(orders_router)


@app.get("/", tags=["Root"])
async def root() -> JSONResponse:
    """Root endpoint with quick API navigation."""
    return JSONResponse(
        content={
            "app": settings.app_name,
            "version": "0.1.0",
            "environment": settings.app_env,
            "endpoints": {
                "health": "/health",
                "db_health": "/db/health",
                "redis_health": "/redis/health",
                "metrics": "/metrics",
                "orders": "/orders",
                "docs": "/docs",
            },
        }
    )

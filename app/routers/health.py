"""Health check endpoints for application, PostgreSQL, and Redis."""

import datetime
from fastapi import APIRouter, Response, status

from app.database import check_db_health
from app.redis_client import check_redis_health
from app.schemas.health import HealthResponse, ServiceDetail, SingleServiceHealth
from app.telemetry import telemetry_collector

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Application and dependencies health check",
    description="Returns aggregate health status of application and its dependencies (DB, Redis).",
)
async def get_health(response: Response) -> HealthResponse:
    """Check overall application health and dependent services."""
    db_healthy, db_latency, db_error = await check_db_health()
    redis_healthy, redis_latency, redis_error = await check_redis_health()

    services = {
        "postgresql": ServiceDetail(
            status="UP" if db_healthy else "DOWN",
            latency_ms=db_latency,
            error=db_error,
        ),
        "redis": ServiceDetail(
            status="UP" if redis_healthy else "DOWN",
            latency_ms=redis_latency,
            error=redis_error,
        ),
    }

    if db_healthy and redis_healthy:
        overall_status = "UP"
        response.status_code = status.HTTP_200_OK
    elif db_healthy or redis_healthy:
        overall_status = "DEGRADED"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        overall_status = "DOWN"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status=overall_status,
        uptime_seconds=telemetry_collector.uptime_seconds,
        timestamp=datetime.datetime.utcnow().isoformat(),
        services=services,
    )


@router.get(
    "/db/health",
    response_model=SingleServiceHealth,
    summary="PostgreSQL database health check",
    description="Probes PostgreSQL connection and executes a test query.",
)
async def get_db_health(response: Response) -> SingleServiceHealth:
    """Dedicated health probe for PostgreSQL."""
    is_healthy, latency_ms, error = await check_db_health()

    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return SingleServiceHealth(
        service="postgresql",
        status="UP" if is_healthy else "DOWN",
        latency_ms=latency_ms,
        error=error,
    )


@router.get(
    "/redis/health",
    response_model=SingleServiceHealth,
    summary="Redis cache health check",
    description="Probes Redis connectivity via PING.",
)
async def get_redis_health(response: Response) -> SingleServiceHealth:
    """Dedicated health probe for Redis cache."""
    is_healthy, latency_ms, error = await check_redis_health()

    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return SingleServiceHealth(
        service="redis",
        status="UP" if is_healthy else "DOWN",
        latency_ms=latency_ms,
        error=error,
    )

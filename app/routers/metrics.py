"""Metrics and telemetry endpoint."""

from fastapi import APIRouter
from app.schemas.metrics import MetricsResponse
from app.telemetry import telemetry_collector

router = APIRouter(tags=["Telemetry"])


@router.get(
    "/metrics",
    response_model=MetricsResponse,
    summary="Infrastructure and application metrics",
    description="Returns structured JSON metrics including CPU, memory, request volume, latency percentiles, and error rate.",
)
async def get_metrics() -> MetricsResponse:
    """Retrieve current system and application metrics snapshot."""
    return telemetry_collector.get_metrics_snapshot()

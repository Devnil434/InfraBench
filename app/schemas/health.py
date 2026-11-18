"""Health check response schemas."""

from typing import Dict, Optional
from pydantic import BaseModel, Field


class ServiceDetail(BaseModel):
    """Health details of an individual dependency."""

    status: str = Field(..., description="Service status: 'UP' or 'DOWN'")
    latency_ms: float = Field(..., description="Probe response latency in milliseconds")
    error: Optional[str] = Field(None, description="Error message if unhealthy")


class SingleServiceHealth(BaseModel):
    """Response schema for dedicated single-service health checks."""

    service: str = Field(..., description="Name of the service (e.g. 'postgresql', 'redis')")
    status: str = Field(..., description="Status: 'UP' or 'DOWN'")
    latency_ms: float = Field(..., description="Probe latency in milliseconds")
    error: Optional[str] = Field(None, description="Error message if unhealthy")


class HealthResponse(BaseModel):
    """Response schema for overall system health check."""

    status: str = Field(..., description="Overall status: 'UP', 'DEGRADED', or 'DOWN'")
    uptime_seconds: float = Field(..., description="Application uptime in seconds")
    timestamp: str = Field(..., description="ISO 8601 timestamp of probe")
    services: Dict[str, ServiceDetail] = Field(
        ..., description="Health status of individual dependent services"
    )

"""Metrics and telemetry response schemas."""

from pydantic import BaseModel, Field


class SystemMetrics(BaseModel):
    """Host/Container system telemetry."""

    cpu_percent: float = Field(..., description="CPU utilization percentage")
    cpu_count: int = Field(..., description="Number of logical CPU cores")
    memory_used_mb: float = Field(..., description="Memory used in megabytes")
    memory_total_mb: float = Field(..., description="Total system memory in megabytes")
    memory_percent: float = Field(..., description="Memory utilization percentage")


class RequestMetrics(BaseModel):
    """HTTP traffic and error-rate telemetry."""

    total: int = Field(..., description="Total HTTP requests processed")
    active: int = Field(..., description="Currently active HTTP requests")
    status_2xx: int = Field(..., description="Count of 2xx Success responses")
    status_3xx: int = Field(..., description="Count of 3xx Redirection responses")
    status_4xx: int = Field(..., description="Count of 4xx Client Error responses")
    status_5xx: int = Field(..., description="Count of 5xx Server Error responses")
    error_rate_percent: float = Field(
        ..., description="Percentage of requests resulting in 4xx or 5xx errors"
    )


class LatencyMetrics(BaseModel):
    """Response latency metrics in milliseconds."""

    avg: float = Field(..., description="Average latency in ms")
    p50: float = Field(..., description="50th percentile (median) latency in ms")
    p95: float = Field(..., description="95th percentile latency in ms")
    p99: float = Field(..., description="99th percentile latency in ms")
    min: float = Field(..., description="Minimum recorded latency in ms")
    max: float = Field(..., description="Maximum recorded latency in ms")


class MetricsResponse(BaseModel):
    """Structured telemetry response."""

    app_name: str = Field(..., description="Application name")
    environment: str = Field(..., description="Environment (development, production, etc.)")
    uptime_seconds: float = Field(..., description="Process uptime in seconds")
    timestamp: str = Field(..., description="ISO 8601 timestamp of metrics reading")
    system: SystemMetrics = Field(..., description="System CPU and Memory metrics")
    http_requests: RequestMetrics = Field(..., description="Traffic volume and error telemetry")
    latency_ms: LatencyMetrics = Field(..., description="Latency distribution in milliseconds")

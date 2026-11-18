"""Integration tests for InfraBench end-to-end services."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root_endpoint_navigation(client: AsyncClient):
    """Test that the root endpoint returns correct API navigation map."""
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["app"] == "InfraBench"
    assert data["endpoints"]["health"] == "/health"
    assert data["endpoints"]["db_health"] == "/db/health"
    assert data["endpoints"]["redis_health"] == "/redis/health"
    assert data["endpoints"]["metrics"] == "/metrics"
    assert data["endpoints"]["orders"] == "/orders"


@pytest.mark.asyncio
async def test_metrics_telemetry_integration(client: AsyncClient):
    """Test end-to-end telemetry reporting across multiple varied endpoints."""
    # Issue requests across different routes
    await client.get("/health")
    await client.get("/invalid-route-1")
    await client.get("/invalid-route-2")

    metrics_resp = await client.get("/metrics")
    assert metrics_resp.status_code == 200
    metrics = metrics_resp.json()

    # Total completed prior to this snapshot was 3 (/health, /invalid-route-1, /invalid-route-2)
    assert metrics["http_requests"]["total"] >= 3
    assert metrics["http_requests"]["status_4xx"] >= 2
    assert metrics["http_requests"]["error_rate_percent"] > 0
    assert metrics["latency_ms"]["avg"] > 0

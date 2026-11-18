"""Tests for metrics and telemetry monitoring."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_metrics_structure(client: AsyncClient):
    """Test that /metrics returns valid structured JSON matching schema."""
    response = await client.get("/metrics")
    assert response.status_code == 200
    data = response.json()

    # High-level attributes
    assert data["app_name"] == "InfraBench"
    assert "environment" in data
    assert "uptime_seconds" in data
    assert "timestamp" in data

    # System metrics
    system = data["system"]
    assert "cpu_percent" in system
    assert system["cpu_count"] >= 1
    assert system["memory_used_mb"] > 0
    assert system["memory_total_mb"] > 0
    assert 0 <= system["memory_percent"] <= 100

    # Request metrics
    http_reqs = data["http_requests"]
    assert "total" in http_reqs
    assert "active" in http_reqs
    assert "status_2xx" in http_reqs
    assert "status_3xx" in http_reqs
    assert "status_4xx" in http_reqs
    assert "status_5xx" in http_reqs
    assert "error_rate_percent" in http_reqs

    # Latency metrics
    latency = data["latency_ms"]
    assert "avg" in latency
    assert "p50" in latency
    assert "p95" in latency
    assert "p99" in latency
    assert "min" in latency
    assert "max" in latency


@pytest.mark.asyncio
async def test_metrics_telemetry_traffic_tracking(client: AsyncClient):
    """Test that requests update telemetry counts, latencies, and error rates."""
    # 1. Hit root endpoint (200 OK)
    resp1 = await client.get("/")
    assert resp1.status_code == 200

    # 2. Hit an invalid endpoint (404 Not Found)
    resp2 = await client.get("/invalid-endpoint-for-testing")
    assert resp2.status_code == 404

    # 3. Check /metrics
    resp3 = await client.get("/metrics")
    assert resp3.status_code == 200
    metrics = resp3.json()

    http_reqs = metrics["http_requests"]
    # Total completed prior to this snapshot was 2 (/ and /invalid-endpoint-for-testing)
    assert http_reqs["total"] >= 2
    assert http_reqs["status_2xx"] >= 1
    assert http_reqs["status_4xx"] >= 1
    # Error rate should be positive because of 404
    assert http_reqs["error_rate_percent"] > 0.0

    # Latencies should have been recorded
    latency = metrics["latency_ms"]
    assert latency["avg"] > 0.0
    assert latency["max"] >= latency["min"]

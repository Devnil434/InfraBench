"""Tests for health check endpoints."""

from unittest.mock import patch
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_all_up(client: AsyncClient):
    """Test /health when all dependent services are healthy."""
    with patch("app.routers.health.check_db_health", return_value=(True, 2.5, None)), \
         patch("app.routers.health.check_redis_health", return_value=(True, 1.2, None)):
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "UP"
        assert "uptime_seconds" in data
        assert "timestamp" in data
        assert data["services"]["postgresql"]["status"] == "UP"
        assert data["services"]["postgresql"]["latency_ms"] == 2.5
        assert data["services"]["redis"]["status"] == "UP"
        assert data["services"]["redis"]["latency_ms"] == 1.2


@pytest.mark.asyncio
async def test_health_db_down(client: AsyncClient):
    """Test /health when PostgreSQL database is down."""
    with patch("app.routers.health.check_db_health", return_value=(False, 10.0, "Connection refused")), \
         patch("app.routers.health.check_redis_health", return_value=(True, 1.0, None)):
        response = await client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "DEGRADED"
        assert data["services"]["postgresql"]["status"] == "DOWN"
        assert "Connection refused" in data["services"]["postgresql"]["error"]
        assert data["services"]["redis"]["status"] == "UP"


@pytest.mark.asyncio
async def test_health_redis_down(client: AsyncClient):
    """Test /health when Redis cache is down."""
    with patch("app.routers.health.check_db_health", return_value=(True, 2.0, None)), \
         patch("app.routers.health.check_redis_health", return_value=(False, 8.5, "Redis timeout")):
        response = await client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "DEGRADED"
        assert data["services"]["postgresql"]["status"] == "UP"
        assert data["services"]["redis"]["status"] == "DOWN"
        assert "Redis timeout" in data["services"]["redis"]["error"]


@pytest.mark.asyncio
async def test_health_both_down(client: AsyncClient):
    """Test /health when both PostgreSQL and Redis are down."""
    with patch("app.routers.health.check_db_health", return_value=(False, 15.0, "DB error")), \
         patch("app.routers.health.check_redis_health", return_value=(False, 15.0, "Redis error")):
        response = await client.get("/health")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "DOWN"
        assert data["services"]["postgresql"]["status"] == "DOWN"
        assert data["services"]["redis"]["status"] == "DOWN"


@pytest.mark.asyncio
async def test_db_health_endpoint_healthy(client: AsyncClient):
    """Test /db/health when database is healthy."""
    with patch("app.routers.health.check_db_health", return_value=(True, 3.1, None)):
        response = await client.get("/db/health")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "postgresql"
        assert data["status"] == "UP"
        assert data["latency_ms"] == 3.1
        assert data["error"] is None


@pytest.mark.asyncio
async def test_db_health_endpoint_unhealthy(client: AsyncClient):
    """Test /db/health when database is unreachable."""
    with patch("app.routers.health.check_db_health", return_value=(False, 50.0, "Connection pool exhausted")):
        response = await client.get("/db/health")
        assert response.status_code == 503
        data = response.json()
        assert data["service"] == "postgresql"
        assert data["status"] == "DOWN"
        assert data["latency_ms"] == 50.0
        assert "Connection pool exhausted" in data["error"]


@pytest.mark.asyncio
async def test_redis_health_endpoint_healthy(client: AsyncClient):
    """Test /redis/health when Redis is healthy."""
    with patch("app.routers.health.check_redis_health", return_value=(True, 0.9, None)):
        response = await client.get("/redis/health")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "redis"
        assert data["status"] == "UP"
        assert data["latency_ms"] == 0.9
        assert data["error"] is None


@pytest.mark.asyncio
async def test_redis_health_endpoint_unhealthy(client: AsyncClient):
    """Test /redis/health when Redis is unreachable."""
    with patch("app.routers.health.check_redis_health", return_value=(False, 30.0, "Connection timed out")):
        response = await client.get("/redis/health")
        assert response.status_code == 503
        data = response.json()
        assert data["service"] == "redis"
        assert data["status"] == "DOWN"
        assert data["latency_ms"] == 30.0
        assert "Connection timed out" in data["error"]

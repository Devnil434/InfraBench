"""Pytest configuration and shared fixtures for InfraBench."""

from typing import AsyncGenerator
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.telemetry import telemetry_collector


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Yield an async test client configured for the FastAPI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture(autouse=True)
def reset_telemetry():
    """Reset telemetry metrics before each test run if needed."""
    telemetry_collector.latencies.clear()
    telemetry_collector.total_requests = 0
    telemetry_collector.active_requests = 0
    telemetry_collector.status_2xx = 0
    telemetry_collector.status_3xx = 0
    telemetry_collector.status_4xx = 0
    telemetry_collector.status_5xx = 0
    yield
    app.dependency_overrides.clear()

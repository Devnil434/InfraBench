"""Tests for orders endpoint and Redis caching layer."""

from datetime import datetime
import json
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from httpx import AsyncClient

from app.database import get_db
from app.main import app
from app.models.order import Order


class MockOrder:
    def __init__(self, id, customer_name, item_count, total_amount, status, created_at):
        self.id = id
        self.customer_name = customer_name
        self.item_count = item_count
        self.total_amount = total_amount
        self.status = status
        self.created_at = created_at

    def to_dict(self):
        return {
            "id": self.id,
            "customer_name": self.customer_name,
            "item_count": self.item_count,
            "total_amount": round(self.total_amount, 2),
            "status": self.status,
            "created_at": "2026-11-18T10:00:00",
        }


@pytest.mark.asyncio
async def test_get_orders_cache_miss_then_hit(client: AsyncClient):
    """Test cache-aside pattern: first call is a miss (queries DB and caches), second call is a hit."""
    fake_orders = [
        MockOrder(1, "Alice", 2, 50.0, "COMPLETED", None),
        MockOrder(2, "Bob", 1, 25.0, "PENDING", None),
    ]

    mock_redis = AsyncMock()
    # 1st call: redis.get returns None (cache miss)
    # 2nd call: redis.get returns json string (cache hit)
    mock_redis.get = AsyncMock(side_effect=[
        None,
        json.dumps([o.to_dict() for o in fake_orders]),
    ])
    mock_redis.set = AsyncMock()

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = fake_orders
    mock_db.execute = AsyncMock(return_value=mock_result)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.routers.orders.get_redis_client", return_value=mock_redis):
        # First request: cache miss
        resp1 = await client.get("/orders")
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["cached"] is False
        assert data1["total"] == 2
        assert len(data1["orders"]) == 2
        assert data1["orders"][0]["customer_name"] == "Alice"
        mock_redis.set.assert_awaited_once()

        # Second request: cache hit
        resp2 = await client.get("/orders")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["cached"] is True
        assert data2["total"] == 2
        assert len(data2["orders"]) == 2


@pytest.mark.asyncio
async def test_get_orders_graceful_on_redis_failure(client: AsyncClient):
    """Test that GET /orders degrades gracefully if Redis fails."""
    fake_orders = [MockOrder(1, "Charlie", 3, 100.0, "COMPLETED", None)]

    mock_redis = AsyncMock()
    mock_redis.get.side_effect = Exception("Redis connection refused")
    mock_redis.set.side_effect = Exception("Redis connection refused")

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = fake_orders
    mock_db.execute = AsyncMock(return_value=mock_result)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.routers.orders.get_redis_client", return_value=mock_redis):
        response = await client.get("/orders")
        assert response.status_code == 200
        data = response.json()
        assert data["cached"] is False
        assert data["total"] == 1
        assert data["orders"][0]["customer_name"] == "Charlie"


@pytest.mark.asyncio
async def test_create_order_invalidates_cache(client: AsyncClient):
    """Test POST /orders creates order and invalidates Redis cache."""
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock()

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    async def fake_refresh(instance):
        instance.id = 42
        instance.created_at = datetime(2026, 11, 18, 12, 0, 0)

    mock_db.refresh = AsyncMock(side_effect=fake_refresh)

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db

    with patch("app.routers.orders.get_redis_client", return_value=mock_redis):
        payload = {
            "customer_name": "David Miller",
            "item_count": 4,
            "total_amount": 199.99,
            "status": "PENDING",
        }

        response = await client.post("/orders", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == 42
        assert data["customer_name"] == "David Miller"
        assert data["total_amount"] == 199.99

        mock_redis.delete.assert_awaited_once_with("infrabench:orders:all")

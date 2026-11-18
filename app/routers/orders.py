"""Orders API router with PostgreSQL persistence and Redis caching."""

import datetime
import json
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.order import Order
from app.redis_client import get_redis_client
from app.schemas.order import OrderCreate, OrderListResponse, OrderResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/orders", tags=["Orders"])

CACHE_KEY_ALL_ORDERS = "infrabench:orders:all"


@router.get(
    "",
    response_model=OrderListResponse,
    summary="List orders",
    description="Retrieves orders from Redis cache if available, falling back to PostgreSQL database.",
)
async def list_orders(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> OrderListResponse:
    """Retrieve orders with Redis cache-aside strategy."""
    redis_client = get_redis_client()
    cached_data = None

    # Attempt to read from Redis cache
    try:
        cached_data = await redis_client.get(CACHE_KEY_ALL_ORDERS)
    except Exception as exc:
        logger.warning(f"Failed to read orders from Redis cache: {exc}")

    if cached_data:
        try:
            orders_dicts = json.loads(cached_data)
            return OrderListResponse(
                total=len(orders_dicts),
                cached=True,
                orders=orders_dicts,
                timestamp=datetime.datetime.utcnow().isoformat(),
            )
        except Exception as exc:
            logger.warning(f"Failed to deserialize cached orders: {exc}")

    # Fallback / cache-miss: query PostgreSQL
    try:
        result = await db.execute(
            select(Order).order_by(Order.id.desc()).limit(limit)
        )
        orders = result.scalars().all()
        orders_dicts = [order.to_dict() for order in orders]
    except Exception as exc:
        logger.error(f"Failed to query orders from database: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to query database for orders.",
        )

    # Store in Redis cache asynchronously
    try:
        await redis_client.set(
            CACHE_KEY_ALL_ORDERS,
            json.dumps(orders_dicts),
            ex=settings.redis_cache_ttl_seconds,
        )
    except Exception as exc:
        logger.warning(f"Failed to set orders in Redis cache: {exc}")

    return OrderListResponse(
        total=len(orders_dicts),
        cached=False,
        orders=orders_dicts,
        timestamp=datetime.datetime.utcnow().isoformat(),
    )


@router.post(
    "",
    response_model=OrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new order",
    description="Inserts a new order into PostgreSQL and invalidates the Redis cache.",
)
async def create_order(
    order_in: OrderCreate,
    db: AsyncSession = Depends(get_db),
) -> OrderResponse:
    """Create a new order and invalidate cached list."""
    new_order = Order(
        customer_name=order_in.customer_name,
        item_count=order_in.item_count,
        total_amount=order_in.total_amount,
        status=order_in.status,
    )
    try:
        db.add(new_order)
        await db.commit()
        await db.refresh(new_order)
    except Exception as exc:
        await db.rollback()
        logger.error(f"Failed to create order: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create order in database.",
        )

    # Invalidate cache
    try:
        redis_client = get_redis_client()
        await redis_client.delete(CACHE_KEY_ALL_ORDERS)
    except Exception as exc:
        logger.warning(f"Failed to invalidate Redis cache: {exc}")

    return OrderResponse.model_validate(new_order.to_dict())

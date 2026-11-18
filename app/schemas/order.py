"""Order schemas for requests and responses."""

from datetime import datetime
from typing import List
from pydantic import BaseModel, ConfigDict, Field


class OrderBase(BaseModel):
    """Base order attributes."""

    customer_name: str = Field(..., min_length=1, max_length=100, json_schema_extra={"example": "Alice Smith"})
    item_count: int = Field(default=1, ge=1, json_schema_extra={"example": 3})
    total_amount: float = Field(..., gt=0, json_schema_extra={"example": 99.95})
    status: str = Field(default="PENDING", max_length=50, json_schema_extra={"example": "PENDING"})


class OrderCreate(OrderBase):
    """Schema for creating a new order."""

    pass


class OrderResponse(OrderBase):
    """Schema for order response."""

    id: int = Field(..., json_schema_extra={"example": 1})
    created_at: datetime = Field(..., json_schema_extra={"example": "2026-11-18T10:00:00"})

    model_config = ConfigDict(from_attributes=True)


class OrderListResponse(BaseModel):
    """Schema for listing orders with caching metadata."""

    total: int = Field(..., description="Number of orders returned")
    cached: bool = Field(..., description="Whether the response was served from Redis cache")
    orders: List[OrderResponse] = Field(..., description="List of orders")
    timestamp: str = Field(..., description="Timestamp of retrieval")

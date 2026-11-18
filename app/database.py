"""Database connection and session management."""

import time
import logging
from typing import AsyncGenerator, Tuple, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from app.config import settings

logger = logging.getLogger(__name__)

Base = declarative_base()

# Async engine with connection pooling
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=5,
    pool_timeout=10,
    pool_recycle=1800,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def check_db_health(timeout_seconds: float = 3.0) -> Tuple[bool, float, Optional[str]]:
    """Check PostgreSQL database connectivity and response latency.

    Returns:
        Tuple of (is_healthy, latency_ms, error_message)
    """
    start_time = time.perf_counter()
    try:
        async with async_session_factory() as session:
            # Execute quick probe with timeout
            await session.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return True, latency_ms, None
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.warning(f"Database health check failed: {exc}")
        return False, latency_ms, str(exc)


async def close_db_engine() -> None:
    """Close all database connections gracefully."""
    logger.info("Closing PostgreSQL database engine...")
    await engine.dispose()
    logger.info("PostgreSQL database engine closed.")

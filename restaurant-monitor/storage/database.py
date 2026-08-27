"""
storage/database.py
--------------------
Async SQLAlchemy engine/session management.

DB target is controlled by the DATABASE_URL env var:
  - Production/dev default: postgresql+asyncpg://... (Postgres, per spec)
  - Test suite: sqlite+aiosqlite:///:memory: (set by tests/conftest.py)

No Postgres credentials are assumed or hardcoded — see docs/DB_SCHEMA.md and
docs/SESSION_REPORT.md for why: this environment's local Postgres service
exists but this session was not given credentials for it, and guessing
system-service credentials is out of scope. A real deployment sets
DATABASE_URL in .env (see .env.example).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from storage.models import Base

DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://restaurant_monitor:restaurant_monitor@localhost:5432/restaurant_monitor"
)


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        url = get_database_url()
        if url.startswith("sqlite"):
            # sqlite needs a shared in-memory pool to persist across
            # connections within a single test process.
            from sqlalchemy.pool import StaticPool

            _engine = create_async_engine(
                url, connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
        else:
            _engine = create_async_engine(url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


async def init_db() -> None:
    """Create all tables if they don't exist. Idempotent."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Usage: async with session_scope() as session: ..."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency."""
    factory = get_session_factory()
    async with factory() as session:
        yield session

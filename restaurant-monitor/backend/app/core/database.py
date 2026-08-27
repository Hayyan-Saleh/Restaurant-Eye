# Database connection initialization (PostgreSQL / Async SQLAlchemy)
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.core.config import settings

# Create Async engine
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,  # Set to True if you want to print SQL queries in Terminal
    future=True
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Unified Base Class for all Models
class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncGenerator:
    """Dependency to pass database session to Endpoints"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
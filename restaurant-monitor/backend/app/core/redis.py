
# Redis connection management (Pub/Sub & Fast State Store)

import redis.asyncio as aioredis
from app.core.config import settings
from typing import AsyncGenerator
import redis.asyncio as redis
from app.core.config import settings


# Get Redis URL from settings or use default
REDIS_URL = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")

# Create direct Redis connection object
redis_client = aioredis.from_url(
    REDIS_URL,
    encoding="utf-8",
    decode_responses=True
)




# redis.asyncio.Redis() does not connect eagerly -- constructing this
# client never touches the network, so an unreachable Redis cannot fail
# app startup. The first real connection attempt happens on first command.
redis_client: redis.Redis = redis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    decode_responses=True,
    socket_timeout=5.0,
    socket_connect_timeout=5.0,
)


async def get_redis_client() -> AsyncGenerator[redis.Redis, None]:
    """Dependency to pass Redis client to Endpoints, same call shape as get_db()."""
    yield redis_client


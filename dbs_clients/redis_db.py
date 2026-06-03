import asyncio
import os
import logging
import redis.asyncio as redis
from redis.asyncio.client import Redis
from typing import cast

logger = logging.getLogger(__name__)

# Example: redis://:your_strong_password@redis:6379/0
redis_url = f'redis://:{os.getenv("REDIS_PASSWORD", None)}@{os.getenv("REDIS_HOST", "localhost")}:{os.getenv("REDIS_PORT", 6379)}/{os.getenv("REDIS_DB", 0)}'

# Create the pool directly from the URL
pool = redis.ConnectionPool.from_url(
    redis_url,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    retry_on_timeout=True,
    health_check_interval=30,
)


redis_client: Redis = cast(Redis, redis.Redis(connection_pool=pool))

async def check_redis_connection():
    """Test connection to Redis and log if it fails."""
    try:
        pong = await redis_client.ping() # type: ignore[reportGeneralTypeIssues]
        if pong:
            logging.info("Connected to Redis successfully.")
    except Exception:
        logging.exception(f"Failed to connect to Redis")
        raise  # Re-raise so the app fails fast instead of silently continuing

# Example use
if __name__ == "__main__":
    asyncio.run(check_redis_connection())
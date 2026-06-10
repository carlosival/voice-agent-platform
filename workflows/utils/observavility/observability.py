import os
import logging
from typing import Optional
from httpx import AsyncClient
from langfuse import Langfuse

logger = logging.getLogger(__name__)

_tracer: Optional[Langfuse] = None


def get_tracer(httpx_client: Optional[AsyncClient] = None) -> Optional[Langfuse]:
    global _tracer

    if _tracer is not None:
        return _tracer

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST")

    if not all([public_key, secret_key, host]):
        logger.warning("Langfuse env vars not set, tracing disabled")
        return None

    try:
        _tracer = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=host,
            httpx_client=httpx_client,
        )
        logger.info("Langfuse tracer initialized")
    except Exception as e:
        logger.error(f"Failed to initialize Langfuse tracer: {e}")
        return None

    return _tracer
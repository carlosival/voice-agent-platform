import asyncio
import os
import sys
import math
import logging

# Ensure absolute imports work when running as a script
# Inside container, /app is in PYTHONPATH, so gateway.worker_router works.
# But for simplicity in this script:
sys.path.append("/app")

from dbs_clients import redis_client
from gateway.worker_router.worker_router import (
    _haversine_km,
    _obtener_region_cercana,
    distance_to_region,
    sort_streams_by_distance,
    _resolver_region_por_ip,
    resolve_stream_key,
    AgentConfig,
    StreamInfo,
    NoCapacityError
)


import gateway.worker_router.worker_router as worker_router

# ── 1. Override constants for isolated testing ────────────────────────────
worker_router.STREAMS_SET_PREFIX = "test:webrtc:offers"
worker_router.CONSUMER_GROUP     = "media-workers-test"

# Configure logging to see what's happening
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_router")

async def setup_test_data():
    """Populate Redis with some test streams"""
    logger.info("Setting up test data in Redis...")
    
    tier = "standard"
    # test:webrtc:offers:standard
    streams_key_test = f"{worker_router.STREAMS_SET_PREFIX}:{tier}"
    
    # Clean up previous test data
    await redis_client.delete(streams_key_test)
    
    test_streams = [
        f"{streams_key_test}:us-west-1",
        f"{streams_key_test}:eu-west-1",
        f"{streams_key_test}:ap-southeast"
    ]
    
    for s in test_streams:
        await redis_client.sadd(streams_key_test, s)
        # Create the group 'media-workers-test' so XINFO GROUPS doesn't fail
        try:
            await redis_client.xgroup_create(s, worker_router.CONSUMER_GROUP, id="0", mkstream=True)
        except Exception:
            pass # Already exists
            
    logger.info(f"Added {len(test_streams)} streams to {streams_key_test}")

async def test_routing_logic():
    logger.info("Testing routing logic with real Redis...")
    
    agent = AgentConfig(
        agent_id="test_agent_123",
        tier="standard",
        regions=["eu-west-1"]
    )
    
    # 1. Test precise match (Preferred region)
    result_key = await resolve_stream_key(agent, "127.0.0.1", redis_client)
    logger.info(f"Result for preferred eu-west-1: {result_key}")
    assert result_key == f"{worker_router.STREAMS_SET_PREFIX}:standard:eu-west-1"

    # 2. Test fallback BLOCKED (Preferred region NOT available, global NOT allowed)
    agent.regions = ["us-east-1"] # We don't have us-east-1 in Redis
    result_key = await resolve_stream_key(agent, "127.0.0.1", redis_client)
    logger.info(f"Result for missing region (no global): {result_key}")
    assert result_key is None

    # 3. Test fallback ALLOWED (Preferred region NOT available, global IS allowed)
    agent.regions = ["us-east-1", "global"]
    result_key = await resolve_stream_key(agent, "127.0.0.1", redis_client)
    logger.info(f"Result for fallback with global: {result_key}")
    # available: us-west-1, eu-west-1, ap-southeast
    # eu-west-1 (53, -6) is closest to 127.0.0.1 (resolves to global fallback 0,0 in tests)
    assert result_key == f"{worker_router.STREAMS_SET_PREFIX}:standard:eu-west-1"

    # 4. Test No Capacity (all streams deleted)
    from gateway.worker_router.worker_router import _stream_cache
    _stream_cache.clear()
    streams_key_test = f"{worker_router.STREAMS_SET_PREFIX}:standard"
    await redis_client.delete(streams_key_test)
    try:
        await resolve_stream_key(agent, "127.0.0.1", redis_client)
        assert False, "Should have raised NoCapacityError"
    except NoCapacityError:
        logger.info("Caught expected NoCapacityError")

async def test_helpers():
    logger.info("Testing helper functions...")
    # Haversine
    dist = _haversine_km(0, 0, 0, 1) # ~111km
    assert 110 < dist < 112
    
    # Region resolution (IP-based)
    # Using a known UK IP: 81.2.69.142
    test_ip = "81.2.69.142"
    reg = _resolver_region_por_ip(test_ip)
    logger.info(f"IP resolution ({test_ip}): {reg}")
    # This should map to eu-west-1 (Ireland/UK)
    assert reg == "eu-west-1"

async def run_all():
    try:
        await test_helpers()
        await setup_test_data()
        await test_routing_logic()
        logger.info("✅ ALL REAL-DEPENDENCY TESTS PASSED!")
    finally:
        # Force disconnect the pool before closing the client
        if hasattr(redis_client, "connection_pool"):
            await redis_client.connection_pool.disconnect()
        await redis_client.aclose()
        await asyncio.sleep(0.2)

if __name__ == "__main__":
    asyncio.run(run_all())

import asyncio
import logging
import signal
import os
from dbs_clients.redis_db import redis_client
from config import TIER_QUEUES

logger = logging.getLogger(__name__)

INTAKE_STREAM = "webrtc:offers"
ROUTER_GROUP = "routers"
ROUTER_ID = os.getenv("ROUTER_ID", f"router-{uuid.uuid4()}")

DEFAULT_QUEUE = TIER_QUEUES["free.global"]


async def ensure_group(redis_client, stream, group):
    try:
        await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise



async def route_message(message_id, data, redis_client):
    session_id = data.get("session_id", "unknown")
    agent_id = data.get("agent_id", "")
    tier = data.get("tier", "")
    region = data.get("region", "")
    key = f"{tier}.{region}" if region else f"{tier}.global"

    try:
        target_queue = TIER_QUEUES[key]

        await redis_client.xadd(target_queue, data)
        await redis_client.xack(INTAKE_STREAM, ROUTER_GROUP, message_id)

        logger.info("Routed offer", extra={
            "session_id": session_id,
            "agent_id": agent_id,
            "tier": tier,
            "queue": target_queue,
        })

    except Exception as e:
        logger.error(f"Routing failed for session {session_id}: {e}")
        # Do not ack — message stays pending for reclaim


async def reclaim_abandoned(redis_client, stop_event):
    """Reclaim messages pending over 30s from dead routers."""
    while not stop_event.is_set():
        await asyncio.sleep(30)
        try:
            pending = await redis_client.xpending_range(
                INTAKE_STREAM, ROUTER_GROUP,
                min="-", max="+",
                count=100,
            )
            for entry in pending:
                if entry["time_since_delivered"] > 30000:
                    await redis_client.xclaim(
                        INTAKE_STREAM, ROUTER_GROUP, ROUTER_ID,
                        min_idle_time=30000,
                        message_ids=[entry["message_id"]],
                    )
                    logger.warning(f"Reclaimed message {entry['message_id']}")
        except Exception as e:
            logger.error(f"Reclaim error: {e}")


async def main():

    await ensure_group(redis_client, INTAKE_STREAM, ROUTER_GROUP)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    asyncio.create_task(reclaim_abandoned(redis_client, stop_event))

    logger.info(f"Router {ROUTER_ID} ready.")

    while not stop_event.is_set():
        try:
            messages = await redis_client.xreadgroup(
                ROUTER_GROUP,
                ROUTER_ID,
                {INTAKE_STREAM: ">"},
                count=50,   # router is lightweight, handle high throughput
                block=2000,
            )
        except Exception as e:
            logger.error(f"Redis read error: {e}")
            await asyncio.sleep(1)
            continue

        if not messages:
            continue

        for stream, entries in messages:
            for message_id, data in entries:
                asyncio.create_task(route_message(message_id, data, redis_client))

    logger.info("Router shutting down.")
    await redis_client.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
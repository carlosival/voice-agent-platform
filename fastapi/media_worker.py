import asyncio
import json
import logging
import signal
import uuid
import os
from dbs_clients.redis_db import redis_client
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from peer import create_peer

logger = logging.getLogger(__name__)

TIER = os.getenv("TIER", "standard")
REGION = os.getenv("REGION", "us-east-1")
WORKER_ID = os.getenv("WORKER_ID", f"worker-{uuid.uuid4()}")
STREAM_KEY = f"webrtc:offers:{TIER}:{REGION}"
GROUP_NAME = f"{TIER}-{REGION}-workers"
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_SESSIONS", 50))



active_connections = set()


async def ensure_group(redis_client, stream, group):
    try:
        await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            raise


async def process_offer(message_id, data, redis_client, semaphore):
    async with semaphore:
        session_id = data.get("session_id", "unknown")
        peer_session = create_peer()
        active_connections.add(peer_session.pc)

        try:
            offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])
            await peer_session.pc.setRemoteDescription(offer)

            for transceiver in peer_session.pc.getTransceivers():
                if transceiver.kind == "audio":
                    transceiver.direction = "sendrecv"

            answer = await peer_session.pc.createAnswer()
            await peer_session.pc.setLocalDescription(answer)

            # Publish answer back — gateway is listening on this channel
            await redis_client.publish(
                f"webrtc:answer:{session_id}",
                json.dumps({
                    "type": peer_session.pc.localDescription.type,
                    "sdp": peer_session.pc.localDescription.sdp,
                })
            )

            # Ack only on success
            await redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)

            logger.info("Offer processed", extra={
                "session_id": session_id,
                "worker_id": WORKER_ID,
                "tier": TIER,
                "active_sessions": len(active_connections),
            })

        except Exception as e:
            logger.error(f"Failed to process offer {session_id}: {e}")
            await pc.close()
            # Do not ack — stays pending for reclaim

        finally:
            active_connections.pop(session_id, None)


async def report_load(redis_client, stop_event):
    """Heartbeat + load reporting every 5 seconds."""
    while not stop_event.is_set():
        try:
            await redis_client.hset("workers:load", WORKER_ID, len(active_connections))
            await redis_client.set(f"workers:heartbeat:{WORKER_ID}", 1, ex=10)
        except Exception as e:
            logger.error(f"Load report failed: {e}")
        await asyncio.sleep(5)


async def get_batch_size() -> int:
    """Backpressure — pull fewer messages when busy."""
    load = len(active_connections)
    if load > 40:
        return 1
    if load > 20:
        return 5
    return 10


async def main():
    redis_client = Redis.from_url(os.getenv("REDIS_URL"), decode_responses=True)
    await ensure_group(redis_client, STREAM_KEY, GROUP_NAME)

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    asyncio.create_task(report_load(redis_client, stop_event))

    logger.info(f"Worker {WORKER_ID} ready — tier={TIER} queue={STREAM_KEY}")

    while not stop_event.is_set():
        try:
            batch_size = await get_batch_size()
            messages = await redis_client.xreadgroup(
                GROUP_NAME,
                WORKER_ID,
                {STREAM_KEY: ">"},
                count=batch_size,
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
                asyncio.create_task(
                    process_offer(message_id, data, redis_client, semaphore)
                )

    logger.info(f"Worker {WORKER_ID} shutting down.")
    await redis_client.aclose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
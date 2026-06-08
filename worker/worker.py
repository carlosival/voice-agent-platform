import asyncio
import json
import logging
import os
import signal
import uuid
from dataclasses import dataclass

import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from redis.asyncio import Redis

from peer.config import build_rtc_config
from peer.context import build_context
from peer.factory import create_peer
from peer.types import PeerDependencies, PeerSession
from pipelines import audio_pipeline
from worker.deps.provider import DepProvider

#This worker use channels for exchange offer/answer WebRTC
from dbs_clients import redis_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
TIER = os.getenv("TIER", "standard")
REGION = os.getenv("REGION", "eu-west")
WORKER_ID = os.getenv("WORKER_ID", f"worker-{uuid.uuid4()}")
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_SESSIONS", 50))

STREAM_KEY = f"webrtc:offers:{TIER}:{REGION}"
GROUP_NAME = f"{TIER}-{REGION}-workers"

# ── Validate required env ─────────────────────────────────────────────────────
_required = {
    "CLOUDFLARE_ACCOUNT_ID": os.getenv("CLOUDFLARE_ACCOUNT_ID"),
    "CLOUDFLARE_API_TOKEN": os.getenv("CLOUDFLARE_API_TOKEN"),
    "REDIS_URL": os.getenv("REDIS_URL"),
}
_missing = [k for k, v in _required.items() if not v]
if _missing:
    raise RuntimeError(f"Missing required env vars: {', '.join(_missing)}")


active_sessions: dict[str, PeerSession] = {}


# ── Redis helpers ─────────────────────────────────────────────────────────────
async def ensure_group(redis_client: Redis) -> None:
    try:
        await redis_client.xgroup_create(
            STREAM_KEY, GROUP_NAME, id="0", mkstream=True
        )
        logger.info(f"Consumer group created: {GROUP_NAME} on {STREAM_KEY}")
    except Exception as e:
        if "BUSYGROUP" in str(e):
            logger.info(f"Consumer group already exists: {GROUP_NAME}")
        else:
            raise


async def reclaim_abandoned(redis_client: Redis, stop_event: asyncio.Event) -> None:
    """Reclaim messages pending over 30s from dead workers."""
    while not stop_event.is_set():
        await asyncio.sleep(30)
        try:
            pending = await redis_client.xpending_range(
                STREAM_KEY, GROUP_NAME,
                min="-", max="+",
                count=100,
            )
            for entry in pending:
                if entry["time_since_delivered"] > 30000:
                    await redis_client.xclaim(
                        STREAM_KEY, GROUP_NAME, WORKER_ID,
                        min_idle_time=30000,
                        message_ids=[entry["message_id"]],
                    )
                    logger.warning(
                        f"Reclaimed abandoned message {entry['message_id']}",
                        extra={"worker_id": WORKER_ID},
                    )
        except Exception as e:
            logger.error(f"Reclaim error: {e}")


# ── Load reporting ────────────────────────────────────────────────────────────
async def report_load(redis_client: Redis, stop_event: asyncio.Event) -> None:
    """Heartbeat + load reporting every 5 seconds."""
    while not stop_event.is_set():
        try:
            load = len(active_sessions)
            await redis_client.hset("workers:load", WORKER_ID, load)
            await redis_client.set(f"workers:heartbeat:{WORKER_ID}", 1, ex=10)
            logger.debug(f"Load reported: {load} active sessions")
        except Exception as e:
            logger.error(f"Load report failed: {e}")
        await asyncio.sleep(5)


# ── Batch size backpressure ───────────────────────────────────────────────────
def get_batch_size() -> int:
    load = len(active_sessions)
    if load >= MAX_CONCURRENT - 5:
        return 1
    if load > MAX_CONCURRENT // 2:
        return 5
    return 10


# ── Offer processing ──────────────────────────────────────────────────────────
async def process_offer(
    message_id: str,
    data: dict,
    redis_client: Redis,
    semaphore: asyncio.Semaphore,
) -> None:
    async with semaphore:
        session_id = data.get("session_id", "unknown")

        logger.info(
            f"Processing offer",
            extra={
                "session_id": session_id,
                "worker_id": WORKER_ID,
                "tier": TIER,
                "region": REGION,
            }
        )

        # ── Build peer Dependencies and Context ────────────────────────────────────────────────────
        deps = DepProvider.build(session_id) 

        peer_session = await create_peer(deps)
        pc = peer_session.pc
        deps.ctx["resources"]["peer_connection"] = pc
        active_sessions[session_id] = peer_session

        try:
            # ── Apply remote offer ────────────────────────────────────────
            offer = RTCSessionDescription(
                sdp=data["sdp"],
                type=data["type"],
            )
            await pc.setRemoteDescription(offer)

            # ── Configure transceivers ────────────────────────────────────
            for transceiver in pc.getTransceivers():
                if transceiver.kind == "audio":
                    transceiver.direction = "sendrecv"
                    transceiver.sender.replaceTrack(peer_session.output_track)

            # ── Generate answer ───────────────────────────────────────────
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            # ── Wait for ICE gathering to complete ───────────────────────
            factor = 0.1
            while pc.iceGatheringState != "complete":
                if factor > 2:
                    break
                await asyncio.sleep(factor)
                factor *= 2

            # ── Publish answer back to gateway ────────────────────────────
            await redis_client.publish(
                f"webrtc:answer:{session_id}",
                json.dumps({
                    "type": pc.localDescription.type,
                    "sdp": pc.localDescription.sdp,
                })
            )

            # ── Ack only on success ───────────────────────────────────────
            await redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)

            logger.info(
                "Offer processed — answer published",
                extra={
                    "session_id": session_id,
                    "worker_id": WORKER_ID,
                }
            )

        except KeyError as e:
            logger.error(f"Malformed offer payload — missing field: {e}", extra={
                "session_id": session_id,
            })
            # Ack malformed messages — retrying won't fix them
            await redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)
            await pc.close()

        except Exception as e:
            logger.error(
                f"process_offer failed: {type(e).__name__} — {e}",
                extra={"session_id": session_id},
                exc_info=True,
            )
            # Do not ack — message stays pending for reclaim
            await pc.close()

        finally:
            active_sessions.pop(session_id, None)


# ── Startup / shutdown ────────────────────────────────────────────────────────
async def startup() -> None:
    logger.info(f"Worker {WORKER_ID} starting — tier={TIER} region={REGION}")

    await ensure_group(app_state.redis)

    # Warm ICE cache
    try:
        await build_rtc_config()
        logger.info("ICE server cache warmed.")
    except Exception as e:
        logger.warning(f"ICE cache warm failed — will retry on first session: {e}")

    logger.info(f"Worker {WORKER_ID} ready — consuming {STREAM_KEY}")


async def shutdown() -> None:
    logger.info(f"Worker {WORKER_ID} shutting down...")

    # Cancel all active sessions
    for session_id, peer_session in list(active_sessions.items()):
        logger.info(f"Closing active session: {session_id}")
        peer_session.cancel_all_tasks()
        if peer_session.pc:
            await peer_session.pc.close()

    active_sessions.clear()

    
    if redis_client:
        # Deregister from load tracking
        await redis_client.hdel("workers:load", WORKER_ID)
        await redis_client.delete(f"workers:heartbeat:{WORKER_ID}")
        await redis_client.aclose()

    logger.info(f"Worker {WORKER_ID} shutdown complete.")


# ── Main loop ─────────────────────────────────────────────────────────────────
async def main() -> None:
    await startup()

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    stop_event = asyncio.Event()

    # Signal handling
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # Background tasks
    asyncio.create_task(report_load(app_state.redis, stop_event))
    asyncio.create_task(reclaim_abandoned(app_state.redis, stop_event))

    try:
        while not stop_event.is_set():
            try:
                batch_size = get_batch_size()
                messages = await app_state.redis.xreadgroup(
                    GROUP_NAME,
                    WORKER_ID,
                    {STREAM_KEY: ">"},
                    count=batch_size,
                    block=2000,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Redis read error: {e}")
                await asyncio.sleep(1)
                continue

            if not messages:
                continue

            for stream, entries in messages:
                for message_id, data in entries:
                    asyncio.create_task(
                        process_offer(message_id, data, semaphore)
                    )

    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())
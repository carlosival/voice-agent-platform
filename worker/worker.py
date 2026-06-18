import asyncio
import json
import logging
import os
import signal
import uuid
import enum

from aiortc import RTCPeerConnection, RTCSessionDescription
from redis.asyncio import Redis


from peer.factory import create_peer
from peer.config import build_rtc_config
from peer.types import PeerSession

from deps_provider import DepProvider

#This worker use channels for exchange offer/answer WebRTC
from dbs_clients import redis_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s"
)
logger = logging.getLogger(__name__)


# ── ICE Strategy ──────────────────────────────────────────────────────────────
class IceStrategy(enum.Enum):
    GATHER_FIRST = "gather_first"
    TRICKLE      = "trickle"


# ── Config ────────────────────────────────────────────────────────────────────
ICE_STRATEGY = IceStrategy(os.getenv("ICE_STRATEGY", IceStrategy.TRICKLE.value))
TIER = os.getenv("TIER")
REGION = os.getenv("REGION")
WORKER_ID = os.getenv("WORKER_ID", f"worker-{uuid.uuid4()}")
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_SESSIONS", 50))
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")

STREAM_KEY = f"webrtc:offers:{TIER}:{REGION}"
GROUP_NAME = f"media-workers"

# ── Validate required env ─────────────────────────────────────────────────────
_required = {
    "CLOUDFLARE_ACCOUNT_ID": CLOUDFLARE_ACCOUNT_ID,
    "CLOUDFLARE_API_TOKEN": CLOUDFLARE_API_TOKEN,
    "TIER": TIER,
    "REGION": REGION,
}
_missing = [k for k, v in _required.items() if not v]
if _missing:
    raise RuntimeError(f"Missing required env vars: {', '.join(_missing)}")


active_sessions: dict[str, PeerSession] = {}


# ── Strategy implementations ──────────────────────────────────────────────────

async def _ice_gather_first(
    session_id: str,
    pc: RTCPeerConnection,
    redis_client: Redis,
) -> None:
    """Block until ICE gathering completes, then publish the answer once."""
    factor = 0.1
    while pc.iceGatheringState != "complete":
        if factor > 2:
            logger.warning(f"[{session_id}] ICE gathering timed out — sending partial SDP")
            break
        await asyncio.sleep(factor)
        factor *= 2

    await redis_client.xadd(
        f"webrtc:answer:{session_id}",
        {"payload": json.dumps({
            "type": pc.localDescription.type,
            "sdp":  pc.localDescription.sdp,
        })}
    )


async def _ice_trickle(
    session_id: str,
    pc: RTCPeerConnection,
    redis_client: Redis,
) -> None:
    """Publish the answer immediately, then exchange candidates bidirectionally."""
    await redis_client.xadd(
        f"webrtc:answer:{session_id}",
        {"payload": json.dumps({
            "type": pc.localDescription.type,
            "sdp":  pc.localDescription.sdp,
        })}
    )

    ice_done = asyncio.Event()
    try:
        await asyncio.gather(
            drain_client_ice(session_id, pc, redis_client, ice_done),
            forward_worker_ice(session_id, pc, redis_client, ice_done),
        )
    finally:
        ice_done.set()
        await redis_client.delete(f"webrtc:ice:client:{session_id}")
        await redis_client.delete(f"webrtc:ice:worker:{session_id}")


_STRATEGY_MAP = {
    IceStrategy.GATHER_FIRST: _ice_gather_first,
    IceStrategy.TRICKLE:      _ice_trickle,
}


async def run_ice_strategy(
    session_id: str,
    pc: RTCPeerConnection,
    redis_client: Redis,
) -> None:
    """Dispatch to the configured ICE strategy."""
    handler = _STRATEGY_MAP[ICE_STRATEGY]
    await handler(session_id, pc, redis_client)

# ── ICE helpers ───────────────────────────────────────────────────────────────

async def drain_client_ice(
    session_id: str,
    pc: RTCPeerConnection,
    redis_client: Redis,
    done_event: asyncio.Event,
) -> None:
    """
    Poll webrtc:ice:client:{session_id} and feed each candidate into the
    peer connection.  Stops when an end-of-candidates sentinel arrives
    (empty candidate string) or done_event is set externally.
    """
    stream = f"webrtc:ice:client:{session_id}"
    last_id = "0-0"

    while not done_event.is_set():
        try:
            async with asyncio.timeout(30):
                results = await redis_client.xread({stream: last_id}, count=10, block=5000)
        except asyncio.TimeoutError:
            logger.warning(f"[{session_id}] ICE client stream timeout — stopping drain")
            break

        if not results:
            continue

        _, messages = results[0]
        for msg_id, data in messages:
            last_id = msg_id

            candidate_str    = data.get(b"candidate",     b"").decode()
            sdp_mid          = data.get(b"sdpMid",        b"").decode()
            sdp_mline_index  = data.get(b"sdpMLineIndex", b"").decode()

            # End-of-candidates sentinel
            if candidate_str == "":
                logger.info(f"[{session_id}] Client ICE gathering complete")
                done_event.set()
                return

            try:
                # Strip the "candidate:" prefix that browsers include
                candidate_init = candidate_str.removeprefix("candidate:")
                ice = RTCIceCandidate(
                    sdpMid=sdp_mid or None,
                    sdpMLineIndex=int(sdp_mline_index) if sdp_mline_index else None,
                    candidate=candidate_init,
                )
                await pc.addIceCandidate(ice)
                logger.debug(f"[{session_id}] Added client ICE candidate: {candidate_init[:60]}…")
            except Exception as e:
                logger.warning(f"[{session_id}] Failed to add ICE candidate: {e}")


async def forward_worker_ice(
    session_id: str,
    pc: RTCPeerConnection,
    redis_client: Redis,
    done_event: asyncio.Event,
) -> None:
    """
    Subscribe to aiortc's icecandidate events and push each one onto
    webrtc:ice:worker:{session_id} so the gateway can forward them to the
    client.  Publishes the end-of-candidates sentinel when gathering is done.
    """
    stream = f"webrtc:ice:worker:{session_id}"
    ice_queue: asyncio.Queue[RTCIceCandidate | None] = asyncio.Queue()

    @pc.on("icecandidate")
    def on_ice_candidate(candidate):
        # candidate is None when gathering is complete
        ice_queue.put_nowait(candidate)

    while not done_event.is_set():
        try:
            candidate = await asyncio.wait_for(ice_queue.get(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning(f"[{session_id}] Worker ICE forward timeout")
            break

        if candidate is None:
            # End-of-candidates sentinel
            await redis_client.xadd(stream, {"candidate": "", "sdpMid": "", "sdpMLineIndex": ""})
            logger.info(f"[{session_id}] Worker ICE gathering complete — sentinel published")
            done_event.set()
            return

        await redis_client.xadd(stream, {
            "candidate":     f"candidate:{candidate.candidate}",
            "sdpMid":        candidate.sdpMid or "",
            "sdpMLineIndex": str(candidate.sdpMLineIndex) if candidate.sdpMLineIndex is not None else "",
        })
        logger.debug(f"[{session_id}] Forwarded worker ICE candidate: {candidate.candidate[:60]}…")


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
    finally:
        await redis_client.sadd( f"webrtc:offers:{TIER}", STREAM_KEY)
        


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
                "ice_strategy": ICE_STRATEGY.value,
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

            # ── Single call — strategy selected via ICE_STRATEGY env var ─────
            await run_ice_strategy(session_id, pc, redis_client)


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

    await ensure_group(redis_client)

    # Warm ICE serverscache
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
        # Leave the consumer group first so XINFO reflects reality
        try:
            await redis_client.xgroup_delconsumer(STREAM_KEY, GROUP_NAME, WORKER_ID)
        except Exception as e:
            logger.warning(f"Could not remove consumer from group: {e}")

        # Atomically: only remove from Set if we were the last consumer
        lua = """
        local consumers = redis.call('XINFO', 'CONSUMERS', KEYS[1], ARGV[1])
        if #consumers <= 0 then
            redis.call('SREM', KEYS[2], KEYS[1])
            return 1
        end
        return 0
        """

        removed = await redis_client.eval(
            lua,
            2,
            STREAM_KEY,           # KEYS[1]
            f"streams:{TIER}",    # KEYS[2]
            GROUP_NAME            # ARGV[1]
        )

        if removed:
            logger.info(f"Last worker on {STREAM_KEY} — removed from stream registry")
        else:
            logger.info(f"Other workers still active on {STREAM_KEY} — registry untouched")

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
        # add signal handler for SIGINT and SIGTERM to set stop event
        loop.add_signal_handler(sig, stop_event.set)

    # Background tasks
    asyncio.create_task(report_load(redis_client, stop_event))
    asyncio.create_task(reclaim_abandoned(redis_client, stop_event))

    try:
        while not stop_event.is_set():
            try:
                batch_size = get_batch_size()
                messages = await redis_client.xreadgroup(
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
                        process_offer(message_id, data, redis_client, semaphore)
                    )

    finally:
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())
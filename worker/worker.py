import asyncio
import json
import logging
import os
import signal
import uuid
import enum

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.sdp import candidate_from_sdp
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
#  Expose a lightweight HTTP health endpoint (using a micro-framework like aiohttp or FastAPI running on a secondary port inside the container)
#  Autoscaler  ─── GET /health ───>  [ Worker Container ]
#             <─── { "load": 12 } ───



# ── Batch size backpressure ───────────────────────────────────────────────────
def get_batch_size() -> int:
    load = len(active_sessions)
    if load >= MAX_CONCURRENT - 5:
        return 1
    if load > MAX_CONCURRENT // 2:
        return 5
    return 10


# ── ICE Candidate Listener ───────────────────────────────────────────────────
async def listen_for_ice_candidates(peer_session: PeerSession, session_id: str, redis_client: Redis):
    stream_key = f"webrtc:client:ice:{session_id}"
    logger.info(f"Listening for client ICE candidates on {stream_key}", extra={"session_id": session_id})
    
    last_id = "0-0" # Start reading from the beginning of the session-specific stream
    
    try:
        while True:
            try:
                # Poll Redis for new candidates
                # Using xread instead of xreadgroup because these are session-specific streams
                messages = await redis_client.xread({stream_key: last_id}, count=10, block=1000)
                if not messages:
                    continue
                    
                for stream, entries in messages:
                    for message_id, data in entries:
                        last_id = message_id
                        payload = data.get("payload")
                        if not payload:
                            continue
                        
                        try:
                            cand_data = json.loads(payload)
                            candidate_str = cand_data.get("candidate")
                            if candidate_str == "":
                                await peer_session.pc.addIceCandidate(None)
                                logger.info(f"Added end-of-candidates sentinel for session {session_id}")
                            elif candidate_str:
                                if candidate_str.startswith("candidate:"):
                                    candidate_str = candidate_str[len("candidate:"):]
                                
                                candidate = candidate_from_sdp(candidate_str)
                                candidate.sdpMid = cand_data.get("sdpMid")
                                candidate.sdpMLineIndex = cand_data.get("sdpMLineIndex")
                                
                                await peer_session.pc.addIceCandidate(candidate)
                                logger.info(f"Added client ICE candidate for session {session_id}")
                        except Exception as e:
                            logger.warning(f"Failed to process ICE candidate for session {session_id}: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error reading ICE candidates for session {session_id}: {e}")
                await asyncio.sleep(1)
    finally:
        logger.info(f"Stopped listening for ICE candidates for session {session_id}")


# ── Message processing ──────────────────────────────────────────────────────────
async def process_message(
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

        try:

            # ── Build peer Dependencies and Context can be as complex as needed ────────────────────────────────────────────────────
            deps = await DepProvider.build(session_id, active_sessions=active_sessions) 

            peer_session = await create_peer(deps)
            active_sessions[session_id] = peer_session


            # ── ICE Candidate Listener ────────────────────────────────────
            ice_task = asyncio.create_task(
                listen_for_ice_candidates(peer_session, session_id, redis_client)
            )
            

            # ── Apply remote offer ────────────────────────────────────────
            offer = RTCSessionDescription(
                sdp=data["sdp"],
                type=data["type"],
            )
            await peer_session.pc.setRemoteDescription(offer)

            # ── Configure transceivers ────────────────────────────────────
            for transceiver in peer_session.pc.getTransceivers():
                if transceiver.kind == "audio":
                    transceiver.direction = "sendrecv"
                    transceiver.sender.replaceTrack(deps.ctx.shared_data["resources"]["output_track"])

            # ── Generate answer ───────────────────────────────────────────
            answer = await peer_session.pc.createAnswer()
            await peer_session.pc.setLocalDescription(answer)


            # ── iortc get all candidates when setLocalDescription is finish sdp contains all ice candidates ─────
            await redis_client.xadd(
                    f"webrtc:answer:{session_id}",
                    {"payload": json.dumps({
                        "type": peer_session.pc.localDescription.type,
                        "sdp":  peer_session.pc.localDescription.sdp,
                    })}
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
            await peer_session.pc.close()

        except Exception as e:
            logger.error(
                f"process_offer failed: {type(e).__name__} — {e}",
                extra={"session_id": session_id},
                exc_info=True,
            )
            # Do not ack — message stays pending for reclaim
            await peer_session.pc.close()



            


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
    bg_tasks = [
        asyncio.create_task(reclaim_abandoned(redis_client, stop_event))
    ]

    # Running messages processing tasks
    running_tasks: set[asyncio.Task] = set()

    # Single persistent stop task — avoids leaking a new task every iteration
    stop_task = asyncio.create_task(stop_event.wait())


    try:
        while not stop_event.is_set():
            try:
                batch_size = get_batch_size()
                
                # Creamos la lectura de Redis como una tarea asegurable
                read_task = asyncio.create_task(
                    redis_client.xreadgroup(
                        GROUP_NAME,
                        WORKER_ID,
                        {STREAM_KEY: ">"},
                        count=batch_size,
                        block=2000,
                    )
                )

                # Esperamos a que termine la lectura O a que se active el evento de parada
                # Esto rompe instantáneamente el bloqueo si Docker envía un SIGTERM
                await asyncio.wait(
                    [read_task, stop_task],
                    return_when=asyncio.FIRST_COMPLETED
                )

                if stop_event.is_set():
                    if not read_task.done():
                        read_task.cancel()
                    break

                messages = await read_task
                if not messages:
                    continue

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
                   task = asyncio.create_task(
                        process_message(message_id, data, redis_client, semaphore)
                    )
                   running_tasks.add(task)
                   task.add_done_callback(running_tasks.discard)

    finally:
        logger.info("Main loop finished. Initiating shutdown sequence...")
        stop_task.cancel()
        logger.info("Main loop finished. Initiating shutdown sequence...")
        for task in bg_tasks:
            task.cancel()
        await asyncio.gather(*bg_tasks, return_exceptions=True)

        if running_tasks:
            logger.info(f"Waiting for {len(running_tasks)} in-flight sessions to finish...")
            await asyncio.gather(*running_tasks, return_exceptions=True)
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())
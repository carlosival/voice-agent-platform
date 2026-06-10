
import logging
from peer.config import build_rtc_config
from peer.types import PeerDependencies, PeerSession


logger = logging.getLogger(__name__)


async def create_peer(peer_dependencies: PeerDependencies):
    peer_session = PeerSession()
    
    config = await build_rtc_config()

    pc = RTCPeerConnection(configuration=config)
    peer_session.set_pc(pc)
 

    # ── ICE state logging ─────────────────────────────────────────────────
    @pc.on("iceconnectionstatechange")
    async def on_ice_state():
        logger.info(f"ICE state: {peer_session.pc.iceConnectionState}")
        if peer_session.pc.iceConnectionState == "failed":
            logger.error("ICE failed — no valid path found")
        if peer_session.pc.iceConnectionState in ["connected", "completed"]:
            logger.info("ICE connected")
            if peer_session.pc.connectionState == "connected": # A fully established connection
                logger.info("PeerConnection established fully")
                peer_dependencies.on_connected_fully()

    # ── Connection state logging ───────────────────────────────────────────
    @pc.on("connectionstatechange")
    async def on_conn_state():
        logger.info(f"Connection state: {peer_session.pc.connectionState}")
        if peer_session.pc.connectionState in ("failed", "closed"):
            # When the connection dies, kill all associated tasks
            for task in peer_session.tasks:
                if not task.done():
                    task.cancel()
            peer_dependencies.on_terminated()
        
        if peer_session.pc.connectionState in ["connected", "completed"]:
            logger.info("PeerConnection established")
            if peer_session.pc.iceConnectionState in ["connected", "completed"]: # A fully established connection
                logger.info("PeerConnection and ICE connected — closing WebSocket")
                peer_dependencies.on_connected_fully()


    # ── Track handler ─────────────────────────────────────────────────────
    @pc.on("track")
    async def on_track(track):
        logger.info(f"Track received: kind={track.kind} id={track.id}")

        if track.kind == "audio":
            # Create the task and add it to our managed set
            if peer_dependencies.audio_handler:
                task = asyncio.create_task(peer_dependencies.audio_handler(track,peer_dependencies.ctx))
                peer_session.add_task(task)
                # Remove from set when done to prevent memory leak
                # task.add_done_callback(pc._managed_tasks.discard)

        elif track.kind == "video":
            if peer_dependencies.video_handler:
                task = asyncio.create_task(peer_dependencies.video_handler(track,peer_dependencies.ctx))
                peer_session.add_task(task)
                # Remove from set when done to prevent memory leak
                # task.add_done_callback(pc._managed_tasks.discard)

    @pc.on("datachannel")
    async def on_datachannel(channel):
        logger.info(f"DataChannel opened: label={channel.label} id={channel.id}")

        if peer_dependencies.datachannel_handler:
            task = asyncio.create_task(peer_dependencies.datachannel_handler(channel))
            peer_session.add_task(task)
            # Remove from set when done to prevent memory leak
            # task.add_done_callback(pc._managed_tasks.discard)

    return peer_session


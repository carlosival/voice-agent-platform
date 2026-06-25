
import logging
from peer.config import build_rtc_config
from peer.types import PeerDependencies, PeerSession
from aiortc import RTCPeerConnection
import asyncio

logger = logging.getLogger(__name__)


async def create_peer(peer_dependencies: PeerDependencies):
    peer_session = PeerSession()
    
    config = await build_rtc_config()

    pc = RTCPeerConnection(configuration=config)
    peer_session.set_pc(pc)
 
    # Inject PC into the shared context so the callbacks can close it
    peer_dependencies.ctx.shared_data["resources"]["pc"] = pc

    _fully_connected = False  # guard against double-fire

    def _check_fully_connected():
        nonlocal _fully_connected
        if _fully_connected: # guard against double-fire
            return
        if (
            pc.iceConnectionState in ("connected", "completed")
            and pc.connectionState == "connected"
        ):
            _fully_connected = True
            return _fully_connected


    def _cancel_tasks(peer_session: PeerSession) -> None:
        for task in peer_session.tasks:
            if not task.done():
                task.cancel()


   


    # ── ICE state logging ─────────────────────────────────────────────────

    @pc.on("iceconnectionstatechange")
    async def on_ice_state():
        logger.info(f"ICE state: {peer_session.pc.iceConnectionState}")
        if peer_session.pc.iceConnectionState == "failed":
            logger.error("ICE failed — no valid path found")
        if peer_session.pc.iceConnectionState in ["connected", "completed"]:
            if _check_fully_connected() and peer_dependencies.on_connected_fully:
                peer_dependencies.on_connected_fully()   # DTLS + ICE both done

    @pc.on("icegatheringstatechange")  
    def on_gathering_change():
        logger.info(f"ICE gathering state changed: {peer_session.pc.iceGatheringState}")
        

    # ── Connection state change ───────────────────────────────────────────
    @pc.on("connectionstatechange")
    async def on_conn_state():
        state = pc.connectionState
        logger.info(f"Connection state: {state}")
        if state in ["connected", "completed"]:
            if _check_fully_connected() and peer_dependencies.on_connected_fully:
                peer_dependencies.on_connected_fully()   # DTLS + ICE both done
        if state in ["failed", "closed"]:
            _cancel_tasks(peer_session)
            if peer_dependencies.on_terminated:
                peer_dependencies.on_terminated()
        
       
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


import asyncio
import json
import logging
import redis.asyncio as redis
from aiortc import RTCSessionDescription, RTCPeerConnection, RTCConfiguration, RTCIceServer
from audio_track import AudioOutputTrack # Your custom tracks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Primary cloud config configuration
RTC_CONFIG = RTCConfiguration(iceServers=[
    RTCIceServer(urls="turn:turn.cloudflare.com:443?transport=udp", username="...", credential="...")
])

async def process_offer(offer_data, redis_client):
    session_id = offer_data["session_id"]
    logger.info(f"Processing handshake request for session: {session_id}")
    
    # Initialize the actual native media components inside your lab node
    pc = RTCPeerConnection(configuration=RTC_CONFIG)
    output_track = AudioOutputTrack()
    
    # Bind your track handlers, pipelines, and state loops here
    # (e.g., @pc.on("track"), audio_pipeline pipelines, etc.)
    
    # Apply remote SDP offer that came from client
    offer = RTCSessionDescription(sdp=offer_data["sdp"], type=offer_data["type"])
    await pc.setRemoteDescription(offer)
    
    # Configure transceivers
    for transceiver in pc.getTransceivers():
        if transceiver.kind == "audio":
            transceiver.direction = "sendrecv"
            transceiver.sender.replaceTrack(output_track)
            
    # Generate local SDP answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    
    # Post the answer back to the Redis response stream
    response_payload = {
        "type": pc.localDescription.type,
        "sdp": pc.localDescription.sdp
    }
    await redis_client.publish(f"webrtc:answer:{session_id}", json.dumps(response_payload))
    logger.info(f"Handshake answer published back to ledger for session {session_id}")

async def main():
    redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("webrtc:offers")
    
    logger.info("Media Worker initialized. Awaiting WebRTC offers from the ledger...")
    
    async for message in pubsub.listen():
        if message["type"] == "message":
            offer_data = json.loads(message["data"])
            # Run tasks concurrently so a single handshake doesn't block the worker loop
            asyncio.create_task(process_offer(offer_data, redis_client))

if __name__ == "__main__":
    asyncio.run(main())
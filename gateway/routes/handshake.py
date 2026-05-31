


# Connects to your Redis instance over your local network/Tailscale
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

class WebRTCOffer(BaseModel):
    sdp: str
    type: str

@app.post("/api/offer")
async def WebRTC_handshake(offer: WebRTCOffer):
    # From SLT get all session info

    # 1. Format the offer data payload
    payload = {
        "session_id": session_id,
        "type": offer.type,
        "sdp": offer.sdp
    }
    

    # 2. Setup a Pub/Sub listener to wait specifically for this session's answer
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"webrtc:answer:{session_id}")
    
    # 3. Drop the offer into the ledger for your workers to process
    await redis_client.publish("webrtc:offers", json.dumps(payload))
    
    # 4. Wait for the worker to compute and return the WebRTC Answer
    try:
        # Give the worker a 10-second timeout window to answer
        async with asyncio.timeout(10):
            async for message in pubsub.listen():
                if message["type"] == "message":
                    answer_data = json.loads(message["data"])
                    return answer_data
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Media worker timeout.")
    finally:
        await pubsub.unsubscribe(f"webrtc:answer:{session_id}")
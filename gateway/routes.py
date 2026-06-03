import os
from fastapi import APIRouter, HTTPException, Request, status
import redis
import jwt
import datetime
import httpx
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter()

CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
DOMAIN = os.getenv("DOMAIN")

ALGORITHM = "HS256"
TOKEN_EXPIRATION_SECONDS = 60
SECRET_KEY = "1234567890"

if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
    raise RuntimeError("Missing Cloudflare Environment Variables!")


@router.get("/get/ice-servers")
async def get_ice_servers():
    cloudflare_url = f"https://rtc.live.cloudflare.com/v1/turn/keys/{CLOUDFLARE_ACCOUNT_ID}/credentials/generate-ice-servers"
    
    headers = {
        "Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    # Request credentials that expire in 10 minutes (600 seconds)
    payload = {
        "ttl": 600 
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(cloudflare_url, headers=headers, json=payload)
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code, 
                    detail=f"Cloudflare API Error: {response.text}"
                )
            
            data = response.json()
            """ Expected response from Cloudflare pass it as is to the client
            {
                "iceServers": [
                {
                "urls": [
                    "stun:stun.cloudflare.com:3478",
                    "turn:turn.cloudflare.com:3478?transport=udp",
                    "turn:turn.cloudflare.com:3478?transport=tcp",
                    "turns:turn.cloudflare.com:5349?transport=tcp"
                ],
                "username": "xxxx",
                "credential": "yyyy",
                }
            ]
            } """
            
            return data
            
        except httpx.RequestError as exc:
            raise HTTPException(status_code=500, detail=f"HTTP Request failed: {exc}")


@router.post("/session/init")
async def initialize_session(request: Request):
    # Simular la solicitud de inicialización de sesión
    data = await request.json()
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id es requerido")
        
    # Generar session_id
    session_id = str(uuid.uuid4())
    
    # Generar token JWT
    token_payload = {
        "session_id": session_id,
        "user_id": user_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(seconds=60)
    }
    token = jwt.encode(token_payload, "secret_key", algorithm="HS256")
    
    # Guardar token en Redis
    redis_client.set(f"valid_tokens:{token}", session_id, ex=60)
    
    # Devolver URL de conexión
    connection_url = f"wss://worker-{session_id}.internal.yourdomain.com:8443/ws"
    
    return {
        "session_id": session_id,
        "connection_url": connection_url,
        "token": token
    }


    # Connects to your Redis instance over your local network/Tailscale
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

class WebRTCOffer(BaseModel):
    sdp: str
    type: str

@router.post("/api/handshake/{session_id}")
async def WebRTC_handshake(session_id: str, offer: WebRTCOffer):
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




# ── REQUEST/RESPONSE SCHEMAS ────────────────────────────────────────
class SessionInitializeRequest(BaseModel):
    pk: str = Field(description="The public key body of the connecting client application")
    agent_id: str = Field(description="The unique string identifier of the target Voice Agent")


class SessionInitializeResponse(BaseModel):
    session_id: str
    connection_url: str
    token: str


# MOCK DATABASE UTILITIES — Replace with your real DB/Redis client execution layer
async def verify_pk_and_agent_in_db(pk: str, agent_id: str) -> Optional[str]:
    """
    Queries SQL Database to ensure the public key is active and the agent exists.
    Returns the associated user_id if valid, else None.
    """
    # Example raw SQL: 
    # SELECT k.user_id FROM user_public_keys k CROSS JOIN voice_agents a 
    # WHERE k.public_key_body = :pk AND k.is_active = true AND a.id = :agent_id;
    return str(uuid.uuid4()) # Simulating a valid UUID match

async def get_healthiest_worker_from_redis(region: str) -> Optional[dict]:
    """
    Queries Tier 2 Redis ZSET to find the least utilized worker in the region.
    """
    # Example Redis command: ZRANGE workers:pool:{region} 0 0
    # Then reads the corresponding hash details.
    return {
        "id": f"worker-{region}-04",
        "connection_url": f"wss://worker-{region}-04.internal.{DOMAIN}:8443/ws"
    }

async def atomic_reserve_in_redis(session_id: str, worker_id: str, region: str, payload_data: dict):
    """
    Increments connection weight onto the worker score and sets a short-lived reservation token.
    """
    # Example Redis commands inside a pipeline:
    # ZINCRBY workers:pool:{region} 1 worker:entry:{worker_id}
    # SETEX reservation:session:{session_id} 60 "{payload_data}"
    pass


@router.post(
    "/v1/sessions/initialize", 
    response_model=SessionInitializeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initialize secure session handshake and lookup worker capacity"
)
async def initialize_session(payload: SessionInitializeRequest):
    logger.info(f"Received session initialization request for Agent: {payload.agent_id}")

    return SessionInitializeResponse(
        session_id=str(uuid.uuid4()),
        connection_url=f"wss://{DOMAIN}/ws",
        token="mock-token"
    )
    
    # Step 1: Validate Public Key and Agent Relationship inside SQL DB
    user_id = await verify_pk_and_agent_in_db(payload.pk, payload.agent_id)
    if not user_id:
        logger.warning(f"Unauthorized session request: Public Key or Agent ID '{payload.agent_id}' is invalid.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, 
            detail="Invalid public key credential or inactive voice agent mapping."
        )

    # Step 2: Query Tier 2 Redis Mesh for regional capacity allocation
    region = "us-east" # Infere the region from the user's IP address
    allocated_worker = await get_healthiest_worker_from_redis(region)
    if not allocated_worker:
        logger.error(f"Capacity Exhausted: No available workers found for region '{region}'")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="System capacity limits reached in your region. Please try again shortly."
        )
        
    session_id = str(uuid.uuid4())
    worker_id = allocated_worker["id"]
    connection_url = allocated_worker["connection_url"]

    # Step 3: Atomic State Reservation inside Redis Mesh
    reservation_payload = {
        "pk": payload.pk,
        "agent_id": payload.agent_id,
        "user_id": user_id
    }
    await atomic_reserve_in_redis(session_id, worker_id, payload.region, reservation_payload)

    # Step 4: Cryptographically sign the short-lived structural JWT Token
    expiration = datetime.datetime.utcnow() + datetime.timedelta(seconds=TOKEN_EXPIRATION_SECONDS)
    token_claims = {
        "sub": user_id,
        "session_id": session_id,
        "pk": payload.pk,
        "agent_id": payload.agent_id,
        "exp": expiration
    }
    
    try:
        signed_token = jwt.encode(token_claims, SECRET_KEY, algorithm=ALGORITHM)
    except Exception as e:
        logger.error(f"Token generation failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal signing failure during secure routing initialization."
        )

    logger.info(f"Successfully allocated session '{session_id}' to worker node '{worker_id}'")

    # Step 5: Deliver Routing payload back to Client
    return SessionInitializeResponse(
        session_id=session_id,
        connection_url=connection_url,
        token=signed_token
    )
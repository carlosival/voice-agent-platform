import os
from fastapi import APIRouter, HTTPException, Request
import redis
import jwt
import datetime

router = APIRouter()

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
import os
import uuid
import jwt
import datetime
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, status, Depends
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.controllers.init_controller import InitController
from dbs_clients.db import get_async_session

from pydantic import BaseModel, Field

router = APIRouter()
logger = logging.getLogger(__name__)

# ── REQUEST/RESPONSE SCHEMAS ────────────────────────────────────────
class SessionInitializeRequest(BaseModel):
    pk: str = Field(description="The public key body of the connecting client application")
    agent_id: str = Field(description="The unique string identifier of the target Voice Agent")


class SessionInitializeResponse(BaseModel):
    session_id: str
    connection_url: str
    token: str

@router.post(
    "/v1/api/init", 
    response_model=SessionInitializeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initialize secure session handshake and lookup worker capacity"
)
async def initialize_session(
    payload: SessionInitializeRequest,
    request: Request
):
    logger.info(f"Received session initialization request for Agent: {payload.agent_id}")
    
    # Extract client origin from headers (for domain verification)
    client_origin = request.headers.get("origin") or request.headers.get("referer") or "localhost"
    
    controller = InitController(request)
    result = await controller.initialize_session(
        pk_body=payload.pk,
        agent_id=payload.agent_id,
        client_origin=client_origin
    )
    
    return SessionInitializeResponse(**result)
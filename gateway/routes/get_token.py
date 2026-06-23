import os
import uuid
import jwt
import datetime
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, status, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.controllers import InitController, SessionInitializeRequest, SessionInitializeResponse
from dbs_clients import get_async_session


router = APIRouter()
logger = logging.getLogger(__name__)

init_controller = InitController()


@router.post(
    "/api/get_token",
    response_model=SessionInitializeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def get_token(request: Request, body: SessionInitializeRequest):
    logger.info(f"Received session initialization request.")
    result = await init_controller.get_token(request, body)
    return SessionInitializeResponse(**result)
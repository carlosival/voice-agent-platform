import logging
import httpx
from fastapi import APIRouter, Request

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/")
async def health(request: Request):
    pass


@router.get("/health/ping")
async def ping():
    return {"pong": True}
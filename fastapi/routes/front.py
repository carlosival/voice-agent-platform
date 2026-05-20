import logging
from fastapi.responses import FileResponse
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/")
def index():
    logger.info("index")
    return FileResponse("static/index.html")
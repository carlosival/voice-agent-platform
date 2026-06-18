import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


# Import routers
from gateway.routes import get_token, handshake, ice_servers, health

# Import DB
from dbs_clients import AsyncSessionFactory, async_engine

# Import Redis
from dbs_clients.redis_db import redis_client

# Import Router cleanup
from gateway.worker_router.worker_router import close_geoip_reader

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    logger.info("Initializing Gateway dependencies...")
    
    # 1. Initialize Redis
    app.state.redis = redis_client
    
    # 2. Store DB Session Maker
    app.state.db = AsyncSessionFactory
    
    yield
    
    # --- Shutdown ---
    logger.info("Cleaning up Gateway dependencies...")
    close_geoip_reader()
    await app.state.redis.aclose()
    await async_engine.dispose()

app = FastAPI(
    title="Voice Agent Gateway",
    description="Regional gateway for secure session initialization and WebRTC handshake routing.",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, restrict this to your frontend domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register Routers
app.include_router(get_token.router, tags=["Session"])
app.include_router(handshake.router, prefix="/v1", tags=["WebRTC"])
app.include_router(ice_servers.router, prefix="/v1", tags=["WebRTC"])
app.include_router(health.router, prefix="/v1", tags=["Health"])


@app.get("/")
def read_root():
    return {"message": "Gateway API is running"}
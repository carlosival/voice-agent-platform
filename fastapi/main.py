import os
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from langfuse import Langfuse
from routes import twilio, health, front, webrtc

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LLM_BASE_URL     = os.getenv("LLM_BASE_URL", "http://llm:8000/v1")
SPEACHES_BASE_URL = os.getenv("SPEACHES_BASE_URL", "http://speaches:8000")
BASE_URL          = os.getenv("BASE_URL", "http://ngrok:4040/api/tunnels")


# Create a filter class
class HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # If the log message contains our healthcheck path, skip it
        return "/health/ping" not in record.getMessage()

# Apply the filter to uvicorn access logs
logging.getLogger("uvicorn.access").addFilter(HealthCheckFilter())

@asynccontextmanager
async def lifespan(app: FastAPI):
    http = httpx.AsyncClient(timeout=60.0)
    app.state.http = http
    app.state.llm_url = LLM_BASE_URL
    app.state.speaches_url = SPEACHES_BASE_URL
    app.state.base_url = BASE_URL
    app.state.tracer = Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST"),
        httpx_client=http
    )

    logger.info(
        f"FastAPI ready\n"
        f"Public URL : {app.state.base_url}\n"
        f"vLLM       : {LLM_BASE_URL}\n"
        f"Speaches   : {SPEACHES_BASE_URL}"
    )
    yield
    await app.state.http.aclose()
    app.state.tracer.shutdown()


app = FastAPI(
    title="Voice AI Pipeline",
    description="FastAPI webhooks → Speaches STT(Faster-Whisper-Large-v3) → LLM (Llama 3.1 8B) → Speaches TTS(Kokoro-82M-v1.0-ONNX)",
    version="0.1.0",
    lifespan=lifespan,
)



app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(twilio.router, prefix="/twilio", tags=["Twilio"])
app.include_router(health.router, prefix="/health", tags=["Health"])
app.include_router(front.router, tags=["Index"])
app.include_router(webrtc.router, prefix="/ws", tags=["WebRTC"])

import pathlib
pathlib.Path("static").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
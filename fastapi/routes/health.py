import logging
import httpx
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def health(request: Request):
    """Check health of all services in the stack."""
    results = {}

    for name, url in [
        ("llm",      f"{request.app.state.llm_url}/models"),
        ("speaches", f"{request.app.state.speaches_url}/health"),
    ]:
        try:
            resp = await request.app.state.http.get(url, timeout=5.0)
            results[name] = "ok" if resp.status_code == 200 else f"http_{resp.status_code}"
        except Exception as e:
            results[name] = f"unreachable: {e}"

    return {
        "fastapi": "ok",
        "public_url": request.app.state.base_url,
        "twilio_webhook": f"{request.app.state.base_url}/twilio/voice/incoming",
        **results,
    }


@router.get("/ping")
async def ping():
    return {"pong": True}
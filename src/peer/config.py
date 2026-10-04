import time
import os
import logging
from services import fetch_cloudflare_ice_servers
from aiortc import RTCIceServer, RTCConfiguration

logger = logging.getLogger(__name__)

ICE_CACHE_TTL=os.getenv("ICE_CACHE_TTL", 3600)


# ── In-memory cache ───────────────────────────────────────────────────────────
_ice_cache: dict = {
    "servers": None,
    "expires_at": 0.0,
}


# ── ICE servers ─────────────────────────────────────────────────────────
# Call Cloudflare STUN and TURN servers using the API and credentials  store in cache


async def get_ice_servers() -> list[RTCIceServer]:
    """
    Fetch ICE servers from Cloudflare API, cache them,
    and return a ready list[RTCIceServer] for RTCPeerConnection.
    """
    now = time.monotonic()

    if _ice_cache["servers"] and now < _ice_cache["expires_at"]:
        return _ice_cache["servers"]  # already list[RTCIceServer]

    try:
        data = await fetch_cloudflare_ice_servers()
        ice_servers = []

        for server in data.get("iceServers", []):  # iterate list
            urls = server.get("urls", [])
            username = server.get("username")
            credential = server.get("credential")

            for url in urls:
                if url.startswith("stun:"):
                    ice_servers.append(RTCIceServer(urls=url))
                elif url.startswith("turn:") or url.startswith("turns:"):
                    ice_servers.append(RTCIceServer(
                        urls=url,
                        username=username,
                        credential=credential,
                    ))

        _ice_cache["servers"] = ice_servers
        _ice_cache["expires_at"] = now + ICE_CACHE_TTL
        return ice_servers

    except Exception as e:
        logger.error(f"Failed to fetch ICE servers: {e}")

        if _ice_cache["servers"]:
            logger.warning("Using stale ICE cache as fallback")
            return _ice_cache["servers"]

        logger.warning("Falling back to Google STUN")
        
        return [ # Primary — Cloudflare STUN (already in your ecosystem)
            RTCIceServer(urls="stun:stun.cloudflare.com:3478"),

            # Fallback — Google STUN
            RTCIceServer(urls="stun:stun.l.google.com:19302"),
            RTCIceServer(urls="stun:stun1.l.google.com:19302"),
        ]

async def build_rtc_config() -> RTCConfiguration:
    """
    Fetch ICE servers from Cloudflare API, cache them,
    and return a ready RTCConfiguration for RTCPeerConnection.
    """
    ice_servers = await get_ice_servers()  # returns list[RTCIceServer]
    return RTCConfiguration(iceServers=ice_servers)  # ← ready to pass directly
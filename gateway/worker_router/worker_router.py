import logging
logger = logging.getLogger(__name__)

# --- Constants ---
MAX_STREAM_DEPTH = 100

import geoip2.database
from dbs_clients import redis_client
from .config import (
    REGIONES_COORDENADAS, 
    TIER_QUEUES, 
    CONSUMER_GROUP, 
    STREAMS_SET_PREFIX, 
    STREAM_CACHE_TTL
)
import asyncio
import time
import math
from dataclasses import dataclass
from typing import Optional
import os



# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class RoutingError(Exception):
    """Base class for all routing errors."""

class UnknownTierError(RoutingError):
    def __init__(self, tier: str):
        super().__init__(f"Unknown agent tier '{tier}'.")
        self.tier = tier

class UnknownRegionError(RoutingError):
    def __init__(self, region: str):
        super().__init__(f"Region '{region}' has no coordinates in REGION_COORDS.")
        self.region = region

class NoCapacityError(RoutingError):
    def __init__(self, hw: str, tier: str):
        super().__init__(
            f"No available workers for hw='{hw}' (tier='{tier}'). "
            "All streams are full or offline."
        )
        self.hw   = hw
        self.tier = tier

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    agent_id:  str
    tier:      str
    regions:   list[str]   # allowed regions from agent config
    # ... other fields (llm_config, tts_config, etc.) omitted for routing

@dataclass
class StreamInfo:
    key:     str    # full Redis stream key
    region:  str
    depth:   int    # pending messages


@dataclass
class _CacheEntry:
    streams:    list[StreamInfo]
    fetched_at: float

_stream_cache: dict[str, _CacheEntry] = {}
_locks: dict[str, asyncio.Lock] = {}
_geoip_reader = geoip2.database.Reader(os.path.join(os.path.dirname(__file__), "GeoLite2-City.mmdb"))


def _get_lock(tier: str) -> asyncio.Lock:
    if tier not in _locks:
        _locks[tier] = asyncio.Lock()
    return _locks[tier]

def close_geoip_reader():
    """Closes the GeoIP database reader."""
    if _geoip_reader:
        logger.info("Closing GeoIP reader...")
        _geoip_reader.close()

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def _euclidean_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return math.sqrt((lat2 - lat1)**2 + (lon2 - lon1)**2)    

def _obtener_region_cercana(lat: float, lon: float) -> str:
    """Calcula la región estándar más cercana por distancia Euclidiana simple"""
    def distancia(reg_coord):
        return (lat - reg_coord[0])**2 + (lon - reg_coord[1])**2
    
    return min(REGIONES_COORDENADAS, key=lambda k: distancia(REGIONES_COORDENADAS[k]))

def sort_streams_by_distance(
    streams: list[StreamInfo],
    from_region: str,
) -> list[StreamInfo]:
    """
    Sorts streams closest-first relative to from_region.
    Streams whose region is unknown are pushed to the end.
    """
    def sort_key(s: StreamInfo) -> float:
        try:
            return distance_to_region(from_region, s.region)
        except UnknownRegionError:
            logger.warning("Unknown region '%s' in stream %s", s.region, s.key)
            return math.inf

    return sorted(streams, key=sort_key)

def distance_to_region(from_region: str, to_region: str) -> float:
    """
    Returns km between two named regions.
    'global' is always treated as infinitely far so it's a last resort.
    """
    if to_region == "global":
        return math.inf
    if from_region not in REGIONES_COORDENADAS:
        raise UnknownRegionError(from_region)
    if to_region not in REGIONES_COORDENADAS:
        raise UnknownRegionError(to_region)
    lat1, lon1 = REGIONES_COORDENADAS[from_region]
    lat2, lon2 = REGIONES_COORDENADAS[to_region]
    return _haversine_km(lat1, lon1, lat2, lon2) # TODO: Use Euclidean distance for better performance



def _resolver_region_por_ip(ip: str) -> str:
    try:
        # IPs locales de prueba saltan a fallback
        if ip in ("127.0.0.1", "localhost", "testclient"):
            return "global" 
            
        reader = _geoip_reader
        if not reader:
            return "global"

        response = reader.city(ip)
        lat = response.location.latitude
        lon = response.location.longitude
        if lat and lon:
            return _obtener_region_cercana(lat, lon)
    except Exception as e:
        logger.error(f"GeoIP resolution failed for {ip}: {e}")
    return "global" # Región por defecto / Fallback

async def get_available_streams(tier: str, redis, consumer_group: str = CONSUMER_GROUP) -> list[StreamInfo]:
    now = time.monotonic()
    entry = _stream_cache.get(tier)
    
    # 1. Fast path (Cache hit)
    if entry and (now - entry.fetched_at) < STREAM_CACHE_TTL:
        return entry.streams

    # 2. Lock específico por Tier para evitar cuellos de botella entre tiers
    async with _get_lock(tier):
        # Re-calcular el tiempo actual tras salir de la espera del lock
        now = time.monotonic() 
        entry = _stream_cache.get(tier)
        
        # Double-check
        if entry and (now - entry.fetched_at) < STREAM_CACHE_TTL:
            return entry.streams

        logger.debug("Refreshing stream cache for tier=%s", tier)
        streams = await _fetch_streams(tier, redis, consumer_group)
        
        # Guardar con el timestamp exacto de la mutación
        _stream_cache[tier] = _CacheEntry(streams=streams, fetched_at=time.monotonic())
        return streams

async def _fetch_streams(tier: str, redis,  consumer_group: str = CONSUMER_GROUP) -> list[StreamInfo]:
    """
    Reads the streams:{tier} Set from Redis, then checks depth of each
    stream via XINFO GROUPS. Returns only healthy (non-full) streams.
    Uses a pipeline to parallelise XINFO calls.
    """
    set_key = f"{STREAMS_SET_PREFIX}:{tier}"
    stream_keys: set[bytes] = await redis.smembers(set_key)

    if not stream_keys:
        logger.warning("No streams registered in %s", set_key)
        return []

    # parallel XINFO GROUPS for all streams
    pipe = redis.pipeline(transaction=False)
    decoded_keys = [k.decode() if isinstance(k, bytes) else k for k in stream_keys]
    for sk in decoded_keys:
        pipe.xinfo_groups(sk)
    results = await pipe.execute(raise_on_error=False)

    streams: list[StreamInfo] = []
    for sk, result in zip(decoded_keys, results):
        if isinstance(result, Exception):
            logger.warning("XINFO GROUPS failed for %s: %s", sk, result)
            continue

        # find the worker consumer group
        pending = 0
        for group in result:
            name = group.get("name", b"")
            if isinstance(name, bytes):
                name = name.decode()
            if name == consumer_group:
                pending = group.get("pending", 0)
                break
        
        # Filtered out streams with too many pending messages. Satured or problems with workers.
        if pending >= MAX_STREAM_DEPTH:
            logger.debug("Stream %s is full (pending=%d), skipping", sk, pending)
            # Notify a subsystem that the stream is full or to many problems with workers.
            continue

        # extract region from key: webrtc:offers:{tier}:{region}
        parts = sk.split(":")
        region = parts[-1] if len(parts) >= 4 else "unknown"

        streams.append(StreamInfo(key=sk, region=region, depth=pending))

    logger.debug("Available streams for tier=%s: %s", tier, [s.key for s in streams])
    return streams


async def resolve_stream_key(
    agent: AgentConfig,
    client_ip: str,
    redis,
) -> Optional[str]:
    """
    Resolves the best Redis stream key for a WebRTC offer.

    Resolution order:
      1. tier is mandatory — hard fail if unknown
      2. Preferred: agent.regions[] ∩ available streams, sorted by distance to client
      3. Fallback:  any available stream for this tier, sorted by distance to client
      4. Error:     NoCapacityError — caller should return 503

    Returns a stream key like 'webrtc:offers:standard:eu-west-1'.
    """
    # 1. resolve tier — mandatory, never compromised
    tier = agent.tier
    if not tier:
        raise UnknownTierError(tier)

    # 2. resolve client closest region from IP
    client_closest_region = _resolver_region_por_ip(client_ip)
    logger.debug(
        agent.agent_id, tier, client_ip, client_closest_region,
    )

    consumer_group = CONSUMER_GROUP

    # 3. fetch available (healthy, non-full) streams for this tier
    available = await get_available_streams(tier, redis, CONSUMER_GROUP)
    if not available:
        raise NoCapacityError(tier, agent.tier)

    # 4. preferred path — agent's allowed regions intersected with available
    allowed = set(agent.regions)
    preferred = [s for s in available if s.region in allowed]

    if preferred:
        ranked = sort_streams_by_distance(preferred, client_closest_region)
        chosen = ranked[0]
        logger.info(
            "Exact match: stream=%s distance=%.0fkm",
            chosen.key,
            distance_to_region(client_closest_region, chosen.region),
        )
        return chosen.key
    
    if "global" in allowed:    

        # 5. fallback — any available stream for this tier, closest to client if global is set
        logger.warning(
            "No preferred region available for agent=%s regions=%s — falling back to nearest",
            agent.agent_id, agent.regions,
        )
        ranked = sort_streams_by_distance(available, client_closest_region)
        chosen = ranked[0]
        logger.info(
            "Cross-region fallback: stream=%s distance=%.0fkm",
            chosen.key,
            distance_to_region(client_closest_region, chosen.region),
        )
        return chosen.key

    return None

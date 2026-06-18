TIER_QUEUES = {
    "premium.global":  "webrtc:offers:premium:global",
    "standard.global": "webrtc:offers:standard:global",
    "free.global":     "webrtc:offers:free:global",
    "premium.sa":  "webrtc:offers:premium:sa",
    "standard.sa": "webrtc:offers:standard:sa",
    "free.sa":     "webrtc:offers:free:sa",
}

_PRIVATE_RANGES = (
    "10.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.", "172.24.",
    "172.25.", "172.26.", "172.27.", "172.28.", "172.29.",
    "172.30.", "172.31.", "192.168.", "127.", "::1", "fc", "fd",
)

CONSUMER_GROUP = "media-workers"
STREAM_CACHE_TTL = 60  # seconds
STREAMS_SET_PREFIX = "webrtc:offers"

# Configuración de regiones por coordenadas aproximadas (Lat, Lon)
REGIONES_COORDENADAS = {
    # US
    "us-west-1": (37.7749, -122.4194),  # California
    "us-west-2":    (45.5, -122.7),  # Oregon
    "us-east-1": (38.03, -78.47),   # Virginia
    # EU
    "eu-west-1": (53.3498, -6.2603),    # Irlanda
    "eu-west-1":    (53.3,  -6.2),   # Dublin
    "eu-central-1": (50.1,   8.7),   # Frankfurt
    "eu-south-1":   (45.5,   9.2),   # Milan
    # Asia
    "ap-southeast": ( 1.3,  103.8),  # Singapore
    "ap-northeast": (35.7,  139.7),  # Tokyo
    "ap-south-1":   (19.1,   72.9),  # Mumbai
    "sa-east-1":    (-23.5, -46.6),  # São Paulo
    # Global Default mean to as fallback region if isn't match with any region
    "global":       (0.0,    0.0),   # catch-all, always last
}
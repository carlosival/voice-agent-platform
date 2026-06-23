from fastapi import Depends, HTTPException, status, Request, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from typing import Optional
import jwt
import os


security = HTTPBearer()


def _get_client_ip(request: Request | WebSocket) -> str:
        """Resolves the client IP, handling reverse proxies."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host

def verify_token_credentials(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    token = credentials.credentials
    token_decoded = is_valid_token(token)
    if not token_decoded:  
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token_decoded


def verify_raw_token(
    token: str
) -> str:
    token_decoded = is_valid_token(token)
    if not token_decoded:  
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token_decoded

def is_valid_token(token: str) -> Optional[dict]:
    try:
        token_decoded = jwt.decode(token, os.getenv("GATEWAY_SECRET_KEY"), algorithms=[os.getenv("ALGORITHM")])
        return token_decoded
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
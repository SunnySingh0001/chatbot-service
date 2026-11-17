"""Rate limiting configuration and verified key resolution."""
from __future__ import annotations

import jwt
from fastapi import HTTPException, Request, status
from limits import parse
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

STRICT_UNVERIFIED_RATE_LIMIT: str = "5/minute"


def get_user_rate_limit_key(request: Request) -> str:
    """Extracts cryptographically verified user_id from JWT, falling back to client IP."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        try:
            # Cryptographic signature verification is strictly enforced
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
            )
            sub = str(payload.get("sub", ""))
            if sub:
                return f"user:{sub}"
        except jwt.PyJWTError:
            pass  # Forged, expired, or invalid token: treat strictly as unauthenticated IP
    return f"ip:{get_remote_address(request)}"


def resolve_rate_limit(key: str) -> str:
    """Returns standard rate limit for verified users, or stricter limit for unverified IP requests."""
    if key.startswith("user:"):
        return settings.rate_limit
    return STRICT_UNVERIFIED_RATE_LIMIT


limiter = Limiter(key_func=get_user_rate_limit_key)


def rate_limit_guard(request: Request) -> None:
    """Enforces verified user and unverified IP rate limits prior to dependency resolution."""
    key = get_user_rate_limit_key(request)
    limit_str = resolve_rate_limit(key)
    parsed = parse(limit_str)
    if not limiter.limiter.hit(parsed, key, "chat_scope"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later.",
        )

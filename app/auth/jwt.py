"""JWT encoding, decoding, and FastAPI dependency for extracting authenticated users."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.models.schemas import AuthenticatedUser
from app.rag.retriever import ROLE_ACCESS

_logger = logging.getLogger("chatbot.auth")
_security = HTTPBearer(auto_error=False)

# Derived directly from ROLE_ACCESS (single source of truth for allowed roles)
ALLOWED_ROLES: frozenset[str] = frozenset(ROLE_ACCESS.keys())


def create_access_token(
    user_id: str,
    role: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Encodes a signed HS256 JWT containing user_id and role claims."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    )
    payload: dict[str, object] = {
        "sub": user_id,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_jwt_token(token: str) -> AuthenticatedUser:
    """Verifies HS256 signature, validates role against whitelist, and returns AuthenticatedUser."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        _logger.warning("JWT verification rejected: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user_id = str(payload.get("sub", ""))
    role = str(payload.get("role", "")).lower()

    if not user_id or not role:
        _logger.warning("JWT payload missing required claims: sub=%s, role=%s", user_id, role)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if role not in ALLOWED_ROLES:
        _logger.warning("JWT role %r not in allowed whitelist %s", role, ALLOWED_ROLES)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access forbidden: role is not permitted",
        )

    return AuthenticatedUser(user_id=user_id, role=role)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_security),
) -> AuthenticatedUser:
    """FastAPI dependency: extracts and verifies JWT from Bearer Authorization header."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_jwt_token(credentials.credentials)
    return decode_jwt_token(credentials.credentials)

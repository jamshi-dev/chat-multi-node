"""Password hashing (argon2) and JWT encode/decode."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import get_settings

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        # Malformed hash etc. — treat as a failed verification, never raise to caller.
        return False


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or the wrong type."""


def create_token(user_id: uuid.UUID, token_type: TokenType) -> str:
    settings = get_settings()
    ttl = (
        settings.access_token_ttl_seconds
        if token_type == "access"
        else settings.refresh_token_ttl_seconds
    )
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, expected_type: TokenType) -> uuid.UUID:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc

    if payload.get("type") != expected_type:
        raise TokenError(f"expected {expected_type} token")
    sub = payload.get("sub")
    if not isinstance(sub, str):
        raise TokenError("invalid subject")
    try:
        return uuid.UUID(sub)
    except ValueError as exc:
        raise TokenError("invalid subject") from exc

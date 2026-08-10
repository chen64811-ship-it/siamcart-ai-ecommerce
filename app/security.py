"""JWT authentication dependencies and password hashing."""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app import config
from app.db import store as store_db
from app.db.users import get_user_by_id

_password_hash = PasswordHash.recommended()
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return _password_hash.verify(password, encoded)


def create_access_token(user_id: str) -> tuple[str, int]:
    expires = timedelta(minutes=config.JWT_ACCESS_TOKEN_MINUTES)
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + expires},
        config.JWT_SECRET_KEY,
        algorithm=config.JWT_ALGORITHM,
    )
    return token, int(expires.total_seconds())


def _unauthorized(detail: str = "Invalid or expired access token") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict[str, Any] | None:
    if credentials is None:
        if config.AUTH_REQUIRED:
            raise _unauthorized("Authentication required")
        return None
    try:
        payload = jwt.decode(
            credentials.credentials,
            config.JWT_SECRET_KEY,
            algorithms=[config.JWT_ALGORITHM],
        )
        user_id = payload.get("sub")
        if not isinstance(user_id, str):
            raise _unauthorized()
    except InvalidTokenError:
        raise _unauthorized()
    user = get_user_by_id(store_db.STORE_DB_PATH, user_id)
    if user is None:
        raise _unauthorized()
    return user


def require_user(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> dict[str, Any]:
    if credentials is None:
        raise _unauthorized("Authentication required")
    user = get_current_user(credentials)
    if user is None:  # pragma: no cover - guarded above
        raise _unauthorized()
    return user

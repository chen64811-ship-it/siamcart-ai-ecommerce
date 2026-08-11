from secrets import token_urlsafe

from fastapi import APIRouter, Depends, HTTPException, status

from app import config
from app.db import store as store_db
from app.db.users import create_user, get_user_by_email
from app.models.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.security import create_access_token, hash_password, require_user, verify_password

router = APIRouter(prefix="/api/auth", tags=["authentication"])

_DEMO_EMAIL = "demo@siamcart.app"
_DEMO_DISPLAY_NAME = "SiamCart Demo Shopper"


def _public_user(user: dict) -> UserResponse:
    return UserResponse(
        user_id=user["user_id"], email=user["email"], display_name=user["display_name"]
    )


def _token_response(user: dict) -> TokenResponse:
    token, expires_in = create_access_token(user["user_id"])
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        user=_public_user(user),
    )


@router.get("/config")
async def auth_config():
    """Expose non-secret browser auth capabilities."""
    return {
        "auth_required": config.AUTH_REQUIRED,
        "demo_login_enabled": config.DEMO_LOGIN_ENABLED,
    }


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(request: RegisterRequest):
    if get_user_by_email(store_db.STORE_DB_PATH, request.email):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    try:
        user = create_user(
            store_db.STORE_DB_PATH,
            request.email,
            hash_password(request.password),
            request.display_name,
        )
    except Exception:
        # A concurrent insert may win after the pre-check. Do not disguise an
        # unrelated database outage as a duplicate-account response.
        if get_user_by_email(store_db.STORE_DB_PATH, request.email):
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        raise
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    user = get_user_by_email(store_db.STORE_DB_PATH, request.email)
    if user is None or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return _token_response(user)


@router.post("/demo", response_model=TokenResponse)
async def demo_login():
    """Issue a token for the shared recruiter-facing demo account.

    The route is disabled by default and deliberately has no reusable
    password. Enabling it is an explicit deployment decision for a public
    portfolio demo; normal users still authenticate through login/register.
    """
    if not config.DEMO_LOGIN_ENABLED:
        raise HTTPException(status_code=404, detail="Demo login is not enabled")

    user = get_user_by_email(store_db.STORE_DB_PATH, _DEMO_EMAIL)
    if user is None:
        try:
            user = create_user(
                store_db.STORE_DB_PATH,
                _DEMO_EMAIL,
                hash_password(token_urlsafe(32)),
                _DEMO_DISPLAY_NAME,
            )
        except Exception:
            # Concurrent first-time demo logins may race on the unique email.
            user = get_user_by_email(store_db.STORE_DB_PATH, _DEMO_EMAIL)
            if user is None:
                raise
    return _token_response(user)


@router.get("/me", response_model=UserResponse)
async def me(user: dict = Depends(require_user)):
    return _public_user(user)

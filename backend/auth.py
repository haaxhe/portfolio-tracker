"""Minimal auth boundary for the web MVP.

Local mode keeps the existing single-user workflow working. Token mode is the
first hosted step: put an auth proxy or frontend session in front of the API and
send a stable user id with each request.
"""
from __future__ import annotations

import requests
from fastapi import Header, HTTPException, status
from pydantic import BaseModel

from backend.config import settings


class CurrentUser(BaseModel):
    user_id: str
    email: str = ""


def _clean_user_id(value: str) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing user identity")
    if len(value) > 128:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "User identity is too long")
    return value


def get_current_user(
    authorization: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
) -> CurrentUser:
    """Resolve the request owner.

    AUTH_MODE=local is for one-user local development. AUTH_MODE=token is a
    private-beta bridge. AUTH_MODE=supabase verifies the bearer token with
    Supabase Auth and uses the Supabase user id as the data owner.
    """
    if settings.AUTH_MODE == "local":
        return CurrentUser(user_id=_clean_user_id(settings.DEFAULT_USER_ID))

    if settings.AUTH_MODE == "token":
        if not settings.API_TOKEN:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "API_TOKEN is required")

        expected = f"Bearer {settings.API_TOKEN}"
        if authorization != expected:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing bearer token")

        return CurrentUser(user_id=_clean_user_id(x_user_id or settings.DEFAULT_USER_ID))

    if settings.AUTH_MODE == "supabase":
        return _get_supabase_user(authorization)

    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Unsupported AUTH_MODE")


def _get_supabase_user(authorization: str | None) -> CurrentUser:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing Supabase bearer token")
    if not settings.SUPABASE_URL or not settings.SUPABASE_PUBLISHABLE_KEY:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Supabase auth is not configured")

    base_url = settings.SUPABASE_URL.rstrip("/")
    try:
        response = requests.get(
            f"{base_url}/auth/v1/user",
            headers={
                "apikey": settings.SUPABASE_PUBLISHABLE_KEY,
                "Authorization": authorization,
            },
            timeout=5,
        )
    except requests.RequestException as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Supabase auth verification failed",
        ) from e

    if response.status_code != 200:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Supabase session")

    data = response.json()
    user_id = data.get("id") or data.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Supabase session has no user id")

    return CurrentUser(
        user_id=_clean_user_id(str(user_id)),
        email=str(data.get("email") or ""),
    )

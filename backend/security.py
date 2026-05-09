"""HTTP security helpers for the FastAPI app."""
from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from time import monotonic
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status
from starlette.datastructures import MutableHeaders

from backend.config import settings


class InMemoryRateLimiter:
    """Small per-process limiter for expensive MVP endpoints."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> None:
        now = monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window_seconds - (now - bucket[0])))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)


rate_limiter = InMemoryRateLimiter()


def enforce_rate_limit(
    request: Request,
    user_id: str,
    action: str,
    limit: int,
    window_seconds: int,
) -> None:
    host = request.client.host if request.client else "unknown"
    key = f"{action}:{user_id}:{host}"
    rate_limiter.check(key, limit=limit, window_seconds=window_seconds)


def apply_security_headers(headers: MutableHeaders) -> None:
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    headers.setdefault("Content-Security-Policy", _content_security_policy())

    if settings.APP_BASE_URL.startswith("https://"):
        headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains; preload",
        )


def _content_security_policy() -> str:
    connect_src = ["'self'"]
    supabase_origin = _origin(settings.SUPABASE_URL)
    if supabase_origin:
        connect_src.append(supabase_origin)

    script_src = ["'self'"]
    if not settings.is_production:
        script_src.extend(
            [
                "'unsafe-inline'",
                "'unsafe-eval'",
                "https://cdnjs.cloudflare.com",
                "https://cdn.jsdelivr.net",
            ]
        )

    return "; ".join(
        [
            "default-src 'self'",
            "script-src " + " ".join(script_src),
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com data:",
            "img-src 'self' data:",
            "connect-src " + " ".join(connect_src),
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "object-src 'none'",
        ]
    )


def _origin(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"

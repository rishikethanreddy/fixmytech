"""Request-level protections: request ids, size limits, rate limiting, security headers, access tokens."""

from __future__ import annotations

import hashlib
import logging
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

log = logging.getLogger("fixmytech.http")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_matches(token: str, stored_hash: str) -> bool:
    return secrets.compare_digest(hash_token(token), stored_hash)


class SlidingWindowLimiter:
    """In-process limiter. Sufficient for a single-instance MVP; use a shared store when scaling out."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window_s = limit, window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.limit:
                return False, self.window_s - (now - q[0])
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory
                self._hits.clear()
            return True, 0.0


CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self' http://127.0.0.1:* http://localhost:*; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'")


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class ProtectionMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, max_body_bytes: int, limiter: SlidingWindowLimiter, trust_proxy: bool) -> None:
        super().__init__(app)
        self.max_body = max_body_bytes
        self.limiter = limiter
        self.trust_proxy = trust_proxy

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        rid = request.headers.get("x-request-id")
        request.state.request_id = rid if rid and len(rid) <= 64 and rid.isascii() else uuid.uuid4().hex
        started = time.monotonic()

        def err(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
            return JSONResponse({"error": {"code": code, "message": message, "request_id": request.state.request_id}},
                                status, headers=headers)

        if request.url.path.startswith("/api/"):
            ok, retry = self.limiter.allow(client_ip(request, self.trust_proxy))
            if not ok:
                return err(429, "rate_limited", "Too many requests. Please slow down.",
                           {"Retry-After": str(int(retry) + 1)})
            if request.method in ("POST", "PUT", "PATCH"):
                length = request.headers.get("content-length")
                if length is not None and (not length.isdigit() or int(length) > self.max_body):
                    return err(413, "payload_too_large", "The request body is too large.")
                body = await request.body()
                if len(body) > self.max_body:
                    return err(413, "payload_too_large", "The request body is too large.")

        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        # Only the phone test page may use the camera/microphone (getUserMedia), and only for this origin.
        media = "(self)" if request.url.path.startswith("/p/") else "()"
        response.headers["Permissions-Policy"] = f"camera={media}, microphone={media}, geolocation=(), usb=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Content-Security-Policy"] = CSP
            # The site's own files: always revalidate (ETag makes this cheap) so an update shows up on the next load.
            response.headers.setdefault("Cache-Control", "no-cache")
        # Log the route, never bodies, tokens or query strings.
        log.info("request id=%s method=%s path=%s status=%s ms=%d", request.state.request_id, request.method,
                 _safe_path(request), response.status_code, int((time.monotonic() - started) * 1000))
        return response


def _safe_path(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path.split("?")[0][:100]

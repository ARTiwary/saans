"""Cross-cutting HTTP concerns: rate limiting, security headers, gzip and JSON errors."""
from __future__ import annotations

import gzip
import threading
import time

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.wrappers import Response

from .config import RATE_LIMITS

_hits: dict[tuple[str, str], list[float]] = {}
_lock = threading.Lock()
MAX_TRACKED_CLIENTS = 5000
GZIP_MIN_BYTES = 1024

#: Strict policy for API/JSON responses; HTML pages get a per-page policy (see ``pages.py``).
API_CSP = "default-src 'none'; frame-ancestors 'none'"


def reset_rate_limits() -> None:
    """Forget all recorded requests (used by tests)."""
    with _lock:
        _hits.clear()


def client_ip() -> str:
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0].strip()


def _rate_limit() -> Response | None:
    limit = RATE_LIMITS.get(request.path)
    if not limit:
        return None
    max_hits, window = limit
    key, now = (client_ip(), request.path), time.time()
    with _lock:
        if len(_hits) > MAX_TRACKED_CLIENTS:  # drop stale clients so memory cannot grow without bound
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > window]:
                del _hits[k]
        recent = [t for t in _hits.get(key, []) if now - t < window]
        if len(recent) >= max_hits:
            _hits[key] = recent
            resp = jsonify(error="Too many requests - please wait a minute.")
            resp.status_code = 429
            resp.headers["Retry-After"] = str(window)
            return resp
        recent.append(now)
        _hits[key] = recent
    return None


def _gzip(resp: Response) -> None:
    """Compress text/JSON bodies when the client accepts gzip."""
    h = resp.headers
    if (resp.status_code != 200 or resp.direct_passthrough or "Content-Encoding" in h
            or "gzip" not in request.headers.get("Accept-Encoding", "")
            or not ((resp.mimetype or "").startswith("text/") or resp.mimetype == "application/json")):
        return
    data = resp.get_data()
    if len(data) > GZIP_MIN_BYTES:
        resp.set_data(gzip.compress(data, compresslevel=6))
        h["Content-Encoding"] = "gzip"
        h["Content-Length"] = str(len(resp.get_data()))
        h.add("Vary", "Accept-Encoding")


def _finish(resp: Response) -> Response:
    h = resp.headers
    is_api = request.path.startswith("/api/")
    h.setdefault("Content-Security-Policy", API_CSP)
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if is_api:
        h["Cache-Control"] = "no-store"
    _gzip(resp)
    return resp


def _json_errors(e: HTTPException):
    """API clients always get JSON errors, never HTML error pages."""
    if request.path.startswith("/api/"):
        return jsonify(error=e.description or e.name), e.code
    return e


def init_security(app: Flask) -> None:
    """Attach rate limiting, headers, compression and JSON errors to ``app``."""
    app.before_request(_rate_limit)
    app.after_request(_finish)
    app.register_error_handler(HTTPException, _json_errors)

"""Serving the two HTML pages: cached in memory, ETag-revalidated, with a hash-based CSP.

The pages carry one inline ``<style>`` and one inline ``<script>``. Instead of allowing
``'unsafe-inline'`` we allow exactly those blocks by their SHA-256 hash, so injected markup
cannot run script.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re

from flask import request
from werkzeug.wrappers import Response

from .config import ROOT

_INLINE = {
    "script": re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I),
    "style": re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I),
}
_cache: dict[str, tuple[bytes, str, str]] = {}


def _sha256(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


def build_csp(html: str) -> str:
    """Content-Security-Policy that allows only this page's own inline script/style blocks."""
    scripts = " ".join(_sha256(m) for m in _INLINE["script"].findall(html)) or "'none'"
    styles = " ".join(_sha256(m) for m in _INLINE["style"].findall(html))
    return "; ".join([
        "default-src 'self'",
        f"script-src {scripts}",
        f"style-src 'self' {styles} https://fonts.googleapis.com".replace("  ", " "),
        "style-src-attr 'unsafe-inline'",   # element style="" attributes only; no inline <style>/<script>
        "font-src https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
        "frame-ancestors 'self'",
        "base-uri 'none'",
        "form-action 'self'",
        "object-src 'none'",
    ])


def _load(name: str) -> tuple[bytes, str, str] | None:
    """Read a page once. Also searches the CWD so it works inside a Vercel function."""
    if name not in _cache:
        for base in (ROOT, os.getcwd(), os.path.join(os.getcwd(), "..")):
            path = os.path.join(base, name)
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    body = f.read()
                etag = hashlib.sha256(body).hexdigest()[:32]
                _cache[name] = (body, etag, build_csp(body.decode("utf-8")))
                break
        else:
            return None
    return _cache[name]


def serve_page(name: str) -> Response:
    page = _load(name)
    if page is None:
        return Response("Page not found: " + name, status=404)
    body, etag, csp = page
    resp = Response(body, mimetype="text/html; charset=utf-8")
    resp.set_etag(etag)
    resp.headers["Cache-Control"] = "no-cache"  # always revalidate; 304 when unchanged
    resp.headers["Content-Security-Policy"] = csp
    return resp.make_conditional(request)

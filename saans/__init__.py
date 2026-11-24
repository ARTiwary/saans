"""Saans - a GenAI assistant that helps families navigate severe winter smog.

Stack: Flask + Groq (LLM) + Open-Meteo (live air quality, no API key needed).
The Groq key lives only on the server (``GROQ_API_KEY``).

Modules
-------
``aqi``        live air data, AQI bands, clean-window detection
``impact``     deterministic exposure / cigarette / CO2 model
``llm``        Groq prompts, JSON-mode plan generation, chat
``fallback``   offline rule-based plan
``routes``     pages + JSON API
``security``   rate limiting, headers, gzip, JSON errors
``pages``      HTML serving with ETag and hash-based CSP
"""
from __future__ import annotations

from flask import Flask

from .config import MAX_BODY_BYTES
from .routes import bp
from .security import init_security

__all__ = ["create_app"]


def create_app() -> Flask:
    """Application factory."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    init_security(app)
    app.register_blueprint(bp)
    return app

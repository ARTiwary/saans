"""Runtime configuration, read from the environment once at import time."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv is optional
    pass

#: Project root (the folder holding landing.html / index.html).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
WX_URL = "https://api.open-meteo.com/v1/forecast"
GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"

#: Largest accepted request body, in bytes.
MAX_BODY_BYTES = 64 * 1024
#: path -> (max requests, window in seconds) per client IP.
RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/api/plan": (30, 60),
    "/api/chat": (30, 60),
    "/api/air": (120, 60),
    "/api/geocode": (120, 60),
    "/api/impact": (120, 60),
}


@dataclass
class Settings:
    """Mutable so tests can flip the Groq key on and off."""

    groq_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", "").strip())
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))

    @property
    def groq_enabled(self) -> bool:
        return bool(self.groq_key)


settings = Settings()

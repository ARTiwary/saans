"""Shared fixtures. Tests never touch the network: Open-Meteo and Groq are faked."""
import math
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("GROQ_API_KEY", None)

from app import app as flask_app  # noqa: E402
from saans import aqi, cache, security, upstream  # noqa: E402
from saans.config import settings  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200, text=""):
        self._payload, self.status_code, self.text = payload, status, text or str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


def sample_air_payload(base_pm=135.0):
    times = [f"2026-11-{24 + d}T{h:02d}:00" for d in (0, 1) for h in range(24)]
    pm = [round(base_pm + 60 * math.cos(2 * math.pi * ((i % 24) - 3) / 24), 1) for i in range(48)]
    return {
        "current": {"time": "2026-11-24T09:00", "us_aqi": 180, "pm2_5": pm[9], "pm10": 210,
                    "nitrogen_dioxide": 48, "ozone": 22},
        "hourly": {"time": times, "us_aqi": [int(x * 1.3) for x in pm], "pm2_5": pm},
    }


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    security.reset_rate_limits()
    cache.AIR_CACHE.clear()
    cache.GEO_CACHE.clear()
    monkeypatch.setattr(settings, "groq_key", "")   # every test starts in offline mode
    yield


@pytest.fixture
def client():
    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


@pytest.fixture
def fake_upstream(monkeypatch):
    """Route Open-Meteo calls to canned data and count them."""
    calls = {"air": 0, "wx": 0, "geo": 0}

    def fake_get(url, params=None, timeout=0):
        if "air-quality" in url:
            calls["air"] += 1
            return FakeResponse(sample_air_payload())
        if "geocoding" in url:
            calls["geo"] += 1
            return FakeResponse({"results": [{"name": "Delhi", "admin1": "Delhi", "country": "India",
                                              "latitude": 28.65, "longitude": 77.23}]})
        calls["wx"] += 1
        return FakeResponse({"current": {"temperature_2m": 11, "relative_humidity_2m": 68, "wind_speed_10m": 3}})

    monkeypatch.setattr(upstream.session, "get", fake_get)
    return calls


@pytest.fixture
def air_context(fake_upstream):
    return aqi.fetch_air(28.65, 77.23)

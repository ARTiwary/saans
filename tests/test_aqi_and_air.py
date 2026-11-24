"""AQI banding, forecast windows and the air-quality fetcher."""
import pytest

import app as saans


@pytest.mark.parametrize("aqi,idx,label", [
    (0, 0, "Good"), (50, 0, "Good"), (51, 1, "Moderate"), (100, 1, "Moderate"),
    (150, 2, "Unhealthy for sensitive groups"), (200, 3, "Unhealthy"),
    (300, 4, "Very unhealthy"), (301, 5, "Hazardous"), (999, 5, "Hazardous"), (None, 0, "Good"),
])
def test_band_boundaries(aqi, idx, label):
    got_idx, got_label, color = saans.band(aqi)
    assert (got_idx, got_label) == (idx, label)
    assert color.startswith("#")


def test_advice_exists_for_every_band():
    assert len(saans.ADVICE) == len(saans.BANDS)


def test_fmt_hour():
    assert saans._fmt_hour(0) == "12 AM"
    assert saans._fmt_hour(12) == "12 PM"
    assert saans._fmt_hour(15) == "3 PM"
    assert saans._fmt_hour(24) == "12 AM"


def test_windows_skip_missing_data_and_average():
    hours = [{"t": f"2026-11-24T{h:02d}:00", "aqi": 100 + h, "pm25": 50.0 + h} for h in range(5)]
    hours[1]["pm25"] = None
    wins = saans._windows(hours)
    assert [w["i"] for w in wins] == [2]          # only the window with complete data survives
    assert wins[0]["pm25"] == pytest.approx(53.0)


def test_fetch_air_shape(air_context):
    a = air_context
    assert a["aqi"] == 180 and a["category"] == "Unhealthy"
    assert a["temp"] == 11 and a["humidity"] == 68 and a["wind"] == 3
    assert 0 < len(a["forecast"]) <= 24
    assert a["best"]["label"] and a["worst"]["label"]
    assert a["trend"] in {"improving", "steady", "worsening"}
    assert a["cigarettes"] == pytest.approx(a["pm25"] / 22, abs=0.06)


def test_best_window_is_in_daytime(air_context):
    best = air_context["best"]
    assert best["day"] in {"today", "tomorrow"}


def test_fetch_air_is_cached(fake_upstream):
    saans.fetch_air(28.65, 77.23)
    saans.fetch_air(28.651, 77.231)               # same ~1 km grid cell
    assert fake_upstream["air"] == 1 and fake_upstream["wx"] == 1


def test_weather_failure_is_not_fatal(monkeypatch, fake_upstream):
    real = saans._http.get

    def flaky(url, params=None, timeout=0):
        if "air-quality" in url:
            return real(url, params=params, timeout=timeout)
        raise saans.requests.ConnectionError("weather down")

    monkeypatch.setattr(saans._http, "get", flaky)
    a = saans.fetch_air(1.0, 1.0)
    assert a["aqi"] == 180 and a["temp"] is None


def test_ttl_cache_expiry_and_eviction(monkeypatch):
    cache = saans.TTLCache(ttl=10, maxsize=2)
    now = [1000.0]
    monkeypatch.setattr(saans.time, "time", lambda: now[0])
    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)                              # evicts the oldest entry
    assert cache.get("a") is None and cache.get("c") == 3
    now[0] += 11
    assert cache.get("c") is None                  # expired

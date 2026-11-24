"""AQI banding, forecast windows and the air-quality fetcher."""
import pytest
import requests

from saans import aqi, cache, upstream


@pytest.mark.parametrize("value,idx,label", [
    (0, 0, "Good"), (50, 0, "Good"), (51, 1, "Moderate"), (100, 1, "Moderate"),
    (150, 2, "Unhealthy for sensitive groups"), (200, 3, "Unhealthy"),
    (300, 4, "Very unhealthy"), (301, 5, "Hazardous"), (999, 5, "Hazardous"), (None, 0, "Good"),
])
def test_band_boundaries(value, idx, label):
    got_idx, got_label, color = aqi.band(value)
    assert (got_idx, got_label) == (idx, label)
    assert color.startswith("#")


def test_advice_exists_for_every_band():
    assert len(aqi.ADVICE) == len(aqi.BANDS)


def test_fmt_hour():
    assert aqi.fmt_hour(0) == "12 AM"
    assert aqi.fmt_hour(12) == "12 PM"
    assert aqi.fmt_hour(15) == "3 PM"
    assert aqi.fmt_hour(24) == "12 AM"


def test_windows_skip_missing_data_and_average():
    hours = [{"t": f"2026-11-24T{h:02d}:00", "aqi": 100 + h, "pm25": 50.0 + h} for h in range(5)]
    hours[1]["pm25"] = None
    wins = aqi.windows(hours)
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
    aqi.fetch_air(28.65, 77.23)
    aqi.fetch_air(28.651, 77.231)               # same ~1 km grid cell
    assert fake_upstream["air"] == 1 and fake_upstream["wx"] == 1


def test_weather_failure_is_not_fatal(monkeypatch, fake_upstream):
    real = upstream.session.get

    def flaky(url, params=None, timeout=0):
        if "air-quality" in url:
            return real(url, params=params, timeout=timeout)
        raise requests.ConnectionError("weather down")

    monkeypatch.setattr(upstream.session, "get", flaky)
    a = aqi.fetch_air(1.0, 1.0)
    assert a["aqi"] == 180 and a["temp"] is None


def test_ttl_cache_expiry_and_eviction(monkeypatch):
    store = cache.TTLCache(ttl=10, maxsize=2)
    now = [1000.0]
    monkeypatch.setattr(cache.time, "time", lambda: now[0])
    store.set("a", 1)
    store.set("b", 2)
    store.set("c", 3)                              # evicts the oldest entry
    assert store.get("a") is None and store.get("c") == 3
    now[0] += 11
    assert store.get("c") is None                  # expired


def test_text_colours_meet_wcag_aa_on_light_backgrounds():
    """The coloured AQI label must be readable (>= 4.5:1) on both page backgrounds."""
    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    def contrast(a, b):
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    for _top, label, _fill, text in aqi.BANDS:
        for bg in ("#FFFFFF", "#F4F8FB"):
            assert contrast(text, bg) >= 4.5, (label, bg)


def test_trend_thresholds():
    rising = [{"pm25": 100}] + [{"pm25": 160}] * 6
    falling = [{"pm25": 100}] + [{"pm25": 40}] * 6
    flat = [{"pm25": 100}] + [{"pm25": 102}] * 6
    assert aqi.trend(rising, 100) == "worsening"
    assert aqi.trend(falling, 100) == "improving"
    assert aqi.trend(flat, 100) == "steady"
    assert aqi.trend([], None) == "steady"


def test_hourly_forecast_starts_at_current_hour_and_is_capped():
    hourly = {"time": [f"2026-11-2{d}T{h:02d}:00" for d in (4, 5) for h in range(24)],
              "us_aqi": list(range(48)), "pm2_5": [10.0] * 48}
    hours = aqi.hourly_forecast(hourly, "2026-11-24T09")
    assert hours[0]["t"] == "2026-11-24T09:00" and len(hours) == 24
    assert all(h["color"].startswith("#") for h in hours)


def test_snapshot_handles_empty_payloads():
    snap = aqi.build_snapshot({}, {})
    assert snap["aqi"] is None and snap["best"] is None and snap["worst"] is None
    assert snap["forecast"] == [] and snap["category"] == "Good"

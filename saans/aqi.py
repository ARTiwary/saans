"""Live air quality: AQI bands, 3-hour forecast windows and the Open-Meteo fetcher."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import upstream
from .cache import AIR_CACHE
from .config import AIR_URL, WX_URL

#: (upper AQI bound, label, fill colour, AA-contrast text colour)
BANDS = [
    (50, "Good", "#2ecc71", "#176B3A"),
    (100, "Moderate", "#f1c40f", "#7D5A00"),
    (150, "Unhealthy for sensitive groups", "#e67e22", "#A04A00"),
    (200, "Unhealthy", "#e74c3c", "#B03A2E"),
    (300, "Very unhealthy", "#9b59b6", "#7D3C98"),
    (10 ** 9, "Hazardous", "#8e1b3d", "#8E1B3D"),
]

ADVICE = [
    "Great air - enjoy outdoor play and exercise.",
    "Fine for most people. Unusually sensitive people should go easy on long, hard outdoor workouts.",
    "Children, elders and people with asthma/heart conditions should limit prolonged outdoor exertion.",
    "Everyone: cut long outdoor exertion. Sensitive groups stay indoors where possible; wear a well-fitted N95/FFP2 outside.",
    "Skip outdoor exercise, keep windows shut, run a purifier, and use an N95 for essential trips.",
    "Health-emergency air. Stay in, seal gaps, run a purifier, and wear an N95 for any essential trip.",
]

#: Hours (local, 24h) in which the "cleanest window" is searched.
DAYTIME_START, DAYTIME_END = 6, 18
FORECAST_HOURS = 24
#: Berkeley Earth rule of thumb: ~22 ug/m3 PM2.5 over 24 h is about one cigarette a day.
PM25_PER_CIGARETTE = 22.0


def band(aqi: float | None) -> tuple[int, str, str]:
    """Map a US AQI value to ``(band index, label, fill colour)``."""
    aqi = aqi or 0
    for i, (top, label, color, _text) in enumerate(BANDS):
        if aqi <= top:
            return i, label, color
    return len(BANDS) - 1, BANDS[-1][1], BANDS[-1][2]


def fmt_hour(h: int) -> str:
    """12-hour clock label, e.g. ``15`` -> ``3 PM``."""
    h = h % 24
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"


def windows(hours: list[dict]) -> list[dict]:
    """Rolling 3-hour averages of the forecast (skips windows with missing data)."""
    out = []
    for i in range(len(hours) - 2):
        w = hours[i:i + 3]
        if any(x["pm25"] is None or x["aqi"] is None for x in w):
            continue
        out.append({
            "i": i,
            "start_h": int(w[0]["t"][11:13]),
            "date": w[0]["t"][:10],
            "pm25": round(sum(x["pm25"] for x in w) / 3, 1),
            "aqi": round(sum(x["aqi"] for x in w) / 3),
        })
    return out


def window_label(win: dict, today: str) -> dict:
    return {
        "label": f"{fmt_hour(win['start_h'])} - {fmt_hour(win['start_h'] + 3)}",
        "day": "today" if win["date"] == today else "tomorrow",
        "pm25": win["pm25"],
        "aqi": win["aqi"],
        "i": win["i"],
    }


def trend(hours: list[dict], pm25: float | None) -> str:
    """Compare the next 6 h average with now: ``improving``, ``steady`` or ``worsening``."""
    nxt = [x["pm25"] for x in hours[1:7] if x["pm25"] is not None]
    if not (nxt and pm25):
        return "steady"
    delta = sum(nxt) / len(nxt) - pm25
    threshold = max(5, pm25 * 0.15)
    if delta > threshold:
        return "worsening"
    if delta < -threshold:
        return "improving"
    return "steady"


def hourly_forecast(hourly: dict, now_key: str) -> list[dict]:
    """Next 24 hourly readings (from the current hour), each tagged with its band colour."""
    hours: list[dict] = []
    for t, aq, pm in zip(hourly.get("time", []), hourly.get("us_aqi", []), hourly.get("pm2_5", []), strict=False):
        if t[:13] >= now_key and len(hours) < FORECAST_HOURS:
            hours.append({"t": t, "aqi": aq, "pm25": pm, "color": band(aq)[2]})
    return hours


def fetch_weather(lat: float, lon: float) -> dict:
    """Current weather; failures are non-fatal (the plan works without it)."""
    try:
        data = upstream.get_json(WX_URL, {
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
            "timezone": "auto",
        }, timeout=10)
        return data.get("current", {})
    except (upstream.RequestError, ValueError):
        return {}


def build_snapshot(air: dict, wx: dict) -> dict[str, Any]:
    """Turn raw Open-Meteo payloads into the JSON the front end consumes."""
    cur = air.get("current", {})
    hours = hourly_forecast(air.get("hourly", {}), (cur.get("time") or "")[:13])
    today = (cur.get("time") or "")[:10]
    wins = windows(hours)
    day_wins = [w for w in wins if DAYTIME_START <= w["start_h"] <= DAYTIME_END]
    aqi, pm25 = cur.get("us_aqi"), cur.get("pm2_5")
    idx, label, color = band(aqi)
    return {
        "time": cur.get("time"),
        "aqi": aqi, "category": label, "color": color, "text_color": BANDS[idx][3], "idx": idx,
        "pm25": pm25, "pm10": cur.get("pm10"),
        "no2": cur.get("nitrogen_dioxide"), "o3": cur.get("ozone"),
        "so2": cur.get("sulphur_dioxide"), "co": cur.get("carbon_monoxide"),
        "temp": wx.get("temperature_2m"), "humidity": wx.get("relative_humidity_2m"),
        "wind": wx.get("wind_speed_10m"),
        "cigarettes": round((pm25 or 0) / PM25_PER_CIGARETTE, 1),
        "trend": trend(hours, pm25),
        "best": window_label(min(day_wins, key=lambda w: w["pm25"]), today) if day_wins else None,
        "worst": window_label(max(wins, key=lambda w: w["pm25"]), today) if wins else None,
        "advice": ADVICE[idx], "forecast": hours,
    }


def fetch_air(lat: float, lon: float) -> dict[str, Any]:
    """Live air quality + weather for a point, cached for 10 minutes (~1 km grid)."""
    key = (round(lat, 2), round(lon, 2))
    cached = AIR_CACHE.get(key)
    if cached is not None:
        return cached
    with ThreadPoolExecutor(max_workers=1) as pool:  # weather runs while the air request is in flight
        wx_future = pool.submit(fetch_weather, lat, lon)
        air = upstream.get_json(AIR_URL, {
            "latitude": lat, "longitude": lon,
            "current": "us_aqi,pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone",
            "hourly": "us_aqi,pm2_5", "forecast_days": 2, "timezone": "auto",
        }, timeout=15)
        wx = wx_future.result()
    snapshot = build_snapshot(air, wx)
    AIR_CACHE.set(key, snapshot)
    return snapshot

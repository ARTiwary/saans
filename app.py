"""Saans - a GenAI assistant that helps families & communities navigate
severe winter smog and urban air pollution.

Stack: Flask + Groq (LLM) + Open-Meteo (live air quality, no API key needed).
The Groq key lives only on the server (GROQ_API_KEY env var).
"""
import json
import os
import time

import requests
from flask import Flask, Response, jsonify, request

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv is optional
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
WX_URL = "https://api.open-meteo.com/v1/forecast"
GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"

app = Flask(__name__)

# ----------------------------------------------------------------------------
# tiny in-memory rate limiter (protects your Groq quota during a live demo)
# ----------------------------------------------------------------------------
_hits = {}


@app.before_request
def _rate_limit():
    if request.path in ("/api/plan", "/api/chat"):
        ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0]
        now = time.time()
        q = [t for t in _hits.get(ip, []) if now - t < 60]
        if len(q) >= 30:
            _hits[ip] = q
            return jsonify(error="Too many requests - please wait a minute."), 429
        q.append(now)
        _hits[ip] = q


# ----------------------------------------------------------------------------
# AQI helpers
# ----------------------------------------------------------------------------
BANDS = [
    (50, "Good", "#2ecc71"),
    (100, "Moderate", "#f1c40f"),
    (150, "Unhealthy for sensitive groups", "#e67e22"),
    (200, "Unhealthy", "#e74c3c"),
    (300, "Very unhealthy", "#9b59b6"),
    (10 ** 9, "Hazardous", "#8e1b3d"),
]

ADVICE = [
    "Great air - enjoy outdoor play and exercise.",
    "Fine for most people. Unusually sensitive people should go easy on long, hard outdoor workouts.",
    "Children, elders and people with asthma/heart conditions should limit prolonged outdoor exertion.",
    "Everyone: cut long outdoor exertion. Sensitive groups stay indoors where possible; wear a well-fitted N95/FFP2 outside.",
    "Skip outdoor exercise, keep windows shut, run a purifier, and use an N95 for essential trips.",
    "Health-emergency air. Stay in, seal gaps, run a purifier, and wear an N95 for any essential trip.",
]


def band(aqi):
    aqi = aqi or 0
    for i, (top, label, color) in enumerate(BANDS):
        if aqi <= top:
            return i, label, color
    return 5, BANDS[-1][1], BANDS[-1][2]


def _fmt_hour(h):
    h = h % 24
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"


def _windows(hours):
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


def _label(win, today):
    return {
        "label": f"{_fmt_hour(win['start_h'])} - {_fmt_hour(win['start_h'] + 3)}",
        "day": "today" if win["date"] == today else "tomorrow",
        "pm25": win["pm25"],
        "aqi": win["aqi"],
        "i": win["i"],
    }


def fetch_air(lat, lon):
    a = requests.get(AIR_URL, params={
        "latitude": lat, "longitude": lon,
        "current": "us_aqi,pm10,pm2_5,carbon_monoxide,nitrogen_dioxide,sulphur_dioxide,ozone",
        "hourly": "us_aqi,pm2_5", "forecast_days": 2, "timezone": "auto",
    }, timeout=15)
    a.raise_for_status()
    a = a.json()
    cur = a.get("current", {})

    wx = {}
    try:
        w = requests.get(WX_URL, params={
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
            "timezone": "auto",
        }, timeout=10).json()
        wx = w.get("current", {})
    except Exception:
        pass

    h = a.get("hourly", {})
    now_key = (cur.get("time") or "")[:13]
    hours = []
    for t, aq, pm in zip(h.get("time", []), h.get("us_aqi", []), h.get("pm2_5", [])):
        if t[:13] >= now_key and len(hours) < 24:
            hours.append({"t": t, "aqi": aq, "pm25": pm})
    for x in hours:
        x["color"] = band(x["aqi"])[2]

    today = (cur.get("time") or "")[:10]
    wins = _windows(hours)
    day_wins = [w for w in wins if 6 <= w["start_h"] <= 18]
    best = _label(min(day_wins, key=lambda w: w["pm25"]), today) if day_wins else None
    worst = _label(max(wins, key=lambda w: w["pm25"]), today) if wins else None

    aqi = cur.get("us_aqi")
    pm25 = cur.get("pm2_5")
    idx, label, color = band(aqi)

    trend = "steady"
    nxt = [x["pm25"] for x in hours[1:7] if x["pm25"] is not None]
    if nxt and pm25:
        d = sum(nxt) / len(nxt) - pm25
        if d > max(5, pm25 * 0.15):
            trend = "worsening"
        elif d < -max(5, pm25 * 0.15):
            trend = "improving"

    return {
        "time": cur.get("time"),
        "aqi": aqi, "category": label, "color": color, "idx": idx,
        "pm25": pm25, "pm10": cur.get("pm10"),
        "no2": cur.get("nitrogen_dioxide"), "o3": cur.get("ozone"),
        "so2": cur.get("sulphur_dioxide"), "co": cur.get("carbon_monoxide"),
        "temp": wx.get("temperature_2m"), "humidity": wx.get("relative_humidity_2m"),
        "wind": wx.get("wind_speed_10m"),
        # Berkeley Earth rule of thumb: ~22 ug/m3 PM2.5 over 24h ~ 1 cigarette/day
        "cigarettes": round((pm25 or 0) / 22, 1),
        "trend": trend, "best": best, "worst": worst,
        "advice": ADVICE[idx], "forecast": hours,
    }


# ----------------------------------------------------------------------------
# Deterministic impact engine (so numbers are never hallucinated by the LLM)
# mode: (speed km/h, in-transit exposure factor vs ambient, kg CO2 per pax-km)
# ----------------------------------------------------------------------------
MODES = {
    "walk": (4.5, 1.0, 0.0), "cycle": (12, 1.05, 0.0), "two_wheeler": (25, 1.1, 0.05),
    "auto_cab": (18, 0.9, 0.10), "car": (20, 0.5, 0.17), "bus": (15, 1.0, 0.03),
    "metro": (30, 0.6, 0.03), "wfh": (1, 0.0, 0.0),
}
OPEN_AIR_MODES = {"walk", "cycle", "two_wheeler", "auto_cab", "bus"}
WORK_DAYS = 260


def _exposure(pm, best_pm, out_h, mode, km, windows_closed, purifier, mask, shift):
    speed, mf, _ = MODES.get(mode, MODES["walk"])
    commute_h = 0 if mode == "wfh" else min(4.0, 2 * km / speed)
    out_h = min(max(out_h, 0), 16)
    indoor_h = max(24 - commute_h - out_h, 0)
    if windows_closed and purifier:
        infil = 0.25
    elif windows_closed:
        infil = 0.55
    elif purifier:
        infil = 0.5
    else:
        infil = 0.8
    mk = 0.4 if mask else 1.0
    out_pm = best_pm if shift else pm
    c_mk = mk if mode in OPEN_AIR_MODES else 1.0
    total = indoor_h * pm * infil + out_h * out_pm * mk + commute_h * pm * mf * c_mk
    return total / 24.0


def compute_impact(d):
    f = lambda k, default: float(d.get(k) if d.get(k) not in (None, "") else default)
    pm = f("pm25", 60)
    best_pm = min(f("best_pm25", pm * 0.7), pm) if d.get("best_pm25") is not None else pm * 0.75
    out_h = f("outdoor_hours", 2)
    km = f("commute_km", 8)
    mode = d.get("mode") or "two_wheeler"
    base_closed = (d.get("baseline_windows") == "closed")
    base_pur = bool(d.get("baseline_purifier"))
    sc = d.get("scenario", {}) or {}
    new_mode = sc.get("new_mode") or mode
    if new_mode not in MODES:
        new_mode = mode

    base = _exposure(pm, best_pm, out_h, mode, km, base_closed, base_pur, False, False)
    scen = _exposure(pm, best_pm, out_h, new_mode, km,
                     base_closed or bool(sc.get("windows_closed")),
                     base_pur or bool(sc.get("purifier")),
                     bool(sc.get("mask")), bool(sc.get("shift_outdoor")))

    def lever(label, **kw):
        args = dict(mode=mode, windows_closed=base_closed, purifier=base_pur, mask=False, shift=False)
        args.update(kw)
        e = _exposure(pm, best_pm, out_h, args["mode"], km, args["windows_closed"],
                      args["purifier"], args["mask"], args["shift"])
        return {"label": label, "reduction_pct": round(100 * (base - e) / base, 1) if base else 0}

    levers = []
    if not base_closed:
        levers.append(lever("Keep windows shut during smog hours", windows_closed=True))
    if not base_pur:
        levers.append(lever("Run an air purifier (HEPA) in the main room", purifier=True))
    levers.append(lever("Wear a fitted N95 outdoors/in open transport", mask=True))
    levers.append(lever("Move outdoor time to the cleanest window", shift=True))
    for m, name in (("metro", "metro"), ("bus", "bus"), ("cycle", "cycle")):
        if m != mode and mode != "wfh":
            levers.append(lever(f"Commute by {name}", mode=m))
    levers.sort(key=lambda x: -x["reduction_pct"])

    co2_base = MODES.get(mode, MODES["walk"])[2] * 2 * km * WORK_DAYS if mode != "wfh" else 0
    co2_new = MODES.get(new_mode, MODES["walk"])[2] * 2 * km * WORK_DAYS if new_mode != "wfh" else 0
    return {
        "baseline_pm25": round(base, 1), "scenario_pm25": round(scen, 1),
        "reduction_pct": round(100 * (base - scen) / base, 1) if base else 0,
        "cigs_baseline": round(base / 22, 2), "cigs_scenario": round(scen / 22, 2),
        "co2_saved_kg_year": round(co2_base - co2_new),
        "levers": levers[:5],
        "assumptions": [
            "Daily time-weighted PM2.5 exposure = indoor + outdoor + commute hours.",
            "Indoor/outdoor ratio: windows open 0.8, shut 0.55, shut + HEPA 0.25, open + HEPA 0.5.",
            "Fitted N95 cuts inhaled particles by ~60% in real-world use (lab values are higher).",
            "Cigarette equivalence: ~22 ug/m3 PM2.5 over 24h ~ 1 cigarette/day (Berkeley Earth rule of thumb).",
            "These are planning estimates, not medical measurements.",
        ],
    }


@app.post("/api/impact")
def api_impact():
    return jsonify(compute_impact(request.get_json(force=True) or {}))


# ----------------------------------------------------------------------------
# Groq
# ----------------------------------------------------------------------------
def groq_chat(messages, json_mode=False, max_tokens=2800, temperature=0.4):
    if not GROQ_KEY:
        raise RuntimeError("GROQ_API_KEY is not set")
    body = {"model": GROQ_MODEL, "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    last = None
    for attempt in range(2):
        r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_KEY}"},
                          json=body, timeout=45)
        if r.status_code == 429 and attempt == 0:
            time.sleep(2)
            continue
        if r.status_code >= 400:
            last = f"Groq API {r.status_code}: {r.text[:200]}"
            break
        return r.json()["choices"][0]["message"]["content"]
    raise RuntimeError(last or "Groq request failed")


def _s(v, n=120):
    return str(v)[:n] if v is not None else ""


def ctx_text(ctx, profile):
    a = ctx or {}
    p = profile or {}
    best, worst = a.get("best") or {}, a.get("worst") or {}
    return (
        f"LOCATION: {_s(a.get('place'), 80)}\n"
        f"AIR NOW: US AQI {a.get('aqi')} ({_s(a.get('category'))}); PM2.5 {a.get('pm25')} ug/m3; "
        f"PM10 {a.get('pm10')}; NO2 {a.get('no2')}; O3 {a.get('o3')}; trend next 6h: {a.get('trend')}.\n"
        f"WEATHER: {a.get('temp')} C, humidity {a.get('humidity')}%, wind {a.get('wind')} km/h "
        f"(low wind + cold nights trap smog near the ground).\n"
        f"CLEANEST 3-HOUR WINDOW: {best.get('label')} {best.get('day')} (PM2.5 ~{best.get('pm25')}). "
        f"WORST WINDOW: {worst.get('label')} {worst.get('day')} (PM2.5 ~{worst.get('pm25')}).\n"
        f"HOUSEHOLD: {', '.join(p.get('household', [])) or 'adults only'}; home: {_s(p.get('home'))}; "
        f"commute: {_s(p.get('mode'))} {_s(p.get('commute_km'))} km one-way; "
        f"outdoor hours/day: {_s(p.get('outdoor_hours'))}; windows usually: {_s(p.get('windows'))}; "
        f"owns purifier: {p.get('purifier_owned')}; cooking fuel: {_s(p.get('cooking'))}; "
        f"budget: {_s(p.get('budget'))}; public transit nearby: {p.get('transit')}; "
        f"notes: {_s(p.get('notes'), 200)}"
    )


PLAN_SYSTEM = """You are Saans, an air-quality health coach for families and communities.
Create a practical, location- and lifestyle-specific plan for winter smog / urban air pollution.

Rules:
- Be specific to the place (local transport, local fuels, local winter habits, local weather). Prefer options affordable in the user's budget; mark cost as one of "Free", "Rs", "Rs Rs", "Rs Rs Rs".
- Never invent measurements. Quote only the numbers provided in the context or in IMPACT NUMBERS; otherwise use hedged qualitative language ("noticeably lower", "roughly half").
- For every action give a short "why" (mechanism) and an "impact" (direct effect of this choice on exposure, health or emissions).
- Respect vulnerable members (children, elderly, pregnancy, asthma/heart/lung disease). You are not a doctor: no diagnoses, no medicine dosages.
- Do NOT recommend ozone generators or "ionizer" gadgets. Do NOT promote burning anything for warmth/cooking indoors.
- Keep each detail to 1-2 sentences. Be encouraging, not scary.
Return ONLY valid JSON with exactly this shape:
{
 "headline": "one-line verdict for today",
 "risk_summary": "2-3 sentences explaining what today's air means for THIS household",
 "routine": [{"when":"morning|afternoon|evening|night","title":"","detail":"","why":"","impact":"","cost":""}],
 "indoor_hacks": [{"title":"","detail":"","why":"","impact":"","cost":""}],
 "sustainable_swaps": [{"title":"A -> B","detail":"","why":"","impact":"","cost":""}],
 "community_actions": [{"title":"","detail":"","why":"","impact":"","cost":""}],
 "care": [{"title":"who","detail":"tip"}],
 "warning_signs": ["when to seek medical help"],
 "myth": {"myth":"","truth":""}
}
Counts: routine 7-9 items (cover all four times of day), indoor_hacks 5-6, sustainable_swaps 4-5, community_actions 3-4, care 2-4 (tailored to the household), warning_signs 3-4."""

ITEM_KEYS = ("title", "detail", "why", "impact", "cost")


def _items(lst, extra=()):
    out = []
    for x in (lst or []):
        if isinstance(x, dict) and x.get("title"):
            out.append({k: _s(x.get(k), 400) for k in ITEM_KEYS + tuple(extra)})
    return out


def normalise_plan(p):
    if not isinstance(p, dict) or not p.get("routine"):
        return None
    myth = p.get("myth") if isinstance(p.get("myth"), dict) else {}
    plan = {
        "headline": _s(p.get("headline"), 200),
        "risk_summary": _s(p.get("risk_summary"), 600),
        "routine": _items(p.get("routine"), ("when",)),
        "indoor_hacks": _items(p.get("indoor_hacks")),
        "sustainable_swaps": _items(p.get("sustainable_swaps")),
        "community_actions": _items(p.get("community_actions")),
        "care": [{"title": _s(c.get("title"), 80), "detail": _s(c.get("detail"), 400)}
                 for c in (p.get("care") or []) if isinstance(c, dict)],
        "warning_signs": [_s(w, 200) for w in (p.get("warning_signs") or [])][:6],
        "myth": {"myth": _s(myth.get("myth"), 200), "truth": _s(myth.get("truth"), 400)},
    }
    for r in plan["routine"]:
        if r["when"] not in ("morning", "afternoon", "evening", "night"):
            r["when"] = "morning"
    return plan


def llm_plan(ctx, profile, impact, lang):
    lang_rule = ("Write every string VALUE in simple everyday Hindi (Devanagari script). Keep JSON keys in English."
                 if lang == "hi" else "Write in clear, simple English.")
    imp = (f"IMPACT NUMBERS (computed, use these): current estimated daily PM2.5 exposure "
           f"{impact['baseline_pm25']} ug/m3 -> {impact['scenario_pm25']} ug/m3 if the household follows the plan "
           f"(~{impact['reduction_pct']}% lower). Biggest levers: "
           + "; ".join(f"{l['label']} (~{l['reduction_pct']}%)" for l in impact["levers"][:3]))
    msgs = [
        {"role": "system", "content": PLAN_SYSTEM + "\n" + lang_rule},
        {"role": "user", "content": ctx_text(ctx, profile) + "\n" + imp},
    ]
    return normalise_plan(json.loads(groq_chat(msgs, json_mode=True)))


# ----------------------------------------------------------------------------
# Offline fallback plan (so the demo never breaks if Groq/network fails)
# ----------------------------------------------------------------------------
def _it(title, detail, why, impact, cost="Free", when=None):
    d = {"title": title, "detail": detail, "why": why, "impact": impact, "cost": cost}
    if when:
        d["when"] = when
    return d


def fallback_plan(ctx, profile):
    idx = int((ctx or {}).get("idx") or 0)
    hh = profile.get("household", [])
    best = ((ctx or {}).get("best") or {}).get("label", "early afternoon")
    worst = ((ctx or {}).get("worst") or {}).get("label", "early morning")
    sens = any(x in hh for x in ("children", "elderly", "pregnant", "asthma"))
    bad = idx >= 3
    headline = ("Smog alert: plan your day around the clean window" if bad
                else "Manageable air today - a few smart habits keep it that way")
    plan = {
        "headline": headline,
        "risk_summary": (f"US AQI is {(ctx or {}).get('aqi')} ({(ctx or {}).get('category')}). "
                         + ("Your household includes people who are more sensitive to fine particles, so extra care pays off. " if sens else "")
                         + f"Air is usually cleanest around {best} and dirtiest around {worst}."),
        "routine": [
            _it(f"Do outdoor activity around {best}", "Schedule walks, school pick-ups and errands in the cleanest window.",
                "Winter smog is trapped lowest to the ground at night and early morning.", "Can cut outdoor exposure by a third or more vs. peak hours.", "Free", "morning"),
            _it(f"Avoid being outside around {worst}", "Keep morning workouts indoors on smoggy days.",
                "Cold, still air holds pollutants near the ground.", "Avoids the highest-concentration hours.", "Free", "morning"),
            _it("Ventilate briefly at the cleanest hour", "Open windows 10-15 min only when AQI is lowest, then shut.",
                "Stale indoor air also builds up CO2 and cooking fumes.", "Balances indoor air freshness with low particle entry.", "Free", "afternoon"),
            _it("Wear a fitted N95/FFP2 outdoors" if bad else "Carry an N95 for dusty stretches",
                "Seal around nose and cheeks; cloth and surgical masks filter far less.", "Fine particles (PM2.5) pass through loose masks.",
                "A well-fitted N95 can block most inhaled particles.", "Rs", "afternoon"),
            _it("Wash face, rinse nose, change clothes after coming home", "Saline rinse and a quick wash remove particles on skin and hair.",
                "Particles ride in on clothes and skin.", "Reduces irritation and indoor particle load.", "Free", "evening"),
            _it("Cook with the exhaust fan or chimney on", "Use induction/LPG if possible and ventilate while frying.",
                "Cooking is a major indoor PM2.5 source.", "Lowers evening indoor spikes.", "Free", "evening"),
            _it("Shut windows before sunset", "Evening cooling traps smoke near the ground.", "Pollution rises as the air cools.",
                "Keeps night-time indoor air cleaner.", "Free", "night"),
            _it("Run a purifier or a DIY fan+filter in the bedroom", "Close the door, run on medium while you sleep.",
                "People breathe in the same air for 7-8 hours at night.", "Large share of daily exposure happens in bed.", "Rs Rs", "night"),
        ],
        "indoor_hacks": [
            _it("Seal draughts around doors and windows", "Use weather-strips or rolled towels.", "Gaps let outdoor smog leak in.", "Lowers indoor/outdoor ratio.", "Rs"),
            _it("HEPA purifier sized to the room", "Check CADR; run in the room where you spend most time.", "HEPA captures fine particles.", "Roughly halves indoor PM2.5 in a closed room.", "Rs Rs Rs"),
            _it("DIY box-fan filter (Corsi-Rosenthal box)", "Tape 4 MERV-13/HEPA-grade filters on a box fan.", "Cheap way to clean a room's air.", "Comparable to a purifier at a fraction of the cost.", "Rs Rs"),
            _it("Damp-mop instead of dry sweeping", "Wet-wipe surfaces daily.", "Dry sweeping re-suspends settled dust.", "Less airborne dust.", "Free"),
            _it("Add low-cost PM2.5 monitor", "Watch indoor numbers to see what actually helps.", "What you measure you can improve.", "Turns guesses into proof.", "Rs Rs"),
        ],
        "sustainable_swaps": [
            _it("Private vehicle -> metro/bus", "Do it for the commute 2-3 days a week.", "Fewer vehicles = fewer exhaust particles and CO2.", "Cuts your transport CO2 and in-vehicle exposure.", "Free"),
            _it("Open burning for warmth -> blankets/layers", "Never burn leaves, waste or wood for warmth.", "Open burning is a big local PM2.5 source.", "Direct drop in neighbourhood smoke.", "Rs"),
            _it("Biomass cooking -> LPG/induction", "Use clean fuel; ventilate.", "Biomass produces heavy indoor smoke.", "Large drop in indoor PM2.5 and CO.", "Rs Rs"),
            _it("Idling engine -> engine off at signals", "Switch off when waiting > 30 seconds.", "Idling still emits.", "Less roadside pollution.", "Free"),
        ],
        "community_actions": [
            _it("Residents' pledge: no waste/leaf burning", "Share a simple notice in society groups.", "Local burning spikes PM2.5.", "Visible neighbourhood improvement.", "Free"),
            _it("Carpool / school-bus rota", "Organise 3-4 families per car or take the school bus.", "Cuts cars outside schools.", "Fewer idling cars at pick-up.", "Free"),
            _it("Plant & protect trees; water-sprinkle dust", "Support local plantation and road-dust control.", "Greenery and wet roads reduce dust.", "Cooler, cleaner street-level air over time.", "Free"),
        ],
        "care": ([{"title": "Children", "detail": "Move play indoors on very poor days; give water, wash hands/face after outdoor play."}] if "children" in hh else [])
                + ([{"title": "Elders", "detail": "Keep medicines handy, avoid morning walks in smog, rest in the cleanest room."}] if "elderly" in hh else [])
                + ([{"title": "Asthma / COPD", "detail": "Follow your doctor's action plan; keep your reliever inhaler accessible."}] if "asthma" in hh else [])
                + ([{"title": "Pregnancy", "detail": "Limit outdoor trips in smog hours and discuss mask use with your doctor."}] if "pregnant" in hh else [])
                or [{"title": "Everyone", "detail": "Stay hydrated, sleep well and avoid hard outdoor workouts on bad-air days."}],
        "warning_signs": ["Chest pain or tightness", "Breathlessness at rest or wheeze that does not settle", "Fainting, confusion or a very fast heartbeat",
                          "Lips or face turning blue - seek emergency care immediately"],
        "myth": {"myth": "If the sky looks clear, the air is clean.",
                 "truth": "The most harmful particles (PM2.5) are invisible. Check the number, not the sky."},
    }
    return plan


# ----------------------------------------------------------------------------
# API routes
# ----------------------------------------------------------------------------
def _page(name):
    """Read an HTML page from disk. Works on a normal server and inside a Vercel function."""
    for base in (HERE, os.getcwd(), os.path.join(os.getcwd(), "..")):
        path = os.path.join(base, name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return Response(f.read(), mimetype="text/html; charset=utf-8")
    return Response("Page not found: " + name, status=404)


@app.get("/")
@app.get("/landing")
@app.get("/landing.html")
def landing():
    return _page("landing.html")


@app.get("/app")
@app.get("/app/")
@app.get("/index.html")
def app_page():
    return _page("index.html")


@app.get("/api/health")
def health():
    return jsonify(ok=True, groq=bool(GROQ_KEY), model=GROQ_MODEL)


@app.get("/api/geocode")
def geocode():
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify(results=[])
    try:
        r = requests.get(GEO_URL, params={"name": q, "count": 6, "language": "en", "format": "json"}, timeout=10)
        res = [{"name": x.get("name"), "admin": x.get("admin1"), "country": x.get("country"),
                "lat": x.get("latitude"), "lon": x.get("longitude")} for x in r.json().get("results", [])]
        return jsonify(results=res)
    except Exception as e:
        return jsonify(error=f"Search failed: {e}", results=[]), 502


@app.get("/api/air")
def air():
    try:
        lat = float(request.args["lat"])
        lon = float(request.args["lon"])
        return jsonify(fetch_air(lat, lon))
    except (KeyError, ValueError):
        return jsonify(error="lat and lon are required"), 400
    except Exception as e:
        return jsonify(error=f"Could not load air-quality data: {e}"), 502


@app.post("/api/plan")
def plan():
    data = request.get_json(force=True) or {}
    ctx, profile, lang = data.get("context") or {}, data.get("profile") or {}, data.get("lang", "en")

    # Impact if the household follows the plan
    mode = profile.get("mode") or "two_wheeler"
    sc = {"windows_closed": True, "purifier": profile.get("budget") not in ("none",),
          "mask": (ctx.get("pm25") or 0) > 55, "shift_outdoor": True,
          "new_mode": "metro" if (profile.get("transit") and mode in ("car", "two_wheeler", "auto_cab")) else mode}
    impact = compute_impact({
        "pm25": ctx.get("pm25") or 60, "best_pm25": (ctx.get("best") or {}).get("pm25"),
        "outdoor_hours": profile.get("outdoor_hours", 2), "commute_km": profile.get("commute_km", 8),
        "mode": mode, "baseline_windows": profile.get("windows"),
        "baseline_purifier": profile.get("purifier_owned"), "scenario": sc,
    })

    source, error, plan_obj = "fallback", None, None
    if GROQ_KEY:
        try:
            plan_obj = llm_plan(ctx, profile, impact, lang)
            if plan_obj:
                source = "groq"
        except Exception as e:  # network, auth, bad JSON...
            error = str(e)[:200]
    else:
        error = "GROQ_API_KEY is not configured on the server"
    if not plan_obj:
        plan_obj = fallback_plan(ctx, profile)
    return jsonify(plan=plan_obj, impact=impact, source=source, model=GROQ_MODEL if source == "groq" else None,
                   error=error)


CHAT_SYSTEM = """You are Saans, a warm, practical air-quality assistant for families and communities facing winter smog.
You have the user's live air data and household profile below. Answer their question directly in under 150 words.
- Tailor to their location and lifestyle; give concrete steps and explain the direct impact of each choice.
- Use only the numbers in the context; otherwise be qualitative and hedge honestly.
- Not a doctor: no diagnoses or drug doses. For chest pain, severe breathlessness, fainting or blue lips tell them to seek medical care immediately.
- No ozone generators/ionizers; never advise burning anything indoors.
- Use short bullets only when listing steps. {lang}

CONTEXT
{ctx}"""


@app.post("/api/chat")
def chat():
    data = request.get_json(force=True) or {}
    msgs = [m for m in (data.get("messages") or []) if m.get("role") in ("user", "assistant")][-10:]
    if not msgs:
        return jsonify(error="No message"), 400
    if not GROQ_KEY:
        return jsonify(reply="I need a Groq API key on the server to chat. Add GROQ_API_KEY to .env and restart. "
                             "Meanwhile, the plan above works fully in offline mode.", source="fallback")
    lang = "Reply in simple Hindi (Devanagari)." if data.get("lang") == "hi" else "Reply in English."
    system = CHAT_SYSTEM.format(lang=lang, ctx=ctx_text(data.get("context"), data.get("profile")))
    clean = [{"role": m["role"], "content": _s(m.get("content"), 1500)} for m in msgs]
    try:
        reply = groq_chat([{"role": "system", "content": system}] + clean, max_tokens=500, temperature=0.5)
        return jsonify(reply=reply, source="groq")
    except Exception as e:
        return jsonify(error=f"AI is unavailable right now: {str(e)[:160]}"), 502


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    print(f"Saans running on http://localhost:{port}  (Groq {'ON' if GROQ_KEY else 'OFF - fallback mode'}, model {GROQ_MODEL})")
    app.run(host="0.0.0.0", port=port, debug=False)

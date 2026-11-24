"""HTTP routes: the two pages and the JSON API."""
from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, jsonify, request
from werkzeug.wrappers import Response

from . import aqi, llm, upstream
from .cache import GEO_CACHE
from .config import GEO_URL, settings
from .fallback import fallback_plan
from .impact import DEFAULT_MODE, compute_impact
from .pages import serve_page
from .validation import clip

bp = Blueprint("saans", __name__)

MAX_CHAT_TURNS = 10
MAX_HOUSEHOLD_TAGS = 10
SMOG_MASK_PM25 = 55  # above this we assume the household would wear an N95 in the "follow the plan" scenario
TRANSIT_SWITCH_FROM = ("car", "two_wheeler", "auto_cab")


def body() -> dict[str, Any]:
    """Parsed JSON object body, or a clean 400."""
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        abort(400, "A JSON object body is required.")
    return data


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


# ------------------------------------------------------------------ pages
@bp.get("/")
@bp.get("/landing")
@bp.get("/landing.html")
def landing() -> Response:
    return serve_page("landing.html")


@bp.get("/app")
@bp.get("/app/")
@bp.get("/index.html")
def app_page() -> Response:
    return serve_page("index.html")


# ------------------------------------------------------------------ data
@bp.get("/api/health")
def health():
    return jsonify(ok=True, groq=settings.groq_enabled, model=settings.groq_model)


@bp.get("/api/geocode")
def geocode():
    q = (request.args.get("q") or "").strip()[:80]
    if len(q) < 2:
        return jsonify(results=[])
    cached = GEO_CACHE.get(q.lower())
    if cached is not None:
        return jsonify(results=cached)
    try:
        data = upstream.get_json(GEO_URL, {"name": q, "count": 6, "language": "en", "format": "json"}, timeout=10)
        results = [{"name": x.get("name"), "admin": x.get("admin1"), "country": x.get("country"),
                    "lat": x.get("latitude"), "lon": x.get("longitude")} for x in data.get("results", [])]
    except (upstream.RequestError, ValueError) as e:
        return jsonify(error=f"Search failed: {e}", results=[]), 502
    GEO_CACHE.set(q.lower(), results)
    return jsonify(results=results)


@bp.get("/api/air")
def air():
    try:
        lat, lon = float(request.args["lat"]), float(request.args["lon"])
    except (KeyError, ValueError):
        return jsonify(error="lat and lon are required numbers"), 400
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return jsonify(error="lat must be within [-90, 90] and lon within [-180, 180]"), 400
    try:
        return jsonify(aqi.fetch_air(lat, lon))
    except (upstream.RequestError, ValueError) as e:
        return jsonify(error=f"Could not load air-quality data: {e}"), 502


@bp.post("/api/impact")
def impact():
    try:
        return jsonify(compute_impact(body()))
    except ValueError as e:
        return jsonify(error=str(e)), 400


# ------------------------------------------------------------------ AI
def _follow_the_plan_scenario(ctx: dict, profile: dict, mode: str) -> dict[str, Any]:
    """The choices we assume a household makes if it follows the generated plan."""
    switch = bool(profile.get("transit")) and mode in TRANSIT_SWITCH_FROM
    return {
        "windows_closed": True,
        "purifier": profile.get("budget") != "none",
        "mask": (ctx.get("pm25") or 0) > SMOG_MASK_PM25,
        "shift_outdoor": True,
        "new_mode": "metro" if switch else mode,
    }


@bp.post("/api/plan")
def plan():
    data = body()
    ctx = _dict(data.get("context"))
    profile = dict(_dict(data.get("profile")))
    lang = "hi" if data.get("lang") == "hi" else "en"
    tags = profile.get("household")
    profile["household"] = [clip(x, 20) for x in tags][:MAX_HOUSEHOLD_TAGS] if isinstance(tags, list) else []

    mode = profile.get("mode") or DEFAULT_MODE
    try:
        impact_data = compute_impact({
            "pm25": ctx.get("pm25") or 60, "best_pm25": _dict(ctx.get("best")).get("pm25"),
            "outdoor_hours": profile.get("outdoor_hours", 2), "commute_km": profile.get("commute_km", 8),
            "mode": mode, "baseline_windows": profile.get("windows"),
            "baseline_purifier": profile.get("purifier_owned"),
            "scenario": _follow_the_plan_scenario(ctx, profile, mode),
        })
    except ValueError as e:
        return jsonify(error=str(e)), 400

    source, error, plan_obj = "fallback", None, None
    if settings.groq_enabled:
        try:
            plan_obj = llm.llm_plan(ctx, profile, impact_data, lang)
            source = "groq" if plan_obj else source
        except llm.LLM_ERRORS as e:  # network, auth, bad/missing JSON...
            error = str(e)[:200]
    else:
        error = "GROQ_API_KEY is not configured on the server"
    return jsonify(plan=plan_obj or fallback_plan(ctx, profile), impact=impact_data, source=source,
                   model=settings.groq_model if source == "groq" else None, error=error)


@bp.post("/api/chat")
def chat():
    data = body()
    raw: list[Any] = data["messages"] if isinstance(data.get("messages"), list) else []
    turns = [m for m in raw if isinstance(m, dict) and m.get("role") in ("user", "assistant")][-MAX_CHAT_TURNS:]
    if not turns:
        return jsonify(error="No message"), 400
    if not settings.groq_enabled:
        return jsonify(reply="I need a Groq API key on the server to chat. Add GROQ_API_KEY to .env and restart. "
                             "Meanwhile, the plan above works fully in offline mode.", source="fallback")
    clean = [{"role": m["role"], "content": clip(m.get("content"), 1500)} for m in turns]
    try:
        reply = llm.chat_reply(clean, data.get("context"), data.get("profile"), data.get("lang", "en"))
    except llm.LLM_ERRORS as e:
        return jsonify(error=f"AI is unavailable right now: {str(e)[:160]}"), 502
    return jsonify(reply=reply, source="groq")

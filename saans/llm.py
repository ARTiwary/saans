"""Groq (Llama 3.3 70B) integration: prompts, JSON-mode plan generation and chat."""
from __future__ import annotations

import json
import time
from typing import Any

from . import upstream
from .config import GROQ_URL, settings
from .validation import clip


class LLMError(RuntimeError):
    """The language model could not be reached or returned an error."""


#: Everything that can go wrong while calling the LLM and parsing its answer.
LLM_ERRORS = (LLMError, upstream.RequestError, ValueError, KeyError, IndexError)


def groq_chat(messages: list[dict], json_mode: bool = False, max_tokens: int = 2800,
              temperature: float = 0.4) -> str:
    """Call Groq chat completions (one retry on HTTP 429). Raises :class:`LLMError` on failure."""
    if not settings.groq_enabled:
        raise LLMError("GROQ_API_KEY is not set")
    body: dict[str, Any] = {"model": settings.groq_model, "messages": messages,
                            "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    last = None
    for attempt in range(2):
        r = upstream.session.post(GROQ_URL, headers={"Authorization": f"Bearer {settings.groq_key}"},
                                  json=body, timeout=45)
        if r.status_code == 429 and attempt == 0:
            time.sleep(2)
            continue
        if r.status_code >= 400:
            last = f"Groq API {r.status_code}: {r.text[:200]}"
            break
        return r.json()["choices"][0]["message"]["content"]
    raise LLMError(last or "Groq request failed")


def ctx_text(ctx, profile):
    a = ctx or {}
    p = profile or {}
    best, worst = a.get("best") or {}, a.get("worst") or {}
    return (
        f"LOCATION: {clip(a.get('place'), 80)}\n"
        f"AIR NOW: US AQI {a.get('aqi')} ({clip(a.get('category'))}); PM2.5 {a.get('pm25')} ug/m3; "
        f"PM10 {a.get('pm10')}; NO2 {a.get('no2')}; O3 {a.get('o3')}; trend next 6h: {a.get('trend')}.\n"
        f"WEATHER: {a.get('temp')} C, humidity {a.get('humidity')}%, wind {a.get('wind')} km/h "
        f"(low wind + cold nights trap smog near the ground).\n"
        f"CLEANEST 3-HOUR WINDOW: {best.get('label')} {best.get('day')} (PM2.5 ~{best.get('pm25')}). "
        f"WORST WINDOW: {worst.get('label')} {worst.get('day')} (PM2.5 ~{worst.get('pm25')}).\n"
        f"HOUSEHOLD: {', '.join(p.get('household', [])) or 'adults only'}; home: {clip(p.get('home'))}; "
        f"commute: {clip(p.get('mode'))} {clip(p.get('commute_km'))} km one-way; "
        f"outdoor hours/day: {clip(p.get('outdoor_hours'))}; windows usually: {clip(p.get('windows'))}; "
        f"owns purifier: {p.get('purifier_owned')}; cooking fuel: {clip(p.get('cooking'))}; "
        f"budget: {clip(p.get('budget'))}; public transit nearby: {p.get('transit')}; "
        f"notes: {clip(p.get('notes'), 200)}"
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
            out.append({k: clip(x.get(k), 400) for k in ITEM_KEYS + tuple(extra)})
    return out


def normalise_plan(p: Any) -> dict | None:
    """Validate and size-limit the model's JSON; ``None`` if it is unusable."""
    if not isinstance(p, dict) or not p.get("routine"):
        return None
    myth: dict[str, Any] = p["myth"] if isinstance(p.get("myth"), dict) else {}
    plan = {
        "headline": clip(p.get("headline"), 200),
        "risk_summary": clip(p.get("risk_summary"), 600),
        "routine": _items(p.get("routine"), ("when",)),
        "indoor_hacks": _items(p.get("indoor_hacks")),
        "sustainable_swaps": _items(p.get("sustainable_swaps")),
        "community_actions": _items(p.get("community_actions")),
        "care": [{"title": clip(c.get("title"), 80), "detail": clip(c.get("detail"), 400)}
                 for c in (p.get("care") or []) if isinstance(c, dict)],
        "warning_signs": [clip(w, 200) for w in (p.get("warning_signs") or [])][:6],
        "myth": {"myth": clip(myth.get("myth"), 200), "truth": clip(myth.get("truth"), 400)},
    }
    for r in plan["routine"]:
        if r["when"] not in ("morning", "afternoon", "evening", "night"):
            r["when"] = "morning"
    return plan


def llm_plan(ctx: dict, profile: dict, impact: dict, lang: str) -> dict | None:
    """Ask the model for a personalised plan, grounded in the computed impact numbers."""
    lang_rule = ("Write every string VALUE in simple everyday Hindi (Devanagari script). Keep JSON keys in English."
                 if lang == "hi" else "Write in clear, simple English.")
    imp = (f"IMPACT NUMBERS (computed, use these): current estimated daily PM2.5 exposure "
           f"{impact['baseline_pm25']} ug/m3 -> {impact['scenario_pm25']} ug/m3 if the household follows the plan "
           f"(~{impact['reduction_pct']}% lower). Biggest levers: "
           + "; ".join(f"{lv['label']} (~{lv['reduction_pct']}%)" for lv in impact["levers"][:3]))
    msgs = [
        {"role": "system", "content": PLAN_SYSTEM + "\n" + lang_rule},
        {"role": "user", "content": ctx_text(ctx, profile) + "\n" + imp},
    ]
    return normalise_plan(json.loads(groq_chat(msgs, json_mode=True)))


CHAT_SYSTEM = """You are Saans, a warm, practical air-quality assistant for families and communities facing winter smog.
You have the user's live air data and household profile below. Answer their question directly in under 150 words.
- Tailor to their location and lifestyle; give concrete steps and explain the direct impact of each choice.
- Use only the numbers in the context; otherwise be qualitative and hedge honestly.
- Not a doctor: no diagnoses or drug doses. For chest pain, severe breathlessness, fainting or blue lips tell them to seek medical care immediately.
- No ozone generators/ionizers; never advise burning anything indoors.
- Use short bullets only when listing steps. {lang}

CONTEXT
{ctx}"""


def chat_reply(messages: list[dict], context: dict | None, profile: dict | None, lang: str) -> str:
    """Answer one chat turn using the live air data and household profile as context."""
    language = "Reply in simple Hindi (Devanagari)." if lang == "hi" else "Reply in English."
    system = CHAT_SYSTEM.format(lang=language, ctx=ctx_text(context, profile))
    return groq_chat([{"role": "system", "content": system}] + messages, max_tokens=500, temperature=0.5)

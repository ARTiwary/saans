"""Offline, rule-based plan used when the LLM is unavailable (so the app never breaks)."""
from __future__ import annotations

from typing import Any

#: AQI band index from which a day counts as a smog alert.
ALERT_BAND = 3
SENSITIVE = ("children", "elderly", "pregnant", "asthma")


def _it(title: str, detail: str, why: str, impact: str, cost: str = "Free", when: str | None = None) -> dict[str, str]:
    d = {"title": title, "detail": detail, "why": why, "impact": impact, "cost": cost}
    if when:
        d["when"] = when
    return d


def _routine(best: str, worst: str, bad: bool) -> list[dict[str, str]]:
    return [
        _it(f"Do outdoor activity around {best}", "Schedule walks, school pick-ups and errands in the cleanest window.",
            "Winter smog is trapped lowest to the ground at night and early morning.",
            "Can cut outdoor exposure by a third or more vs. peak hours.", "Free", "morning"),
        _it(f"Avoid being outside around {worst}", "Keep morning workouts indoors on smoggy days.",
            "Cold, still air holds pollutants near the ground.", "Avoids the highest-concentration hours.", "Free", "morning"),
        _it("Ventilate briefly at the cleanest hour", "Open windows 10-15 min only when AQI is lowest, then shut.",
            "Stale indoor air also builds up CO2 and cooking fumes.",
            "Balances indoor air freshness with low particle entry.", "Free", "afternoon"),
        _it("Wear a fitted N95/FFP2 outdoors" if bad else "Carry an N95 for dusty stretches",
            "Seal around nose and cheeks; cloth and surgical masks filter far less.",
            "Fine particles (PM2.5) pass through loose masks.",
            "A well-fitted N95 can block most inhaled particles.", "Rs", "afternoon"),
        _it("Wash face, rinse nose, change clothes after coming home",
            "Saline rinse and a quick wash remove particles on skin and hair.",
            "Particles ride in on clothes and skin.", "Reduces irritation and indoor particle load.", "Free", "evening"),
        _it("Cook with the exhaust fan or chimney on", "Use induction/LPG if possible and ventilate while frying.",
            "Cooking is a major indoor PM2.5 source.", "Lowers evening indoor spikes.", "Free", "evening"),
        _it("Shut windows before sunset", "Evening cooling traps smoke near the ground.",
            "Pollution rises as the air cools.", "Keeps night-time indoor air cleaner.", "Free", "night"),
        _it("Run a purifier or a DIY fan+filter in the bedroom", "Close the door, run on medium while you sleep.",
            "People breathe in the same air for 7-8 hours at night.",
            "Large share of daily exposure happens in bed.", "Rs Rs", "night"),
    ]


def _indoor_hacks() -> list[dict[str, str]]:
    return [
        _it("Seal draughts around doors and windows", "Use weather-strips or rolled towels.",
            "Gaps let outdoor smog leak in.", "Lowers indoor/outdoor ratio.", "Rs"),
        _it("HEPA purifier sized to the room", "Check CADR; run in the room where you spend most time.",
            "HEPA captures fine particles.", "Roughly halves indoor PM2.5 in a closed room.", "Rs Rs Rs"),
        _it("DIY box-fan filter (Corsi-Rosenthal box)", "Tape 4 MERV-13/HEPA-grade filters on a box fan.",
            "Cheap way to clean a room's air.", "Comparable to a purifier at a fraction of the cost.", "Rs Rs"),
        _it("Damp-mop instead of dry sweeping", "Wet-wipe surfaces daily.",
            "Dry sweeping re-suspends settled dust.", "Less airborne dust.", "Free"),
        _it("Add low-cost PM2.5 monitor", "Watch indoor numbers to see what actually helps.",
            "What you measure you can improve.", "Turns guesses into proof.", "Rs Rs"),
    ]


def _swaps() -> list[dict[str, str]]:
    return [
        _it("Private vehicle -> metro/bus", "Do it for the commute 2-3 days a week.",
            "Fewer vehicles = fewer exhaust particles and CO2.", "Cuts your transport CO2 and in-vehicle exposure.", "Free"),
        _it("Open burning for warmth -> blankets/layers", "Never burn leaves, waste or wood for warmth.",
            "Open burning is a big local PM2.5 source.", "Direct drop in neighbourhood smoke.", "Rs"),
        _it("Biomass cooking -> LPG/induction", "Use clean fuel; ventilate.",
            "Biomass produces heavy indoor smoke.", "Large drop in indoor PM2.5 and CO.", "Rs Rs"),
        _it("Idling engine -> engine off at signals", "Switch off when waiting > 30 seconds.",
            "Idling still emits.", "Less roadside pollution.", "Free"),
    ]


def _community() -> list[dict[str, str]]:
    return [
        _it("Residents' pledge: no waste/leaf burning", "Share a simple notice in society groups.",
            "Local burning spikes PM2.5.", "Visible neighbourhood improvement.", "Free"),
        _it("Carpool / school-bus rota", "Organise 3-4 families per car or take the school bus.",
            "Cuts cars outside schools.", "Fewer idling cars at pick-up.", "Free"),
        _it("Plant & protect trees; water-sprinkle dust", "Support local plantation and road-dust control.",
            "Greenery and wet roads reduce dust.", "Cooler, cleaner street-level air over time.", "Free"),
    ]


_CARE = {
    "children": ("Children", "Move play indoors on very poor days; give water, wash hands/face after outdoor play."),
    "elderly": ("Elders", "Keep medicines handy, avoid morning walks in smog, rest in the cleanest room."),
    "asthma": ("Asthma / COPD", "Follow your doctor's action plan; keep your reliever inhaler accessible."),
    "pregnant": ("Pregnancy", "Limit outdoor trips in smog hours and discuss mask use with your doctor."),
}


def _care(household: list[str]) -> list[dict[str, str]]:
    care = [{"title": t, "detail": d} for key, (t, d) in _CARE.items() if key in household]
    return care or [{"title": "Everyone", "detail": "Stay hydrated, sleep well and avoid hard outdoor workouts on bad-air days."}]


def fallback_plan(ctx: dict | None, profile: dict) -> dict[str, Any]:
    """Build a complete plan from rules, anchored to today's cleanest and worst windows."""
    ctx = ctx or {}
    household = profile.get("household", [])
    best = (ctx.get("best") or {}).get("label", "early afternoon")
    worst = (ctx.get("worst") or {}).get("label", "early morning")
    bad = int(ctx.get("idx") or 0) >= ALERT_BAND
    sensitive = any(x in household for x in SENSITIVE)
    return {
        "headline": ("Smog alert: plan your day around the clean window" if bad
                     else "Manageable air today - a few smart habits keep it that way"),
        "risk_summary": (f"US AQI is {ctx.get('aqi')} ({ctx.get('category')}). "
                         + ("Your household includes people who are more sensitive to fine particles, "
                            "so extra care pays off. " if sensitive else "")
                         + f"Air is usually cleanest around {best} and dirtiest around {worst}."),
        "routine": _routine(best, worst, bad),
        "indoor_hacks": _indoor_hacks(),
        "sustainable_swaps": _swaps(),
        "community_actions": _community(),
        "care": _care(household),
        "warning_signs": ["Chest pain or tightness", "Breathlessness at rest or wheeze that does not settle",
                          "Fainting, confusion or a very fast heartbeat",
                          "Lips or face turning blue - seek emergency care immediately"],
        "myth": {"myth": "If the sky looks clear, the air is clean.",
                 "truth": "The most harmful particles (PM2.5) are invisible. Check the number, not the sky."},
    }

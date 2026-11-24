"""Deterministic exposure/impact model.

Every number the user sees (exposure, % reduction, cigarette-equivalents, CO2) is computed here,
never by the LLM, so results are reproducible and explainable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .aqi import PM25_PER_CIGARETTE
from .validation import num

#: mode -> (speed km/h, in-transit exposure factor vs ambient, kg CO2 per passenger-km)
MODES: dict[str, tuple[float, float, float]] = {
    "walk": (4.5, 1.0, 0.0), "cycle": (12, 1.05, 0.0), "two_wheeler": (25, 1.1, 0.05),
    "auto_cab": (18, 0.9, 0.10), "car": (20, 0.5, 0.17), "bus": (15, 1.0, 0.03),
    "metro": (30, 0.6, 0.03), "wfh": (1, 0.0, 0.0),
}
OPEN_AIR_MODES = {"walk", "cycle", "two_wheeler", "auto_cab", "bus"}
WORK_DAYS = 260
MAX_COMMUTE_HOURS = 4.0
N95_FACTOR = 0.4  # fraction of particles still inhaled with a fitted N95 (real-world ~60% cut)
#: (windows closed, purifier running) -> indoor/outdoor PM2.5 ratio
INFILTRATION = {(True, True): 0.25, (True, False): 0.55, (False, True): 0.5, (False, False): 0.8}
DEFAULT_MODE = "two_wheeler"
MAX_LEVERS = 5

ASSUMPTIONS = [
    "Daily time-weighted PM2.5 exposure = indoor + outdoor + commute hours.",
    "Indoor/outdoor ratio: windows open 0.8, shut 0.55, shut + HEPA 0.25, open + HEPA 0.5.",
    "Fitted N95 cuts inhaled particles by ~60% in real-world use (lab values are higher).",
    "Cigarette equivalence: ~22 ug/m3 PM2.5 over 24h ~ 1 cigarette/day (Berkeley Earth rule of thumb).",
    "These are planning estimates, not medical measurements.",
]


@dataclass(frozen=True)
class Household:
    """Inputs that stay fixed while we compare scenarios."""

    pm: float          # ambient PM2.5, ug/m3
    best_pm: float     # PM2.5 in the cleanest 3-hour window
    outdoor_hours: float
    commute_km: float

    def exposure(self, mode: str, *, windows_closed: bool, purifier: bool,
                 mask: bool = False, shift: bool = False) -> float:
        """Time-weighted daily PM2.5 exposure (ug/m3) for one set of choices."""
        speed, transit_factor, _ = MODES.get(mode, MODES["walk"])
        commute_h = 0.0 if mode == "wfh" else min(MAX_COMMUTE_HOURS, 2 * self.commute_km / speed)
        indoor_h = max(24 - commute_h - self.outdoor_hours, 0)
        mask_factor = N95_FACTOR if mask else 1.0
        outdoor_pm = self.best_pm if shift else self.pm
        commute_mask = mask_factor if mode in OPEN_AIR_MODES else 1.0
        total = (indoor_h * self.pm * INFILTRATION[(windows_closed, purifier)]
                 + self.outdoor_hours * outdoor_pm * mask_factor
                 + commute_h * self.pm * transit_factor * commute_mask)
        return total / 24.0


def _co2_per_year(mode: str, km: float) -> float:
    if mode == "wfh":
        return 0.0
    return MODES.get(mode, MODES["walk"])[2] * 2 * km * WORK_DAYS


def _levers(home: Household, mode: str, closed: bool, purifier: bool, base: float) -> list[dict[str, Any]]:
    """Each single change on its own, ranked by how much exposure it removes."""
    def lever(label: str, *, new_mode: str = mode, windows_closed: bool = closed,
              purifier_on: bool = purifier, mask: bool = False, shift: bool = False) -> dict[str, Any]:
        e = home.exposure(new_mode, windows_closed=windows_closed, purifier=purifier_on, mask=mask, shift=shift)
        return {"label": label, "reduction_pct": round(100 * (base - e) / base, 1) if base else 0}

    levers = []
    if not closed:
        levers.append(lever("Keep windows shut during smog hours", windows_closed=True))
    if not purifier:
        levers.append(lever("Run an air purifier (HEPA) in the main room", purifier_on=True))
    levers.append(lever("Wear a fitted N95 outdoors/in open transport", mask=True))
    levers.append(lever("Move outdoor time to the cleanest window", shift=True))
    if mode != "wfh":
        levers += [lever(f"Commute by {m}", new_mode=m) for m in ("metro", "bus", "cycle") if m != mode]
    return sorted(levers, key=lambda x: -x["reduction_pct"])[:MAX_LEVERS]


def compute_impact(d: dict[str, Any]) -> dict[str, Any]:
    """Compare today's habits with a scenario. Raises ``ValueError`` for non-numeric input."""
    pm = num(d, "pm25", 60, 0, 1000)
    best_pm = min(num(d, "best_pm25", pm * 0.7, 0, 1000), pm) if d.get("best_pm25") is not None else pm * 0.75
    home = Household(pm, best_pm, num(d, "outdoor_hours", 2, 0, 16), num(d, "commute_km", 8, 0, 200))
    mode: str = d["mode"] if d.get("mode") in MODES else DEFAULT_MODE
    closed, purifier = d.get("baseline_windows") == "closed", bool(d.get("baseline_purifier"))
    sc: dict[str, Any] = d["scenario"] if isinstance(d.get("scenario"), dict) else {}
    new_mode: str = sc["new_mode"] if sc.get("new_mode") in MODES else mode

    base = home.exposure(mode, windows_closed=closed, purifier=purifier)
    scen = home.exposure(new_mode, windows_closed=closed or bool(sc.get("windows_closed")),
                         purifier=purifier or bool(sc.get("purifier")),
                         mask=bool(sc.get("mask")), shift=bool(sc.get("shift_outdoor")))
    return {
        "baseline_pm25": round(base, 1), "scenario_pm25": round(scen, 1),
        "reduction_pct": round(100 * (base - scen) / base, 1) if base else 0,
        "cigs_baseline": round(base / PM25_PER_CIGARETTE, 2),
        "cigs_scenario": round(scen / PM25_PER_CIGARETTE, 2),
        "co2_saved_kg_year": round(_co2_per_year(mode, home.commute_km) - _co2_per_year(new_mode, home.commute_km)),
        "levers": _levers(home, mode, closed, purifier, base),
        "assumptions": ASSUMPTIONS,
    }

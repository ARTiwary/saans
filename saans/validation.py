"""Input validation helpers shared by the API routes and the impact model."""
from __future__ import annotations

import math
from typing import Any


def num(d: dict[str, Any], key: str, default: float, lo: float, hi: float) -> float:
    """Read a numeric field, using ``default`` when absent and clamping to ``[lo, hi]``.

    Raises ``ValueError`` when the value is present but not a finite number.
    """
    v = d.get(key)
    if v in (None, ""):
        return default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"'{key}' must be a number") from None
    if not math.isfinite(v):
        raise ValueError(f"'{key}' must be a number")
    return max(lo, min(hi, v))


def clip(v: Any, n: int = 120) -> str:
    """Safe, length-limited string (keeps user input from bloating prompts)."""
    return str(v)[:n] if v is not None else ""

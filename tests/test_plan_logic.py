"""Plan generation helpers: LLM output normalisation and the offline fallback."""
import json

import pytest
import requests  # noqa: F401

from saans import fallback, llm, upstream
from saans.config import settings
from saans.impact import compute_impact

PROFILE = {"household": ["children", "elderly"], "mode": "two_wheeler", "commute_km": 8}
CTX = {"idx": 3, "aqi": 180, "category": "Unhealthy", "pm25": 120,
       "best": {"label": "1 PM - 4 PM", "day": "today", "pm25": 80}, "worst": {"label": "6 AM - 9 AM", "day": "today", "pm25": 190}}


def test_fallback_plan_is_complete_and_personalised():
    plan = fallback.fallback_plan(CTX, PROFILE)
    for key in ("routine", "indoor_hacks", "sustainable_swaps", "community_actions", "care", "warning_signs"):
        assert plan[key], key
    assert {r["when"] for r in plan["routine"]} == {"morning", "afternoon", "evening", "night"}
    titles = " ".join(c["title"] for c in plan["care"])
    assert "Children" in titles and "Elders" in titles
    assert "1 PM - 4 PM" in json.dumps(plan)
    assert all(it["why"] and it["impact"] for it in plan["routine"])


def test_fallback_headline_changes_with_severity():
    bad = fallback.fallback_plan({**CTX, "idx": 4}, PROFILE)["headline"]
    ok = fallback.fallback_plan({**CTX, "idx": 0}, PROFILE)["headline"]
    assert bad != ok


def test_normalise_plan_rejects_junk():
    assert llm.normalise_plan(None) is None
    assert llm.normalise_plan({"routine": []}) is None
    assert llm.normalise_plan("text") is None


def test_normalise_plan_cleans_and_bounds_fields():
    raw = {"headline": "x" * 999, "routine": [{"title": "t", "when": "midnight-snack", "detail": "d" * 5000},
                                              {"no_title": True}, "bad"],
           "warning_signs": ["w"] * 20, "myth": "not-a-dict"}
    plan = llm.normalise_plan(raw)
    assert len(plan["headline"]) == 200
    assert len(plan["routine"]) == 1                      # items without a title are dropped
    assert plan["routine"][0]["when"] == "morning"        # invalid time-of-day is repaired
    assert len(plan["routine"][0]["detail"]) == 400
    assert len(plan["warning_signs"]) == 6
    assert plan["myth"] == {"myth": "", "truth": ""}


def test_ctx_text_contains_context_and_survives_missing_data():
    text = llm.ctx_text({"place": "Delhi", "aqi": 180}, {"household": ["children"], "notes": "n" * 1000})
    assert "Delhi" in text and "180" in text and "children" in text
    assert len(text) < 2500
    assert "LOCATION" in llm.ctx_text(None, None)


def test_llm_plan_uses_impact_numbers_and_language(monkeypatch):
    seen = {}

    def fake_groq(messages, json_mode=False, **kw):
        seen["system"], seen["user"], seen["json"] = messages[0]["content"], messages[1]["content"], json_mode
        return json.dumps({"headline": "h", "routine": [{"title": "t", "when": "morning"}]})

    monkeypatch.setattr(llm, "groq_chat", fake_groq)
    impact = compute_impact({"pm25": 150, "scenario": {"mask": True}})
    plan = llm.llm_plan(CTX, PROFILE, impact, "hi")
    assert plan["headline"] == "h"
    assert "Hindi" in seen["system"] and seen["json"] is True
    assert str(impact["baseline_pm25"]) in seen["user"]


def test_groq_chat_requires_key(monkeypatch):
    monkeypatch.setattr(settings, "groq_key", "")
    with pytest.raises(RuntimeError):
        llm.groq_chat([{"role": "user", "content": "hi"}])


def test_groq_chat_retries_once_on_429(monkeypatch):
    from conftest import FakeResponse
    monkeypatch.setattr(settings, "groq_key", "k")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    replies = [FakeResponse({}, status=429, text="slow down"),
               FakeResponse({"choices": [{"message": {"content": "ok"}}]})]
    monkeypatch.setattr(upstream.session, "post", lambda *a, **k: replies.pop(0))
    assert llm.groq_chat([{"role": "user", "content": "hi"}]) == "ok"


def test_groq_chat_raises_on_error(monkeypatch):
    from conftest import FakeResponse
    monkeypatch.setattr(settings, "groq_key", "k")
    monkeypatch.setattr(upstream.session, "post", lambda *a, **k: FakeResponse({}, status=401, text="bad key"))
    with pytest.raises(RuntimeError, match="401"):
        llm.groq_chat([{"role": "user", "content": "hi"}])

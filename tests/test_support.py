"""Config, validation, security helpers and the offline plan's building blocks."""
import pytest

from saans import fallback, llm, security, validation
from saans.config import Settings, settings


def test_settings_reads_environment(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "  abc  ")
    monkeypatch.setenv("GROQ_MODEL", "custom-model")
    s = Settings()
    assert s.groq_key == "abc" and s.groq_model == "custom-model" and s.groq_enabled is True
    monkeypatch.delenv("GROQ_API_KEY")
    assert Settings().groq_enabled is False


@pytest.mark.parametrize("value,expected", [(5, 5.0), ("7.5", 7.5), (None, 3.0), ("", 3.0), (-4, 0.0), (999, 10.0)])
def test_num_defaults_and_clamps(value, expected):
    assert validation.num({"x": value}, "x", 3, 0, 10) == expected


@pytest.mark.parametrize("bad", ["abc", float("nan"), float("inf"), [1], {}])
def test_num_rejects_non_finite_or_non_numeric(bad):
    with pytest.raises(ValueError):
        validation.num({"x": bad}, "x", 3, 0, 10)


def test_clip():
    assert validation.clip(None) == "" and validation.clip("abcdef", 3) == "abc" and validation.clip(12) == "12"


def test_care_blocks_follow_the_household():
    assert [c["title"] for c in fallback._care(["children", "asthma"])] == ["Children", "Asthma / COPD"]
    assert fallback._care([])[0]["title"] == "Everyone"


def test_fallback_handles_missing_context():
    plan = fallback.fallback_plan(None, {})
    assert plan["routine"] and "early afternoon" in plan["risk_summary"]


def test_chat_reply_builds_a_language_aware_prompt(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm, "groq_chat", lambda msgs, **kw: seen.update(msgs=msgs, kw=kw) or "ok")
    assert llm.chat_reply([{"role": "user", "content": "hi"}], {"place": "Pune"}, {}, "hi") == "ok"
    assert "Hindi" in seen["msgs"][0]["content"] and "Pune" in seen["msgs"][0]["content"]
    assert seen["kw"]["max_tokens"] == 500


def test_groq_disabled_without_key():
    assert settings.groq_enabled is False


def test_rate_limit_state_can_be_reset(client):
    for _ in range(3):
        client.post("/api/impact", json={})
    assert security._hits
    security.reset_rate_limits()
    assert not security._hits


def test_cross_origin_opener_policy_and_permissions(client):
    h = client.get("/api/health").headers
    assert h["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "geolocation=(self)" in h["Permissions-Policy"]
    assert h["Content-Security-Policy"].startswith("default-src 'none'")


def test_unknown_page_returns_html_404_not_json(client):
    r = client.get("/definitely-not-here")
    assert r.status_code == 404 and r.mimetype == "text/html"

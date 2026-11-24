"""HTTP-level tests: routes, validation, error handling, fallbacks and security headers."""
import json

import app as saans

PROFILE = {"household": ["children"], "mode": "car", "commute_km": 10, "outdoor_hours": 2,
           "windows": "open", "purifier_owned": False, "budget": "low", "transit": True}


def post(client, url, payload, **kw):
    return client.post(url, data=json.dumps(payload), content_type="application/json", **kw)


# ---- pages & health --------------------------------------------------------------
def test_pages_are_served(client):
    for path, marker in (("/", b"Breathe smarter"), ("/landing", b"Breathe smarter"),
                         ("/app", b"winter air plan"), ("/app/", b"winter air plan"), ("/index.html", b"winter air plan")):
        r = client.get(path)
        assert r.status_code == 200 and marker in r.data, path
        assert r.mimetype == "text/html"


def test_pages_support_etag_revalidation(client):
    first = client.get("/app")
    second = client.get("/app", headers={"If-None-Match": first.headers["ETag"]})
    assert second.status_code == 304


def test_responses_are_gzipped_when_accepted(client):
    r = client.get("/app", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("Content-Encoding") == "gzip"
    assert len(r.data) < len(client.get("/app").data)


def test_security_headers_present(client):
    h = client.get("/").headers
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "SAMEORIGIN"
    assert "Referrer-Policy" in h


def test_api_is_never_cached(client):
    assert client.get("/api/health").headers["Cache-Control"] == "no-store"


def test_health_never_leaks_the_key(client, monkeypatch):
    monkeypatch.setattr(saans, "GROQ_KEY", "gsk_super_secret")
    body = client.get("/api/health").get_data(as_text=True)
    assert "gsk_super_secret" not in body and json.loads(body)["groq"] is True


def test_unknown_api_route_returns_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and "error" in r.get_json()


# ---- geocode / air -----------------------------------------------------------------
def test_geocode_short_query_is_empty(client):
    assert client.get("/api/geocode?q=a").get_json() == {"results": []}


def test_geocode_returns_places_and_caches(client, fake_upstream):
    for _ in range(2):
        r = client.get("/api/geocode?q=Delhi")
        assert r.get_json()["results"][0]["name"] == "Delhi"
    assert fake_upstream["geo"] == 1


def test_geocode_upstream_failure_is_502(client, monkeypatch):
    def boom(*a, **k):
        raise saans.requests.ConnectionError("down")

    monkeypatch.setattr(saans._http, "get", boom)
    r = client.get("/api/geocode?q=Delhi")
    assert r.status_code == 502 and r.get_json()["results"] == []


def test_air_validates_input(client):
    assert client.get("/api/air").status_code == 400
    assert client.get("/api/air?lat=abc&lon=1").status_code == 400
    assert client.get("/api/air?lat=91&lon=0").status_code == 400
    assert client.get("/api/air?lat=0&lon=181").status_code == 400


def test_air_ok(client, fake_upstream):
    r = client.get("/api/air?lat=28.65&lon=77.23")
    assert r.status_code == 200 and r.get_json()["aqi"] == 180


def test_air_upstream_failure_is_502(client, monkeypatch):
    def boom(*a, **k):
        raise saans.requests.Timeout("slow")

    monkeypatch.setattr(saans._http, "get", boom)
    assert client.get("/api/air?lat=1&lon=1").status_code == 502


# ---- impact --------------------------------------------------------------------------
def test_impact_endpoint(client):
    r = post(client, "/api/impact", {"pm25": 150, "scenario": {"purifier": True, "mask": True}})
    d = r.get_json()
    assert r.status_code == 200 and d["reduction_pct"] > 0 and d["levers"]


def test_impact_rejects_bad_input(client):
    assert post(client, "/api/impact", {"pm25": "lots"}).status_code == 400
    assert client.post("/api/impact", data="not json", content_type="application/json").status_code == 400
    assert post(client, "/api/impact", [1, 2, 3]).status_code == 400


# ---- plan ------------------------------------------------------------------------------
def test_plan_falls_back_without_key(client, air_context, monkeypatch):
    monkeypatch.setattr(saans, "GROQ_KEY", "")
    r = post(client, "/api/plan", {"context": air_context, "profile": PROFILE})
    d = r.get_json()
    assert r.status_code == 200 and d["source"] == "fallback" and d["model"] is None
    assert d["plan"]["routine"] and d["impact"]["reduction_pct"] > 0
    assert "not configured" in d["error"]


def test_plan_uses_groq_when_available(client, air_context, monkeypatch):
    monkeypatch.setattr(saans, "GROQ_KEY", "k")
    monkeypatch.setattr(saans, "groq_chat", lambda *a, **k: json.dumps(
        {"headline": "AI plan", "routine": [{"title": "t", "when": "evening"}]}))
    d = post(client, "/api/plan", {"context": air_context, "profile": PROFILE, "lang": "hi"}).get_json()
    assert d["source"] == "groq" and d["plan"]["headline"] == "AI plan" and d["model"] == saans.GROQ_MODEL


def test_plan_recovers_from_bad_llm_output(client, air_context, monkeypatch):
    monkeypatch.setattr(saans, "GROQ_KEY", "k")
    monkeypatch.setattr(saans, "groq_chat", lambda *a, **k: "this is not json")
    d = post(client, "/api/plan", {"context": air_context, "profile": PROFILE}).get_json()
    assert d["source"] == "fallback" and d["plan"]["routine"] and d["error"]


def test_plan_recovers_from_llm_outage(client, air_context, monkeypatch):
    monkeypatch.setattr(saans, "GROQ_KEY", "k")

    def down(*a, **k):
        raise RuntimeError("Groq API 503")

    monkeypatch.setattr(saans, "groq_chat", down)
    d = post(client, "/api/plan", {"context": air_context, "profile": PROFILE}).get_json()
    assert d["source"] == "fallback" and "503" in d["error"]


def test_plan_tolerates_malformed_profile(client, monkeypatch):
    monkeypatch.setattr(saans, "GROQ_KEY", "")
    r = post(client, "/api/plan", {"context": "x", "profile": {"household": "children", "commute_km": 5}})
    assert r.status_code == 200
    assert post(client, "/api/plan", {"profile": {"commute_km": "far"}}).status_code == 400


# ---- chat ------------------------------------------------------------------------------
def test_chat_requires_a_message(client):
    assert post(client, "/api/chat", {"messages": []}).status_code == 400
    assert post(client, "/api/chat", {"messages": [{"role": "system", "content": "x"}]}).status_code == 400


def test_chat_without_key_explains_setup(client, monkeypatch):
    monkeypatch.setattr(saans, "GROQ_KEY", "")
    d = post(client, "/api/chat", {"messages": [{"role": "user", "content": "hi"}]}).get_json()
    assert d["source"] == "fallback" and "GROQ_API_KEY" in d["reply"]


def test_chat_passes_context_and_trims_history(client, monkeypatch):
    monkeypatch.setattr(saans, "GROQ_KEY", "k")
    seen = {}

    def fake(messages, **kw):
        seen["messages"] = messages
        return "Wear an N95."

    monkeypatch.setattr(saans, "groq_chat", fake)
    msgs = [{"role": "user", "content": f"q{i}"} for i in range(30)]
    d = post(client, "/api/chat", {"messages": msgs, "lang": "hi", "context": {"place": "Delhi"}}).get_json()
    assert d["reply"] == "Wear an N95." and d["source"] == "groq"
    assert len(seen["messages"]) == 11                      # system + last 10
    assert "Delhi" in seen["messages"][0]["content"] and "Hindi" in seen["messages"][0]["content"]


def test_chat_upstream_failure_is_502(client, monkeypatch):
    monkeypatch.setattr(saans, "GROQ_KEY", "k")

    def down(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(saans, "groq_chat", down)
    assert post(client, "/api/chat", {"messages": [{"role": "user", "content": "hi"}]}).status_code == 502


# ---- limits ----------------------------------------------------------------------------
def test_rate_limit_kicks_in_with_retry_after(client, monkeypatch):
    monkeypatch.setattr(saans, "GROQ_KEY", "")
    codes = [post(client, "/api/chat", {"messages": [{"role": "user", "content": "hi"}]}).status_code
             for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
    r = post(client, "/api/chat", {"messages": [{"role": "user", "content": "hi"}]})
    assert r.headers["Retry-After"] == "60"


def test_rate_limit_is_per_client(client, monkeypatch):
    monkeypatch.setattr(saans, "GROQ_KEY", "")
    for _ in range(30):
        post(client, "/api/chat", {"messages": [{"role": "user", "content": "hi"}]})
    other = post(client, "/api/chat", {"messages": [{"role": "user", "content": "hi"}]},
                 headers={"X-Forwarded-For": "203.0.113.9"})
    assert other.status_code == 200


def test_oversized_body_is_rejected(client):
    r = client.post("/api/impact", data="x" * (70 * 1024), content_type="application/json")
    assert r.status_code == 413 and "error" in r.get_json()

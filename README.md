# 🌬️ Saans - GenAI Smog & Urban Air Assistant

> *Saans* (सांस) = "breath". A GenAI assistant that helps families and communities navigate severe winter smog with **live local air data**, **personalised low-emission routines**, **indoor air-quality hacks**, **sustainable alternatives** - and a transparent simulator that shows the **direct impact of every choice**.

## Quick start (2 minutes)

```bash
pip install -r requirements.txt
cp .env.example .env        # then paste your Groq key into .env
python app.py               # landing page: http://localhost:5000   app: http://localhost:5000/app
```

`/` is the marketing landing page (3D atmosphere layers, live AQI seed, how it works, FAQ). `/app` is the Saans assistant itself.

No Groq key? The app still works in **offline smart-rules mode**, so a demo never breaks.

## What it does

| Problem-statement ask | How Saans delivers |
|---|---|
| Tailored to **location** | Live AQI, PM2.5/PM10, wind, humidity and a 24 h forecast for any place (Open-Meteo, no key needed); the LLM gets the local context |
| Tailored to **lifestyle** | Household (kids/elders/asthma/pregnancy), commute, home type, cooking fuel, budget, transit access, free-text notes |
| **Low-emission daily routines** | Morning → night routine built around the *cleanest and worst 3-hour windows* computed from the forecast |
| **Indoor air quality hacks** | Budget-aware: sealing, HEPA, DIY Corsi-Rosenthal box, cooking ventilation, wet-mopping… |
| **Sustainable alternatives** | "A → B" swaps (private vehicle → metro, biomass → LPG/induction, idling → engine off) |
| **Direct impact explained** | Every action has a *why* and an *impact*; an exposure model computes PM2.5 reduction, cigarette-equivalents/day and CO₂ saved/year |
| **Families & communities** | Community actions tab, WhatsApp share, print/PDF, Hindi/English AI replies |

## Why it's trustworthy (judges love this)

- **LLM explains, a model computes.** All numbers (exposure, % reduction, CO₂) come from a deterministic, documented engine (`compute_impact` in `app.py`) - the LLM is told not to invent measurements.
- **Safety guard-rails:** not a doctor, no dosages, red-flag symptoms → seek care, no ozone generators/ionizers, never burn anything indoors.
- **Graceful degradation:** if Groq/network fails → rule-based plan, clearly labelled.
- **Privacy:** key stays server-side; profile stored only in the browser's localStorage.

## Quality, security & performance

- **Tests:** 69 automated tests (pytest, ~97% coverage of `app.py`) - the impact model, AQI banding, forecast windows, plan normalisation, fallbacks, every API route, validation, rate limiting and security headers. Upstream APIs are faked, so the suite runs offline in under a second.
  ```bash
  pip install -r requirements-dev.txt
  pytest --cov=app --cov-report=term-missing
  ```
  GitHub Actions (`.github/workflows/ci.yml`) runs lint + tests on every push.
- **Security:** strict CSP and security headers, request-size limit, per-route rate limits, strict input validation and clamping, JSON-only API errors, secrets only on the server (never returned by `/api/health`).
- **Efficiency:** pooled HTTP session, parallel weather + air fetch, 10-minute air and 1-hour geocode caches, HTML read once with ETag/304 revalidation, gzip on text/JSON.
- **Accessibility:** skip link, landmarks, labelled controls, live regions for results and errors, `aria-pressed` on toggles, reduced-motion support.

## Architecture

```
Browser (index.html)
   │  /api/geocode  /api/air  /api/impact  /api/plan  /api/chat
Flask (app.py)
   ├─ Open-Meteo Air Quality + Forecast + Geocoding  → live AQI, forecast, clean windows
   ├─ TTL cache + pooled session + security middleware
   ├─ Impact engine (pure Python)                    → exposure, cigarettes, CO₂
   └─ Groq chat completions (JSON mode)              → plan + context-aware chat
```

## 3-minute demo script

1. **Open with the problem** (10 s): "Winter smog sends millions of families to hospitals - but most advice is generic."
2. Search **Delhi** (or Lucknow/Patna) → show live AQI ring, the cigarette equivalence, and the green/red 3-hour windows.
3. Set profile: *child + elder, two-wheeler, 10 km, wood/coal cooking, low budget* → **Generate**.
4. Walk the tabs: routine anchored to the clean window → indoor hacks with ₹ cost → swaps → community actions.
5. **Impact simulator:** toggle windows/purifier/N95/commute→metro; watch exposure bar, cigarette-equivalents and CO₂ move. Open "How is this calculated?" (transparency).
6. Switch **language to हिन्दी** → regenerate → shows reach.
7. Ask chat: *"Can my kids play outside today?"* → grounded answer.
8. Hit **Share on WhatsApp** → community spread. Close with the roadmap.

## Roadmap (mention to judges)

Indoor sensor integration (PurpleAir/low-cost PM2.5) · school/RWA dashboards · push alerts when the clean window opens · voice mode for low-literacy users · more regional languages · hyper-local source detection (stubble burning, traffic, construction).

## Deploy free

- **Render / Railway:** new Web Service from this folder, build `pip install -r requirements.txt`, start uses the `Procfile`, add env var `GROQ_API_KEY`.
- Never commit your real `.env`.

## Data & references

Open-Meteo Air Quality API (CAMS global/regional models) · US AQI breakpoints · Berkeley Earth PM2.5↔cigarette rule of thumb · Corsi-Rosenthal box research. Estimates are for planning, not medical advice.

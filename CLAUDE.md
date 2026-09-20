# Project context for Claude Code

Hackathon project. **Hard deadline: stop building 5:30 PM EDT, Sept 20, 2026.**
Submission is on Devpost (Open Track, plus The Code Registry code-quality track).

## What this is
"Pothole Pilot": a Waze-style map. User enters start and destination in Philly; an
agent geocodes, fetches route alternatives, scans each for open pothole reports
(311 'Street Defect'), and recommends one. See README.md.
- `agent.py`   generic tool loop (`run_agent`) plus the 311 analyst (`investigate`)
- `trip.py`    trip agent, its tools (TripContext), and the no-LLM fallback planner
- `routing.py` geocoders, OSRM, PostGIS pothole query (SQL built from numbers only)
- `webapp.py`  Flask API plus static Leaflet UI in `static/`
- `guardrails.py` validator for model-written SQL (analyst agent only)

## Judging criteria to optimise for
Technical execution, agentic design, innovation, impact, reliability and safety,
demo and completeness. The Code Registry scores security, dependencies, quality.

## Rules
- Never commit secrets. `.env` is gitignored; keep it that way.
- Keep dependencies pinned in requirements.txt and keep the list short.
- Every new behaviour gets a test. Run `pytest -q` before each commit.
- All model-written SQL must go through `validate_sql`. Never bypass it.
- Small commits with clear messages.
- Prefer finishing and polishing over adding features after 4:00 PM.

## Data facts (verified live today)
- Endpoint: https://phl.carto.com/api/v2/sql?q=<SQL>, no key, PostgreSQL dialect.
- Table `public_cases_fc`, ~5.9M rows, 2014 to today.
- zipcode/address/lat/lon are often NULL. Filter zips with `zipcode ~ '^191[0-9]{2}$'`.
- 2026 Street Defect: 8,401 reports, ~19% still open, avg 27 days to close.

- Geocoding: Census handles street addresses and intersections; Nominatim handles
  landmarks. Neither alone is enough. CARTO basemap tiles now need a key; use OSM tiles.
- Verified demo trip: Temple University -> Citizens Bank Park (route A 9 reports, B 13).

## Status
Verified working without an API key (fallback planner, map, 27 tests). The agent
path is unit-tested with a fake client but has NOT yet been run against the real
Anthropic API. That is step 1.

## Suggested next steps, in order
1. Put ANTHROPIC_API_KEY in .env, run `python -m pothole_agent.webapp`, plan a trip,
   and confirm the briefing says "Written by the AI agent". Fix whatever breaks.
2. Tune TRIP_SYSTEM_PROMPT in trip.py so briefings are short, specific, and honest.
3. Stream agent steps to the UI live (Server-Sent Events) instead of after the fact.
   Waiting 20 seconds on a static message is the weakest part of the demo.
4. Ideas if time allows: a follow-up question box wired to the analyst agent
   ("how long do potholes in this zip take to get fixed?"); a "report a pothole"
   link to Philly311; severity weighting by report age.
5. Record a demo under 3 minutes: type two landmarks, show the two routes and the
   pothole counts, open "What the agent did", mention the fallback and guardrails.

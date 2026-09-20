# Demo video script — potholejawn (≤ 3:00)

One continuous screen recording. Two windows only: the browser (map UI) and one terminal. Speak plainly, no filler. Total spoken script below runs about 2:40 at normal pace, leaving buffer.

## Pre-flight checklist (do all of this BEFORE recording)

- [ ] `ANTHROPIC_API_KEY` set in `.env`; server running: `python -m pothole_agent.webapp`
- [ ] Browser at `http://127.0.0.1:5000`, **zoom 125–150%** so text is legible in the recording
- [ ] Do one throwaway trip first (warms geocoder/OSRM caches and confirms the agent path works — briefing should NOT say fallback)
- [ ] Terminal open in the repo with the venv active, font size bumped, ready to paste the CLI command
- [ ] Landmarks to type, exactly: start `Temple University`, end `Citizens Bank Park` (verified: route A 9 open reports, route B 13, ~17 min drive)
- [ ] CLI question ready to paste: `python -m pothole_agent.cli "Which zip codes wait longest for pothole repairs?"`
- [ ] Close notifications / other tabs; hide bookmarks bar
- [ ] Timer visible to yourself, not on screen

## Script

### 0:00 – 0:15 — Hook

Show the map UI, empty.

> "This is potholejawn — potholejawn dot com. Philadelphia publishes every 311 request since 2014, about five point nine million rows. This agent uses that data, live, to route you around potholes. Watch."

### 0:15 – 0:35 — Type the trip

Type `Temple University` and `Citizens Bank Park`, hit Plan.

> "I'll type where I am and where I'm going — Temple University to Citizens Bank Park. That's it. No dropdowns, no forms — the agent figures out the rest."

### 0:35 – 1:10 — Live agent steps (SSE)

The steps panel streams live while the agent works. Point at each step as it appears.

> "Now watch the agent work in real time. It's deciding which tools to call, on its own: it geocodes both places — Census geocoder for addresses, OpenStreetMap for landmarks. It pulls the driving route and an alternative from OSRM. Now it's scanning each route — that's a live PostGIS query against the city's 311 API for open street-defect reports within thirty meters of the road. And it records its recommendation as structured output before writing the briefing."

(If a step lingers, fill with: "This is hitting the real city API right now — no cached data.")

### 1:10 – 1:45 — The result

Map renders: two routes, pothole markers, recommended route highlighted. Read a line of the briefing.

> "Here's the answer. Two routes, same seventeen-minute drive — one passes nine open pothole reports, the other thirteen. Every marker is a real open 311 report, plotted along the route with mile markers. The agent picked the cleaner route and explains why, in plain English — where the worst stretch is and the oldest open report on the way. And note the wording: these are resident *reports*, not verified potholes. The app is honest about that."

### 1:45 – 2:10 — "What the agent did" audit

Open the "What the agent did" panel. Scroll it slowly.

> "Full transparency: every thought, every tool call, every result, even token usage. There's a hard twelve-step limit so it can never loop forever, and this whole trail is also written to a JSONL audit log on disk."

### 2:10 – 2:40 — Analyst agent in the terminal

Switch to the terminal, paste and run:

```
python -m pothole_agent.cli "Which zip codes wait longest for pothole repairs?"
```

> "Same repo, second agent — a 311 accountability analyst. Ask it an open question and it writes its own SQL against those five point nine million rows. Every query it writes is treated as untrusted: a guardrail forces read-only single SELECTs on an allowlisted table, capped at two hundred rows. When a query fails, the error goes back to the model and it fixes its own SQL and retries — you can see that happening here."

(You don't need it to finish on camera — show a couple of query/retry steps, then cut to the final report if time allows.)

### 2:40 – 2:55 — Safety & fallback, one breath

Back to the map.

> "One more thing: if the model API ever goes down, a plain-Python fallback produces the same map and a simpler briefing — the demo never dies. Coordinates are geofenced to Philadelphia, and the route query is built from validated numbers the model never touches."

### 2:55 – 3:00 — Close

> "potholejawn. Built solo in a day at Coffee and Code Philly. potholejawn dot com."

## Cut-for-time priority (if running long)

1. Trim the audit-panel scroll to 10 seconds.
2. Cut the analyst's final report; show only the SQL-retry steps.
3. Trim the hook to one sentence.
Never cut: live steps streaming, the 9-vs-13 comparison, the guardrails/fallback sentence.

"""Trip planning: find routes, scan them for potholes, recommend one.

Two paths produce the same result shape:
  * agent path: Claude drives the tools and explains its choice
  * fallback path: plain Python, used when no API key is set or the agent fails
The map therefore always works, and the agent adds judgment on top.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from .agent import run_agent
from .routing import (
    DEFAULT_BUFFER_M,
    Route,
    RoutingError,
    fetch_routes,
    geocode,
    potholes_along,
)

TRIP_SYSTEM_PROMPT = """\
You are a driving assistant for Philadelphia that helps people avoid potholes.
The user gives a start and a destination. Work like this:
1. geocode_address for both places (call them together in one turn).
2. find_routes between the two points.
3. scan_route for every route returned (call them together in one turn).
4. recommend_route with the best route_id and a one-sentence reason. Weigh
   pothole count first, then how long reports have sat open, then drive time.
   A couple of extra minutes is worth it to skip several potholes.
5. Reply with a short plain-text briefing, 3 to 5 sentences, no markdown:
   which route you picked and why, where the worst stretch is (use the
   addresses and mile markers), and the oldest open report on the way.

Only state numbers that came from tool results. These are resident reports of
street defects, not verified potholes, and some may already be patched, so
say "reported" rather than promising what is on the road.
"""


class TripContext:
    """Holds the state of one trip so tools can share it and the UI can draw it."""

    def __init__(self) -> None:
        self.places: dict[str, dict[str, Any]] = {}
        self.routes: dict[str, Route] = {}
        self.recommended: str | None = None
        self.reason: str = ""

    # --- tools exposed to the model -------------------------------------
    def geocode_address(self, address: str, role: str = "") -> dict[str, Any]:
        found = geocode(address)
        key = (
            role if role in ("start", "end") else ("start" if "start" not in self.places else "end")
        )
        self.places[key] = found
        return {"role": key, **found}

    def find_routes(
        self, start_lat: float, start_lon: float, end_lat: float, end_lon: float
    ) -> dict[str, Any]:
        routes = fetch_routes(start_lat, start_lon, end_lat, end_lon)
        self.routes = {route.route_id: route for route in routes}
        return {"routes": [route.summary() for route in routes]}

    def scan_route(self, route_id: str, buffer_m: int = DEFAULT_BUFFER_M) -> dict[str, Any]:
        route = self.routes.get(route_id)
        if route is None:
            raise RoutingError(f"Unknown route_id '{route_id}'. Known: {sorted(self.routes)}")
        route.potholes = potholes_along(route, buffer_m)
        return {
            **route.summary(),
            "open_reports": len(route.potholes),
            "reports": [
                {k: p[k] for k in ("address", "days_open", "mile_marker")}
                for p in route.potholes[:40]
            ],
        }

    def recommend_route(self, route_id: str, reason: str = "") -> dict[str, Any]:
        if route_id not in self.routes:
            raise RoutingError(f"Unknown route_id '{route_id}'. Known: {sorted(self.routes)}")
        self.recommended, self.reason = route_id, str(reason)[:300]
        return {"recommended": route_id}

    def tool_functions(self) -> dict[str, Callable[..., Any]]:
        return {
            "geocode_address": self.geocode_address,
            "find_routes": self.find_routes,
            "scan_route": self.scan_route,
            "recommend_route": self.recommend_route,
        }

    # --- deterministic helpers ------------------------------------------
    def ensure_complete(self) -> None:
        """Fill in anything the agent skipped so the map is never half-drawn."""
        for route in self.routes.values():
            if route.potholes is None:
                route.potholes = potholes_along(route)
        if self.recommended not in self.routes and self.routes:
            best = min(self.routes.values(), key=lambda r: (len(r.potholes or []), r.minutes))
            self.recommended = best.route_id
            self.reason = "Fewest open pothole reports, then shortest drive time."

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.places.get("start"),
            "end": self.places.get("end"),
            "recommended": self.recommended,
            "reason": self.reason,
            "routes": [
                {
                    **route.summary(),
                    "coordinates": route.coordinates,
                    "potholes": route.potholes or [],
                }
                for route in self.routes.values()
            ],
        }


TRIP_TOOL_SPECS = [
    {
        "name": "geocode_address",
        "description": "Look up coordinates for a Philadelphia address, intersection, or landmark.",
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
                "role": {"type": "string", "enum": ["start", "end"]},
            },
            "required": ["address", "role"],
        },
    },
    {
        "name": "find_routes",
        "description": "Get the main driving route and alternatives between two points.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_lat": {"type": "number"},
                "start_lon": {"type": "number"},
                "end_lat": {"type": "number"},
                "end_lon": {"type": "number"},
            },
            "required": ["start_lat", "start_lon", "end_lat", "end_lon"],
        },
    },
    {
        "name": "scan_route",
        "description": (
            "List open pothole (Street Defect) reports from the last 12 months within "
            "buffer_m meters of a route, in driving order with mile markers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "route_id": {"type": "string"},
                "buffer_m": {"type": "integer", "description": "10 to 100, default 30"},
            },
            "required": ["route_id"],
        },
    },
    {
        "name": "recommend_route",
        "description": "Record your final route choice. Call once, before your briefing.",
        "input_schema": {
            "type": "object",
            "properties": {"route_id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["route_id", "reason"],
        },
    },
]


def fallback_briefing(context: TripContext) -> str:
    """Plain-Python briefing used when the agent is unavailable."""
    route = context.routes[context.recommended]
    potholes = route.potholes or []
    if not potholes:
        return (
            f"Route {route.route_id} ({route.miles} mi, about {route.minutes} min) has no open "
            "pothole reports from the last 12 months."
        )
    oldest = max(potholes, key=lambda p: p["days_open"])
    return (
        f"Route {route.route_id} ({route.miles} mi, about {route.minutes} min) passes "
        f"{len(potholes)} open pothole report(s). The oldest is near {oldest['address']} "
        f"around mile {oldest['mile_marker']}, reported {oldest['days_open']} days ago."
    )


def plan_trip(
    start: str,
    end: str,
    use_agent: bool = True,
    client: Any = None,
    run_dir: str = "runs",
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Plan a trip. Returns routes, potholes, a recommendation, and the agent's steps.

    ``on_step`` (optional) is called with each step event as it happens, so a
    caller can stream progress to a UI while the run is still in flight.
    """
    context = TripContext()
    steps: list[dict[str, Any]] = []
    briefing, mode = "", "fallback"

    def record(event: dict[str, Any]) -> None:
        steps.append(event)
        if on_step:
            on_step(event)

    if use_agent:
        try:
            briefing = run_agent(
                f"I am driving from: {start}\nTo: {end}\nWhich route has the fewest potholes?",
                system_prompt=TRIP_SYSTEM_PROMPT,
                tool_specs=TRIP_TOOL_SPECS,
                tool_functions=context.tool_functions(),
                client=client,
                on_event=record,
                run_dir=run_dir,
                # Opt-in cheaper model for the trip loop (e.g. claude-haiku-4-5);
                # unset means run_agent's normal POTHOLE_MODEL/default applies.
                model=os.environ.get("POTHOLE_TRIP_MODEL"),
            )
            mode = "agent"
        except RoutingError:
            raise
        except Exception as error:  # no key, network trouble, model error: degrade gracefully
            record({"type": "agent_unavailable", "detail": f"{type(error).__name__}: {error}"})

    if not context.routes:  # agent did not get far enough, or was skipped
        record({"type": "fallback", "text": f"Looking up '{start}' and '{end}'."})
        first = context.places.get("start") or context.geocode_address(start, "start")
        last = context.places.get("end") or context.geocode_address(end, "end")
        record({"type": "fallback", "text": "Fetching driving routes between the two points."})
        context.find_routes(first["lat"], first["lon"], last["lat"], last["lon"])
        record(
            {
                "type": "fallback",
                "text": f"Scanning {len(context.routes)} route(s) for open pothole reports.",
            }
        )
        mode = "fallback"

    context.ensure_complete()
    if mode == "fallback" or not briefing.strip():
        briefing = fallback_briefing(context)
    if mode == "fallback":
        record(
            {"type": "fallback", "text": f"Picked route {context.recommended}: {context.reason}"}
        )
    return {**context.to_dict(), "briefing": briefing, "mode": mode, "steps": steps}

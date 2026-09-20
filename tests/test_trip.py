"""Trip planner tests. All network calls are replaced with fakes."""

from types import SimpleNamespace

import pytest

from pothole_agent import routing, trip
from pothole_agent.routing import Route, RoutingError, build_pothole_sql, in_philadelphia
from pothole_agent.webapp import create_app

CITY_HALL = {"lat": 39.9526, "lon": -75.1652, "matched_address": "CITY HALL", "source": "fake"}
STADIUM = {"lat": 39.9061, "lon": -75.1665, "matched_address": "STADIUM", "source": "fake"}
LINE = [[-75.1652, 39.9526], [-75.1660, 39.9300], [-75.1665, 39.9061]]


def _pothole(n, days):
    return {"id": n, "address": f"{n} BROAD ST", "lat": 39.93, "lon": -75.166,
            "reported": "2026-01-01", "days_open": days, "meters_from_route": 5.0, "mile_marker": 1.0}


@pytest.fixture
def fake_services(monkeypatch):
    places = {"start": CITY_HALL, "end": STADIUM}
    monkeypatch.setattr(trip, "geocode", lambda address: places["start" if "hall" in address.lower() else "end"])
    monkeypatch.setattr(
        trip, "fetch_routes",
        lambda *_: [Route("A", 3.4, 12, LINE), Route("B", 3.9, 13, LINE)],
    )
    monkeypatch.setattr(
        trip, "potholes_along",
        lambda route, buffer_m=30: [_pothole(i, 100 + i) for i in range(5 if route.route_id == "A" else 1)],
    )


def test_bounds_check():
    assert in_philadelphia(39.9526, -75.1652)
    assert not in_philadelphia(40.7128, -74.0060)  # New York


def test_fetch_routes_rejects_points_outside_the_city():
    with pytest.raises(RoutingError):
        routing.fetch_routes(40.7128, -74.0060, 39.95, -75.16)


def test_pothole_sql_contains_only_validated_numbers():
    sql = build_pothole_sql(LINE, buffer_m=99999)
    assert ", 100)" in sql, "buffer should be clamped to the maximum"
    assert "status = 'Open'" in sql and "Street Defect" in sql
    with pytest.raises((ValueError, TypeError)):
        build_pothole_sql([["0); DROP TABLE x; --", 1], [2, 3]])


def test_long_routes_are_thinned():
    long_line = [[-75.2 + i * 1e-5, 39.9] for i in range(5000)]
    assert len(routing._thin(long_line)) <= routing.MAX_ROUTE_POINTS + 1
    assert routing._thin(long_line)[-1] == long_line[-1]


def test_fallback_planner_prefers_fewer_potholes(fake_services):
    result = trip.plan_trip("City Hall", "Stadium", use_agent=False)
    assert result["mode"] == "fallback"
    assert result["recommended"] == "B"
    assert [len(r["potholes"]) for r in result["routes"]] == [5, 1]
    assert "Route B" in result["briefing"]


def test_agent_failure_degrades_to_fallback(fake_services, tmp_path):
    class BrokenClient:
        messages = SimpleNamespace(create=lambda **_: (_ for _ in ()).throw(RuntimeError("no key")))

    result = trip.plan_trip("City Hall", "Stadium", client=BrokenClient(), run_dir=tmp_path)
    assert result["mode"] == "fallback" and result["recommended"] == "B"
    assert any(step["type"] == "agent_unavailable" for step in result["steps"])


def test_agent_path_uses_tools_and_records_choice(fake_services, tmp_path):
    def use(tool_id, name, **tool_input):
        return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)

    def reply(content, stop):
        usage = SimpleNamespace(input_tokens=1, output_tokens=1)
        return SimpleNamespace(content=content, stop_reason=stop, usage=usage)

    script = [
        reply([use("1", "geocode_address", address="City Hall", role="start"),
               use("2", "geocode_address", address="Stadium", role="end")], "tool_use"),
        reply([use("3", "find_routes", start_lat=39.9526, start_lon=-75.1652,
                   end_lat=39.9061, end_lon=-75.1665)], "tool_use"),
        reply([use("4", "scan_route", route_id="A")], "tool_use"),  # agent forgets route B
        reply([use("5", "recommend_route", route_id="A", reason="testing")], "tool_use"),
        reply([SimpleNamespace(type="text", text="Take route A.")], "end_turn"),
    ]
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: script.pop(0)))
    result = trip.plan_trip("City Hall", "Stadium", client=client, run_dir=tmp_path)

    assert result["mode"] == "agent" and result["recommended"] == "A"
    assert result["briefing"] == "Take route A."
    route_b = next(r for r in result["routes"] if r["route_id"] == "B")
    assert len(route_b["potholes"]) == 1, "skipped route should be scanned by ensure_complete"


def test_unknown_route_id_is_an_error(fake_services):
    context = trip.TripContext()
    with pytest.raises(RoutingError):
        context.scan_route("Z")


def test_api_validates_input():
    client = create_app().test_client()
    assert client.post("/api/trip", json={"start": "", "end": "x"}).status_code == 400
    assert client.post("/api/trip", json={"start": "a" * 500, "end": "x"}).status_code == 400
    assert client.get("/healthz").get_json()["ok"] is True


def test_api_returns_trip(fake_services, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = create_app().test_client().post("/api/trip", json={"start": "City Hall", "end": "Stadium"})
    assert response.status_code == 200
    assert response.get_json()["recommended"] == "B"
    assert response.headers["X-Content-Type-Options"] == "nosniff"

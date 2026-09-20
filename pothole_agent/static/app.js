"use strict";

(function () {
  const OLD_REPORT_DAYS = 90;
  const COLORS = { recommended: "#006b54", other: "#4a6785" };

  const map = L.map("map", { zoomControl: true }).setView([39.9526, -75.1652], 12);
  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 19,
      attribution:
        'Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; Sources: Esri, HERE, Garmin, OpenStreetMap contributors',
    }
  ).addTo(map);

  const layers = L.layerGroup().addTo(map);
  const form = document.getElementById("trip-form");
  const button = document.getElementById("go");
  const statusEl = document.getElementById("status");
  const results = document.getElementById("results");
  let trip = null;
  let activeId = null;
  let markersById = {};

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text; // textContent only: API data is never parsed as HTML
    return node;
  }

  function setStatus(message, isError) {
    statusEl.textContent = message;
    statusEl.classList.toggle("error", Boolean(isError));
  }

  function ageLabel(days) {
    if (days < 1) return "today";
    if (days < 60) return days + " days open";
    return Math.round(days / 30) + " months open";
  }

  function divIcon(className, size) {
    return L.divIcon({ className: "", html: '<div class="' + className + '"></div>', iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
  }

  function drawMap() {
    layers.clearLayers();
    markersById = {};
    const ordered = trip.routes.slice().sort((a, b) => (a.route_id === activeId) - (b.route_id === activeId));
    ordered.forEach(function (route) {
      const isActive = route.route_id === activeId;
      const latlngs = route.coordinates.map((c) => [c[1], c[0]]);
      const line = L.polyline(latlngs, {
        color: route.route_id === trip.recommended ? COLORS.recommended : COLORS.other,
        weight: isActive ? 7 : 4,
        opacity: isActive ? 0.95 : 0.6,
      }).addTo(layers);
      line.on("click", () => selectRoute(route.route_id));
      if (isActive) map.fitBounds(line.getBounds(), { padding: [40, 40] });
    });

    const active = trip.routes.find((r) => r.route_id === activeId);
    active.potholes.forEach(function (p) {
      const old = p.days_open >= OLD_REPORT_DAYS;
      const marker = L.marker([p.lat, p.lon], { icon: divIcon("pothole-marker" + (old ? " old" : ""), 22) }).addTo(layers);
      const popup = el("div");
      popup.append(el("strong", "", p.address), el("br"), document.createTextNode("Reported " + p.reported + " (" + ageLabel(p.days_open) + ")"), el("br"), document.createTextNode("Mile " + p.mile_marker + " of this route"));
      marker.bindPopup(popup);
      markersById[p.id] = marker;
    });

    [trip.start, trip.end].forEach(function (place) {
      if (place) L.marker([place.lat, place.lon], { icon: divIcon("endpoint-marker", 16) }).bindPopup(place.matched_address).addTo(layers);
    });
  }

  function drawRouteSigns() {
    const box = document.getElementById("routes");
    box.replaceChildren();
    trip.routes.forEach(function (route) {
      const sign = el("button", "route-sign");
      sign.type = "button";
      if (route.route_id === trip.recommended) sign.classList.add("recommended");
      if (route.route_id === activeId) sign.classList.add("active");
      sign.setAttribute("aria-pressed", String(route.route_id === activeId));

      const meta = el("span", "route-meta");
      meta.append(el("strong", "", route.route_id === trip.recommended ? "Recommended" : "Alternative"), document.createTextNode(route.miles + " mi, about " + route.minutes + " min"));
      const count = el("span", "route-count");
      count.append(el("b", "", String(route.potholes.length)), document.createTextNode("reported"));
      sign.append(el("span", "route-letter", route.route_id), meta, count);
      sign.addEventListener("click", () => selectRoute(route.route_id));
      box.append(sign);
    });
  }

  function drawPotholeList() {
    const active = trip.routes.find((r) => r.route_id === activeId);
    document.getElementById("list-title").textContent = active.potholes.length
      ? "Along route " + active.route_id + ", in driving order"
      : "No open reports along route " + active.route_id;
    const list = document.getElementById("pothole-list");
    list.replaceChildren();
    active.potholes.forEach(function (p) {
      const item = el("li");
      const age = el("span", "age" + (p.days_open >= OLD_REPORT_DAYS ? " old" : ""), ageLabel(p.days_open));
      item.append(el("span", "mile", "mi " + p.mile_marker), el("span", "", p.address), age);
      item.addEventListener("click", function () {
        const marker = markersById[p.id];
        if (marker) { map.setView(marker.getLatLng(), 17); marker.openPopup(); }
      });
      list.append(item);
    });
  }

  function describeStep(event) {
    switch (event.type) {
      case "thought": return event.text;
      case "tool_call":
        if (event.input && typeof event.input.query === "string") {
          return "Ran SQL (" + event.tool + "): " + event.input.query;
        }
        return "Called " + event.tool + " " + JSON.stringify(event.input);
      case "tool_error": return "Error from " + event.tool + ": " + event.preview;
      case "usage": return null;
      case "agent_unavailable": return "Agent unavailable (" + event.detail + "). Used the built-in planner instead.";
      case "fallback": return event.text;
      case "final": return "Finished in " + event.steps + " model turns.";
      default: return null;
    }
  }

  function drawSteps() {
    const list = document.getElementById("steps");
    list.replaceChildren();
    let tokens = 0;
    trip.steps.forEach(function (event) {
      if (event.type === "usage") tokens += event.input_tokens + event.output_tokens;
      const text = describeStep(event);
      if (text) list.append(el("li", event.type === "tool_error" ? "error" : "", text));
    });
    if (tokens) list.append(el("li", "", "Total tokens used: " + tokens.toLocaleString()));
    document.getElementById("activity").hidden = trip.steps.length === 0;
  }

  function selectRoute(routeId) {
    activeId = routeId;
    drawMap();
    drawRouteSigns();
    drawPotholeList();
  }

  function render(data) {
    trip = data;
    const briefing = document.getElementById("briefing");
    briefing.replaceChildren(document.createTextNode(data.briefing));
    briefing.append(el("span", "mode", data.mode === "agent" ? "Written by the AI agent from the data below." : "Built-in planner (AI agent not used)."));
    drawSteps();
    results.hidden = false;
    selectRoute(data.recommended);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    button.disabled = true;
    setStatus("Looking up addresses, routes, and open pothole reports.");
    results.hidden = true;
    const live = document.getElementById("live");
    const liveList = document.getElementById("live-steps");
    liveList.replaceChildren();
    live.hidden = false;

    const source = new EventSource(
      "/api/trip/stream?start=" + encodeURIComponent(form.start.value) +
      "&end=" + encodeURIComponent(form.end.value)
    );
    function finish() {
      source.close();
      live.hidden = true;
      button.disabled = false;
    }
    source.addEventListener("step", function (e) {
      const step = JSON.parse(e.data);
      const text = describeStep(step);
      if (text) {
        liveList.append(el("li", step.type === "tool_error" ? "error" : "", text));
        liveList.lastChild.scrollIntoView({ block: "nearest" });
      }
    });
    source.addEventListener("result", function (e) {
      finish();
      setStatus("");
      render(JSON.parse(e.data));
    });
    source.addEventListener("trip_error", function (e) {
      finish();
      setStatus(JSON.parse(e.data).error || "Something went wrong.", true);
    });
    source.onerror = function () { // connection-level failure (e.g. bad input, server down)
      finish();
      setStatus("Could not plan the trip. Check the addresses and try again.", true);
    };
  });

  // Follow-up question box wired to the 311 analyst agent.
  const askForm = document.getElementById("ask-form");
  const askButton = document.getElementById("ask-go");
  const askStatusEl = document.getElementById("ask-status");
  const answerEl = document.getElementById("answer");

  function setAskStatus(message, isError) {
    askStatusEl.textContent = message;
    askStatusEl.classList.toggle("error", Boolean(isError));
  }

  askForm.addEventListener("submit", function (event) {
    event.preventDefault();
    const question = askForm.question.value.trim();
    if (!question) { setAskStatus("Type a question first.", true); return; }
    askButton.disabled = true;
    setAskStatus("The analyst is querying the city's 311 data.");
    answerEl.hidden = true;
    const askLive = document.getElementById("ask-live");
    const askSteps = document.getElementById("ask-live-steps");
    askSteps.replaceChildren();
    askLive.hidden = false;

    const source = new EventSource("/api/ask/stream?q=" + encodeURIComponent(question));
    function finish() {
      source.close();
      askLive.hidden = true;
      askButton.disabled = false;
    }
    source.addEventListener("step", function (e) {
      const step = JSON.parse(e.data);
      const text = describeStep(step);
      if (text) {
        askSteps.append(el("li", step.type === "tool_error" ? "error" : "", text));
        askSteps.lastChild.scrollIntoView({ block: "nearest" });
      }
    });
    source.addEventListener("result", function (e) {
      finish();
      setAskStatus("");
      answerEl.textContent = JSON.parse(e.data).answer; // textContent only, never innerHTML
      answerEl.hidden = false;
    });
    source.addEventListener("ask_error", function (e) {
      finish();
      setAskStatus(JSON.parse(e.data).error || "Something went wrong.", true);
    });
    source.onerror = function () { // connection-level failure (e.g. bad input, server down)
      finish();
      setAskStatus("Could not reach the analyst. Try again.", true);
    };
  });
})();

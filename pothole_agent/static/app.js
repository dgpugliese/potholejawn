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

  // ---- Citywide open-report layer -------------------------------------------
  // Before a trip is planned, show every open pothole report in view as quiet
  // dots on a canvas renderer. Hidden while a trip result is on screen so the
  // routes stay the hero.
  const CITYWIDE_MIN_ZOOM = 12;
  const CITYWIDE_DEBOUNCE_MS = 400;
  const citywideRenderer = L.canvas({ padding: 0.4 });
  const citywideLayer = L.layerGroup().addTo(map);
  const citywideBadge = document.createElement("div");
  citywideBadge.className = "citywide-badge";
  citywideBadge.hidden = true;
  const CitywideBadge = L.Control.extend({ onAdd: () => citywideBadge });
  new CitywideBadge({ position: "bottomleft" }).addTo(map);
  let citywideVisible = true;
  let citywideSeq = 0; // ignores out-of-order fetch responses
  let citywideTimer = null;

  function fetchCitywide() {
    if (!citywideVisible) return;
    if (map.getZoom() < CITYWIDE_MIN_ZOOM) {
      citywideSeq += 1;
      citywideLayer.clearLayers();
      citywideBadge.hidden = true;
      return;
    }
    const b = map.getBounds();
    const bbox = [b.getSouth(), b.getWest(), b.getNorth(), b.getEast()]
      .map((v) => v.toFixed(5))
      .join(",");
    citywideSeq += 1;
    const seq = citywideSeq;
    fetch("/api/potholes?bbox=" + encodeURIComponent(bbox))
      .then((r) => {
        if (!r.ok) throw new Error("potholes fetch failed: " + r.status);
        return r.json();
      })
      .then((data) => {
        if (seq !== citywideSeq || !citywideVisible) return; // a newer fetch or a trip took over
        citywideLayer.clearLayers();
        data.potholes.forEach(function (p) {
          const dot = L.circleMarker([p.lat, p.lon], {
            renderer: citywideRenderer,
            radius: 3.5,
            weight: 1,
            color: "#26282b",
            opacity: 0.55,
            fillColor: "#ffcc00",
            fillOpacity: 0.5,
          }).addTo(citywideLayer);
          const popup = el("div");
          popup.append(
            el("strong", "", p.address),
            el("br"),
            document.createTextNode("Reported " + p.reported + " (" + ageLabel(p.days_open) + ")")
          );
          dot.bindPopup(popup);
        });
        const n = data.potholes.length;
        citywideBadge.textContent =
          n.toLocaleString() + (n >= 1500 ? "+" : "") +
          " open pothole report" + (n === 1 ? "" : "s") + " in view";
        citywideBadge.hidden = false;
      })
      .catch(function () { /* keep the last good dots; the map stays usable */ });
  }

  function hideCitywide() {
    citywideVisible = false;
    citywideSeq += 1; // cancel any in-flight fetch
    map.removeLayer(citywideLayer);
    citywideBadge.hidden = true;
  }

  function showCitywide() {
    if (!citywideVisible) {
      citywideVisible = true;
      citywideLayer.addTo(map);
    }
    fetchCitywide();
  }

  map.on("moveend", function () {
    clearTimeout(citywideTimer);
    citywideTimer = setTimeout(fetchCitywide, CITYWIDE_DEBOUNCE_MS);
  });
  fetchCitywide(); // populate the map on first load, before any trip is planned
  // ---------------------------------------------------------------------------

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
    statusEl.classList.toggle("busy", Boolean(message) && !isError);
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
    hideCitywide(); // trip results are the hero; the citywide dots would be noise
    selectRoute(data.recommended);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    button.disabled = true;
    setStatus("Looking up addresses, routes, and open pothole reports.");
    results.hidden = true;
    showCitywide(); // a new search starts fresh: bring back the citywide dots
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
    askStatusEl.classList.toggle("busy", Boolean(message) && !isError);
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

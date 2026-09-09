/** Rendering of planned routes, attraction markers and their popups. */

import {
  DAY_COLORS,
  clearMapLayers,
  map,
  mapFitOptions,
  state
} from "./map-context.js?v=10";
import {
  byId,
  escapeHtml,
  formatRouteDuration,
  formatVisitDuration,
  renderWarnings,
  safeImageUrl
} from "./ui-utils.js?v=5";


const MARKER_GROUP_DISTANCE_METERS = 50;
const METERS_PER_LATITUDE_DEGREE = 111_320;


/** Remove all result layers while preserving uploaded-data previews. */
export function clearResultMap() {
  clearMapLayers(state.resultLayers);
  state.dayLayers = {};
}


/** Render every route day on the map and populate cards and legend. */
export function renderResult(result) {
  clearResultMap();
  result.days.forEach(day => renderDayLayers(day));
  fitAllResultLayers();
  showResultPanel(result);
}


function renderDayLayers(day) {
  const color = DAY_COLORS[(day.day - 1) % DAY_COLORS.length];
  const connectorLayer = createAttractionConnectorLayer(day, color).addTo(map);
  const routeLayer = L.geoJSON(day.route, {
    style: { color, weight: 6, opacity: .88, lineCap: "round" }
  }).addTo(map);
  const directionLayer = createDirectionLayer(day, color).addTo(map);
  const attractionLayer = createAttractionLayer(day, color).addTo(map);
  const layers = [connectorLayer, routeLayer, directionLayer, attractionLayer];
  state.resultLayers.push(...layers);
  state.dayLayers[day.day] = layers;
}


function fitAllResultLayers() {
  const bounds = L.featureGroup(state.resultLayers).getBounds();
  if (bounds.isValid()) map.fitBounds(bounds.pad(.08), mapFitOptions());
}


function showResultPanel(result) {
  byId("results").hidden = false;
  byId("progressPanel").hidden = true;
  byId("resultsContent").hidden = false;
  byId("results").classList.add("has-plan");
  byId("planStep").classList.add("completed");
  byId("planStepStatus").textContent = "Plan gotowy";
  const warnings = byId("warnings");
  warnings.textContent = "";
  const visibleWarnings = (result.warnings || []).filter(
    message => !String(message).startsWith("Pominięto krawędź")
  );
  renderWarnings(warnings, visibleWarnings, "Pokaż pominięte obiekty i ostrzeżenia");
  byId("dayCards").innerHTML = result.days.map(renderDayCard).join("");
  document.querySelectorAll("[data-day-toggle]").forEach(toggle => (
    toggle.addEventListener("change", event => {
      setDayVisibility(Number(event.target.dataset.dayToggle), event.target.checked);
    })
  ));
}


function renderDayCard(day) {
  const color = DAY_COLORS[(day.day - 1) % DAY_COLORS.length];
  const duration = formatRouteDuration(day.total_time_seconds);
  const itinerary = day.itinerary.length
    ? day.itinerary.map(item => (
      `<li><b>${item.order}</b><span>${escapeHtml(item.name)}</span>`
      + `<time>${formatVisitDuration(item.duration_seconds)}</time></li>`
    )).join("")
    : "<li>Brak atrakcji</li>";
  return `<article class="day-card" data-day-card="${day.day}">`
    + '<div class="day-card-head"><div>'
    + `<div class="day-number"><i style="background:${color}"></i>Dzień ${day.day}</div>`
    + `<div class="day-stats">${duration} · waga ${day.total_weight}</div>`
    + '</div><label class="day-visibility">'
    + `<input type="checkbox" data-day-toggle="${day.day}" checked>`
    + '<span aria-hidden="true"></span><em>Pokaż</em></label></div>'
    + `<ol class="itinerary">${itinerary}</ol></article>`;
}


function createAttractionLayer(day, color) {
  const layer = L.featureGroup();
  const itineraryByOrder = new Map(
    (day.itinerary || []).map(item => [Number(item.order), item])
  );
  const groups = groupNearbyAttractions(
    day.attractions?.features || [], MARKER_GROUP_DISTANCE_METERS
  );
  groups.forEach(group => {
    group.features.sort((first, second) => attractionOrder(first) - attractionOrder(second));
    const orders = group.features.map(feature => attractionOrder(feature, "•"));
    const title = orders.length === 1
      ? `Atrakcja ${orders[0]}`
      : `Atrakcje ${orders.join(", ")}`;
    const marker = L.marker(group.latlng, {
      icon: createAttractionIcon(orders, color),
      riseOnHover: true,
      title
    });
    const popupContent = createAttractionGroupPopup(
      group.features, day, itineraryByOrder
    );
    popupContent.addEventListener("carouselchange", () => marker.getPopup()?.update());
    marker.bindPopup(popupContent, { maxWidth: 320, minWidth: 240 }).addTo(layer);
  });
  return layer;
}

export function groupNearbyAttractions(features, maximumDistanceMeters) {
  const points = features
    .map(feature => ({ feature, latlng: toLatLng(feature.geometry?.coordinates) }))
    .filter(point => point.latlng);
  if (!points.length) return [];

  const referenceLatitude = points.reduce((sum, point) => sum + point.latlng[0], 0)
    / points.length;
  const longitudeScale = METERS_PER_LATITUDE_DEGREE
    * Math.cos(referenceLatitude * Math.PI / 180);
  const cellFor = latlng => [
    Math.floor((latlng[1] * longitudeScale) / maximumDistanceMeters),
    Math.floor((latlng[0] * METERS_PER_LATITUDE_DEGREE) / maximumDistanceMeters)
  ];
  const cellKey = (x, y) => `${x}:${y}`;
  const groups = [];
  const groupIndexesByCell = new Map();

  points.forEach(point => {
    const [cellX, cellY] = cellFor(point.latlng);
    const candidates = new Set();
    for (let offsetX = -1; offsetX <= 1; offsetX += 1) {
      for (let offsetY = -1; offsetY <= 1; offsetY += 1) {
        (groupIndexesByCell.get(cellKey(cellX + offsetX, cellY + offsetY)) || [])
          .forEach(index => candidates.add(index));
      }
    }
    const matchingIndex = [...candidates].find(index => (
      groups[index].every(existing => (
        map.distance(existing.latlng, point.latlng) <= maximumDistanceMeters
      ))
    ));
    if (matchingIndex !== undefined) {
      groups[matchingIndex].push(point);
      return;
    }
    const newIndex = groups.push([point]) - 1;
    const key = cellKey(cellX, cellY);
    if (!groupIndexesByCell.has(key)) groupIndexesByCell.set(key, []);
    groupIndexesByCell.get(key).push(newIndex);
  });

  return groups.map(group => ({
    latlng: [
      group.reduce((sum, point) => sum + point.latlng[0], 0) / group.length,
      group.reduce((sum, point) => sum + point.latlng[1], 0) / group.length
    ],
    features: group.map(point => point.feature)
  }));
}


function attractionOrder(feature, fallback = Number.MAX_SAFE_INTEGER) {
  const rawOrder = Number(feature.properties?.visit_order);
  return Number.isFinite(rawOrder) && rawOrder > 0 ? Math.round(rawOrder) : fallback;
}


function createAttractionIcon(orders, color) {
  if (orders.length > 1) {
    const maximumDigits = Math.max(...orders.map(order => String(order).length));
    const estimatedSize = Math.max(32, orders.length * 10 + 10, maximumDigits * 6 + 12);
    const pinSize = Math.ceil(estimatedSize / 2) * 2;
    const iconSize = Math.ceil(pinSize * Math.SQRT2);
    const numbers = orders.map(order => `<b>${order}</b>`).join("");
    return L.divIcon({
      className: "attraction-marker-icon attraction-marker-icon--group",
      html: `<div class="attraction-pin attraction-pin--group" `
        + `style="--attraction-color:${color};--pin-size:${pinSize}px;--pin-box-size:${iconSize}px">`
        + `<span class="attraction-pin-orders">${numbers}</span></div>`,
      iconSize: [iconSize, iconSize],
      iconAnchor: [iconSize / 2, iconSize],
      popupAnchor: [0, -iconSize + 8]
    });
  }
  const order = orders[0] ?? "•";
  return L.divIcon({
    className: "attraction-marker-icon",
    html: `<div class="attraction-pin" style="--attraction-color:${color}"><span>${order}</span></div>`,
    iconSize: [26, 34],
    iconAnchor: [13, 32],
    popupAnchor: [0, -28]
  });
}


function createCarouselButton(modifier, label, symbol) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `attraction-popup-arrow attraction-popup-arrow--${modifier}`;
  button.setAttribute("aria-label", label);
  button.textContent = symbol;
  return button;
}


function createAttractionGroupPopup(features, day, itineraryByOrder) {
  if (features.length === 1) {
    return createAttractionPopup(features[0], day, itineraryByOrder);
  }
  const container = document.createElement("section");
  container.className = "attraction-popup-carousel";
  const previous = createCarouselButton("previous", "Poprzednia atrakcja", "‹");
  const next = createCarouselButton("next", "Następna atrakcja", "›");
  const viewport = document.createElement("div");
  viewport.className = "attraction-popup-viewport";
  const cards = features.map((feature, index) => {
    const card = createAttractionPopup(feature, day, itineraryByOrder);
    card.hidden = index !== 0;
    viewport.appendChild(card);
    return card;
  });
  let selectedIndex = 0;
  const showCard = index => {
    selectedIndex = Math.max(0, Math.min(cards.length - 1, index));
    cards.forEach((card, cardIndex) => { card.hidden = cardIndex !== selectedIndex; });
    previous.disabled = selectedIndex === 0;
    next.disabled = selectedIndex === cards.length - 1;
    container.dispatchEvent(new Event("carouselchange"));
  };
  previous.addEventListener("click", event => {
    event.stopPropagation();
    showCard(selectedIndex - 1);
  });
  next.addEventListener("click", event => {
    event.stopPropagation();
    showCard(selectedIndex + 1);
  });
  container.append(previous, viewport, next);
  showCard(0);
  return container;
}


function createAttractionConnectorLayer(day, color) {
  const layer = L.featureGroup();
  const markerGroups = groupNearbyAttractions(
    day.attractions?.features || [], MARKER_GROUP_DISTANCE_METERS
  );
  markerGroups.forEach(group => {
    const vertices = new Map();
    group.features.forEach(feature => {
      const properties = feature.properties || {};
      const vertex = toLatLng(properties.vertex_coordinates);
      if (!vertex) return;
      const key = String(properties.vertex_id ?? vertex.join(","));
      if (!vertices.has(key)) vertices.set(key, vertex);
    });
    vertices.forEach(vertex => {
      L.polyline([vertex, group.latlng], {
        color,
        weight: 2.5,
        opacity: .72,
        dashArray: "1 7",
        lineCap: "round",
        lineJoin: "round",
        interactive: false
      }).addTo(layer);
    });
  });
  return layer;
}


function toLatLng(coordinates) {
  if (!Array.isArray(coordinates) || coordinates.length < 2) return null;
  const lon = Number(coordinates[0]);
  const lat = Number(coordinates[1]);
  return Number.isFinite(lon) && Number.isFinite(lat) ? [lat, lon] : null;
}


function setDayVisibility(day, visible) {
  (state.dayLayers[day] || []).forEach(layer => {
    if (visible && !map.hasLayer(layer)) map.addLayer(layer);
    if (!visible && map.hasLayer(layer)) map.removeLayer(layer);
  });
  document.querySelector(`[data-day-card="${day}"]`)?.classList.toggle("day-hidden", !visible);
  fitVisibleRoutes();
}


function createDirectionLayer(day, color) {
  const group = L.featureGroup();
  const features = day.route?.features || [];
  const stride = Math.max(1, Math.ceil(features.length / 6));
  features.forEach((feature, index) => {
    if (index % stride !== 0 && index !== features.length - 1) return;
    const position = lineMidpointAndAngle(feature.geometry?.coordinates || []);
    if (!position) return;
    const icon = L.divIcon({
      className: "route-arrow-icon",
      html: `<span style="--arrow-angle:${position.angle}deg;--arrow-color:${color}">➤</span>`,
      iconSize: [22, 22],
      iconAnchor: [11, 11]
    });
    L.marker([position.lat, position.lon], { icon, keyboard: false })
      .addTo(group);
  });
  return group;
}


/** Find a line's length-weighted midpoint and its screen-facing angle. */
function lineMidpointAndAngle(coordinates) {
  if (!Array.isArray(coordinates) || coordinates.length < 2) return null;
  const segments = [];
  let totalLength = 0;
  for (let index = 0; index < coordinates.length - 1; index += 1) {
    const from = coordinates[index];
    const to = coordinates[index + 1];
    const length = map.distance([from[1], from[0]], [to[1], to[0]]);
    if (length > 0) {
      segments.push({ from, to, length });
      totalLength += length;
    }
  }
  if (!segments.length) return null;
  const target = totalLength / 2;
  let travelled = 0;
  let selected = segments[segments.length - 1];
  for (const segment of segments) {
    if (travelled + segment.length >= target) {
      selected = segment;
      break;
    }
    travelled += segment.length;
  }
  const ratio = Math.min(1, Math.max(0, (target - travelled) / selected.length));
  const lon = selected.from[0] + (selected.to[0] - selected.from[0]) * ratio;
  const lat = selected.from[1] + (selected.to[1] - selected.from[1]) * ratio;
  const meanLatitude = (selected.from[1] + selected.to[1]) * Math.PI / 360;
  const dx = (selected.to[0] - selected.from[0]) * Math.cos(meanLatitude);
  const dy = selected.to[1] - selected.from[1];
  return { lat, lon, angle: Math.atan2(-dy, dx) * 180 / Math.PI };
}


function fitVisibleRoutes() {
  const visibleLayers = Object.values(state.dayLayers)
    .flat()
    .filter(layer => map.hasLayer(layer));
  if (!visibleLayers.length) return;
  const bounds = L.featureGroup(visibleLayers).getBounds();
  if (bounds.isValid()) map.fitBounds(bounds.pad(.08), mapFitOptions());
}


function createAttractionPopup(feature, day, itineraryByOrder) {
  const properties = feature.properties || {};
  const itineraryItem = itineraryByOrder.get(Number(properties.visit_order));
  const container = document.createElement("article");
  container.className = "attraction-popup";
  const imageUrl = safeImageUrl(properties.image);
  if (imageUrl) {
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = itineraryItem?.name ? `Zdjęcie: ${itineraryItem.name}` : "Zdjęcie atrakcji";
    image.loading = "lazy";
    image.referrerPolicy = "no-referrer";
    image.decoding = "async";
    image.addEventListener("error", () => image.remove());
    container.appendChild(image);
  }
  const content = document.createElement("div");
  const title = document.createElement("h3");
  title.textContent = itineraryItem?.name || "Atrakcja";
  const meta = document.createElement("small");
  const duration = itineraryItem?.duration_seconds
    ? ` · ${formatVisitDuration(itineraryItem.duration_seconds)}`
    : "";
  meta.textContent = `Dzień ${day.day} · punkt ${properties.visit_order}${duration}`;
  content.append(title, meta);
  if (properties.description) {
    const description = document.createElement("p");
    description.textContent = String(properties.description);
    content.appendChild(description);
  }
  container.appendChild(content);
  return container;
}

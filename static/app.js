import { map, mapFitOptions, state } from "./js/map-context.js?v=12";
import { clearResultMap, renderResult } from "./js/result-map.js?v=22";
import { apiErrorMessage, byId } from "./js/ui-utils.js?v=5";
import {
  loadPublicNetworkEdits,
  requestPersistentLocalStorage,
  savePublicNetworkEdits
} from "./js/local-store.js?v=2";

let startMarker;
let dataReady = false;
let startReady = false;
let selectingStart = false;
const mobileLayout = window.matchMedia("(max-width: 900px)");
const PUBLIC_PREFIX = "public:";
let localAdminEnabled = false;
let editorRows = [];
let editorReference = null;


function networkReference(value) {
  if (value.startsWith(PUBLIC_PREFIX)) return { source: "public", id: value.slice(PUBLIC_PREFIX.length) };
  return null;
}

function setMobilePlanningPanel(collapsed) {
  const sidebar = document.querySelector(".sidebar");
  const toggle = byId("mobilePanelToggle");
  if (!sidebar || !toggle) return;
  const shouldCollapse = mobileLayout.matches && collapsed;
  sidebar.classList.toggle("mobile-collapsed", shouldCollapse);
  toggle.setAttribute("aria-expanded", String(!shouldCollapse));
  toggle.setAttribute(
    "aria-label",
    shouldCollapse ? "Rozwiń panel planowania" : "Zwiń panel planowania"
  );
  toggle.querySelector(".mobile-panel-toggle-label").textContent = shouldCollapse ? "Rozwiń" : "Zwiń";
  window.setTimeout(() => map.invalidateSize(), 220);
}

function setMobileResultView(view) {
  const results = byId("results");
  if (!results.classList.contains("has-plan")) return;
  results.classList.remove("is-dragging");
  results.style.removeProperty("height");
  results.dataset.mobileView = view;
  byId("mobileShowMap").setAttribute("aria-pressed", String(view === "map"));
  byId("mobileShowPlan").setAttribute("aria-pressed", String(view === "plan"));
  window.setTimeout(() => {
    map.invalidateSize();
    if (view === "plan" || !state.resultLayers.length) return;
    const bounds = L.featureGroup(state.resultLayers).getBounds();
    if (bounds.isValid()) map.fitBounds(bounds.pad(.08), mapFitOptions());
  }, 250);
}

function activateMobileResultLayout() {
  document.querySelector(".app-shell").classList.add("mobile-results-layout");
  map.invalidateSize({ pan: false });
}

function deactivateMobileResultLayout() {
  document.querySelector(".app-shell").classList.remove("mobile-results-layout");
  const results = byId("results");
  results.classList.remove("is-dragging");
  results.style.removeProperty("height");
  delete results.dataset.mobileView;
}

byId("mobileShowMap").addEventListener("click", () => setMobileResultView("map"));
byId("mobileShowPlan").addEventListener("click", () => setMobileResultView("plan"));
byId("mobileEditPlan").addEventListener("click", () => {
  deactivateMobileResultLayout();
  setMobilePlanningPanel(false);
  openStep("planStep");
});

const mobileResultsDrag = byId("mobileResultsDrag");
let resultDragStartY = 0;
let resultDragStartHeight = 0;

mobileResultsDrag.addEventListener("pointerdown", event => {
  if (!mobileLayout.matches) return;
  const results = byId("results");
  resultDragStartY = event.clientY;
  resultDragStartHeight = results.getBoundingClientRect().height;
  results.classList.add("is-dragging");
  results.style.height = `${resultDragStartHeight}px`;
  mobileResultsDrag.setPointerCapture(event.pointerId);
});

mobileResultsDrag.addEventListener("pointermove", event => {
  if (!mobileResultsDrag.hasPointerCapture(event.pointerId)) return;
  const results = byId("results");
  const workspaceHeight = document.querySelector(".workspace").getBoundingClientRect().height;
  const nextHeight = Math.max(56, Math.min(workspaceHeight, resultDragStartHeight + resultDragStartY - event.clientY));
  results.style.height = `${nextHeight}px`;
});

function finishResultDrag(event) {
  if (!mobileResultsDrag.hasPointerCapture(event.pointerId)) return;
  mobileResultsDrag.releasePointerCapture(event.pointerId);
  const results = byId("results");
  const workspaceHeight = document.querySelector(".workspace").getBoundingClientRect().height;
  const ratio = results.getBoundingClientRect().height / workspaceHeight;
  setMobileResultView(ratio < .25 ? "map" : ratio > .75 ? "plan" : "split");
}

mobileResultsDrag.addEventListener("pointerup", finishResultDrag);
mobileResultsDrag.addEventListener("pointercancel", finishResultDrag);
mobileResultsDrag.addEventListener("keydown", event => {
  const current = byId("results").dataset.mobileView || "split";
  if (event.key === "Home") setMobileResultView("map");
  else if (event.key === "End") setMobileResultView("plan");
  else if (event.key === "ArrowUp") setMobileResultView(current === "map" ? "split" : "plan");
  else if (event.key === "ArrowDown") setMobileResultView(current === "plan" ? "split" : "map");
  else return;
  event.preventDefault();
});

byId("mobilePanelToggle").addEventListener("click", () => {
  const collapsed = document.querySelector(".sidebar").classList.contains("mobile-collapsed");
  setMobilePlanningPanel(!collapsed);
});

mobileLayout.addEventListener("change", event => {
  if (!event.matches) setMobilePlanningPanel(false);
  else window.setTimeout(() => map.invalidateSize(), 0);
});

function openStep(stepId) {
  const target = byId(stepId);
  if (!target || target.classList.contains("locked")) return;
  document.querySelectorAll(".step-card").forEach(card => card.classList.toggle("active", card === target));
}

document.querySelectorAll(".step-head").forEach(button => button.addEventListener("click", () => {
  const card = byId(button.dataset.step);
  if (card.classList.contains("locked")) return;
  if (card.id === "planStep" && card.classList.contains("active") && card.classList.contains("completed")) {
    card.classList.remove("active");
    return;
  }
  openStep(card.id);
}));

function unlockStep(stepId) { byId(stepId).classList.remove("locked"); }
function lockStep(stepId) { byId(stepId).classList.add("locked"); byId(stepId).classList.remove("active", "completed"); }

function markDataReady(label) {
  dataReady = true;
  byId("dataStep").classList.add("completed");
  byId("dataStepStatus").textContent = label;
  unlockStep("startStep");
  byId("startStepStatus").textContent = startReady ? "Punkt startowy ustawiony" : "Wybierz punkt na mapie";
  openStep(startReady ? "planStep" : "startStep");
}

function resetDataStep() {
  dataReady = false; clearStart();
  resetResult();
  byId("dataStep").classList.remove("completed");
  byId("dataStepStatus").textContent = localAdminEnabled
    ? "Przygotuj nowy zestaw"
    : "Wybierz przygotowany zestaw";
  lockStep("startStep"); lockStep("planStep"); openStep("dataStep");
}

async function loadPreparedNetworks(selectedId = "") {
  let items = [];
  try {
    const response = await fetch("/api/networks");
    const data = await response.json();
    if (response.ok) {
      items = (data.items || []).map(item => ({
        ...item,
        source: "public",
        selectionId: `${PUBLIC_PREFIX}${item.id}`
      }));
    }
  } catch (_) {
    items = [];
  }
  state.networkMetadata = Object.fromEntries(items.map(item => [item.selectionId, item]));
  const select = byId("preparedNetwork");
  select.textContent = "";
  select.add(new Option("— wybierz zestaw —", ""));
  const appendGroup = (label, sourceItems) => {
    if (!sourceItems.length) return;
    const group = document.createElement("optgroup");
    group.label = label;
    sourceItems.forEach(item => {
      group.appendChild(new Option(`${item.name} · ${item.attraction_vertices} atrakcji`, item.selectionId));
    });
    select.appendChild(group);
  };
  appendGroup("Przygotowane zestawy", items);
  if (selectedId) select.value = selectedId;
  const hasNetworks = items.length > 0;
  select.disabled = !hasNetworks;
  byId("newNetworkButton").hidden = !localAdminEnabled;
  byId("preparationPanel").hidden = !localAdminEnabled || hasNetworks;
  if (!hasNetworks) {
    select.textContent = "";
    select.add(new Option("Brak dostępnych zestawów", ""));
    byId("preparationPanel").hidden = !localAdminEnabled;
    resetDataStep();
  }
  updatePreparedInfo();
  if (selectedId) {
    await activateNetwork(selectedId);
  }
}


async function activateNetwork(selectionId) {
  const reference = networkReference(selectionId);
  if (!reference) return;
  state.edges = null;
  state.attractions = null;
  state.localFields = null;
  state.localPreparationOptions = null;
  state.publicAttractionEdits = await loadPublicNetworkEdits(reference.id);
  state.activeNetwork = reference;
  clearStart();
  fitPreparedBounds(selectionId);
  const select = byId("preparedNetwork");
  const selectedOption = select.options[select.selectedIndex];
  markDataReady(selectedOption?.text || state.networkMetadata[selectionId]?.name || "Zestaw wybrany");
  updatePreparedInfo();
}

function updatePreparedInfo() {
  const select = byId("preparedNetwork");
  const reference = networkReference(select.value);
  byId("editAttractionsButton").hidden = !reference;
  byId("preparedInfo").textContent = select.value
    ? "Wybrano zestaw z listy dostępnych. Możesz rozpocząć planowanie trasy lub edytować atrakcje."
    : select.disabled
      ? "Nie ma jeszcze dostępnych przygotowanych zestawów."
      : localAdminEnabled
        ? "Wybierz zestaw albo przygotuj nowy jako administrator lokalny."
        : "Wybierz jeden z przygotowanych zestawów.";
}

byId("preparedNetwork").addEventListener("change", async event => {
  updatePreparedInfo();
  try {
    if (event.target.value) {
      byId("preparationPanel").hidden = true;
      await activateNetwork(event.target.value);
    } else {
      state.activeNetwork = null;
      state.edges = null;
      state.attractions = null;
      state.localFields = null;
      state.localPreparationOptions = null;
      byId("preparationPanel").hidden = !localAdminEnabled;
      resetDataStep();
    }
  } catch (error) { showError(error.message); }
});

async function initializeApplication() {
  try {
    const response = await fetch("/api/config");
    const config = await response.json();
    localAdminEnabled = response.ok && config.local_admin === true;
  } catch (_) {
    localAdminEnabled = false;
  }
  await loadPreparedNetworks();
}

initializeApplication().catch(error => {
  byId("preparedInfo").textContent = "Nie udało się wczytać katalogu przygotowanych zestawów.";
  byId("preparedNetwork").disabled = true;
  byId("preparationPanel").hidden = !localAdminEnabled;
  resetDataStep();
  showError(error.message);
});

byId("newNetworkButton").addEventListener("click", () => {
  if (!localAdminEnabled) return;
  byId("preparedNetwork").value = "";
  state.activeNetwork = null;
  state.edges = null;
  state.attractions = null;
  state.localFields = null;
  state.localPreparationOptions = null;
  byId("preparationPanel").hidden = false;
  updatePreparedInfo(); resetDataStep(); previewData();
});

function visibleEditorRows() {
  const query = byId("attractionSearch").value.trim().toLocaleLowerCase("pl");
  return query
    ? editorRows.filter(row => row.name.toLocaleLowerCase("pl").includes(query))
    : editorRows;
}

function updateEditorSummary() {
  const visible = visibleEditorRows();
  const enabledCount = editorRows.filter(row => row.enabled).length;
  byId("attractionEditorCount").textContent = `${enabledCount} z ${editorRows.length} atrakcji włączonych`;
  const toggle = byId("toggleAllAttractions");
  toggle.checked = visible.length > 0 && visible.every(row => row.enabled);
  toggle.indeterminate = visible.some(row => row.enabled) && !toggle.checked;
}

function renderEditorRows() {
  const body = byId("attractionEditorRows");
  body.textContent = "";
  visibleEditorRows().forEach(row => {
    const tableRow = document.createElement("tr");
    tableRow.dataset.attractionId = row.id;
    tableRow.classList.toggle("disabled", !row.enabled);

    const enabledCell = document.createElement("td");
    const enabled = document.createElement("input");
    enabled.type = "checkbox";
    enabled.className = "editor-enabled";
    enabled.checked = row.enabled;
    enabled.setAttribute("aria-label", `Uwzględnij: ${row.name}`);
    enabledCell.appendChild(enabled);

    const nameCell = document.createElement("td");
    nameCell.textContent = row.name;
    const weightCell = document.createElement("td");
    weightCell.className = "weight-cell";
    weightCell.textContent = String(row.weight);
    const durationCell = document.createElement("td");
    durationCell.className = "duration-cell";
    const duration = document.createElement("input");
    duration.type = "number";
    duration.min = "0";
    duration.step = "1";
    duration.className = "duration-edit";
    duration.value = String(Math.round(Number(row.duration_seconds) / 60 * 100) / 100);
    duration.setAttribute("aria-label", `Czas zwiedzania: ${row.name}`);
    durationCell.append(duration, document.createTextNode("min"));
    tableRow.append(enabledCell, nameCell, weightCell, durationCell);
    body.appendChild(tableRow);
  });
  updateEditorSummary();
}

function closeAttractionEditor() {
  byId("attractionEditor").hidden = true;
  editorRows = [];
  editorReference = null;
}

async function openAttractionEditor() {
  hideError();
  const reference = networkReference(byId("preparedNetwork").value);
  if (!reference) return;
  const button = byId("editAttractionsButton");
  button.disabled = true;
  try {
    const response = await fetch(`/api/networks/${encodeURIComponent(reference.id)}/attractions`);
    const data = await response.json();
    if (!response.ok) throw new Error(apiErrorMessage(data, "Nie udało się wczytać atrakcji."));
    const saved = new Map((state.publicAttractionEdits || []).map(edit => [String(edit.id), edit]));
    editorRows = (data.items || []).map(item => ({ ...item, ...(saved.get(String(item.id)) || {}) }));
    byId("attractionEditorIntro").textContent = "Możesz włączać i wyłączać atrakcje oraz zmieniać czas potrzebny na ich zwiedzenie, dostosowując plan do swoich preferencji.";
    editorReference = reference;
    byId("attractionSearch").value = "";
    renderEditorRows();
    byId("attractionEditor").hidden = false;
    byId("attractionSearch").focus();
  } finally {
    button.disabled = false;
  }
}

byId("editAttractionsButton").addEventListener("click", () => {
  openAttractionEditor().catch(error => showError(error.message));
});
byId("closeAttractionEditor").addEventListener("click", closeAttractionEditor);
byId("cancelAttractionEditor").addEventListener("click", closeAttractionEditor);
byId("attractionEditor").addEventListener("click", event => {
  if (event.target === byId("attractionEditor")) closeAttractionEditor();
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !byId("attractionEditor").hidden) closeAttractionEditor();
});
byId("attractionSearch").addEventListener("input", renderEditorRows);
byId("toggleAllAttractions").addEventListener("change", event => {
  visibleEditorRows().forEach(row => { row.enabled = event.target.checked; });
  renderEditorRows();
});
byId("attractionEditorRows").addEventListener("change", event => {
  const tableRow = event.target.closest("tr[data-attraction-id]");
  const row = editorRows.find(item => item.id === tableRow?.dataset.attractionId);
  if (!row) return;
  if (event.target.classList.contains("editor-enabled")) {
    row.enabled = event.target.checked;
    tableRow.classList.toggle("disabled", !row.enabled);
  } else if (event.target.classList.contains("duration-edit")) {
    row.duration_seconds = Number(event.target.value) * 60;
  }
  updateEditorSummary();
});

byId("saveAttractionEditor").addEventListener("click", async () => {
  if (!editorReference) return;
  const reference = editorReference;
  const invalid = editorRows.find(row => !Number.isFinite(Number(row.duration_seconds)) || Number(row.duration_seconds) < 0);
  if (invalid) {
    showError(`Podaj poprawny czas zwiedzania atrakcji: ${invalid.name}.`);
    return;
  }
  const edits = editorRows.map(row => ({
    id: row.id,
    enabled: Boolean(row.enabled),
    duration_seconds: Math.round(Number(row.duration_seconds) * 100) / 100
  }));
  const saveButton = byId("saveAttractionEditor");
  saveButton.disabled = true;
  try {
    await requestPersistentLocalStorage();
    await savePublicNetworkEdits(reference.id, edits);
    state.publicAttractionEdits = edits;
    closeAttractionEditor();
    updatePreparedInfo();
  } catch (error) {
    showError(error.message);
  } finally {
    saveButton.disabled = false;
  }
});

function setStart(lat, lon) {
  if (!dataReady) {
    return;
  }
  if (state.result) resetResult();
  byId("startLat").value = Number(lat).toFixed(6);
  byId("startLon").value = Number(lon).toFixed(6);
  if (startMarker) map.removeLayer(startMarker);
  const icon = L.divIcon({
    className: "hotel-marker-icon",
    html: '<div class="hotel-marker"><div class="hotel-pin"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 21V3h14v18M3 21h18M8 7h2m4 0h2M8 11h2m4 0h2M10 21v-5h4v5"/></svg></div></div>',
    iconSize: [32, 36],
    iconAnchor: [16, 31]
  });
  startMarker = L.marker([lat, lon], { icon }).addTo(map).bindTooltip("Punkt startowy");
  startReady = true;
  stopStartSelection();
  byId("startStep").classList.add("completed");
  byId("startStepStatus").textContent = "Punkt startowy ustawiony";
  unlockStep("planStep");
  byId("planStepStatus").textContent = "Wpisz liczbę dni i czas trwania każdej trasy";
  openStep("planStep");
  setMobilePlanningPanel(false);
}

function clearStart() {
  startReady = false;
  stopStartSelection();
  if (startMarker) { map.removeLayer(startMarker); startMarker = null; }
  byId("startLat").value = ""; byId("startLon").value = "";
  byId("startStep").classList.remove("completed");
  lockStep("planStep");
}

function resetResult() {
  clearResultMap();
  state.result = null;
  byId("resultsContent").hidden = true;
  byId("progressPanel").hidden = true;
  byId("results").hidden = true;
  byId("results").classList.remove("has-plan");
  deactivateMobileResultLayout();
}

function beginStartSelection() {
  if (!dataReady) return;
  selectingStart = true;
  map.getContainer().classList.add("selecting-start");
  byId("startMapButton").classList.add("active");
  byId("startMapButton").textContent = "Kliknij teraz wybrane miejsce na mapie…";
  setMobilePlanningPanel(true);
  if (mobileLayout.matches) {
    window.setTimeout(() => {
      map.invalidateSize({ pan: false });
      const networkId = byId("preparedNetwork").value;
      if (networkId) fitPreparedBounds(networkId);
      else previewData();
    }, 230);
  }
}

function stopStartSelection() {
  selectingStart = false;
  map.getContainer().classList.remove("selecting-start");
  const button = byId("startMapButton");
  button.classList.remove("active");
  button.textContent = startReady ? "Zmień punkt na mapie" : "Wybierz punkt na mapie";
}

byId("startMapButton").addEventListener("click", () => {
  if (selectingStart) stopStartSelection();
  else beginStartSelection();
});

map.on("click", event => {
  if (selectingStart) setStart(event.latlng.lat, event.latlng.lng);
});
["startLat", "startLon"].forEach(id => byId(id).addEventListener("change", () => {
  const latValue = byId("startLat").value.trim(), lonValue = byId("startLon").value.trim();
  if (!latValue || !lonValue) return;
  const lat = Number(latValue), lon = Number(lonValue);
  if (Number.isFinite(lat) && Number.isFinite(lon)) setStart(lat, lon);
}));

function propertyNames(collection) {
  const names = new Set();
  (collection.features || []).slice(0, 100).forEach(feature => Object.keys(feature.properties || {}).forEach(name => names.add(name)));
  return [...names].sort((a, b) => a.localeCompare(b));
}

function fillSelect(id, properties, emptyLabel, guesses = []) {
  const select = byId(id);
  select.innerHTML = `<option value="">${emptyLabel}</option>`;
  properties.forEach(name => select.add(new Option(name, name)));
  const normalized = value => value.toLowerCase().replace(/[_ -]/g, "");
  const guess = properties.find(name => guesses.some(item => normalized(name) === normalized(item)));
  if (guess) select.value = guess;
}

function updateEdgeTimeControls() {
  const edgeTime = byId("edgeTime");
  const walkingSpeed = byId("walkingSpeed");
  const edgeTimeUnit = byId("edgeTimeUnit");
  if (!edgeTime || !walkingSpeed || !edgeTimeUnit) return;
  const calculateFromLength = !edgeTime.value;
  walkingSpeed.disabled = !calculateFromLength;
  edgeTimeUnit.disabled = calculateFromLength;
  walkingSpeed.closest("label")?.classList.toggle("is-disabled", !calculateFromLength);
  edgeTimeUnit.closest("label")?.classList.toggle("is-disabled", calculateFromLength);
}

byId("edgeTime").addEventListener("change", updateEdgeTimeControls);
updateEdgeTimeControls();

function updateFieldSelectors(type, collection) {
  const properties = propertyNames(collection);
  if (type === "edges") {
    fillSelect("edgeTime", properties, "Oblicz z długości", ["walk_time", "impedance_", "czas", "time"]);
    updateEdgeTimeControls();
  } else {
    fillSelect("attractionWeight", properties, "Wybierz pole", ["weight", "views", "waga", "rating"]);
    fillSelect("attractionDuration", properties, "Wybierz pole", ["time", "duration", "czas", "visit_time"]);
    fillSelect("attractionName", properties, "— bez nazwy —", ["polish_name", "name", "nazwa"]);
    fillSelect("attractionImage", properties, "— bez zdjęcia —", ["image", "image_url", "photo", "photo_url", "zdjecie"]);
    fillSelect("attractionDescription", properties, "— bez opisu —", ["description", "description_pl", "opis"]);
  }
}

function previewData() { drawPreview(state.edges, state.attractions); }

function drawPreview(edges, attractions) {
  const bounds = collectionBounds(edges, attractions);
  if (bounds) map.fitBounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]], { padding: [25, 25] });
}

function fitPreparedBounds(networkId) {
  const bounds = state.networkMetadata[networkId]?.bounds;
  if (!Array.isArray(bounds) || bounds.length !== 4) return;
  map.fitBounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]], { padding: [25, 25] });
}


function collectionBounds(...collections) {
  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  const stack = collections.flatMap(collection => (collection?.features || []).map(feature => feature?.geometry?.coordinates));
  while (stack.length) {
    const value = stack.pop();
    if (!Array.isArray(value)) continue;
    if (value.length >= 2 && Number.isFinite(Number(value[0])) && Number.isFinite(Number(value[1]))) {
      const lon = Number(value[0]), lat = Number(value[1]);
      minLon = Math.min(minLon, lon); minLat = Math.min(minLat, lat);
      maxLon = Math.max(maxLon, lon); maxLat = Math.max(maxLat, lat);
    } else {
      value.forEach(item => stack.push(item));
    }
  }
  return Number.isFinite(minLon) ? [minLon, minLat, maxLon, maxLat] : null;
}

async function readFile(file, type) {
  const collection = JSON.parse(await file.text());
  if (collection.type !== "FeatureCollection" || !Array.isArray(collection.features)) throw new Error("Plik nie jest kolekcją GeoJSON FeatureCollection.");
  state[type] = collection;
  state.activeNetwork = null;
  byId("preparedNetwork").value = "";
  updatePreparedInfo();
  const status = byId(type === "edges" ? "edgesStatus" : "attractionsStatus");
  status.textContent = `${file.name} · ${collection.features.length} obiektów`;
  status.closest("label").classList.add("ready");
  updateFieldSelectors(type, collection);
  previewData();
}

byId("edgesFile").addEventListener("change", event => handleFile(event, "edges"));
byId("attractionsFile").addEventListener("change", event => handleFile(event, "attractions"));
async function handleFile(event, type) {
  try { if (event.target.files[0]) await readFile(event.target.files[0], type); hideError(); }
  catch (error) { showError(error.message); }
}

function numberValue(id) { return Number(byId(id).value); }
function commonOptions() {
  return {
    days: numberValue("days"), max_hours_per_day: numberValue("maxHours"),
    must_see_per_day: numberValue("mustSee"), nearby_distance_m: numberValue("nearbyDistance"),
    topology_tolerance_m: numberValue("topologyTolerance"), snap_distance_m: numberValue("snapDistance"),
    start_snap_distance_m: 5000, walking_speed_mps: numberValue("walkingSpeed")
  };
}

function fieldMapping() {
  return {
    edge_time: byId("edgeTime").value,
    edge_time_unit: byId("edgeTimeUnit").value,
    attraction_weight: byId("attractionWeight").value,
    attraction_duration: byId("attractionDuration").value,
    attraction_duration_unit: byId("attractionDurationUnit").value,
    attraction_name: byId("attractionName").value,
    attraction_image: byId("attractionImage").value,
    attraction_description: byId("attractionDescription").value
  };
}

function requireRawData() {
  if (!state.edges || !state.attractions) throw new Error("Dodaj sieć pieszą i warstwę atrakcji.");
  if (!byId("attractionWeight").value || !byId("attractionDuration").value) throw new Error("Wskaż pola wagi i czasu zwiedzania atrakcji.");
}

function payload() {
  const lat = numberValue("startLat"), lon = numberValue("startLon");
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) throw new Error("Ustaw punkt startowy na mapie.");
  const reference = networkReference(byId("preparedNetwork").value);
  if (reference?.source === "public") {
    return {
      network_id: reference.id,
      start: { lat, lon },
      options: commonOptions(),
      attraction_edits: state.publicAttractionEdits || []
    };
  }
  throw new Error("Wybierz przygotowany zestaw danych.");
}

byId("prepareButton").addEventListener("click", async () => {
  hideError();
  byId("prepareMessage").hidden = true;
  byId("prepareMessage").className = "message success";
  try {
    requireRawData();
    const name = byId("networkName").value.trim();
    if (!name) throw new Error("Podaj nazwę przygotowanego zestawu.");
    if (!localAdminEnabled) throw new Error("Przygotowanie zestawów jest dostępne tylko lokalnie dla administratora.");
    setPreparing(true);
    byId("prepareMessage").textContent = "Przygotowuję graf i macierz kosztów. To może potrwać kilka minut.";
    byId("prepareMessage").hidden = false;
    const response = await fetch("/api/admin/networks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        edges: state.edges,
        attractions: state.attractions,
        fields: fieldMapping(),
        options: commonOptions()
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(apiErrorMessage(data, "Nie udało się przygotować zestawu."));
    const metadata = data.metadata;
    await loadPreparedNetworks(`${PUBLIC_PREFIX}${metadata.id}`);
    const preparationMessage = byId("prepareMessage");
    preparationMessage.textContent = "";
    const preparationSummary = document.createElement("div");
    preparationSummary.textContent = `Przygotowano „${name}”. Plik data/prepared/${metadata.id}.json jest gotowy do publikacji w Vercel Blob.`;
    preparationMessage.appendChild(preparationSummary);
    byId("prepareMessage").hidden = false;
    resetResult();
  } catch (error) {
    showProgressError(error.message);
    byId("prepareMessage").className = "message error";
    byId("prepareMessage").textContent = error.message;
    byId("prepareMessage").hidden = false;
  }
  finally { setPreparing(false); }
});

byId("planButton").addEventListener("click", async () => {
  hideError();
  try {
    const body = payload();
    const serialized = JSON.stringify(body);
    setRouteBusy(true);
    showProgress(10, "Serwer oblicza trasę w ramach bieżącego żądania.");
    const response = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: serialized
    });
    const data = await response.json();
    if (!response.ok) throw new Error(apiErrorMessage(data, "Nie udało się wyznaczyć trasy."));
    state.result = data;
    activateMobileResultLayout();
    renderResult(data);
    setMobileResultView("split");
  } catch (error) { showProgressError(error.message); showError(error.message); }
  finally { setRouteBusy(false); }
});

function showProgress(value, message, eyebrow = "Wyznaczanie trasy", title = "Układam najlepsze trasy…") {
  byId("results").classList.remove("has-plan");
  byId("results").hidden = false;
  byId("resultsContent").hidden = true;
  byId("progressPanel").hidden = false;
  byId("progressEyebrow").textContent = eyebrow;
  byId("progressTitle").textContent = value < 100 ? title : "Gotowe";
  byId("progressPercent").textContent = `${value}%`;
  byId("progressBar").style.width = `${value}%`;
  byId("progressMessage").textContent = message;
}

function showProgressError(message) {
  byId("progressTitle").textContent = "Nie udało się przygotować planu";
  byId("progressMessage").textContent = message;
}

function showError(message) { byId("formError").textContent = message; byId("formError").hidden = false; }
function hideError() { byId("formError").hidden = true; }
function setPreparing(active) {
  byId("prepareButton").disabled = active;
  byId("prepareButton").textContent = active ? "Przygotowuję zestaw…" : "Przygotuj zestaw";
}
function setRouteBusy(active) {
  byId("planButton").disabled = active;
  byId("planButton").querySelector("span").textContent = active ? "Wyznaczam plan…" : "Wyznacz plan";
}

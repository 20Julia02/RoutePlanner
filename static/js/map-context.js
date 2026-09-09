const STATE_KEY = Symbol.for("wanderplan.state");
const MAP_KEY = Symbol.for("wanderplan.map");

export const state = globalThis[STATE_KEY] ??= {
  edges: null,
  attractions: null,
  result: null,
  resultLayers: [],
  dayLayers: {},
  networkMetadata: {},
  activeNetwork: null,
  localFields: null,
  localPreparationOptions: null
};

export const DAY_COLORS = [
  "#f26a3d", "#4269e1", "#188765", "#a847b7", "#d59d16", "#db3d77", "#59636f"
];

const MAP_PALETTE = {
  park: "#bcdcb8",
  landcover_wood: "#c7dec1",
  building: "#f5edcf",
  water: "#c7e2f0"
};

const POLYGON_FILTER = [
  "match",
  ["geometry-type"],
  ["MultiPolygon", "Polygon"],
  true,
  false
];

function applyMapPalette(vectorMap) {
  Object.entries(MAP_PALETTE).forEach(([layerId, color]) => {
    if (vectorMap.getLayer(layerId)) vectorMap.setPaintProperty(layerId, "fill-color", color);
  });

  const styleLayers = vectorMap.getStyle().layers;
  const landcoverReference = styleLayers.find(layer => layer.id === "landcover_wood");
  const landuseReference = styleLayers.find(layer => layer.id === "landuse_residential");

  if (landcoverReference && !vectorMap.getLayer("wanderplan-vegetation")) {
    vectorMap.addLayer({
      id: "wanderplan-vegetation",
      type: "fill",
      source: landcoverReference.source,
      "source-layer": landcoverReference["source-layer"],
      minzoom: landcoverReference.minzoom,
      filter: [
        "all",
        POLYGON_FILTER,
        ["match", ["get", "class"], ["grass", "farmland", "wetland"], true, false]
      ],
      paint: {
        "fill-color": [
          "match",
          ["get", "subclass"],
          ["garden", "flowerbed", "allotments", "orchard", "plant_nursery"], "#bfdda8",
          ["park", "recreation_ground", "village_green", "golf_course"], "#c5dfb4",
          [
            "match",
            ["get", "class"],
            "farmland", "#e0e8cb",
            "wetland", "#d1e4d4",
            "#cee4c3"
          ]
        ],
        "fill-opacity": 0.9
      }
    }, "landcover_wood");
  }

  if (landuseReference && !vectorMap.getLayer("wanderplan-green-landuse")) {
    vectorMap.addLayer({
      id: "wanderplan-green-landuse",
      type: "fill",
      source: landuseReference.source,
      "source-layer": landuseReference["source-layer"],
      minzoom: landuseReference.minzoom,
      filter: [
        "all",
        POLYGON_FILTER,
        [
          "match",
          ["get", "class"],
          ["cemetery", "pitch", "playground", "stadium", "theme_park", "zoo"],
          true,
          false
        ]
      ],
      paint: {
        "fill-color": [
          "match",
          ["get", "class"],
          ["pitch", "stadium"], "#c4dfb0",
          ["zoo", "theme_park"], "#cce3bc",
          "#d3e5c8"
        ],
        "fill-opacity": 0.88
      }
    }, "landcover_wood");
  }
}

function createMap() {
  const instance = L.map("map", { zoomControl: false }).setView([41.9028, 12.4964], 13);
  L.control.zoom({ position: "bottomright" }).addTo(instance);
  const baseLayer = L.maplibreGL({
    style: "https://tiles.openfreemap.org/styles/positron"
  }).addTo(instance);
  const vectorMap = baseLayer.getMaplibreMap();
  if (vectorMap.isStyleLoaded()) applyMapPalette(vectorMap);
  else vectorMap.once("load", () => applyMapPalette(vectorMap));
  return instance;
}

export const map = globalThis[MAP_KEY] ??= createMap();

export function clearMapLayers(layers) {
  layers.forEach(layer => map.removeLayer(layer));
  layers.length = 0;
}

export function mapFitOptions() {
  return window.innerWidth > 900
    ? { paddingTopLeft: [30, 30], paddingBottomRight: [410, 30] }
    : { padding: [20, 20] };
}

import * as maplibregl from "https://unpkg.com/maplibre-gl@6.12.0/dist/maplibre-gl.mjs";

(() => {
  "use strict";

  const message = document.getElementById("message");
  const managed = new Map();
  const sourceMetadata = new Map();
  const empty = {type: "FeatureCollection", features: []};
  let bridge = null;
  let map = null;

  function showMessage(text, isError = false) {
    message.textContent = text || "";
    message.classList.toggle("error", Boolean(isError));
  }

  function reportError(error) {
    const text = String(error && error.stack ? error.stack : error);
    console.error(text);
    showMessage(`Kaartfout: ${text}`, true);
    if (bridge && bridge.reportError) {
      bridge.reportError(text);
    }
  }

  window.addEventListener("error", event => {
    reportError(event.error || event.message || "Onbekende JavaScript-fout");
  });
  window.addEventListener("unhandledrejection", event => {
    reportError(event.reason || "Onbehandelde JavaScript-belofte");
  });

  const token = (dataset, layer) => {
    const raw = `${dataset}\u001f${layer}`;
    let hash = 2166136261;
    for (let index = 0; index < raw.length; index++) {
      hash ^= raw.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return `nen-${(hash >>> 0).toString(16)}`;
  };

  const layerIds = id => [
    `${id}-fill`,
    `${id}-outline`,
    `${id}-line-casing`,
    `${id}-dash-casing`,
    `${id}-line`,
    `${id}-dash`,
    `${id}-point`
  ];

  const featureColor = [
    "to-color",
    ["coalesce", ["get", "__color"], "#505050"]
  ];
  const featureWidth = [
    "to-number",
    ["coalesce", ["get", "__width"], 1]
  ];
  const pointSize = [
    "to-number",
    ["coalesce", ["get", "__point_size"], 5]
  ];
  const renderedLineWidth = [
    "interpolate",
    ["linear"],
    ["zoom"],
    7,
    ["max", 1.25, featureWidth],
    17,
    ["*", ["max", 1.25, featureWidth], 2.5]
  ];
  const casingLineWidth = [
    "interpolate",
    ["linear"],
    ["zoom"],
    7,
    ["+", ["max", 1.25, featureWidth], 2],
    17,
    ["+", ["*", ["max", 1.25, featureWidth], 2.5], 2]
  ];
  const selectionLineWidth = [
    "interpolate",
    ["linear"],
    ["zoom"],
    7,
    5,
    17,
    8
  ];
  const selectionCasingWidth = [
    "interpolate",
    ["linear"],
    ["zoom"],
    7,
    8,
    17,
    11
  ];
  const selectionLayerIds = [
    "selection-fill",
    "selection-line-casing",
    "selection-line",
    "selection-point"
  ];

  function moveSelectionToTop() {
    for (const layerId of selectionLayerIds) {
      if (map.getLayer(layerId)) {
        map.moveLayer(layerId);
      }
    }
  }

  function addStyleLayers(id) {
    map.addLayer({
      id: `${id}-fill`,
      type: "fill",
      source: id,
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: {
        "fill-color": featureColor,
        "fill-opacity": 0.25
      }
    });
    map.addLayer({
      id: `${id}-outline`,
      type: "line",
      source: id,
      filter: ["==", ["geometry-type"], "Polygon"],
      paint: {
        "line-color": featureColor,
        "line-width": 2
      }
    });
    map.addLayer({
      id: `${id}-line-casing`,
      type: "line",
      source: id,
      filter: [
        "all",
        ["==", ["geometry-type"], "LineString"],
        ["!=", ["get", "__dashed"], true]
      ],
      layout: {
        "line-cap": "round",
        "line-join": "round"
      },
      paint: {
        "line-color": "rgba(255,255,255,0.78)",
        "line-width": casingLineWidth
      }
    });
    map.addLayer({
      id: `${id}-dash-casing`,
      type: "line",
      source: id,
      filter: [
        "all",
        ["==", ["geometry-type"], "LineString"],
        ["==", ["get", "__dashed"], true]
      ],
      layout: {
        "line-cap": "round",
        "line-join": "round"
      },
      paint: {
        "line-color": "rgba(255,255,255,0.78)",
        "line-width": casingLineWidth,
        "line-dasharray": [3, 2]
      }
    });
    map.addLayer({
      id: `${id}-line`,
      type: "line",
      source: id,
      filter: [
        "all",
        ["==", ["geometry-type"], "LineString"],
        ["!=", ["get", "__dashed"], true]
      ],
      layout: {
        "line-cap": "round",
        "line-join": "round"
      },
      paint: {
        "line-color": featureColor,
        "line-width": renderedLineWidth
      }
    });
    map.addLayer({
      id: `${id}-dash`,
      type: "line",
      source: id,
      filter: [
        "all",
        ["==", ["geometry-type"], "LineString"],
        ["==", ["get", "__dashed"], true]
      ],
      layout: {
        "line-cap": "round",
        "line-join": "round"
      },
      paint: {
        "line-color": featureColor,
        "line-width": renderedLineWidth,
        "line-dasharray": [3, 2]
      }
    });
    map.addLayer({
      id: `${id}-point`,
      type: "circle",
      source: id,
      filter: ["==", ["geometry-type"], "Point"],
      paint: {
        "circle-color": featureColor,
        "circle-radius": pointSize,
        "circle-stroke-color": "#FFFFFF",
        "circle-stroke-width": 1
      }
    });
    managed.set(id, layerIds(id));
    moveSelectionToTop();
  }

  function renderedLayers() {
    return Array.from(managed.values())
      .flat()
      .filter(id => map.getLayer(id));
  }

  function installMapApi() {
    window.nenMap = {
      setLayerUrl(dataset, layer, url) {
        const id = token(dataset, layer);
        sourceMetadata.set(id, {dataset, layer});
        const source = map.getSource(id);
        if (source) {
          source.setData(url);
          return;
        }
        map.addSource(id, {
          type: "geojson",
          data: url
        });
        addStyleLayers(id);
      },

      removeLayer(dataset, layer) {
        const id = token(dataset, layer);
        for (const layerId of (managed.get(id) || []).slice().reverse()) {
          if (map.getLayer(layerId)) {
            map.removeLayer(layerId);
          }
        }
        if (map.getSource(id)) {
          map.removeSource(id);
        }
        managed.delete(id);
        sourceMetadata.delete(id);
      },

      setLayerVisibility(dataset, layer, visible) {
        const id = token(dataset, layer);
        for (const layerId of managed.get(id) || []) {
          if (map.getLayer(layerId)) {
            map.setLayoutProperty(
              layerId,
              "visibility",
              visible ? "visible" : "none"
            );
          }
        }
      },

      setSelection(feature) {
        if (!map.getSource("selection")) {
          map.addSource("selection", {
            type: "geojson",
            data: empty
          });
          map.addLayer({
            id: "selection-fill",
            type: "fill",
            source: "selection",
            filter: ["==", ["geometry-type"], "Polygon"],
            paint: {
              "fill-color": "#A70707",
              "fill-opacity": 0.25
            }
          });
          map.addLayer({
            id: "selection-line-casing",
            type: "line",
            source: "selection",
            filter: ["==", ["geometry-type"], "LineString"],
            layout: {
              "line-cap": "butt",
              "line-join": "round"
            },
            paint: {
              "line-color": "rgba(255,255,255,0.92)",
              "line-width": selectionCasingWidth
            }
          });
          map.addLayer({
            id: "selection-line",
            type: "line",
            source: "selection",
            filter: ["==", ["geometry-type"], "LineString"],
            layout: {
              "line-cap": "butt",
              "line-join": "round"
            },
            paint: {
              "line-color": "#A70707",
              "line-width": selectionLineWidth
            }
          });
          map.addLayer({
            id: "selection-point",
            type: "circle",
            source: "selection",
            filter: ["==", ["geometry-type"], "Point"],
            paint: {
              "circle-color": "#A70707",
              "circle-radius": 9,
              "circle-stroke-color": "#FFFFFF",
              "circle-stroke-width": 2
            }
          });
        }
        map.getSource("selection").setData({
          type: "FeatureCollection",
          features: [feature]
        });
        moveSelectionToTop();
      },

      clearSelection() {
        const source = map.getSource("selection");
        if (source) {
          source.setData(empty);
        }
      },

      fitBounds(bounds) {
        if (!bounds || bounds.length !== 4) {
          return;
        }
        const [minX, minY, maxX, maxY] = bounds;
        if (![minX, minY, maxX, maxY].every(Number.isFinite)) {
          throw new Error("Ongeldige kaartgrenzen ontvangen.");
        }
        if (minX === maxX && minY === maxY) {
          map.easeTo({
            center: [minX, minY],
            zoom: 17,
            duration: 350
          });
          return;
        }
        map.fitBounds(
          [[minX, minY], [maxX, maxY]],
          {padding: 40, duration: 350, maxZoom: 17}
        );
      }
    };
  }

  function featureIdentity(feature) {
    const properties = feature.properties || {};
    return [
      String(properties.__dataset || ""),
      String(properties.__layer || ""),
      String(properties.__fid ?? "")
    ].join("\u001f");
  }

  function uniqueClickableFeatures(features) {
    const seen = new Set();
    const unique = [];
    for (const feature of features) {
      if (!feature.properties || !feature.properties.__dataset) {
        continue;
      }
      const identity = featureIdentity(feature);
      if (!seen.has(identity)) {
        seen.add(identity);
        unique.push(feature);
      }
    }
    return unique;
  }

  function createMap() {
    if (!maplibregl || !maplibregl.Map) {
      throw new Error("MapLibre kon niet worden geladen.");
    }
    map = new maplibregl.Map({
      container: "map",
      center: [5.2913, 52.1326],
      zoom: 7,
      attributionControl: false,
      style: {
        version: 8,
        sources: {
          osm: {
            type: "raster",
            tiles: [
              "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
              "https://b.tile.openstreetmap.org/{z}/{x}/{y}.png",
              "https://c.tile.openstreetmap.org/{z}/{x}/{y}.png"
            ],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors"
          }
        },
        layers: [
          {
            id: "base-background",
            type: "background",
            paint: {
              "background-color": "#EEF1F2"
            }
          },
          {
            id: "osm",
            type: "raster",
            source: "osm",
            paint: {
              "raster-opacity": 0.94,
              "raster-fade-duration": 150
            }
          }
        ]
      }
    });

    map.addControl(
      new maplibregl.NavigationControl(),
      "top-right"
    );
    map.addControl(
      new maplibregl.AttributionControl({compact: true}),
      "bottom-right"
    );

    map.on("click", event => {
      if (!bridge) {
        return;
      }
      const layers = renderedLayers();
      const candidates = layers.length
        ? uniqueClickableFeatures(
            map.queryRenderedFeatures(event.point, {layers})
          )
        : [];
      if (!candidates.length) {
        showMessage("");
        if (bridge.clearFeatureSelection) {
          bridge.clearFeatureSelection();
        }
        return;
      }

      const identities = candidates.map(feature => ({
        dataset: String(feature.properties.__dataset),
        layer: String(feature.properties.__layer),
        fid: Number(feature.properties.__fid)
      }));
      if (bridge.selectOverlappingFeatures) {
        bridge.selectOverlappingFeatures(JSON.stringify(identities));
      } else {
        const first = identities[0];
        bridge.selectFeature(first.dataset, first.layer, first.fid);
      }
      showMessage(
        candidates.length > 1
          ? `Overlappende resultaten: 1 van ${candidates.length}. ` +
            "Gebruik Vorige en Volgende in het featurepaneel."
          : ""
      );
    });

    map.on("mousemove", event => {
      const layers = renderedLayers();
      const hit = layers.length
        ? map.queryRenderedFeatures(event.point, {layers}).length > 0
        : false;
      map.getCanvas().style.cursor = hit ? "pointer" : "";
    });

    map.on("error", event => {
      const sourceId = event.sourceId || "";
      if (sourceMetadata.has(sourceId)) {
        const metadata = sourceMetadata.get(sourceId);
        reportError(
          `Laag ${metadata.dataset} / ${metadata.layer} kon niet worden verwerkt: ` +
          `${event.error ? event.error.message : "onbekende bronfout"}`
        );
      } else if (!sourceId) {
        reportError(
          event.error ? event.error.message : "Onbekende MapLibre-fout"
        );
      }
    });

    map.on("load", () => {
      installMapApi();
      showMessage("");
      bridge.ready();
    });

    const observer = new ResizeObserver(() => map.resize());
    observer.observe(document.getElementById("map"));
  }

  function connectBridge() {
    return new Promise((resolve, reject) => {
      if (
        typeof QWebChannel === "undefined" ||
        typeof qt === "undefined" ||
        !qt.webChannelTransport
      ) {
        reject(new Error("QWebChannel is niet beschikbaar."));
        return;
      }
      new QWebChannel(qt.webChannelTransport, channel => {
        bridge = channel.objects.bridge;
        if (!bridge) {
          reject(new Error("De Python-kaartbridge ontbreekt."));
          return;
        }
        resolve();
      });
    });
  }

  async function bootstrap() {
    showMessage("Kaart initialiseren…");
    await connectBridge();
    createMap();
  }

  bootstrap().catch(reportError);
})();

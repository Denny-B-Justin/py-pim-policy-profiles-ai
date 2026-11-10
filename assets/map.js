/*
 * Global map – port of `web/app/map/MapClient.tsx` (Leaflet + Natural Earth country polygons).
 * Loaded on demand by pages/map_page.py (this file is excluded from Dash's auto-include via `assets_ignore`).
 * Exposes window.pimMap.init(elementId, countries), `countries` being the camelCase list built in Python:
 *   [{iso3, name, records, engDocs, oriDocs, originalLanguage, status: "english" | "nonEnglish"}]
 */
(function () {
  "use strict";

  // Escape any DB-sourced text before interpolating into Leaflet popup/tooltip HTML.
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // PIM-PAM brand palette — encode by the country's ORIGINAL policy language.
  var COLORS = { english: "#374291", nonEnglish: "#f26b23" };
  var STATUS_LABEL = { english: "Original language: English", nonEnglish: "Original language: not English" };
  var NO_DATA_FILL = "#e8edf2";
  var DARK_FILL = { english: true };   // white country labels on this fill

  var SMALL_LABEL_ISO = toSet(["XKX", "MNE", "MKD", "ALB", "BIH", "SRB", "ARM", "AZE", "GEO", "HRV", "MDA", "LUX", "IRL", "LTU"]);
  var CLUSTER_ISO = toSet(["XKX", "MNE", "MKD", "ALB", "BIH", "SRB", "HRV", "ARM", "AZE", "GEO", "MDA"]);
  var CLUSTER_LABEL_MINZOOM = 5;

  var LABEL_OVERRIDE = {
    FRA: [46.6, 2.2], USA: [39.5, -98.5], RUS: [62.0, 95.0], NOR: [62.0, 10.0],
    BRA: [-10.3, -52.0], ZAF: [-29.0, 25.0], CAN: [56.0, -96.0], IDN: [-2.5, 118.0],
    HRV: [45.2, 16.0], IRL: [53.3, -8.1], LTU: [55.3, 23.9], POL: [52.0, 19.5],
    AUS: [-25.0, 134.0], MDG: [-19.0, 46.7], MOZ: [-18.0, 35.5]
  };

  function toSet(a) { var o = {}; a.forEach(function (k) { o[k] = true; }); return o; }

  function normalize(s) {
    return String(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
      .replace(/&/g, "and").replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
  }

  function makeResolveIso(countries) {
    var validIso = toSet(countries.map(function (c) { return c.iso3; }));
    var isoByLcName = {};
    countries.forEach(function (c) { isoByLcName[normalize(c.name)] = c.iso3; });
    return function resolveIso(p) {
      p = p || {};
      var candidates = [p.ISO_A3, p.ISO3, p.iso_a3, p.adm0_a3, p.ADM0_A3, p.ISO_A3_EH,
        p["ISO3166-1-Alpha-3"], p.A3, p.sov_a3, p.SOV_A3, p.wb_a3, p.WB_A3, p.su_a3, p.SU_A3, p.brk_a3, p.BRK_A3];
      for (var i = 0; i < candidates.length; i++) {
        var c = candidates[i];
        if (c && typeof c === "string" && c !== "-99" && c.length === 3) {
          var up = c.toUpperCase();
          if (validIso[up]) return up;
          if (up === "KOS" && validIso.XKX) return "XKX";
        }
      }
      var nameCandidates = [p.ADMIN, p.NAME, p.name, p.NAME_EN, p.NAME_LONG, p.FORMAL_EN, p.sovereignt];
      for (var j = 0; j < nameCandidates.length; j++) {
        var n = nameCandidates[j];
        if (!n) continue;
        var key = normalize(n);
        if (isoByLcName[key]) return isoByLcName[key];
        if (key.indexOf("kosovo") !== -1 && validIso.XKX) return "XKX";
      }
      return null;
    };
  }

  var current = null;   // the live Leaflet map (removed on re-init, like the React effect cleanup)

  function hideLoading() {
    var el = document.getElementById("map-loading");
    if (el) el.style.display = "none";
  }

  function init(elId, countries) {
    var L = window.L;
    var el = document.getElementById(elId);
    if (!L || !el) return;
    if (current) { try { current.remove(); } catch (e) { /* element already gone */ } current = null; }
    if (el._leaflet_id) return;

    var geoUrls = [
      (window.pim ? window.pim.basePath() : "/") + "assets/countries.geojson",
      "https://cdn.jsdelivr.net/gh/nvkelso/natural-earth-vector@master/geojson/ne_110m_admin_0_countries.geojson"
    ];
    var resolveIso = makeResolveIso(countries);
    var dataByIso = {};
    countries.forEach(function (c) { dataByIso[c.iso3] = c; });

    var map = L.map(el, { worldCopyJump: true, minZoom: 2, maxZoom: 7, attributionControl: true }).setView([22, 20], 2);
    current = map;

    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
      subdomains: "abcd", maxZoom: 19
    }).addTo(map);

    var clusterLabels = [];
    var wbHint = null;

    function updateClusterLabels() {
      var reveal = map.getZoom() >= CLUSTER_LABEL_MINZOOM;
      clusterLabels.forEach(function (o) {
        var on = map.hasLayer(o.lbl);
        if (reveal && !on) o.lbl.addTo(map);
        else if (!reveal && on) map.removeLayer(o.lbl);
      });
      if (!clusterLabels.length) return;
      if (!reveal && !wbHint) {
        wbHint = L.marker([43.4, 20.2], {
          interactive: false, keyboard: false,
          icon: L.divIcon({
            className: "wb-hint",
            html: "<span>Balkans &amp; Caucasus<br><small>zoom in for country names</small></span>",
            iconSize: [150, 34], iconAnchor: [75, 17]
          })
        }).addTo(map);
      } else if (reveal && wbHint) {
        map.removeLayer(wbHint);
        wbHint = null;
      }
    }

    function labelAnchor(lyr) {
      var f = lyr.feature;
      var iso = resolveIso((f && f.properties) || {});
      if (iso && LABEL_OVERRIDE[iso]) return L.latLng(LABEL_OVERRIDE[iso][0], LABEL_OVERRIDE[iso][1]);
      try {
        if (f && f.geometry && f.geometry.type === "MultiPolygon") {
          var best = null, bestArea = 0;
          f.geometry.coordinates.forEach(function (poly) {
            var ring = poly[0];
            var minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
            ring.forEach(function (pt) {
              var x = pt[0], y = pt[1];
              if (x < minx) minx = x; if (x > maxx) maxx = x;
              if (y < miny) miny = y; if (y > maxy) maxy = y;
            });
            var area = (maxx - minx) * (maxy - miny);
            if (area > bestArea) { bestArea = area; best = [(miny + maxy) / 2, (minx + maxx) / 2]; }
          });
          if (best) return L.latLng(best[0], best[1]);
        }
      } catch (e) { /* fall through to bounds centre */ }
      return lyr.getBounds().getCenter();
    }

    function addGeo(geo) {
      var layer = L.geoJSON(geo, {
        style: function (feature) {
          var d = feature ? dataByIso[resolveIso(feature.properties || {}) || ""] : null;
          if (!d) return { fillColor: NO_DATA_FILL, color: "#c5cdd6", weight: 0.35, fillOpacity: 0.55 };
          return { fillColor: COLORS[d.status], color: "#fff", weight: 1.1, fillOpacity: 0.95 };
        },
        onEachFeature: function (feature, lyr) {
          var iso = resolveIso(feature.properties || {});
          var d = iso ? dataByIso[iso] : null;
          if (!d || !iso) { lyr.options.interactive = false; return; }
          lyr.bindTooltip(esc(d.name) + " — " + esc(STATUS_LABEL[d.status]),
            { className: "country-tip", sticky: true, direction: "auto" });
          var lang = d.originalLanguage !== "English" && d.originalLanguage !== "Non-English"
            ? "<div><b>Language:</b> " + esc(d.originalLanguage) + "</div>" : "";
          lyr.bindPopup(
            '<div style="font-family:inherit;min-width:230px">' +
            '<h3 style="margin:0 0 6px 0;font-size:14px;color:#374291">' + esc(d.name) + "</h3>" +
            '<span style="display:inline-block;padding:2px 9px;border-radius:11px;font-size:11px;font-weight:600;background:' +
              COLORS[d.status] + ';color:#fff">' + esc(STATUS_LABEL[d.status]) + "</span>" +
            '<div style="font-size:11.5px;color:#1f2d3d;margin-top:8px;line-height:1.6">' + lang +
            "<div><b>" + d.records + "</b> policy record" + (d.records === 1 ? "" : "s") + "</div>" +
            "<div><b>" + d.engDocs + "</b> English document" + (d.engDocs === 1 ? "" : "s") +
              " · <b>" + d.oriDocs + "</b> original-language</div></div>" +
            '<a href="?page=country&iso3=' + encodeURIComponent(iso) +
              '" style="display:inline-block;margin-top:8px;font-size:12px;color:#374291;font-weight:600">View country profile →</a>' +
            "</div>");
          lyr.on({
            mouseover: function (e) { e.target.setStyle({ weight: 2.4, color: "#0c2a47", fillOpacity: 1 }); },
            mouseout: function (e) { layer.resetStyle(e.target); }
          });

          var classes = ["country-name-label", DARK_FILL[d.status] ? "on-dark" : "", SMALL_LABEL_ISO[iso] ? "small" : ""]
            .filter(Boolean).join(" ");
          var lbl = L.tooltip({ permanent: true, direction: "center", className: classes, interactive: false, opacity: 1 })
            .setLatLng(labelAnchor(lyr)).setContent(esc(d.name));

          if (CLUSTER_ISO[iso]) {
            clusterLabels.push({ lbl: lbl, iso: iso });
            if (map.getZoom() >= CLUSTER_LABEL_MINZOOM) lbl.addTo(map);
          } else {
            lbl.addTo(map);
          }
        }
      }).addTo(map);
      updateClusterLabels();
      map.on("zoomend", updateClusterLabels);
    }

    // Try the bundled polygons first, then the CDN copy (same order as the original).
    (function load(i) {
      if (i >= geoUrls.length) { hideLoading(); return; }
      fetch(geoUrls[i])
        .then(function (res) { if (!res.ok) throw new Error("HTTP " + res.status); return res.json(); })
        .then(function (geo) { if (current !== map) return; addGeo(geo); hideLoading(); })
        .catch(function () { load(i + 1); });
    })(0);
  }

  window.pimMap = { init: init };
})();

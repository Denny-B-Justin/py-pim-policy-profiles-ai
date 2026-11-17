"""Global map – port of `web/app/map/{page,MapView,MapClient}.tsx` + `modules/map/queries.ts`.

Leaflet (vendored in assets/vendor/leaflet) draws the Natural-Earth country polygons coloured by the country's
ORIGINAL policy language; `assets/map.js` is a line-by-line port of MapClient.tsx.  Python supplies the data,
the title card, the legend and the “Loading map…” pill.
"""

from __future__ import annotations

from dash import Input, Output, dcc, html

from pages.pages_constants import (
    MAP_TITLE as TITLE,
    MAP_COLORS as COLORS,
    MAP_NO_DATA_FILL as NO_DATA_FILL,
    MAP_LEGEND as LEGEND,
)
from utils import queries


def _countries() -> list[dict]:
    return [{"iso3": c["iso3"], "name": c["name"], "records": c["records"], "engDocs": c["eng_docs"],
             "oriDocs": c["ori_docs"], "originalLanguage": c["original_language"], "status": c["status"]}
            for c in queries.get_map_countries()]


def layout(params=None):
    countries = _countries()
    return html.Div(
        html.Div([
            html.Div(id="map-root", className="h-full w-full", style={"background": "#eef4f9"}),
            # Title
            html.Div([
                html.H1("PIM policy data by country", className="text-sm font-bold", style={"color": "#374291"}),
                html.P(f"{len(countries)} countries with public investment management policies. "
                       "Click a country for detail.", className="mt-0.5 text-xs text-[#5a6470]"),
            ], className="pointer-events-none absolute left-3 top-3 z-[1000] max-w-xs rounded-lg bg-white/95 px-4 py-3 "
                         "shadow-md ring-1 ring-black/5"),
            # Legend
            html.Div([
                html.Div("Document language", className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide "
                                                         "text-[#5a6470]"),
                html.Ul([html.Li([
                    html.Span(className="inline-block h-3 w-3 shrink-0 rounded-sm ring-1 ring-black/10",
                              style={"background": color}), label,
                ], className="flex items-center gap-2 text-xs text-[#1f2d3d]") for color, label in LEGEND],
                    className="space-y-1.5"),
            ], className="absolute bottom-6 left-3 z-[1000] rounded-lg bg-white/95 px-3.5 py-3 shadow-md ring-1 ring-black/5"),
            html.Div(html.Span("Loading map…", className="rounded-full bg-white/90 px-4 py-2 text-sm font-medium shadow",
                               style={"color": "#374291"}),
                     id="map-loading",
                     className="pointer-events-none absolute inset-0 z-[1000] flex items-center justify-center"),
            dcc.Store(id="map-data", data=countries),
            dcc.Store(id="map-noop"),
        ], className="relative h-full w-full", role="region", **{"aria-label": "Global map of countries with PIM policy data"}),
        className="relative w-full", style={"height": "calc(100dvh - 8rem)", "minHeight": "520px"},
    )


def register_callbacks(app):
    # Leaflet + map.js are vendored and loaded on demand, then the map is drawn into #map-root.
    app.clientside_callback(
        """function(data){
            if (!data || !window.pim) { return window.dash_clientside.no_update; }
            var P = window.pim, b = P.basePath();
            Promise.all([P.loadCss(b + "assets/vendor/leaflet/leaflet.css"),
                         P.loadScript(b + "assets/vendor/leaflet/leaflet.js")])
              .then(function(){ return P.loadScript(b + "assets/map.js"); })
              .then(function(){
                  (function go(n){
                      if (document.getElementById("map-root")) { window.pimMap.init("map-root", data); }
                      else if (n > 0) { setTimeout(function(){ go(n - 1); }, 50); }
                  })(20);
              })
              .catch(function(){
                  var l = document.getElementById("map-loading");
                  if (l) { l.textContent = "The map could not be loaded."; }
              });
            return window.dash_clientside.no_update;
        }""",
        Output("map-noop", "data"), Input("map-data", "data"),
    )

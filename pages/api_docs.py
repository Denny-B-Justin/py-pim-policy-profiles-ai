"""API reference – port of `web/app/api-docs/page.tsx` (Redoc over `/api/v1/openapi.json`)."""

from __future__ import annotations

from dash import Input, Output, dcc, html

TITLE = "API — PIM Policy Profiles AI"

_CODE = "rounded bg-foreground/10 px-1.5 py-0.5 text-xs"


def layout(params=None):
    return html.Div([
        html.Header([
            html.H1("Plug-in API for ePIM-CBA", className="text-3xl font-bold tracking-tight"),
            html.P("Authenticated REST API for embedding curated PIM data and AI analytics into national ePIM-CBA "
                   "deployments and partner systems. Each key is rate-limited per minute.",
                   className="mt-2 max-w-3xl text-muted-foreground"),
        ]),
        html.Section([
            html.P("Getting a key", className="font-medium text-foreground"),
            html.P("The platform operator issues partner API keys. To request one, email the operator with the "
                   "partner organisation name and the expected request volume.", className="text-muted-foreground"),
            html.P(["Authentication uses a single header on every request: ",
                    html.Code("X-API-Key: pim_v1_xxxxxxxx", className=_CODE),
                    ". Rate limits and remaining quota are returned in ",
                    html.Code("X-RateLimit-*", className=_CODE), " response headers."],
                   className="text-muted-foreground"),
        ], className="rounded-lg border bg-muted/30 p-4 text-sm space-y-2"),
        html.Div(id="redoc-container", className="rounded-lg border bg-white"),
        dcc.Store(id="redoc-trigger", data=1),
        dcc.Store(id="redoc-noop"),
    ], className="space-y-6")


def register_callbacks(app):
    # fires when the page (and the Redoc mount point) is rendered → loads the vendored bundle and mounts it
    app.clientside_callback(
        "function(_){ if (window.pim && window.pim.initRedoc) { window.pim.initRedoc(); } "
        "return window.dash_clientside.no_update; }",
        Output("redoc-noop", "data"), Input("redoc-trigger", "data"))

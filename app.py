"""
PIM Policy Profiles AI – Dash application (converted from the Next.js app).

Entry point for Posit Connect:   rsconnect deploy dash --entrypoint app:app .
Every page is addressed by query string:   /?page=home   /?page=country&iso3=LTU   …
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

import dash
from dash import Input, Output, dcc, html

import constants as C
from pages import load, load_all
from pages import error_page, not_found
from utils.layout import shell
from utils.routes import parse_search

logging.basicConfig(level=C.LOG_LEVEL,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pim")

app = dash.Dash(
    __name__,
    title=C.APP_NAME,
    update_title=None,
    suppress_callback_exceptions=True,
    # Leaflet / Redoc are heavy and only needed on one page each → loaded on demand (assets/app.js).
    assets_ignore=r"vendor/.*|map\.js",
    meta_tags=[
        {"name": "viewport", "content": "width=device-width, initial-scale=1"},
        {"name": "description", "content": C.APP_DESCRIPTION},
    ],
)
server = app.server

# Signed session cookie for local accounts (set PIM_SECRET_KEY so all Connect processes share it).
server.secret_key = C.SECRET_KEY or secrets.token_hex(32)
server.config.update(SESSION_COOKIE_NAME=C.SESSION_COOKIE_NAME, SESSION_COOKIE_HTTPONLY=True,
                     SESSION_COOKIE_SAMESITE=C.SESSION_COOKIE_SAMESITE,
                     PERMANENT_SESSION_LIFETIME=timedelta(days=C.SESSION_LIFETIME_DAYS), MAX_CONTENT_LENGTH=C.MAX_UPLOAD_BYTES + 1024 * 1024)
if not C.SECRET_KEY:
    log.warning("PIM_SECRET_KEY is not set – local sign-ins will not survive a restart / other processes.")

app.layout = shell


# ── router ────────────────────────────────────────────────────────────────────
@app.callback(Output("page-content", "children"), Output("nav-title", "data"), Input("url", "search"),
              Input("page-refresh", "data"))
def route(search, _refresh=None):
    params = parse_search(search)
    name = (params.get("page") or C.DEFAULT_PAGE).strip().lower()
    mod = load(name)
    if mod is None:
        return not_found.layout(params), C.APP_NAME
    try:
        body = mod.layout(params)
        title = mod.title(params) if hasattr(mod, "title") else getattr(mod, "TITLE", C.APP_NAME)   # per-record titles
        return body, title
    except Exception:  # noqa: BLE001 – error boundary (web/app/error.tsx)
        log.exception("page %s failed", name)
        return error_page.layout(params), C.APP_NAME


app.clientside_callback(
    "function(t){return window.dash_clientside.pim.navigated(t);}",
    Output("nav-noop", "children"), Input("nav-title", "data"),
)
app.clientside_callback(
    "function(d){return window.dash_clientside.pim.toast(d);}",
    Output("toast-noop", "children"), Input("toast", "data"), prevent_initial_call=True,
)

# ── page callbacks + Flask routes ─────────────────────────────────────────────
for _mod in load_all():
    if hasattr(_mod, "register_callbacks"):
        _mod.register_callbacks(app)

from utils import widgets  # noqa: E402  (shared widgets used by several pages)

widgets.register_recheck_callbacks(app)

from utils import api_routes  # noqa: E402  (JSON endpoints under /api, plus the cron routes)

api_routes.register(server)

from utils import ai_search  # noqa: E402

ai_search.start_startup_check()   # logs an 'AI Search access report' once per process (read-only checks)

if __name__ == "__main__":
    app.run(debug=C.DASH_DEBUG, host=C.HOST, port=C.PORT)

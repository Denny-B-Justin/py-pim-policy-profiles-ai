"""
One module per page.  Every module exposes

    TITLE                      browser tab title
    layout(params) -> Component  the page body (params = parsed query string)
    register_callbacks(app)    Dash callbacks for the page (optional)

`ROUTES` maps the `?page=<name>` value to the module.
"""

from __future__ import annotations

import importlib

import constants as C

ROUTES: dict[str, str] = {
    "home": "home",
    "countries": "countries",
    "country": "country_detail",
    "map": "map_page",
    "repository": "repository",
    "batch": "batch_upload",
    "record-new": "record_new",
    "record": "record_detail",
    "record-edit": "record_edit",
    "coach": "coach",
    "country-profile": "country_profile",
    "country-transparency": "country_transparency",
    "raging": "raging",
    "benchmark": "benchmark",
    "statistics": "statistics",
    "policy-changes": "policy_changes",
    "api-docs": "api_docs",
    "about": "about",
    "release-notes": "release_notes",
    "signin": "signin",
    "register": "register",
}
AUTH_PAGES = {"signin", "register"}           # 404 while local sign-in is off (AUTH_ENABLED=false)


def load(name: str):
    mod = ROUTES.get(name)
    if not mod or (name in AUTH_PAGES and not C.AUTH_ENABLED):
        return None
    return importlib.import_module(f"pages.{mod}")


def load_all():
    return [importlib.import_module(f"pages.{mod}") for mod in dict.fromkeys(ROUTES.values())]

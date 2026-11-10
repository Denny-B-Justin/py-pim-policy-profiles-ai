"""
URL helpers.  The whole site lives on one path and is routed by query string:

    /?page=home            /?page=countries         /?page=country&iso3=LTU
    /?page=record&id=<id>  /?page=record-edit&id=…  /?page=repository  …

All links are *relative* (`?page=…`) so they keep working under the Posit Connect
content path (`/content/<guid>/`).
"""

from __future__ import annotations

from urllib.parse import parse_qsl, quote, urlencode

import dash


def href(page: str, **params) -> str:
    """Relative href for an internal page, e.g. href("country", iso3="LTU") -> "?page=country&iso3=LTU"."""
    q = {"page": page}
    q.update({k: v for k, v in params.items() if v not in (None, "")})
    return "?" + urlencode(q, quote_via=quote)


def parse_search(search: str | None) -> dict[str, str]:
    """Parse the `dcc.Location.search` string into a flat dict (last value wins)."""
    if not search:
        return {}
    return dict(parse_qsl(search.lstrip("?"), keep_blank_values=False))


def api_url(path: str) -> str:
    """Absolute-path URL for a Flask route, including the Connect content prefix."""
    return dash.get_relative_path(path if path.startswith("/") else "/" + path)


def asset(path: str) -> str:
    return dash.get_asset_url(path)

"""API reference – intentionally empty for now (the original rendered Redoc over `/api/v1/openapi.json`).

The route stays registered so the nav and home-page links resolve; the `/api/v1/*` endpoints are unaffected.
"""

from __future__ import annotations

from dash import html

from pages.pages_constants import API_DOCS_TITLE as TITLE


def layout(params=None):
    return html.Div()

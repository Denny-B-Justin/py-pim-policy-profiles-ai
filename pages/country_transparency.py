"""Country PIM Transparency Assessment – port of `web/app/country-transparency/page.tsx` (BriefingPanel "transparency")."""

from __future__ import annotations

from pages import coach
from pages.pages_constants import COUNTRY_TRANSPARENCY_TITLE as TITLE
from utils import briefing_panel, mai_client


def layout(params=None):
    if not mai_client.is_configured():
        return coach.coming_soon()
    return briefing_panel.panel("transparency")


def register_callbacks(app) -> None:
    briefing_panel.register_callbacks(app)

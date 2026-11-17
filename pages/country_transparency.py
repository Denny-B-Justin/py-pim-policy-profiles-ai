"""Country PIM Transparency Assessment – port of `web/app/country-transparency/page.tsx` (BriefingPanel "transparency")."""

from __future__ import annotations

from pages.pages_constants import COUNTRY_TRANSPARENCY_TITLE as TITLE
from utils import briefing_panel


def layout(params=None):
    return briefing_panel.panel("transparency")


def register_callbacks(app) -> None:
    briefing_panel.register_callbacks(app)

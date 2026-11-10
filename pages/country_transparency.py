"""Country PIM Transparency Assessment – port of `web/app/country-transparency/page.tsx` (BriefingPanel "transparency")."""

from __future__ import annotations

from utils import briefing_panel

TITLE = "PIM Policy Profiles AI"


def layout(params=None):
    return briefing_panel.panel("transparency")


def register_callbacks(app) -> None:
    briefing_panel.register_callbacks(app)

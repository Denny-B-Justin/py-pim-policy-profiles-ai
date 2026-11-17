"""Country PIM Institutional Profile – port of `web/app/country-profile/page.tsx` (BriefingPanel "profile")."""

from __future__ import annotations

from pages.pages_constants import COUNTRY_PROFILE_TITLE as TITLE
from utils import briefing_panel


def layout(params=None):
    return briefing_panel.panel("profile")


def register_callbacks(app) -> None:
    briefing_panel.register_callbacks(app)

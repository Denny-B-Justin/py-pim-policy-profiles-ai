"""Reference data for the 196 countries (port of `web/lib/countries-data.ts`): GDP + public investment 2024 (estimates)."""

from __future__ import annotations

import json
from functools import lru_cache

import constants as C


@lru_cache(maxsize=1)
def countries_data() -> list[dict]:
    with open(C.COUNTRIES_JSON, encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def countries_by_iso3() -> dict[str, dict]:
    return {c["iso3"]: c for c in countries_data()}


def resolve_country_name(iso3_or_name: str) -> str:
    c = countries_by_iso3().get(iso3_or_name)
    return c["name"] if c else iso3_or_name

"""
Record validation – port of `web/modules/records/schema.ts` (zod `policyRecordSchema` / `policyRecordUpdateSchema`).

`validate_record(data, partial=False)` returns `(clean, errors)`:
  * keys may be snake_case (DB / Dash form) or camelCase (public API / batch import) – both are accepted;
  * `clean` always uses snake_case column names;
  * `errors` maps the snake_case field name to a user-facing message (empty dict = valid).
With `partial=True` (PATCH semantics) only the keys that were sent are validated and returned.
"""

from __future__ import annotations

import re
from typing import Any

import constants as C

JURISDICTION_LEVELS = tuple(C.JURISDICTION_LEVELS)
RECORD_STATUSES = tuple(C.RECORD_STATUSES)

_UNSET = object()


def snake(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def superseded_link_kind(href: str | None) -> str | None:
    """in-app record path / external URL / neither (single source of truth, `supersededLinkKind`)."""
    if not href:
        return None
    if href.startswith("/records/"):
        return "internal"
    if re.match(r"^https?://", href):
        return "external"
    return None


# ── preprocessors ────────────────────────────────────────────────────────────
def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _to_int(v: Any, errors: dict, field: str, lo: int | None = None, hi: int | None = None, msg: str | None = None):
    if _blank(v):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        errors[field] = msg or "Must be a number"
        return None
    if n != int(n):
        errors[field] = "Must be a whole number"
        return None
    n = int(n)
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        errors[field] = msg or f"Must be between {lo} and {hi}"
        return None
    return n


def _text(v: Any, errors: dict, field: str, max_len: int, too_long: str, blank_to_null: bool = False):
    if v is None or (blank_to_null and isinstance(v, str) and v.strip() == ""):
        return None
    if not isinstance(v, str):
        errors[field] = "Must be text"
        return None
    if len(v) > max_len:
        errors[field] = too_long
    return v


def _bool(v: Any, errors: dict, field: str):
    if _blank(v):
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("y", "yes", "true", "1"):
        return True
    if s in ("n", "no", "false", "0"):
        return False
    errors[field] = "Must be Yes or No"
    return None


def validate_record(data: dict, partial: bool = False) -> tuple[dict, dict[str, str]]:
    src = {snake(k): v for k, v in (data or {}).items()}
    errors: dict[str, str] = {}
    out: dict[str, Any] = {}

    def has(f: str) -> bool:
        return (f in src) if partial else True

    def val(f: str):
        return src.get(f)

    if has("country"):
        v = val("country")
        if not isinstance(v, str) or not v.strip():
            errors["country"] = "Country is required"
        elif len(v) > 3:
            errors["country"] = "Must be an ISO3 country code"
        out["country"] = v
    if has("name_eng"):
        v = val("name_eng")
        if not isinstance(v, str) or not v.strip():
            errors["name_eng"] = "English name is required"
        elif len(v) > 1000:
            errors["name_eng"] = "Name too long"
        out["name_eng"] = v
    if has("name_orig"):
        out["name_orig"] = _text(val("name_orig"), errors, "name_orig", 1000, "Name too long")
    if has("year"):
        out["year"] = _to_int(val("year"), errors, "year", 1900, 2100)
    if has("source"):
        out["source"] = _text(val("source"), errors, "source", 500, "Source too long")
    if has("year_revised"):
        out["year_revised"] = _to_int(val("year_revised"), errors, "year_revised", 1900, 2100)
    if has("overview"):
        out["overview"] = _text(val("overview"), errors, "overview", 50000, "Overview too long")
    if has("policy_guidance_tier"):
        out["policy_guidance_tier"] = _to_int(val("policy_guidance_tier"), errors, "policy_guidance_tier", 1, 4)
    if has("strategy_tier"):
        out["strategy_tier"] = _to_int(val("strategy_tier"), errors, "strategy_tier", 1, 6)
    if has("jurisdiction_level"):
        v = val("jurisdiction_level")
        v = "national" if _blank(v) else str(v).lower()
        if v not in JURISDICTION_LEVELS:
            errors["jurisdiction_level"] = "Must be national, subnational or supranational"
        out["jurisdiction_level"] = v
    if has("jurisdiction_name"):
        out["jurisdiction_name"] = _text(val("jurisdiction_name"), errors, "jurisdiction_name", 200, "Name too long",
                                         blank_to_null=True)
    if has("record_status"):
        v = val("record_status")
        v = "latest" if _blank(v) else str(v).lower()
        if v not in RECORD_STATUSES:
            errors["record_status"] = "Must be latest or archived"
        out["record_status"] = v
    if has("superseded_by_link"):
        v = _text(val("superseded_by_link"), errors, "superseded_by_link", 2000, "URL too long", blank_to_null=True)
        if v and "superseded_by_link" not in errors and superseded_link_kind(v) is None:
            errors["superseded_by_link"] = "Must be an http(s) URL or a /records/… path"
        out["superseded_by_link"] = v
    if has("superseded_by_note"):
        out["superseded_by_note"] = _text(val("superseded_by_note"), errors, "superseded_by_note", 5000, "Note too long",
                                          blank_to_null=True)
    if has("machine_readable"):
        out["machine_readable"] = _bool(val("machine_readable"), errors, "machine_readable")
    if has("comment"):
        out["comment"] = _text(val("comment"), errors, "comment", 10000, "Comment too long")
    if has("link"):
        v = _text(val("link"), errors, "link", 2000, "URL too long")
        if v and "link" not in errors and not re.match(r"^https?://", v):
            errors["link"] = "Must be a valid URL starting with http:// or https://"
        out["link"] = v
    if has("pages"):
        out["pages"] = _to_int(val("pages"), errors, "pages")
    if has("tokens"):
        out["tokens"] = _to_int(val("tokens"), errors, "tokens")

    # a sub-/supra-national record must name its entity (country page grouping + briefing scope selector)
    level = out.get("jurisdiction_level")
    if level and level != "national" and not out.get("jurisdiction_name") and "jurisdiction_name" not in errors:
        errors["jurisdiction_name"] = "Entity name is required for sub-national and supranational records"
    return out, errors

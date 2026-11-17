"""
Shared record components (ports of `components/records/{TierBadge,RecordStatusBadge}.tsx`,
`lib/tiers.ts`, `lib/record-display.ts`, `lib/translation-sources.ts`).
"""

from __future__ import annotations

from dash import html

import constants as C
from utils.icons import icon
from utils.ui import badge, cn

# ── tiers ────────────────────────────────────────────────────────────────────
POLICY_COLORS = {
    1: "bg-blue-100 text-blue-800 border-blue-200",
    2: "bg-indigo-100 text-indigo-800 border-indigo-200",
    3: "bg-violet-100 text-violet-800 border-violet-200",
    4: "bg-purple-100 text-purple-800 border-purple-200",
}
STRATEGY_COLORS = {
    1: "bg-emerald-100 text-emerald-800 border-emerald-200",
    2: "bg-teal-100 text-teal-800 border-teal-200",
    3: "bg-cyan-100 text-cyan-800 border-cyan-200",
    4: "bg-sky-100 text-sky-800 border-sky-200",
    5: "bg-lime-100 text-lime-800 border-lime-200",
    6: "bg-green-100 text-green-800 border-green-200",
}


def tier_label(tier: int | None) -> str | None:
    if tier is None:
        return None
    return next((t["label"] for t in C.TIERS if t["value"] == tier), f"Tier {tier}")


def strategy_label(value: int | None) -> str | None:
    if value is None:
        return None
    return next((s["label"] for s in C.STRATEGY_TYPES if s["value"] == value), f"Strategy {value}")


def strategy_short(value: int | None) -> str | None:
    if value is None:
        return None
    return next((s["short"] for s in C.STRATEGY_TYPES if s["value"] == value), f"S{value}")


def tier_badge(tier: int | None, type: str):
    """<TierBadge tier type="policy|strategy"/>"""
    if tier is None:
        return None
    if type == "policy":
        return badge(f"PG-T{tier}", variant="outline", title=tier_label(tier) or f"Policy Guidance Tier {tier}",
                     cls=cn("text-xs font-semibold cursor-default", POLICY_COLORS.get(tier, "bg-gray-100 text-gray-800")))
    return badge(strategy_short(tier) or f"S{tier}", variant="outline", title=strategy_label(tier) or f"Strategy {tier}",
                 cls=cn("text-xs font-semibold cursor-default", STRATEGY_COLORS.get(tier, "bg-gray-100 text-gray-800")))


# ── status ───────────────────────────────────────────────────────────────────
ARCHIVED_TITLE = "Superseded or repealed — excluded from AI briefs and coach answers"
NOT_MACHINE_READABLE_TITLE = ("Not machine-readable — an attached file is an image-only scan; OCR is needed and must "
                              "be validated before the AI can read it")


def record_status_badge(status: str | None, show_latest: bool = False, size: str = "sm"):
    sizing = "px-1.5 py-0.5 text-[10px]" if size == "xs" else "text-xs"
    if status == "archived":
        return badge(icon("archive", "h-2.5 w-2.5" if size == "xs" else "h-3 w-3"), "Archived", variant="outline",
                     title=ARCHIVED_TITLE, cls=cn("gap-1 cursor-default border-gray-300 bg-gray-100 text-gray-600", sizing))
    if not show_latest:
        return None
    return badge("Latest", variant="outline", title="Current / in force",
                 cls=cn("cursor-default border-emerald-200 bg-emerald-50 text-emerald-700", sizing))


def machine_readable_badge(machine_readable: bool | None, size: str = "sm"):
    if machine_readable is not False:
        return None
    sizing = "px-1.5 py-0.5 text-[10px]" if size == "xs" else "text-xs"
    return badge(icon("scan-text", "h-2.5 w-2.5" if size == "xs" else "h-3 w-3"), "Needs OCR", variant="outline",
                 title=NOT_MACHINE_READABLE_TITLE, cls=cn("gap-1 cursor-default border-red-300 bg-red-50 text-red-700", sizing))


def title_class(record: dict, base: str = "") -> str:
    """Red when not machine-readable (precedence), grey when archived, default otherwise."""
    if record.get("machine_readable") is False:
        return cn(base, "text-red-700")
    if record.get("record_status") == "archived":
        return cn(base, "text-muted-foreground/70")
    return base


# ── translation provenance ───────────────────────────────────────────────────
def translation_source_label(value: str | None) -> str | None:
    if not value:
        return None
    return next((s["label"] for s in C.TRANSLATION_SOURCES if s["value"] == value), value)


def translation_source_badge(value: str | None, cls: str = ""):
    label = translation_source_label(value)
    if not label:
        return None
    return badge(label, variant="outline",
                 cls=cn("text-[10px] px-1.5 py-0", C.TRANSLATION_SOURCE_BADGE.get(value, C.TRANSLATION_SOURCE_BADGE["other"]), cls))


def fmt_num(n: float, decimals: int = 1) -> str:
    return f"{n:,.{decimals}f}"


def fmt_date_gb(d) -> str:
    """'9 Nov 2026' (en-GB short month)."""
    return f"{d.day} {d:%b} {d.year}"


def star(cls: str = ""):
    """Grey PIMA star (an inline <svg> in the original; a CSS mask here)."""
    return html.Span(className=cn("pima-star h-3.5 w-3.5 bg-slate-400", cls), **{"aria-hidden": "true"})


# ── source-link health (components/records/OriginalLinkButton.tsx) ───────────
HEALTH_BTN_CLASS = {
    "green": "border-green-300 bg-green-50 text-green-700 hover:bg-green-100",
    "yellow": "border-amber-300 bg-amber-50 text-amber-700 hover:bg-amber-100",
    "red": "border-red-300 bg-red-50 text-red-700 hover:bg-red-100",
    "gray": "border-gray-200 bg-gray-50 text-gray-600 hover:bg-gray-100",
}
HEALTH_ICON_CLASS = {"green": "text-green-600", "yellow": "text-amber-600", "red": "text-red-600",
                     "gray": "text-muted-foreground"}
HEALTH_TITLE = {
    "green": "Source link is working (as of the last check)",
    "yellow": "Source link worked previously but is not working now",
    "red": "Source link is not working",
    "gray": "Source link has not been checked yet",
}


def link_health(link_ok: int | None, link_last_ok: bool) -> str:
    if link_ok == 1:
        return "green"
    if link_ok == 0:
        return "yellow" if link_last_ok else "red"
    return "gray"


def original_link_button(link: str | None, link_ok: int | None, link_last_ok: bool = False, label: str = "Original link"):
    from utils.ui import button_class  # noqa: PLC0415 (avoid a circular import at module load)

    if not link:
        return None
    h = link_health(link_ok, link_last_ok)
    return html.A([icon("external-link", "h-3.5 w-3.5"), label], href=link, target="_blank", rel="noopener noreferrer",
                  title=HEALTH_TITLE[h],
                  className=button_class("outline", "sm", f"h-7 gap-1.5 px-2 text-xs {HEALTH_BTN_CLASS[h]}"))


# ── superseded-by link (modules/records/schema.ts::supersededLinkKind) ───────
def superseded_link_kind(href: str | None) -> str | None:
    """'internal' (an in-app /records/<id> path), 'external' (http/https URL) or None."""
    if not href:
        return None
    if href.startswith("/records/") or href.startswith("?page=record"):
        return "internal"
    if href.startswith(("http://", "https://")):
        return "external"
    return None


def superseded_href(href: str) -> str:
    """Stored `/records/<id>` paths become `?page=record&id=<id>` links in the Dash app."""
    from utils.routes import href as _href  # noqa: PLC0415

    if href.startswith("/records/"):
        return _href("record", id=href[len("/records/"):].split("/")[0].split("?")[0])
    return href


def fmt_size(num_bytes: int | None) -> str:
    if not num_bytes:
        return ""
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"

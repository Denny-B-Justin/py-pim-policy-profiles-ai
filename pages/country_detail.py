"""Country policies – port of `web/app/countries/[iso3]/{page,CountryPoliciesClient}.tsx`."""

from __future__ import annotations

from dash import html

from pages import not_found
from utils import queries
from utils.components import (fmt_size, machine_readable_badge, original_link_button, record_status_badge,
                              superseded_href, superseded_link_kind, tier_badge, tier_label, title_class)
from utils import constants as C
from utils.countries_data import countries_by_iso3
from utils.icons import icon
from utils.routes import api_url, href
from utils.ui import button, button_class, cn, link
from utils.widgets import translate_button, translate_dialog

TITLE = "Policies — PIM Repository"

LEVEL_ORDER = ["national", "subnational", "supranational"]
LEVEL_META = {
    "national": dict(label="National", badge="border-slate-200 bg-slate-50 text-slate-600", dot="bg-slate-400"),
    "subnational": dict(label="Sub-national", badge="border-emerald-200 bg-emerald-50 text-emerald-700", dot="bg-emerald-500"),
    "supranational": dict(label="Supranational", badge="border-violet-200 bg-violet-50 text-violet-700", dot="bg-violet-500"),
}


def level_meta(level: str) -> dict:
    return LEVEL_META.get(level, LEVEL_META["national"])


def _bulk_button(iso3: str, lang: str, count: int, label: str, icon_name: str, variant: str = "outline"):
    if count == 0:
        return button(icon(icon_name, "h-3.5 w-3.5"), f"{label} (0)", size="sm", variant="outline", disabled=True,
                      cls="gap-1.5 opacity-60")
    return html.A([icon(icon_name, "h-3.5 w-3.5"), f"{label} ({count})"],
                  href=f"{api_url(f'/api/download/country/{iso3}')}?lang={lang}", download="",
                  className=button_class(variant, "sm", "gap-1.5"))


def _doc_link(doc: dict, label: str):
    src = doc.get("translation_source")
    from utils.components import translation_source_label  # noqa: PLC0415

    return html.A([
        icon("file-text", "h-3.5 w-3.5 text-blue-600"),
        html.Span(label, className="max-w-[10rem] truncate"),
        html.Span(translation_source_label(src), className=cn("rounded border px-1 text-[9px] font-medium",
                                                              C.TRANSLATION_SOURCE_BADGE.get(src, ""))) if src else None,
        html.Span(f"· {fmt_size(doc['file_size'])}", className="text-muted-foreground") if doc.get("file_size") else None,
        icon("download", "h-3 w-3 shrink-0"),
    ], href=api_url(f"/api/download/{doc['id']}"), download="", title=doc.get("file_name") or None,
        className=button_class("ghost", "sm", "h-7 gap-1.5 px-2 text-xs"))


def _policy_row(p: dict, docs: list[dict]):
    docs = sorted(docs, key=lambda d: 0 if d["lang_type"] == "ENG" else 1)
    eng = next((d for d in docs if d["lang_type"] == "ENG"), None)
    ori = next((d for d in docs if d["lang_type"] == "ORI"), None)
    archived = p["record_status"] == "archived"
    kind = superseded_link_kind(p.get("superseded_by_link"))
    level = p.get("jurisdiction_level") or "national"
    meta = level_meta(level)

    badges = [
        machine_readable_badge(p["machine_readable"], "xs"),
        record_status_badge(p["record_status"], size="xs"),
    ]
    if archived and kind:
        ext = dict(target="_blank", rel="noopener noreferrer") if kind == "external" else {}
        badges.append(html.A("→ current version", href=superseded_href(p["superseded_by_link"]),
                             className="text-[10px] font-medium text-blue-700 hover:underline", **ext))
    if p.get("year"):
        badges.append(html.Span(f"{p['year']}" + (f" · rev. {p['year_revised']}" if p.get("year_revised") else ""),
                                className="text-xs text-muted-foreground"))
    badges += [tier_badge(p["policy_guidance_tier"], "policy"), tier_badge(p["strategy_tier"], "strategy")]
    if level != "national":
        badges.append(html.Span([html.Span(className=f"h-1.5 w-1.5 rounded-full {meta['dot']}"),
                                 p.get("jurisdiction_name") or meta["label"]],
                                title=meta["label"],
                                className=f"inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] "
                                          f"font-medium {meta['badge']}"))
    if p.get("source"):
        badges.append(html.Span(f"· {p['source']}", className="text-xs text-muted-foreground"))

    right = [
        original_link_button(p.get("link"), p.get("link_ok"), p.get("link_last_ok_at") is not None),
        _doc_link(eng, "English") if eng else None,
        _doc_link(ori, ori.get("lang_label") or "Original language") if ori else None,
        translate_button(p["id"]) if (not eng and ori and ori.get("lang_code") and ori["lang_code"] != "en") else None,
        html.Span("No files", className="text-xs text-muted-foreground sm:pt-1") if (not eng and not ori and not p.get("link")) else None,
    ]
    return html.Div([
        html.Div([
            link(p["name_eng"], href=href("record", id=p["id"]),
                 cls=title_class(p, "text-sm font-medium leading-snug hover:underline")),
            html.P(p["name_orig"], className="text-xs italic text-muted-foreground") if p.get("name_orig") else None,
            html.Div([b for b in badges if b is not None], className="flex flex-wrap items-center gap-1.5 pt-0.5"),
        ], className="min-w-0 space-y-1"),
        html.Div([r for r in right if r is not None], className="flex shrink-0 flex-col items-stretch gap-1 sm:items-end"),
    ], className="flex flex-col gap-3 p-4 sm:flex-row sm:items-start sm:justify-between")


def _tier_groups(items: list[tuple[dict, list[dict]]]):
    groups: list[tuple[int | None, list]] = []
    for item in items:
        tier = item[0]["policy_guidance_tier"]
        if groups and groups[-1][0] == tier:
            groups[-1][1].append(item)
        else:
            groups.append((tier, [item]))
    return groups


def layout(params=None):
    params = params or {}
    iso3 = (params.get("iso3") or "").upper()
    name = (countries_by_iso3().get(iso3) or {}).get("name")
    rows = queries.get_country_policies(iso3) if iso3 else []
    if not rows and not name:
        return not_found.layout(params)
    name = name or iso3

    all_docs = [d for r in rows for d in r["docs"]]
    eng_count = sum(1 for d in all_docs if d["lang_type"] == "ENG")
    ori_count = sum(1 for d in all_docs if d["lang_type"] == "ORI")
    ori_en = sum(1 for d in all_docs if d["lang_type"] == "ORI" and d.get("lang_code") == "en")
    ori_non = sum(1 for d in all_docs if d["lang_type"] == "ORI" and d.get("lang_code") not in (None, "en"))
    ori_unknown = sum(1 for d in all_docs if d["lang_type"] == "ORI" and d.get("lang_code") is None)
    total_docs = eng_count + ori_count
    policies = [(r["record"], r["docs"]) for r in rows]

    # level → entity → items (consecutive grouping reproduces the server ordering)
    level_map: dict[str, list[dict]] = {}
    for rec, docs in policies:
        lvl = rec["jurisdiction_level"] if rec["jurisdiction_level"] in LEVEL_META else "national"
        entity = None if lvl == "national" else (rec.get("jurisdiction_name") or "Other")
        arr = level_map.setdefault(lvl, [])
        if arr and arr[-1]["entity"] == entity:
            arr[-1]["items"].append((rec, docs))
        else:
            arr.append({"entity": entity, "items": [(rec, docs)]})
    sections = [dict(level=l, count=sum(len(e["items"]) for e in level_map[l]), entities=level_map[l])
                for l in LEVEL_ORDER if l in level_map]
    has_non_national = any(s["level"] != "national" for s in sections)

    n = len(policies)
    summary = f"{n} polic{'y' if n == 1 else 'ies'} · {total_docs} document{'' if total_docs == 1 else 's'}"
    summary += (" · " + " · ".join(f"{s['count']} {level_meta(s['level'])['label'].lower()}" for s in sections)
                if has_non_national else " · sorted by policy-guidance tier")

    body: list = []
    if not policies:
        body = [html.Div(f"No policies have been recorded for {name} yet.",
                         className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground")]
    else:
        secs = []
        for s in sections:
            meta = level_meta(s["level"])
            block = []
            if has_non_national:
                block.append(html.Div([
                    html.Span(className=f"h-2.5 w-2.5 rounded-full {meta['dot']}"),
                    html.H2(meta["label"], className="text-base font-bold tracking-tight"),
                    html.Span(f"({s['count']})", className="text-sm font-normal text-muted-foreground"),
                ], className="flex items-center gap-2 border-b pb-1.5"))
            for ent in s["entities"]:
                inner = []
                if ent["entity"]:
                    inner.append(html.H3([ent["entity"], html.Span(f"({len(ent['items'])})", className="font-normal opacity-70")],
                                         className=f"inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 "
                                                   f"text-sm font-semibold {meta['badge']}"))
                for tier, items in _tier_groups(ent["items"]):
                    inner.append(html.Section([
                        html.H4([html.Span(tier_label(tier) if tier is not None else "Untiered"),
                                 html.Span(f"({len(items)})", className="font-normal text-muted-foreground")],
                                className="flex items-center gap-2 text-sm font-semibold"),
                        html.Div([_policy_row(rec, docs) for rec, docs in items], className="divide-y rounded-lg border"),
                    ], className="space-y-2.5"))
                block.append(html.Div(inner, className="space-y-4"))
            secs.append(html.Div(block, className="space-y-5"))
        body = [html.Div(secs, className="space-y-10")]

    ori_summary = None
    if ori_count > 0:
        ori_summary = html.Div([
            html.Span("Original-language documents:", className="font-medium text-muted-foreground"),
            html.Span([icon("languages", "h-3 w-3 text-blue-600"), f"{ori_en} in English"],
                      className="inline-flex items-center gap-1 rounded-full border bg-muted/40 px-2 py-0.5"),
            html.Span(f"{ori_non} non-English", className="inline-flex items-center gap-1 rounded-full border bg-muted/40 px-2 py-0.5"),
            html.Span(f"{ori_unknown} unrecorded", className="inline-flex items-center gap-1 rounded-full border "
                      "border-amber-200 bg-amber-50 px-2 py-0.5 text-amber-700") if ori_unknown > 0 else None,
        ], className="flex flex-wrap items-center gap-2 text-xs")

    return html.Div([
        link([icon("arrow-left", "h-4 w-4"), "All countries"], href=href("countries"),
             cls="inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"),
        html.Div([
            html.Div([
                html.H1([name, html.Span(iso3, className="ml-2 font-mono text-base font-normal text-muted-foreground")],
                        className="text-2xl font-bold tracking-tight"),
                html.P(summary, className="mt-1 text-sm text-muted-foreground"),
            ]),
            link([icon("history", "h-3.5 w-3.5 text-teal-600"), "Policy changes"],
                 href=href("policy-changes", country=iso3, mode="latest"),
                 cls=button_class("outline", "sm", "gap-1.5")),
        ], className="flex flex-wrap items-start justify-between gap-3"),
        ori_summary,
        html.Div([
            html.Div([
                html.Span([icon("file-down", "h-4 w-4 text-blue-600"), "Download all"],
                          className="mr-1 inline-flex items-center gap-1.5 text-sm font-medium"),
                _bulk_button(iso3, "ENG", eng_count, "English translations", "download"),
                _bulk_button(iso3, "ORI", ori_count, "Original-language", "languages"),
                _bulk_button(iso3, "all", total_docs, "Everything", "download", "default") if total_docs > 0 else None,
            ], className="flex flex-wrap items-center gap-2.5"),
            html.P(["Each option downloads a single ", html.Code(".zip"), " archive of the matching documents."],
                   className="mt-2 text-xs text-muted-foreground"),
        ], className="rounded-lg border bg-muted/30 p-4"),
        *body,
        translate_dialog(),
    ], className="space-y-6")

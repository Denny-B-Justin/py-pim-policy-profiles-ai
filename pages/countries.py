"""Countries – port of `web/app/countries/{page,CountriesClient}.tsx`."""

from __future__ import annotations

from datetime import datetime

from dash import Input, Output, dcc, html

from pages.pages_constants import COUNTRIES_TITLE as TITLE, COUNTRIES_PIMA_STATUS_TEXT as STATUS_TEXT
from utils import imf_pima, queries
from utils.components import fmt_date_gb, fmt_num, star
from utils.countries_data import countries_data
from utils.icons import icon
from utils.routes import api_url, href
from utils.ui import cn, link, text_input

_A_BLANK = dict(target="_blank", rel="noopener noreferrer")


def _pima_title(a: dict) -> str:
    published = f" (published {a['published_at'][:7]})" if a.get("published_at") and a["status"] == "published" else ""
    src = a["source"]
    tag = "" if src == "portal" else f" [{'imf.org high-level summary' if src == 'imf.org' else 'curated reference'}]"
    return f"{a['assessment_type']} {a['year']} — {STATUS_TEXT.get(a['status'], a['status'])}{published}{tag}"


def _marker(a: dict, url: str):
    published = a["status"] == "published"
    climate = a["assessment_type"] == "C-PIMA"
    if a["status"] == "summary":
        inner = star("bg-sky-300" if climate else "")
    elif climate:
        shape = "h-2.5 w-2.5 rotate-45 bg-sky-500" if published else "h-2.5 w-2.5 rotate-45 border-2 border-sky-500 bg-white"
        inner = html.Span(className=f"inline-block {shape}")
    else:
        shape = ("h-3 w-3 rounded-full bg-emerald-500" if published
                 else "h-3 w-3 rounded-full border-2 border-emerald-500 bg-white")
        inner = html.Span(className=f"inline-block {shape}")
    return html.A(inner, href=url, title=_pima_title(a), className="inline-flex items-center p-0.5", **_A_BLANK)


def _split(s: dict):
    core = [a for a in s["assessments"] if a["assessment_type"] in ("PIMA", "PIMA Update")]
    climate = [a for a in s["assessments"] if a["assessment_type"] == "C-PIMA"]
    return core, climate


def _status_cell(s: dict | None):
    if not s or not s["assessments"]:
        return html.Span("—", className="text-muted-foreground")
    core, climate = _split(s)
    url = lambda a: a["report_url"] or a["reference_url"] or s["source_url"]  # noqa: E731
    parts = [_marker(a, url(a)) for a in core]
    if climate and core:
        parts.append(html.Span(className="mx-0.5 h-3 w-px bg-slate-200"))
    parts += [_marker(a, url(a)) for a in climate]
    return html.Span(parts, className="inline-flex items-center gap-0.5")


def _years_cell(s: dict | None):
    if not s or not s["assessments"]:
        return html.Span("—", className="text-muted-foreground")
    core, climate = _split(s)

    def lnk(a: dict, label: str):
        colour = ("text-emerald-700" if a["status"] == "published" else
                  "text-slate-500" if a["status"] == "summary" else "text-slate-600")
        target = s["source_url"] if a["source"] == "portal" else (a["reference_url"] or s["source_url"])
        what = "open the IMF country page" if a["source"] == "portal" else "open the IMF reference"
        return html.A(label, href=target, title=f"{_pima_title(a)} — {what}", className=f"hover:underline {colour}", **_A_BLANK)

    parts: list = []
    for i, a in enumerate(core):
        if i:
            parts.append(html.Span(" · ", className="text-slate-300"))
        parts.append(lnk(a, str(a["year"])))
    for i, a in enumerate(climate):
        if parts:
            parts.append(html.Span(" · ", className="text-slate-300"))
        parts.append(lnk(a, f"C-{a['year']}"))
    return html.Span(parts, className="whitespace-nowrap tabular-nums text-xs")


TH = "px-4 py-3 font-medium text-muted-foreground"


def _header_cell(text: str, sub: str | None = None, align: str = "left", cls: str = ""):
    kids: list = [text]
    if sub:
        kids += [html.Br(), html.Span(sub, className="font-normal text-xs")]
    return html.Th(kids, className=cn(TH, f"text-{align}", cls))


def _row(i: int, c: dict, count: int, pima: dict | None):
    name = c["name"]
    return html.Tr([
        html.Td(str(i), className="row-idx px-4 py-2.5 text-muted-foreground tabular-nums"),
        html.Td(link(name, href=href("country", iso3=c["iso3"]), cls="text-blue-700 hover:underline") if count > 0 else name,
                className="px-4 py-2.5 font-medium"),
        html.Td(html.Span(c["iso3"], className="inline-block rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-600"),
                className="px-4 py-2.5 text-center"),
        html.Td(fmt_num(c["gdpBn"]), className="px-4 py-2.5 text-right tabular-nums"),
        html.Td(f"{fmt_num(c['pubInvPctGdp'] * 100, 1)}%", className="px-4 py-2.5 text-right tabular-nums"),
        html.Td(fmt_num(c["pubInvBn"], 2), className="px-4 py-2.5 text-right tabular-nums"),
        html.Td(link(str(count), href=href("country", iso3=c["iso3"]), title=f"View {name} policies",
                     cls="inline-flex items-center justify-center rounded-full bg-blue-100 text-blue-700 text-xs "
                         "font-semibold min-w-[1.5rem] px-2 py-0.5 hover:bg-blue-200 transition-colors")
                if count > 0 else html.Span("—", className="text-muted-foreground"),
                className="px-4 py-2.5 text-center"),
        html.Td(_status_cell(pima), className="px-4 py-2.5 text-center"),
        html.Td(_years_cell(pima), className="px-4 py-2.5"),
    ], className="hover:bg-muted/30 transition-colors",
        **{"data-name": name.lower(), "data-iso": c["iso3"].lower(), "data-pima": "1" if pima and pima["assessments"] else "0"})


def layout(params=None):
    counts = {r["iso3"]: r["count"] for r in queries.list_policy_counts()}
    pima = imf_pima.list_summaries()
    data = countries_data()
    pima_countries = sum(1 for s in pima.values() if s["assessments"])
    checked = max((s["fetched_at"] for s in pima.values()), default="")
    checked_txt = ""
    if checked:
        try:
            checked_txt = f", last checked {fmt_date_gb(datetime.fromisoformat(checked))}"
        except ValueError:
            checked_txt = ""

    legend = html.Div([
        html.Span([html.Span(className="inline-block h-3 w-3 rounded-full bg-emerald-500"), "PIMA, full report published"],
                  className="inline-flex items-center gap-1.5"),
        html.Span([html.Span(className="inline-block h-3 w-3 rounded-full border-2 border-emerald-500 bg-white"),
                   "PIMA conducted, not published"], className="inline-flex items-center gap-1.5"),
        html.Span([star(), "IMF reference or high-level summary only"], className="inline-flex items-center gap-1.5"),
        html.Span([html.Span(className="inline-block h-2.5 w-2.5 rotate-45 bg-sky-500"), "Climate PIMA (C-PIMA)"],
                  className="inline-flex items-center gap-1.5"),
        html.Span("one marker per assessment, oldest first"),
    ], className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground")

    return html.Div([
        html.Div([
            html.H1("Countries", className="text-2xl font-bold tracking-tight"),
            html.P([
                f"Public investment reference data for {len(data)} countries (GDP & public investment estimates, 2024). "
                "Sources: IMF World Economic Outlook (Oct 2024), World Bank, national statistics. "
                f"IMF PIMA status for {pima_countries} countries from the ",
                html.A("IMF Infrastructure Governance portal", href=imf_pima.IMF_PIMA_ABOUT_URL,
                       className="underline hover:text-foreground", **_A_BLANK),
                f"{checked_txt}; refreshed monthly.",
            ], className="mt-1 text-sm text-muted-foreground"),
        ]),
        html.Div([
            html.Div([
                icon("search", "absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground"),
                text_input("countries-search", placeholder="Search by country or ISO code…", cls="pl-8", debounce=False),
            ], className="relative max-w-sm flex-1 min-w-[220px]"),
            dcc.Checklist(id="countries-pima-only", options=[{"label": "IMF PIMA countries only", "value": "1"}], value=[],
                          className="inline-flex items-center text-sm", labelClassName="inline-flex items-center gap-2 text-sm",
                          inputClassName="m-0", labelStyle={"display": "inline-flex"}),
            html.A([icon("file-spreadsheet", "h-4 w-4 text-emerald-700"), "Download Excel"],
                   href=api_url("/api/imf-pima/export"), download="",
                   title="Download the IMF PIMA status for all countries as an Excel workbook (with a ReadMe sheet "
                         "explaining each variable)",
                   className="inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium "
                             "transition-colors hover:bg-muted"),
            legend,
        ], className="flex flex-wrap items-center gap-x-6 gap-y-3"),
        html.Div(
            html.Table([
                html.Thead(html.Tr([
                    _header_cell("#", align="left", cls="w-10"),
                    _header_cell("Country"),
                    _header_cell("ISO3", align="center", cls="w-16"),
                    _header_cell("GDP 2024", "(USD Billions, Est.)", "right"),
                    _header_cell("Public Inv. % of GDP", "(Est.)", "right"),
                    _header_cell("Public Investment 2024", "(USD Billions, Est.)", "right"),
                    _header_cell("Policies", align="center"),
                    _header_cell("IMF PIMA", "(status)", "center"),
                    _header_cell("PIMA years", "(links to IMF page)"),
                ]), className="bg-muted/50"),
                html.Tbody([_row(i + 1, c, counts.get(c["iso3"], 0), pima.get(c["iso3"])) for i, c in enumerate(data)]
                           + [html.Tr(html.Td(id="countries-empty", colSpan=9, children="",
                                              className="px-4 py-10 text-center text-muted-foreground"),
                                      id="countries-empty-row", style={"display": "none"})],
                           id="countries-body", className="divide-y"),
            ], className="w-full text-sm"),
            className="rounded-lg border overflow-hidden"),
        html.P(f"Showing {len(data)} of {len(data)} countries", id="countries-count", className="text-xs text-muted-foreground"),
    ], className="space-y-6")


def register_callbacks(app):
    app.clientside_callback(
        "function(q, only){return window.dash_clientside.pim.filterCountries(q, only);}",
        Output("countries-count", "children"), Input("countries-search", "value"), Input("countries-pima-only", "value"),
        prevent_initial_call=True,
    )

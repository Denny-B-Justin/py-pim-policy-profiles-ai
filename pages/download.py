"""Download documents by country – port of `web/app/download/{page,DownloadClient}.tsx`."""

from __future__ import annotations

from dash import Input, Output, State, html, no_update

from utils import queries
from utils.components import fmt_size
from utils.icons import icon
from utils.routes import api_url, href
from utils.ui import badge, button_class, select

TITLE = "Download — PIM Repository"


def _doc_row(d: dict):
    lang = "English" if d["lang_type"] == "ENG" else (d.get("lang_label") or "Native Language")
    return html.Div([
        html.Div([
            icon("file-text", "h-4 w-4 text-blue-600 shrink-0"),
            html.Div([
                html.P(d.get("file_name") or "Document", className="text-sm font-medium truncate"),
                html.P([badge(lang, variant="outline", cls="text-[10px] px-1 py-0 mr-1.5"), fmt_size(d.get("file_size"))],
                       className="text-xs text-muted-foreground"),
            ], className="min-w-0"),
        ], className="flex items-center gap-2 min-w-0"),
        html.A([icon("download", "h-3.5 w-3.5"), "Download"], href=api_url(f"/api/download/{d['id']}"), download="",
               className=button_class("outline", "sm", "shrink-0 gap-1.5")),
    ], className="flex items-center justify-between gap-4 rounded-md bg-muted/40 px-3 py-2")


def _record(r: dict):
    return html.Div([
        html.Div(html.Div([
            html.P(r["name_eng"], className="font-medium text-sm leading-snug"),
            html.P(str(r["year"]), className="text-xs text-muted-foreground mt-0.5") if r.get("year") else None,
        ]), className="flex items-start justify-between gap-4"),
        html.Div([_doc_row(d) for d in r["documents"]], className="space-y-2 pl-1"),
    ], className="p-4 space-y-3")


def layout(params=None):
    iso3 = (params or {}).get("iso3") or None
    countries = queries.list_countries()
    selected = next((c for c in countries if c["iso3"] == iso3), None)
    records = [r for r in (queries.get_country_records_with_documents(iso3) if iso3 else []) if r["documents"]]
    total_docs = sum(len(r["documents"]) for r in records)

    options = [{"label": "Select a country…", "value": "none"}] + [
        {"label": html.Span([c["name"], html.Span(c["iso3"], className="ml-2 font-mono text-xs text-muted-foreground")]),
         "value": c["iso3"]} for c in countries]

    results = None
    if iso3 and selected:
        results = html.Div([
            html.Div([
                html.H2([selected["name"], html.Span(iso3, className="ml-2 font-mono text-sm text-muted-foreground font-normal")],
                        className="text-lg font-semibold"),
                html.Span(f"{len(records)} record{'' if len(records) == 1 else 's'} · "
                          f"{total_docs} document{'' if total_docs == 1 else 's'}",
                          className="text-sm text-muted-foreground"),
            ], className="flex items-center justify-between"),
            html.Div("No uploaded documents found for this country.",
                     className="rounded-lg border border-dashed p-10 text-center text-muted-foreground text-sm")
            if not records else html.Div([_record(r) for r in records], className="rounded-lg border divide-y"),
        ], className="space-y-4")

    return html.Div([
        html.Div([
            html.H1("Download Documents", className="text-2xl font-bold tracking-tight"),
            html.P("Select a country to view and download all associated policy documents from the repository.",
                   className="mt-1 text-sm text-muted-foreground"),
        ]),
        html.Div([
            html.Label("Country", className="text-sm font-medium"),
            select("download-country", options, value=iso3 or "none", placeholder="Select a country…", cls="w-full",
                   menu_width="18rem"),
            html.P(f"Showing {len(countries)} countries with at least one policy record.",
                   className="text-xs text-muted-foreground"),
        ], className="max-w-sm space-y-1.5"),
        results,
    ], className="space-y-8")


def register_callbacks(app):
    @app.callback(Output("url", "search", allow_duplicate=True), Input("download-country", "value"),
                  State("url", "search"), prevent_initial_call=True)
    def pick(v, search):
        from utils.routes import parse_search  # noqa: PLC0415

        cur = parse_search(search).get("iso3") or "none"
        if not v or v == cur:
            return no_update
        return href("download") if v == "none" else href("download", iso3=v)

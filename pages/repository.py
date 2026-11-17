"""Repository – port of `web/app/repository/page.tsx` + `components/records/RecordTable.tsx`."""

from __future__ import annotations

import time
from datetime import datetime

from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

import constants as C
from pages.pages_constants import REPOSITORY_TITLE as TITLE
from utils import auth, documents, queries
from utils.audit import log_audit
from utils.components import (HEALTH_ICON_CLASS, HEALTH_TITLE, link_health, machine_readable_badge, record_status_badge,
                              tier_badge, title_class)
from utils.icons import icon
from utils.routes import href
from utils.toast import toast
from utils.ui import (DIALOG_HIDE, DIALOG_SHOW, badge, button, button_class, cn, dialog, dialog_description,
                      dialog_footer, dialog_header, dialog_title, link, select, spinner, table, tbody, td, text_input,
                      th, thead, tr)
from utils.widgets import recheck_links_widget

PAGE_SIZE = C.PAGE_SIZE
TH = "text-xs font-semibold uppercase tracking-wide"


# ── pieces ───────────────────────────────────────────────────────────────────
def _stat(label: str, value: int):
    return html.Div([
        html.P(label, className="text-xs text-muted-foreground uppercase tracking-wide font-medium"),
        html.P(str(value), className="text-3xl font-bold mt-1"),
    ], className="border rounded-lg p-4")


def _stats(summary: list[dict], total_records: int):
    return [_stat("Countries", len(summary)), _stat("Policy Records", total_records),
            _stat("English Docs", sum(c["eng_docs"] for c in summary)),
            _stat("Non-English Docs", sum(c["ori_docs"] for c in summary))]


def _fmt_us(iso: str) -> str:
    if not iso:
        return ""
    d = datetime.fromisoformat(iso)
    return f"{d:%b} {d.day}, {d.year}"


def _yes_cell(count: int, cls: str = ""):
    return (badge(str(count), variant="outline", cls=cn("text-xs font-medium", cls)) if count > 0
            else html.Span("—", className="text-xs text-muted-foreground"))


def _dashboard_table(summary: list[dict]):
    heads = [("Country", "", "Total policy records for the country", None),
             ("Policies", "text-center w-[90px]", "Total policy records for the country", None),
             ("With Docs", "text-center w-[110px]", "Records with at least one uploaded document", None),
             ("Working Link", "text-center w-[110px]", "Records whose external source link was working at the last check", None),
             ("Non-Eng Original", "text-center w-[130px]", "Is the country's original-language document non-English?", None),
             ("In English", "text-center w-[100px]", "Records that have an English document", None),
             ("Latest Update", "text-right w-[130px]", None, None)]
    head = thead(tr(*[th(h[0], cls=cn(TH, h[1]), **({"title": h[2]} if h[2] and h[0] != "Country" else {}))
                      for h in heads], cls="bg-muted/40"))
    rows = []
    for r in summary:
        rows.append(tr(
            td(html.Div([
                link(r["name"], href=href("country", iso3=r["iso3"]), cls="font-medium text-sm text-blue-700 hover:underline"),
                html.Span(r["iso3"], className="text-[10px] font-mono text-muted-foreground bg-muted px-1.5 py-0.5 rounded"),
            ], className="flex items-center gap-2"), cls="py-2"),
            td(badge(str(r["total_policies"]), variant="secondary", cls="text-xs font-medium"), cls="text-center py-2"),
            td(_yes_cell(r["records_with_docs"]), cls="text-center py-2"),
            td(_yes_cell(r["records_with_working_link"], "border-green-200 bg-green-50 text-green-700"), cls="text-center py-2"),
            td(badge("Yes" if r["orig_non_english"] else "No", variant="outline",
                     cls="text-xs font-medium border-amber-200 bg-amber-50 text-amber-700" if r["orig_non_english"]
                     else "text-xs font-medium text-muted-foreground"), cls="text-center py-2"),
            td(_yes_cell(r["records_with_english"]), cls="text-center py-2"),
            td(_fmt_us(r["latest_update"]), cls="text-right py-2 text-sm text-muted-foreground tabular-nums"),
            cls="hover:bg-muted/30"))
    return table(head, tbody(*rows))


def _record_row(r: dict, names: dict[str, str]):
    name_link_cls = (title_class(r, "text-sm hover:underline font-medium line-clamp-2")
                     if (r["machine_readable"] is False or r["record_status"] == "archived")
                     else "text-sm hover:underline font-medium line-clamp-2 text-blue-700")
    h = link_health(r["link_ok"], r["link_last_ok_at"] is not None)
    actions = html.Div([
        html.A(html.Span(icon("external-link", cn("h-3.5 w-3.5", HEALTH_ICON_CLASS[h])),
                         className=button_class("ghost", "icon", "h-7 w-7")),
               href=r["link"], target="_blank", rel="noopener noreferrer", title=HEALTH_TITLE[h]) if r.get("link") else None,
        link(html.Span(icon("file-text", "h-3.5 w-3.5"), className=button_class("ghost", "icon", "h-7 w-7"), title="View record"),
             href=href("record", id=r["id"])),
        html.Button(icon("trash-2", "h-3.5 w-3.5"), id={"type": "repo-del", "id": r["id"]}, type="button", title="Delete record",
                    className=button_class("ghost", "icon", "h-7 w-7 text-destructive hover:text-destructive "
                                                              "hover:bg-destructive/10")),
    ], className="flex gap-1 justify-end")
    return tr(
        td(html.Div([html.Span(names.get(r["country"], r["country"]), className="font-medium text-sm"),
                     html.Span(r["country"], className="block text-[10px] font-mono text-muted-foreground")],
                    className="space-y-0.5"), cls="py-2.5"),
        td(html.Div([
            link(r["name_eng"], href=href("record", id=r["id"]), cls=name_link_cls),
            html.Span([machine_readable_badge(r["machine_readable"], "xs"), record_status_badge(r["record_status"], size="xs")],
                      className="inline-flex flex-wrap gap-1"),
            html.P(r["name_orig"], className="text-xs text-muted-foreground mt-0.5 line-clamp-1 italic") if r.get("name_orig") else None,
        ], className="max-w-xs"), cls="py-2.5"),
        td(html.Div([html.Span(str(r["year"]) if r["year"] is not None else "—"),
                     html.Span(f"(rev. {r['year_revised']})", className="text-muted-foreground ml-1 text-xs") if r.get("year_revised") else None],
                    className="text-sm text-center"), cls="py-2.5"),
        td(html.Div([tier_badge(r["policy_guidance_tier"], "policy"), tier_badge(r["strategy_tier"], "strategy")],
                    className="flex gap-1 flex-wrap"), cls="py-2.5"),
        td(html.Span(str(r["pages"]) if r["pages"] is not None else "—", className="text-sm text-center text-muted-foreground"),
           cls="py-2.5"),
        td(actions, cls="py-2.5"),
        cls="hover:bg-muted/30")


def _all_records() -> list[dict]:
    return queries.list_records(limit=1_000_000)


def _filter_sort(records, names, search, country, ptier, stier, sort):
    q = (search or "").lower()
    out = []
    for r in records:
        if country not in (None, "all") and r["country"] != country:
            continue
        if ptier not in (None, "all") and r["policy_guidance_tier"] != int(ptier):
            continue
        if stier not in (None, "all") and r["strategy_tier"] != int(stier):
            continue
        if q and not (q in r["name_eng"].lower() or q in names.get(r["country"], r["country"]).lower()
                      or q in r["country"].lower() or q in (r.get("overview") or "").lower()):
            continue
        out.append(r)
    if sort:
        col, direction = sort
        rev = direction == "desc"
        if col == "country":
            out.sort(key=lambda r: r["country"].lower(), reverse=rev)
        else:
            have = sorted((r for r in out if r["year"] is not None), key=lambda r: r["year"], reverse=rev)
            out = have + [r for r in out if r["year"] is None]
    return out


def _render_table(filtered, names, page):
    pages = -(-len(filtered) // PAGE_SIZE)
    page = max(0, min(page, max(pages - 1, 0)))
    chunk = filtered[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [_record_row(r, names) for r in chunk] or [
        tr(td("No records found.", colSpan=6, cls="h-24 text-center text-muted-foreground"))]
    return rows, page, pages


def _sort_header(label: str, button_id: str):
    return th(button(label, icon("arrow-up-down", "ml-2 h-3 w-3"), id=button_id, variant="ghost", size="sm", cls="-ml-3 h-8"),
              cls=TH)


# ── layout ───────────────────────────────────────────────────────────────────
def layout(params=None):
    records = _all_records()
    summary = queries.list_country_dashboard()
    names = queries.country_name_map()
    countries = [{"label": c["name"], "value": c["iso3"]} for c in summary]
    rows, page, pages = _render_table(records, names, 0)

    filter_bar = html.Div([
        html.Div([
            html.Div([
                icon("search", "absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground"),
                text_input("repo-search", placeholder="Search documents, countries…", cls="pl-9 h-9", debounce=False, value=""),
                html.Button(icon("x", "h-4 w-4"), id="repo-search-x", type="button", style={"display": "none"},
                            className="absolute right-2.5 top-2.5 text-muted-foreground hover:text-foreground cursor-pointer"),
            ], className="relative flex-1 max-w-sm"),
            select("repo-country", [("all", "All countries")] + [(c["value"], c["label"]) for c in countries], value="all",
                   cls="w-[180px]", menu_width="14rem"),
            select("repo-ptier", [("all", "All Policy Tiers")] + [(str(t["value"]), t["label"]) for t in C.TIERS], value="all",
                   cls="w-[200px]", menu_width="30rem"),
            select("repo-stier", [("all", "All Strategy Types")] + [(str(s["value"]), s["label"]) for s in C.STRATEGY_TYPES],
                   value="all", cls="w-[200px]", menu_width="22rem"),
            button(icon("x", "h-3.5 w-3.5 mr-1"), "Clear", id="repo-clear", variant="ghost", size="sm",
                   cls="h-9 text-muted-foreground", style={"display": "none"}),
        ], className="flex flex-col sm:flex-row gap-2 flex-1"),
        link(icon("plus", "h-4 w-4 mr-1.5"), "Add Record", href=href("record-new"), cls=button_class("default", "sm", "h-9")),
    ], className="flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between")

    dashboard = html.Div([
        html.Button([
            html.Span([icon("layout-dashboard", "h-3.5 w-3.5"), "Country Overview",
                       badge(str(len(summary)), variant="secondary", cls="text-[10px] font-normal ml-1", id="repo-dash-count")],
                      className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground"),
            html.Span([html.Span(icon("chevron-up", "h-4 w-4 text-muted-foreground"), id="repo-dash-up"),
                       html.Span(icon("chevron-down", "h-4 w-4 text-muted-foreground"), id="repo-dash-down", style={"display": "none"})]),
        ], id="repo-dash-toggle", type="button", n_clicks=0,
            className="flex items-center justify-between w-full px-4 py-2.5 text-left hover:bg-muted/40 transition-colors cursor-pointer"),
        html.Div(_dashboard_table(summary), id="repo-dash-body"),
    ], className="rounded-md border") if summary else html.Div(id="repo-dash-body")

    pager = html.Div([
        html.P(f"Page {page + 1} of {pages}", id="repo-page", className="text-sm text-muted-foreground"),
        html.Div([
            button(icon("chevron-left", "h-4 w-4"), id="repo-prev", variant="outline", size="sm", disabled=page <= 0),
            button(icon("chevron-right", "h-4 w-4"), id="repo-next", variant="outline", size="sm", disabled=page >= pages - 1),
        ], className="flex gap-2"),
    ], className="flex items-center justify-between")

    delete_dialog = dialog(
        "repo-del-dialog",
        dialog_header(dialog_title("Delete this record?"),
                      dialog_description("This will permanently delete the policy record and any uploaded document files. "
                                         "This action cannot be undone.")),
        dialog_footer(button("Cancel", id="repo-del-cancel", variant="outline"),
                      button("Delete Record", id="repo-del-confirm", variant="destructive")))

    return html.Div([
        html.Div([
            html.Div([
                html.H1("Country Policy Profile Repository", className="text-2xl font-bold tracking-tight"),
                html.P("A curated database of Public Investment Management policy and strategy documents across countries. "
                       "Each record references up to one English and one native-language version of the source document.",
                       className="mt-1 text-muted-foreground text-sm max-w-2xl"),
            ]),
            html.Div([
                recheck_links_widget(),
                link("⬆ Batch upload", href=href("batch"),
                     cls="inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-sm font-medium "
                         "transition-colors hover:bg-muted"),
            ], className="flex shrink-0 flex-wrap items-center gap-2"),
        ], className="flex flex-wrap items-start justify-between gap-3"),
        html.Div(_stats(summary, len(records)), id="repo-stats", className="grid grid-cols-2 sm:grid-cols-4 gap-4"),
        html.Div([
            filter_bar,
            dashboard,
            html.P(f"Showing {len(records)} of {len(records)} records", id="repo-count", className="text-sm text-muted-foreground"),
            html.Div(table(
                thead(tr(_sort_header("Country", "repo-sort-country"), th("Document Name", cls=TH),
                         _sort_header("Year", "repo-sort-year"), th("Tiers", cls=TH), th("Pages", cls=TH), th("", cls=TH),
                         cls="bg-muted/40")),
                tbody(*rows, id="repo-tbody")), className="rounded-md border"),
            pager,
            dcc.Store(id="repo-state", data={"sort": None, "page": page}),
            dcc.Store(id="repo-version"),
            dcc.Store(id="repo-del-target"),
            delete_dialog,
        ], className="space-y-4"),
    ], className="space-y-8")


# ── callbacks ────────────────────────────────────────────────────────────────
def register_callbacks(app):
    app.clientside_callback(
        "function(n){return window.dash_clientside.pim.toggleSection(n, 'repo-dash-body', 'repo-dash-up', 'repo-dash-down');}",
        Output("repo-dash-toggle", "title"), Input("repo-dash-toggle", "n_clicks"), prevent_initial_call=True)

    @app.callback(
        Output("repo-tbody", "children"), Output("repo-count", "children"), Output("repo-page", "children"),
        Output("repo-prev", "disabled"), Output("repo-next", "disabled"), Output("repo-state", "data"),
        Output("repo-clear", "style"), Output("repo-search-x", "style"),
        Input("repo-search", "value"), Input("repo-country", "value"), Input("repo-ptier", "value"), Input("repo-stier", "value"),
        Input("repo-sort-country", "n_clicks"), Input("repo-sort-year", "n_clicks"), Input("repo-prev", "n_clicks"),
        Input("repo-next", "n_clicks"), Input("repo-version", "data"), State("repo-state", "data"), prevent_initial_call=True)
    def render(search, country, ptier, stier, _sc, _sy, _prev, _next, _version, state):
        state = state or {"sort": None, "page": 0}
        trig = ctx.triggered_id
        sort, page = state.get("sort"), state.get("page", 0)
        if trig in ("repo-sort-country", "repo-sort-year"):
            col = "country" if trig == "repo-sort-country" else "year"
            sort = [col, "desc" if (sort and sort[0] == col and sort[1] == "asc") else "asc"]
            page = 0
        elif trig == "repo-prev":
            page -= 1
        elif trig == "repo-next":
            page += 1
        elif trig in ("repo-search", "repo-country", "repo-ptier", "repo-stier"):
            page = 0
        records, names = _all_records(), queries.country_name_map()
        filtered = _filter_sort(records, names, search, country, ptier, stier, sort)
        rows, page, pages = _render_table(filtered, names, page)
        active = (country not in (None, "all")) or (ptier not in (None, "all")) or (stier not in (None, "all")) or bool(search)
        return (rows, f"Showing {len(filtered)} of {len(records)} records", f"Page {page + 1} of {pages}",
                page <= 0, page >= pages - 1, {"sort": sort, "page": page},
                {"display": "inline-flex"} if active else {"display": "none"},
                {} if search else {"display": "none"})

    @app.callback(
        Output("repo-search", "value"), Output("repo-country", "value"), Output("repo-ptier", "value"),
        Output("repo-stier", "value"), Input("repo-clear", "n_clicks"), Input("repo-search-x", "n_clicks"),
        prevent_initial_call=True)
    def reset(n_clear, n_x):
        if ctx.triggered_id == "repo-clear" and n_clear:
            return "", "all", "all", "all"
        if ctx.triggered_id == "repo-search-x" and n_x:
            return "", no_update, no_update, no_update
        raise PreventUpdate

    # delete flow
    @app.callback(
        Output("repo-del-dialog", "style"), Output("repo-del-target", "data"),
        Input({"type": "repo-del", "id": ALL}, "n_clicks"), Input("repo-del-dialog-x", "n_clicks"),
        Input("repo-del-dialog-overlay", "n_clicks"), Input("repo-del-cancel", "n_clicks"), prevent_initial_call=True)
    def toggle_delete(_d, _x, _o, _c):
        trig = ctx.triggered_id
        if not trig or not ctx.triggered or not ctx.triggered[0].get("value"):
            raise PreventUpdate
        if isinstance(trig, dict):
            return DIALOG_SHOW, trig["id"]
        return DIALOG_HIDE, None

    @app.callback(
        Output("repo-del-dialog", "style", allow_duplicate=True), Output("repo-del-target", "data", allow_duplicate=True),
        Output("repo-version", "data"), Output("toast", "data", allow_duplicate=True),
        Input("repo-del-confirm", "n_clicks"), State("repo-del-target", "data"), prevent_initial_call=True)
    def confirm_delete(n, rid):
        if not n or not rid:
            raise PreventUpdate
        user, err = auth.write_guard()
        if err:
            return DIALOG_HIDE, None, no_update, toast("error", err)
        try:
            ok = documents.delete_record(rid)
        except Exception:  # noqa: BLE001
            return DIALOG_HIDE, None, no_update, toast("error", "Failed to delete record")
        if not ok:
            return DIALOG_HIDE, None, time.time(), toast("error", "Failed to delete record")
        log_audit(user, "delete", "record", rid)
        return DIALOG_HIDE, None, time.time(), toast("success", "Record deleted")

    @app.callback(Output("repo-stats", "children"), Output("repo-dash-body", "children"), Output("repo-dash-count", "children"),
                  Input("repo-version", "data"), prevent_initial_call=True)
    def refresh_static(_v):
        summary = queries.list_country_dashboard()
        return _stats(summary, len(_all_records())), _dashboard_table(summary), str(len(summary))

"""
PIM Policy Changes – port of `web/app/policy-changes/page.tsx` + `components/changes/PolicyChangesClient.tsx`.

`panel(params, embedded=False)` is shared with the Coach hub (where the original rendered the same client with
`embedded`: no page chrome, no URL sync).  Ids are prefixed `pc-`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from dash import Input, Output, State, ctx, dcc, html, no_update

from pages.pages_constants import (
    POLICY_CHANGES_TITLE as TITLE,
    POLICY_CHANGES_KIND_STYLE as KIND_STYLE,
    POLICY_CHANGES_TIER_DESC as TIER_DESC,
)
from utils import changes, export, queries
from utils.auth import client_ip
from utils.components import original_link_button, tier_badge
from utils.icons import icon
from utils.ratelimit import rate_limit
from utils.routes import href
from utils.toast import toast
from utils.ui import button, cn, link, select, spinner, text_input

_DATE_RX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SHOW, _HIDE = {"display": "inline-block"}, {"display": "none"}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def default_since() -> str:
    return f"{datetime.now(timezone.utc).year - 1}-01-01"


# ── report view ──────────────────────────────────────────────────────────────
def _kind_badge(kind: str):
    return html.Span(changes.CHANGE_KIND_LABEL[kind],
                     className=cn("inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
                                  KIND_STYLE[kind]))


def _item_row(it: dict):
    yr = changes.year_text(it)
    archived = it["recordStatus"] == "archived"
    succ = it["successor"]
    note = it["supersededByNote"]
    meta = [_kind_badge(k) for k in it["kinds"]]
    if it["instrumentDate"] or yr:
        meta.append(html.Span(changes.fmt_date(it["instrumentDate"]) if it["instrumentDate"] else yr,
                              title="Exact date from the source line" if it["datePrecision"] == "exact"
                              else "Year-level date", className="text-xs text-muted-foreground"))
    meta.append(tier_badge(it["strategyTier"], "strategy"))
    if it["jurisdictionLevel"] != "national" and it["jurisdictionName"]:
        meta.append(html.Span(it["jurisdictionName"],
                              className="inline-flex items-center rounded-full border border-emerald-200 bg-emerald-50 "
                                        "px-1.5 py-0.5 text-[10px] font-medium text-emerald-700"))
    if it["source"]:
        meta.append(html.Span(f"· {it['source']}", className="text-xs text-muted-foreground"))

    docs = ("English + original-language documents" if it["hasEnglish"] and it["hasOriginal"] else
            "English document" if it["hasEnglish"] else
            "Original-language document" if it["hasOriginal"] else "No document attached")
    updated = (f", updated {changes.fmt_date(it['updatedAt'])}"
               if it["updatedAt"][:10] != it["createdAt"][:10] else "")

    superseded_line = None
    if archived and (succ or note):
        if succ:
            superseded_line = html.P(["Superseded by ",
                                      link(succ["nameEng"], href=href("record", id=succ["id"]),
                                           cls="font-medium hover:underline"),
                                      f" — {note}" if note else ""], className="text-xs text-amber-800")
        else:
            superseded_line = html.P(note, className="text-xs text-amber-800")

    return html.Div([
        html.Div([
            link(it["nameEng"], href=href("record", id=it["id"]),
                 cls=cn("text-sm font-medium hover:underline", "text-muted-foreground" if archived else "")),
            html.P(it["nameOrig"], className="text-xs italic text-muted-foreground") if it["nameOrig"] else None,
            html.Div(meta, className="flex flex-wrap items-center gap-1.5 pt-0.5"),
            superseded_line,
            html.P(f"{docs} · added to profile {changes.fmt_date(it['createdAt'])}{updated}",
                   className="text-[11px] text-muted-foreground"),
        ], className="min-w-0 flex-1 space-y-1"),
        html.Div(original_link_button(it["link"], None, False), className="shrink-0 sm:pt-0.5"),
    ], className="flex flex-col gap-2 border-b px-3 py-3 last:border-b-0 sm:flex-row sm:items-start sm:justify-between")


def _tier_cards(r: dict):
    cards = []
    for t in (1, 2, 3, 4):
        g = next((x for x in r["groups"] if x["tier"] == t), None)
        n = len(g["items"]) if g else 0
        kinds = (" · ".join(f"{g['counts'][k]} {changes.CHANGE_KIND_LABEL[k].lower()}"
                            for k in changes.CHANGE_KINDS if g["counts"][k]) if g else "")
        cards.append(html.Div([
            html.Div([tier_badge(t, "policy"), html.Span(str(n), className="text-2xl font-bold")],
                     className="flex items-center justify-between"),
            html.P(TIER_DESC[t], className="mt-1 text-[11px] leading-tight text-muted-foreground"),
            html.P(kinds, className="mt-1 text-[11px] text-muted-foreground") if g else None,
        ], className="rounded-lg border p-3"))
    return html.Div(cards, className="grid grid-cols-2 gap-3 sm:grid-cols-4")


def report_view(r: dict):
    latest = r["latestUpdate"]
    sections = [
        html.Section([
            html.H3([tier_badge(g["tier"], "policy"), g["label"],
                      html.Span(f"({len(g['items'])})", className="text-sm font-normal text-muted-foreground")],
                    className="flex items-center gap-2 text-base font-semibold"),
            html.Div([_item_row(it) for it in g["items"]], className="rounded-lg border"),
        ], className="space-y-2 break-inside-avoid") for g in r["groups"]
    ]
    updates = [html.Li(f"{changes.fmt_date(u['from']) if u['from'] == u['to'] else changes.fmt_date(u['from']) + ' – ' + changes.fmt_date(u['to'])}"
                       f": {u['created']} added{', ' + str(u['updated']) + ' updated' if u['updated'] else ''}"
                       f"{', ' + str(u['archived']) + ' archived' if u['archived'] else ''}")
               for u in reversed(r["updates"])]

    def dl_btn(fmt: str, label: str, icon_name: str):
        return button(icon(icon_name, "h-3.5 w-3.5", id=f"pc-{fmt}-icon"),
                      html.Span(spinner("h-3.5 w-3.5"), id=f"pc-{fmt}-spin", style=_HIDE, className="contents"),
                      label, id=f"pc-{fmt}", variant="outline", size="sm", cls="gap-1.5")

    return html.Div([
        html.Div([
            html.Div([
                html.H2(changes.report_title(r), className="text-xl font-semibold"),
                html.P([f"Generated {changes.fmt_date(r['generatedAt'])} · profile last updated "
                        f"{changes.fmt_date(latest['to']) if latest else '—'} · ",
                        link("open the country profile", href=href("country", iso3=r["country"]["iso3"]),
                             cls="underline hover:text-foreground")],
                       className="mt-1 text-xs text-muted-foreground"),
            ]),
            html.Div([
                button(icon("file-text", "h-3.5 w-3.5"), "Markdown", id="pc-md", variant="outline", size="sm",
                       cls="gap-1.5"),
                dl_btn("docx", "Word", "file-down"),
                dl_btn("pdf", "PDF", "download"),
                button(icon("printer", "h-3.5 w-3.5"), "Print", id="pc-print", variant="outline", size="sm",
                       cls="gap-1.5"),
            ], className="flex flex-wrap gap-2 print:hidden"),
        ], className="flex flex-wrap items-start justify-between gap-3"),
        html.P(changes.summary_text(r), className="rounded-lg border bg-muted/30 p-4 text-sm leading-relaxed"),
        _tier_cards(r),
        html.P("No changes identified for this period.",
               className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground")
        if not r["groups"] else None,
        *sections,
        html.Section([html.H3("Profile updates", className="text-base font-semibold"),
                      html.Ul(updates, className="text-sm text-muted-foreground")], className="space-y-2"),
        html.Section([html.H3("Notes", className="text-sm font-semibold"),
                      html.Ul([html.Li(n) for n in r["notes"]],
                              className="list-disc space-y-1 pl-5 text-xs text-muted-foreground")],
                     className="space-y-1"),
    ], className="space-y-6 print:space-y-4")


# ── page ─────────────────────────────────────────────────────────────────────
def _run(iso3: str, mode: str, since: str) -> tuple[dict | None, str | None]:
    """(report, error) – the same failure messages as the original's fetch handler."""
    try:
        if mode == "since" and not (since and _DATE_RX.match(since) and "1990-01-01" <= since <= _today()):
            return None, "since must be a date between 1990-01-01 and today (YYYY-MM-DD)"
        r = changes.get_policy_changes(iso3, mode, since if mode == "since" else None)
    except Exception:  # noqa: BLE001
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).exception("policy changes failed for %s", iso3)
        return None, "Failed to check policy changes."
    if not r:
        return None, "No profile found for this country"
    return r, None


def panel(params: dict | None = None, embedded: bool = False):
    params = params or {}
    iso3 = (params.get("country") or "").upper()
    mode = params.get("mode") if params.get("mode") in ("latest", "since") else "latest"
    since = params.get("since") if params.get("since") and _DATE_RX.match(params["since"]) else default_since()
    countries = queries.list_countries()
    if iso3 and iso3 not in {c["iso3"] for c in countries}:
        iso3 = iso3 if re.fullmatch(r"[A-Z]{3}", iso3) else ""
    report, error = (_run(iso3, mode, since) if iso3 else (None, None))

    head = html.Div([
        html.H1([icon("history", "h-6 w-6 text-teal-600"), "PIM Policy Changes"],
                className="flex items-center gap-2 text-2xl font-bold tracking-tight"),
        html.P("Check what has changed in a country's public investment management framework — the laws and "
               "regulations that set the rules, the manuals and templates that say how they are applied, and the "
               "strategies and plans that decide what gets built — either since the latest profile update or since "
               "a date you choose. Results are broken down by policy-guidance tier.",
               className="mt-1 max-w-2xl text-sm text-muted-foreground"),
    ])

    form = html.Div([
        html.Div([
            html.Div([
                html.Label("Country", htmlFor="pc-country", className="mb-1 block text-sm font-medium"),
                select("pc-country", [(c["iso3"], c["name"]) for c in countries], value=iso3 or None,
                       placeholder="Choose a country…", cls="w-full", searchable=True),
            ], className="min-w-[220px] flex-1"),
            html.Div([
                html.Div("Report", className="mb-1 block text-sm font-medium"),
                html.Div([
                    dcc.RadioItems(
                        id="pc-mode", value=mode,
                        options=[{"label": "Latest profile update", "value": "latest"},
                                 {"label": "Changes since", "value": "since"}],
                        className="flex flex-col gap-1.5 text-sm sm:flex-row sm:gap-4",
                        labelClassName="inline-flex items-center gap-2", inputClassName="m-0"),
                    dcc.Input(id="pc-since", type="date", value=since, min="1990-01-01", max=_today(),
                              debounce=False,
                              className="rounded-md border border-[var(--border)] bg-[var(--background)] px-2 py-1 "
                                        "text-sm"),
                ], className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-2"),
            ], className="min-w-[280px]"),
            button(icon("refresh-cw", "h-4 w-4", id="pc-run-icon"),
                   html.Span(spinner("h-4 w-4"), id="pc-run-spin", style=_HIDE, className="contents"),
                   "Check changes", id="pc-run", disabled=not iso3, cls="gap-1.5"),
        ], className="flex flex-wrap items-end gap-4"),
        html.P(error, id="pc-error", className="text-sm text-red-600") if error else
        html.P("", id="pc-error", className="text-sm text-red-600", hidden=True),
    ], className="flex flex-col gap-4 rounded-lg border bg-muted/30 p-5")

    return html.Div([
        head, form,
        html.Div(report_view(report) if report else None, id="pc-report-wrap"),
        dcc.Store(id="pc-report", data=report),
        dcc.Store(id="pc-embedded", data=embedded),
        dcc.Store(id="pc-noop"),
        dcc.Download(id="pc-download"),
    ], className="mx-auto max-w-4xl px-4 py-8 space-y-6" if embedded else "mx-auto max-w-4xl space-y-6")


def layout(params=None):
    return panel(params, embedded=False)


# ── callbacks ────────────────────────────────────────────────────────────────
def register_callbacks(app) -> None:
    if getattr(app, "_pc_registered", False):
        return
    app._pc_registered = True

    app.clientside_callback("function(v){return !v;}", Output("pc-run", "disabled"), Input("pc-country", "value"))
    # picking a date switches the radio to "Changes since" (the original's onChange)
    app.clientside_callback("function(v){return v ? 'since' : window.dash_clientside.no_update;}",
                            Output("pc-mode", "value", allow_duplicate=True), Input("pc-since", "value"),
                            prevent_initial_call=True)
    app.clientside_callback("function(n){if(n){window.print();} return window.dash_clientside.no_update;}",
                            Output("pc-noop", "data", allow_duplicate=True), Input("pc-print", "n_clicks"),
                            prevent_initial_call=True)
    # keep the address bar in sync (history.replaceState – the original's deep link), not when embedded
    app.clientside_callback(
        """function(report, embedded, mode, since){
            if (embedded || !report) { return window.dash_clientside.no_update; }
            try {
                var u = new URL(window.location.href);
                u.searchParams.set('page', 'policy-changes');
                u.searchParams.set('country', report.country.iso3);
                u.searchParams.set('mode', report.mode);
                if (report.mode === 'since') { u.searchParams.set('since', report.window.from.slice(0, 10)); }
                else { u.searchParams.delete('since'); }
                window.history.replaceState(null, '', u.toString());
            } catch (e) {}
            return window.dash_clientside.no_update;
        }""",
        Output("pc-noop", "data"), Input("pc-report", "data"), State("pc-embedded", "data"),
        State("pc-mode", "value"), State("pc-since", "value"), prevent_initial_call=True)

    @app.callback(
        Output("pc-report-wrap", "children"), Output("pc-error", "children"), Output("pc-error", "hidden"),
        Output("pc-report", "data"),
        Input("pc-run", "n_clicks"), State("pc-country", "value"), State("pc-mode", "value"),
        State("pc-since", "value"), prevent_initial_call=True,
        running=[(Output("pc-run", "disabled"), True, False), (Output("pc-country", "disabled"), True, False),
                 (Output("pc-since", "disabled"), True, False),
                 (Output("pc-run-icon", "style"), _HIDE, _SHOW), (Output("pc-run-spin", "style"), _SHOW, _HIDE)])
    def run(n, iso3, mode, since):
        if not n or not iso3:
            return no_update, no_update, no_update, no_update
        report, error = _run(iso3, mode or "latest", since or default_since())
        if error:
            return None, error, False, None
        return report_view(report), "", True, report

    def _filename(report: dict, fmt: str) -> str:
        if fmt == "md":
            return changes.download_name(report, report["window"]["from"][:10])
        return f"PIM_Policy_Changes_{report['country']['iso3']}.{fmt}"

    @app.callback(Output("pc-download", "data"), Input("pc-md", "n_clicks"), State("pc-report", "data"),
                  State("url", "href"), prevent_initial_call=True)
    def download_markdown(n, report, url_href):
        if not n or not report:
            return no_update
        return dcc.send_string(changes.render_markdown(report, _app_base(url_href)), _filename(report, "md"),
                               type="text/markdown;charset=utf-8")

    for fmt in ("docx", "pdf"):
        def make(fmt=fmt):
            @app.callback(
                Output("pc-download", "data", allow_duplicate=True), Output("pc-error", "children", allow_duplicate=True),
                Output("pc-error", "hidden", allow_duplicate=True), Output("toast", "data", allow_duplicate=True),
                Input(f"pc-{fmt}", "n_clicks"), State("pc-report", "data"), State("url", "href"),
                prevent_initial_call=True,
                running=[(Output("pc-docx", "disabled"), True, False), (Output("pc-pdf", "disabled"), True, False),
                         (Output(f"pc-{fmt}-icon", "style"), _HIDE, _SHOW),
                         (Output(f"pc-{fmt}-spin", "style"), _SHOW, _HIDE)])
            def _export(n, report, url_href):
                if not n or not report:
                    return no_update, no_update, no_update, no_update
                ok, _retry = rate_limit(f"export:{client_ip()}", 10, 60)
                if not ok:
                    msg = "Too many export requests. Please try again in a minute, or download the Markdown."
                    return no_update, msg, False, toast("error", msg)
                try:
                    data, mime, _name = export.export(fmt, changes.render_markdown(report, _app_base(url_href)),
                                                      changes.report_title(report), "profile")
                except Exception:  # noqa: BLE001
                    import logging  # noqa: PLC0415

                    logging.getLogger(__name__).exception("policy changes export failed")
                    msg = "Export failed."
                    return no_update, msg, False, toast("error", msg)
                return dcc.send_bytes(data, _filename(report, fmt), type=mime), "", True, no_update
            return _export
        make()


def _app_base(url_href: str | None) -> str:
    """The app's root URL (scheme + host + Connect content path), taken from the browser's current location."""
    if not url_href:
        return ""
    return url_href.split("?", 1)[0].split("#", 1)[0].rstrip("/")

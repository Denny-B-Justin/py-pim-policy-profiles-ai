"""
Standard-briefing generator – port of `components/rag/briefing/BriefingPanel.tsx`.

country picker → generated Markdown report → Word/PDF export, plus the archived (cached) brief.  Used standalone
(`?page=country-profile`, `?page=country-transparency`) and as a tab of the Coach hub.

The original streamed tokens over SSE; the mAI gateway cannot stream, so generation runs in a background job
(`utils.briefings.start`) that publishes status lines while the page polls it.  The "AI Model" selector of the
original listed the backend's provider models; mAI exposes one configured endpoint, so the selector is omitted.
Ids are prefixed `bp-`; only one panel exists in the layout at a time.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from dash import Input, Output, State, dcc, html, no_update

from utils import briefings, db, export, jobs, queries
from utils.auth import client_ip, current_user
from utils.icons import icon
from utils.ratelimit import rate_limit
from utils.routes import href
from utils.ui import cn, link, loading_spinner, select

log = logging.getLogger(__name__)

ARCHIVE_DISCLAIMER = ("Archive briefs reflect the status of the policy repository at the time of writing, as well as "
                      "instructions to the AI at the time of writing.")
_HIDE = {"display": "none"}
_BTN_PRIMARY = ("inline-flex items-center gap-2 rounded-md bg-primary px-5 py-2 text-sm font-medium "
                "text-primary-foreground transition-colors hover:opacity-90 disabled:cursor-not-allowed "
                "disabled:opacity-50")
_BTN_OUTLINE_PRIMARY = ("inline-flex items-center gap-2 rounded-md border border-primary px-5 py-2 text-sm "
                        "font-medium text-primary transition-colors hover:bg-primary/10 "
                        "disabled:cursor-not-allowed disabled:opacity-50")
_BTN_WORD_SM = ("inline-flex items-center gap-2 rounded-md border border-primary px-3 py-1.5 text-xs font-medium "
                "text-primary transition-colors hover:bg-primary/10 disabled:opacity-50")
_BTN_PDF_SM = ("inline-flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-xs font-medium "
               "text-foreground transition-colors hover:bg-muted disabled:opacity-50")
_AMBER = "rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800"
_LABEL = "mb-1 block text-sm font-medium"


def scope_label(scope: str) -> str:
    if scope == "national":
        return "national framework only"
    if scope.startswith("sub:"):
        return scope[4:]
    return ""


def scoped_name(country_name: str, scope: str) -> str:
    """Display name for exports/titles: reflects the selected scope."""
    if scope.startswith("sub:"):
        return f"{scope[4:]} — {country_name}"
    if scope == "national":
        return f"{country_name} (National)"
    return country_name


def format_brief_date(iso: str) -> str:
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", ""))
    except ValueError:
        return ""
    return f"{d:%b} {d.day}, {d.year}"


def _entities(iso3: str) -> list[str]:
    rows = db.cached("jurisdictions", 60, queries.list_jurisdictions)
    return [j["name"] for j in rows if j["country"] == iso3]


def _scope_options(iso3: str | None) -> list[dict]:
    opts = [{"label": "Full profile (all levels)", "value": "all"}, {"label": "National only", "value": "national"}]
    return opts + [{"label": n, "value": f"sub:{n}"} for n in (_entities(iso3) if iso3 else [])]


# ── layout ───────────────────────────────────────────────────────────────────
def panel(key: str):
    spec = briefings.get_briefing(key)
    countries = [(c["iso3"], c["name"]) for c in queries.list_countries()]
    controls = [
        html.Div([html.Label("Select Country", htmlFor="bp-country", className=_LABEL),
                  select("bp-country", countries, value=None, placeholder="Choose a country…", cls="w-full",
                         searchable=True)], className="min-w-[200px] flex-1"),
    ]
    controls.append(html.Div([
        html.Label("Document Language", htmlFor="bp-lang", className=_LABEL),
        select("bp-lang", [("ENG", "English"), ("ORI", "Original Language")], value="ENG", cls="w-full")],
        className="min-w-[140px]", style=None if spec["lang_toggle"] else _HIDE))
    controls.append(html.Div([
        html.Label("Jurisdiction", htmlFor="bp-scope", className=_LABEL),
        select("bp-scope", _scope_options(None), value="all", cls="w-full", menu_width="16rem")],
        id="bp-scope-wrap", className="min-w-[200px]", style=_HIDE))
    controls.append(html.Button("Generate Briefing", id="bp-gen", type="button", n_clicks=0, disabled=True,
                                className=_BTN_PRIMARY))
    controls.append(html.Button([icon("download", "h-4 w-4"), "Word Briefing"], id="bp-one", type="button",
                                n_clicks=0, disabled=True,
                                title="Generate the briefing and download it as a Word document in one click",
                                className=_BTN_OUTLINE_PRIMARY))

    return html.Div([
        html.H2(spec["title"], className="mb-3 text-2xl font-bold"),
        html.P([f"{spec['description']} Draws on documents from the ",
                link("PIM Policy Repository", href=href("repository"), cls="underline hover:text-foreground"),
                " and the AI Coach's knowledge base."], className="mb-6 text-sm text-muted-foreground"),
        html.Div([
            html.Div(controls, className="flex flex-wrap items-end gap-4"),
            html.Div(id="bp-ori-note"),
            html.P(id="bp-status", className="text-sm text-muted-foreground", **{"aria-live": "polite"}, hidden=True),
        ], className="space-y-4 rounded-lg border border-border bg-muted/40 p-5"),
        html.Div(id="bp-archive"),
        html.Div(id="bp-error"),
        html.Div(id="bp-report-wrap"),
        dcc.Store(id="bp-key", data=key),
        dcc.Store(id="bp-report", data=None),
        dcc.Store(id="bp-cached", data=None),
        dcc.Store(id="bp-job", data=None),
        dcc.Store(id="bp-state", data={"running": False, "oneclick": False}),
        dcc.Store(id="bp-bump", data=0),
        dcc.Interval(id="bp-poll", interval=1200, disabled=True),
        dcc.Download(id="bp-download"),
    ], className="mx-auto max-w-4xl px-4 py-8")


def _alert(msg: str):
    return html.Div(msg, role="alert", className="mt-4 rounded-md border border-red-300 bg-red-50 p-3 text-sm "
                                                  "text-red-800")


def _archive_box(cached: dict, lang: str, scope: str):
    return html.Div([
        html.Div([
            html.Div([
                icon("archive", "h-4 w-4"),
                html.Span("Archived brief available", className="font-medium"),
                html.Span(f"— generated {format_brief_date(cached['generatedAt'])} · "
                          f"{'original language' if lang == 'ORI' else 'English'}"
                          f"{' · ' + scope_label(scope) if scope != 'all' else ''}", className="text-muted-foreground"),
            ], className="flex items-center gap-2 text-sm"),
            html.Div([
                html.Button("Download Word", id="bp-cdl-docx", type="button", n_clicks=0, className=_BTN_WORD_SM,
                            **{"aria-label": "Download archived brief as Word document"}),
                html.Button("Download PDF", id="bp-cdl-pdf", type="button", n_clicks=0, className=_BTN_PDF_SM,
                            **{"aria-label": "Download archived brief as PDF"}),
                html.Button("View", id="bp-view", type="button", n_clicks=0,
                            className="inline-flex items-center rounded-md px-3 py-1.5 text-xs font-medium "
                                      "text-muted-foreground transition-colors hover:text-foreground"),
            ], className="flex flex-wrap gap-2"),
        ], className="flex flex-wrap items-center justify-between gap-3"),
        html.P(ARCHIVE_DISCLAIMER, className="mt-3 text-xs italic text-muted-foreground"),
    ], className="mt-4 rounded-lg border border-border bg-muted/30 p-4")


def _report_view(content: str | None, running: bool):
    if not (content or running):
        return None
    buttons = None
    if content and not running:
        buttons = html.Div([
            html.Button("Download Word (.docx)", id="bp-dl-docx", type="button", n_clicks=0,
                        className=cn(_BTN_WORD_SM, "px-4 py-2 text-sm"), **{"aria-label": "Download as Word document"}),
            html.Button("Download PDF", id="bp-dl-pdf", type="button", n_clicks=0,
                        className=cn(_BTN_PDF_SM, "px-4 py-2 text-sm"), **{"aria-label": "Download as PDF"}),
        ], className="mb-4 flex gap-3")
    return html.Div([
        buttons,
        html.Div(html.Div([
            dcc.Markdown(content or "", className="md-prose"),
            html.Span(className="inline-block h-4 w-1 animate-pulse bg-primary") if running else None,
        ]), className="rounded-lg border border-border bg-background p-6 shadow-sm"),
    ], className="mt-8")


# ── callbacks ────────────────────────────────────────────────────────────────
def _safe_name(name: str) -> str:
    return re.sub(r"\s+", "_", re.sub(r"[^a-z0-9_\- ]", "", name, flags=re.I))


def _country_name(iso3: str | None) -> str:
    return queries.country_name_map().get(iso3 or "", iso3 or "")


def _do_export(fmt: str, content: str, key: str, iso3: str, scope: str):
    """→ (dcc download dict, error message)."""
    ok, _retry = rate_limit(f"export:{client_ip()}", 120, 3600)
    if not ok:
        return None, "Too many requests — please wait a moment and try again."
    spec = briefings.get_briefing(key)
    name = scoped_name(_country_name(iso3), scope or "all")
    try:
        data, mime, _fn = export.export(fmt, content, name, key)
    except Exception:  # noqa: BLE001
        log.exception("briefing export failed")
        return None, "Export failed. Please try again."
    return dcc.send_bytes(data, f"{spec['file_prefix']}_{_safe_name(name)}.{fmt}", type=mime), None


def register_callbacks(app) -> None:
    if getattr(app, "_bp_registered", False):
        return
    app._bp_registered = True

    @app.callback(
        Output("bp-scope-wrap", "style"), Output("bp-scope", "options"), Output("bp-scope", "value"),
        Output("bp-report", "data"), Output("bp-error", "children"),
        Input("bp-country", "value"), prevent_initial_call=True)
    def on_country(iso3):
        """A scope only makes sense for the country it was chosen for; the selector appears only for countries
        with sub-national records."""
        opts = _scope_options(iso3)
        return ({} if len(opts) > 2 else _HIDE), opts, "all", None, None

    @app.callback(
        Output("bp-archive", "children"), Output("bp-cached", "data"),
        Input("bp-country", "value"), Input("bp-lang", "value"), Input("bp-scope", "value"), Input("bp-bump", "data"),
        State("bp-key", "data"), State("bp-state", "data"))
    def lookup(iso3, lang, scope, _bump, key, state):
        """Latest archived (cached) version for this country + language + scope."""
        if not iso3 or (state or {}).get("running"):
            return None, None
        row = briefings.get_cached(key, iso3, lang or "ENG", scope or "all")
        if not row or not row.get("content"):
            return None, None
        cached = {"content": row["content"], "generatedAt": row["generated_at"].isoformat()}
        return _archive_box(cached, lang or "ENG", scope or "all"), cached

    @app.callback(Output("bp-ori-note", "children"), Input("bp-lang", "value"), State("bp-key", "data"))
    def ori_note(lang, key):
        if lang != "ORI" or not briefings.get_briefing(key)["lang_toggle"]:
            return None
        return html.Div("Original language mode — the briefing will be generated from non-English source documents "
                        "and the response will be in the document's original language.", className=_AMBER)

    @app.callback(
        Output("bp-gen", "disabled"), Output("bp-gen", "children"), Output("bp-one", "disabled"),
        Output("bp-one", "children"), Output("bp-country", "disabled"), Output("bp-lang", "disabled"),
        Output("bp-scope", "disabled"), Output("bp-status", "hidden"),
        Input("bp-country", "value"), Input("bp-state", "data"))
    def controls(iso3, state):
        running = bool((state or {}).get("running"))
        oneclick = bool((state or {}).get("oneclick"))
        gen = [loading_spinner(), "Generating…"] if running and not oneclick else "Generate Briefing"
        one = ([loading_spinner(), "Preparing Word…"] if running and oneclick
               else [icon("download", "h-4 w-4"), "Word Briefing"])
        off = not iso3 or running
        return off, gen, off, one, running, running, running, not running

    @app.callback(
        Output("bp-job", "data"), Output("bp-state", "data"), Output("bp-poll", "disabled"),
        Output("bp-report", "data", allow_duplicate=True), Output("bp-error", "children", allow_duplicate=True),
        Output("bp-status", "children"),
        Input("bp-gen", "n_clicks"), Input("bp-one", "n_clicks"),
        State("bp-country", "value"), State("bp-lang", "value"), State("bp-scope", "value"), State("bp-key", "data"),
        State("url", "href"), State("bp-state", "data"), prevent_initial_call=True)
    def start(n_gen, n_one, iso3, lang, scope, key, url_href, state):
        from dash import ctx  # noqa: PLC0415

        trig = ctx.triggered_id
        if not iso3 or (state or {}).get("running") or not ((n_gen if trig == "bp-gen" else n_one) or 0):
            return (no_update,) * 6
        lang, scope = lang or "ENG", scope or "all"
        if not briefings.valid_params(key, iso3, lang, scope):
            return None, no_update, True, None, _alert("Invalid parameters"), no_update
        ok, retry = rate_limit(f"briefing:{client_ip()}", 30, 3600)  # 30 briefings / hour / IP (briefing_limiter)
        if not ok:
            return None, no_update, True, None, _alert(f"Too many requests. Try again in {retry}s."), no_update
        base = (url_href or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")
        try:
            job_id = briefings.start(key, iso3, lang, scope, base)
        except Exception as exc:  # noqa: BLE001
            log.exception("briefing start failed")
            return None, no_update, True, None, _alert(str(exc) or "Generation failed. Please try again."), no_update
        job = {"id": job_id, "oneclick": trig == "bp-one", "key": key, "iso3": iso3, "lang": lang, "scope": scope}
        return job, {"running": True, "oneclick": job["oneclick"]}, False, None, None, ""

    @app.callback(
        Output("bp-state", "data", allow_duplicate=True), Output("bp-poll", "disabled", allow_duplicate=True),
        Output("bp-job", "data", allow_duplicate=True), Output("bp-report", "data", allow_duplicate=True),
        Output("bp-error", "children", allow_duplicate=True), Output("bp-status", "children", allow_duplicate=True),
        Output("bp-download", "data"), Output("bp-bump", "data"),
        Input("bp-poll", "n_intervals"), State("bp-job", "data"), State("bp-bump", "data"), prevent_initial_call=True)
    def poll(_n, job, bump):
        idle = {"running": False, "oneclick": False}
        if not job:
            return idle, True, None, no_update, no_update, no_update, no_update, no_update
        state = jobs.get(job["id"]) or {"status": "error", "error": "Generation failed. Please try again."}
        if state["status"] == "running":
            return no_update, no_update, no_update, no_update, no_update, state.get("status_text") or "", no_update, \
                no_update
        if state["status"] == "error":
            return idle, True, None, None, _alert(state.get("error") or "Generation failed. Please try again."), "", \
                no_update, no_update
        content = (state.get("result") or {}).get("content") or ""
        download, error = no_update, None
        if len(content) > briefings.MIN_CONTENT:
            # archive a clean, complete generation (best effort – needs a signed-in user, like POST /api/briefings)
            try:
                if current_user():
                    briefings.save_cached(job["key"], job["iso3"], job["lang"], job["scope"], content)
            except Exception:  # noqa: BLE001
                log.info("briefing cache not saved", exc_info=True)
        if job.get("oneclick") and content:
            download, error = _do_export("docx", content, job["key"], job["iso3"], job["scope"])
            error = _alert(error) if error else None
        return idle, True, None, content or None, error, "", (download or no_update), (bump or 0) + 1

    @app.callback(Output("bp-report-wrap", "children"), Input("bp-report", "data"), Input("bp-state", "data"))
    def render_report(content, state):
        return _report_view(content, bool((state or {}).get("running")))

    def _export_cb(fmt: str, button: str, cached: bool):
        suffix = "Download Word" if fmt == "docx" else "Download PDF"
        label_off = (suffix if cached else ("Download Word (.docx)" if fmt == "docx" else "Download PDF"))
        busy = [loading_spinner(), "Exporting…"] if not cached else loading_spinner()
        running = [(Output(button, "children"), busy, label_off)]
        running += [(Output(b, "disabled"), True, False)
                    for b in (("bp-cdl-docx", "bp-cdl-pdf") if cached else ("bp-dl-docx", "bp-dl-pdf"))]

        @app.callback(
            Output("bp-download", "data", allow_duplicate=True), Output("bp-error", "children", allow_duplicate=True),
            Input(button, "n_clicks"), State("bp-report", "data"), State("bp-cached", "data"),
            State("bp-country", "value"), State("bp-scope", "value"), State("bp-key", "data"),
            prevent_initial_call=True, running=running)
        def _cb(n, report, cache, iso3, scope, key):
            content = (cache or {}).get("content") if cached else report
            if not n or not content or not iso3:
                return no_update, no_update
            if cached:
                content = (f"_Archived brief — generated {format_brief_date(cache['generatedAt'])}. "
                           f"{ARCHIVE_DISCLAIMER}_\n\n") + content
            data, err = _do_export(fmt, content, key, iso3, scope or "all")
            return (data, None) if data else (no_update, _alert(err))

    for _fmt in ("docx", "pdf"):
        _export_cb(_fmt, f"bp-dl-{_fmt}", False)
        _export_cb(_fmt, f"bp-cdl-{_fmt}", True)

    @app.callback(Output("bp-report", "data", allow_duplicate=True), Output("bp-error", "children", allow_duplicate=True),
                  Input("bp-view", "n_clicks"), State("bp-cached", "data"), prevent_initial_call=True)
    def view_cached(n, cached):
        """Load the archived version into the reader without re-generating."""
        if not n or not cached:
            return no_update, no_update
        return cached["content"], None

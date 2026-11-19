"""RAG admin console – port of `web/app/raging/{page,RagingClient}.tsx`.

Original: index stats + three re-index buttons that stream progress over Server-Sent Events from the FastAPI
service, gated by a sign-in and an “Admin API Key” field.

Dash version
  * the re-index runs in a background job (`utils.jobs`) inside this app – there is no separate RAG service –
    and `dcc.Interval` polls it (same status line, progress bar, green summary and red error box);
  * the page requires a signed-in user (Connect SSO or a local account), like the original's redirect to
    `/auth/signin?callbackUrl=/raging`;
  * the “Admin API Key” field is rendered only when `RAG_ADMIN_KEY` is set (optional second factor).
Component ids are prefixed `raging-`.  The page is not linked from the nav (curators reach it by URL).
"""

from __future__ import annotations

import hmac
import logging

from dash import Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

import constants as C
from pages.pages_constants import RAGING_BUTTON_LABELS, RAGING_TITLE as TITLE
from utils import ai_search, auth, indexer, jobs, rag
from utils.audit import log_audit
from utils.routes import asset, href
from utils.ui import cn, loading_spinner

log = logging.getLogger(__name__)


CARD = "rounded-lg border bg-muted/40 p-5"
BTN = "inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors"
BTN_DISABLED = "disabled:opacity-50 disabled:cursor-not-allowed"
BUTTONS = {
    "all": (RAGING_BUTTON_LABELS["all"],
            cn(BTN, "bg-primary text-primary-foreground hover:opacity-90", BTN_DISABLED)),
    "eng": (RAGING_BUTTON_LABELS["eng"],
            cn(BTN, "border border-primary text-primary hover:bg-primary/10", BTN_DISABLED)),
    "ori": (RAGING_BUTTON_LABELS["ori"],
            cn(BTN, "border border-amber-500 text-amber-700 hover:bg-amber-50", BTN_DISABLED)),
}
FILTERS = tuple(RAGING_BUTTON_LABELS)


# ── pieces ──────────────────────────────────────────────────────────────────────────────
def _n(v) -> str:
    return f"{int(v):,}"


def index_view():
    """One line on the AI Search index: status, rows, last sync, and any access problem (from utils/ai_search)."""
    try:
        st = ai_search.status()
    except Exception:  # noqa: BLE001
        log.exception("raging index status failed")
        return None
    sync = st.get("sync") or {}
    rows = st.get("indexed_row_count")
    line = [html.Strong("Search index: "), st["index"], " · ",
            ("ready" if st.get("ready") else "not ready" if st.get("ready") is False else "status unknown"),
            f" · {int(rows):,} rows" if rows is not None else "",
            f" · last sync {sync['last_ok']}" if sync.get("last_ok") else "",
            "" if sync.get("sync_on_write") else " · automatic sync after uploads is off (PIM_AI_SEARCH_SYNC=0)"]
    problems = sync.get("problems") or []
    if not st.get("enabled"):
        problems = [{"operation": "configuration", "error": st.get("message"), "fix": ""}]
    return html.Div([
        html.P(line, className="text-sm text-muted-foreground"),
        *[html.Div([c for c in (html.Strong(f"Access problem ({p['operation']}): "), p.get("error") or "",
                                html.Br() if p.get("fix") else None, p.get("fix") or "") if c is not None],
                   className="mt-2 rounded-md border border-red-300 bg-red-50 p-3 text-xs text-red-800")
          for p in problems],
    ], className="mt-4")


def stats_view():
    try:
        s = rag.stats_detail(force=True)["summary"]
    except Exception:  # noqa: BLE001
        log.exception("raging stats failed")
        return html.P("Unable to load collection statistics.", className="text-sm text-muted-foreground")
    return html.Div([html.Div([
        html.Div([html.P("Last Updated", className="text-sm text-muted-foreground"),
                  html.P(s["last_updated"] or "—", className="mt-1 text-2xl font-semibold")], className=CARD),
        html.Div([html.P("Documents Indexed", className="text-sm text-muted-foreground"),
                  html.P(_n(s["total_documents"]), className="mt-1 text-2xl font-semibold"),
                  html.P(f"{_n(s['lang_eng_documents'])} English · {_n(s['lang_ori_documents'])} Original language",
                         className="mt-1 text-xs text-muted-foreground")], className=CARD),
        html.Div([html.P("Total Tokens", className="text-sm text-muted-foreground"),
                  html.P(_n(s["total_tokens"]) if s["total_tokens"] is not None else "—",
                         className="mt-1 text-2xl font-semibold")], className=CARD),
    ], className="grid grid-cols-1 gap-4 sm:grid-cols-3"), index_view()])


def button_children(key: str, active: str | None):
    label = BUTTONS[key][0]
    if active == key:
        return [loading_spinner(), "Re-indexing…"]
    return label


def status_view(job: dict | None, error: str | None = None):
    """The area under the buttons: phase text, progress bar, completion summary, error box."""
    job = job or {}
    result = job.get("result") if job.get("status") == "done" else None
    err = error or (job.get("error") if job.get("status") == "error" else None)
    progress, text = job.get("progress"), job.get("status_text")
    parts = []
    if text and not result:
        parts.append(html.P(text, className="text-sm text-muted-foreground"))
    if progress:
        pct = (progress["current"] / progress["total"] * 100) if progress.get("total") else 0
        parts.append(html.Div([
            html.Div([html.Span(progress["document"], className="truncate max-w-[70%]"),
                      html.Span(f"{progress['current']} / {progress['total']}", className="tabular-nums")],
                     className="flex justify-between text-xs text-muted-foreground mb-1"),
            html.Div(html.Div(className="h-2 rounded-full bg-primary transition-all duration-300",
                              style={"width": f"{pct}%"}), className="h-2 w-full rounded-full bg-border"),
            html.P(f"Error: {progress['error']}", className="mt-1 text-xs text-red-600")
            if progress.get("status") == "error" and progress.get("error") else None,
        ]))
    if result:
        if result.get("message"):
            body = html.P(result["message"])
        else:
            body = html.P(["Re-index complete: ", html.Strong(str(result["succeeded"])), " succeeded",
                           [", ", html.Strong(str(result["failed"])), " failed"] if result["failed"] else None,
                           ". ", html.Strong(_n(result["total_chunks"])), " total chunks created."])
        parts.append(html.Div(body, className="rounded-md border border-green-300 bg-green-50 p-3 text-sm text-green-800"))
    if err:
        parts.append(html.Div(err, className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800"))
    return html.Div([p for p in parts if p is not None], className="space-y-3 pt-2")


def _key_required() -> bool:
    return bool(C.RAG_ADMIN_KEY)


# ── page ────────────────────────────────────────────────────────────────────────────────
def layout(params=None):
    if not auth.current_user():
        # Original: redirect("/auth/signin?callbackUrl=/raging") – done by the `gate` callback below.
        return html.Div([
            dcc.Interval(id="raging-gate", interval=150, max_intervals=1),
            html.P("Redirecting to sign in…", className="text-sm text-muted-foreground"),
        ], className="mx-auto max-w-3xl px-4 py-16")

    disabled = _key_required()   # until a key is typed
    return html.Div([
        dcc.Store(id="raging-job", data=None),
        dcc.Interval(id="raging-poll", interval=1000, disabled=True),
        html.H2("Retrieval-Augmented Generation (RAG)", className="mb-6 text-3xl font-bold"),
        html.P([
            "Retrieval-augmented generation (RAG) is a technique that enables large language models (LLMs) to "
            "retrieve and incorporate new information from external data sources. The AI Coach implements RAG "
            "workflows from the documents stored and updated in the PIM Country Policy Profiles Repository "
            "Application. These documents are maintained in both source/original language, as well as English "
            "translations (if this is not the source language). Implementing Retrieval-Augmented Generation (RAG) "
            "involves three core steps: ", html.Strong("indexing"), " (preparing data), ", html.Strong("retrieval"),
            " (finding relevant context), and ", html.Strong("generation"),
            " (producing the answer). Key actions include data ingestion, chunking, embedding, vector storage, query "
            "processing, and prompt augmentation for an LLM. This prototype application implements these different "
            "steps in ways that try to cater to different language contexts, as well as the particular structure of "
            "policy documents (including law).",
        ], className="mb-4 text-muted-foreground"),
        html.Div(html.Img(
            src=asset("rag-diagram.svg"), width="960", height="540", className="w-full h-auto",
            alt="Working Across Languages for AI Retrieval Augmented Generation — diagram showing English and "
                "Non-English source data flowing to English and Non-English responses"),
            className="my-8 overflow-hidden rounded-lg border"),

        # ── Input Updates ───────────────────────────────────────
        html.H3("Input Updates", className="mb-4 mt-12 text-2xl font-bold"),
        html.P("The default RAG index uses English documents. Use “Add Original Language Docs” to append non-English "
               "documents for country briefs in the original language.", className="mb-6 text-muted-foreground"),
        html.Div(stats_view(), id="raging-stats"),

        # ── Re-Index Controls ───────────────────────────────────
        html.H3("Re-Index Controls", className="mb-4 mt-12 text-2xl font-bold"),
        html.P([
            "Trigger a re-index of documents from the ",
            html.A("PIM Policy Repository", href=href("repository"), className="underline hover:text-foreground"),
            ". Every matching document is re-chunked in place (search keeps working meanwhile) and the AI Search "
            "index is then asked to sync. "
            + ("An admin API key is required." if _key_required() else "You must be signed in."),
        ], className="mb-4 text-sm text-muted-foreground"),
        html.Div([
            html.Div([
                html.Label("Admin API Key", htmlFor="raging-key", className="block text-sm font-medium mb-1"),
                dcc.Input(id="raging-key", type="password", value="", debounce=False, placeholder="Enter your API key",
                          className="w-full rounded-md border bg-background px-3 py-2 text-sm focus:outline-none "
                                    "focus:ring-2 focus:ring-primary/40"),
            ], style=None if _key_required() else {"display": "none"}),
            html.Div([
                html.Button(BUTTONS[k][0], id=f"raging-{k}", n_clicks=0, disabled=disabled, className=BUTTONS[k][1])
                for k in FILTERS
            ], className="flex flex-wrap gap-3"),
            html.Div(id="raging-status"),
        ], className=cn(CARD, "space-y-4")),
    ], className="mx-auto max-w-3xl px-4 py-16")


# ── callbacks ───────────────────────────────────────────────────────────────────────────
def _run(job_id: str, lang: str, clear: bool):
    return indexer.reindex(lang, clear,
                           status=lambda m: jobs.update(job_id, status_text=m),
                           progress=lambda p: jobs.update(job_id, progress=p))


def register_callbacks(app):
    @app.callback(Output("url", "search", allow_duplicate=True), Input("raging-gate", "n_intervals"),
                  prevent_initial_call=True)
    def gate(n):
        if not n:
            raise PreventUpdate
        return href("signin", callbackUrl=href("raging"))

    outputs = [
        Output("raging-job", "data"), Output("raging-poll", "disabled"), Output("raging-status", "children"),
        *[Output(f"raging-{k}", "children") for k in FILTERS],
        *[Output(f"raging-{k}", "disabled") for k in FILTERS],
        Output("raging-stats", "children"),
    ]

    def _ui(job, poll_disabled, status, active, disabled, stats=no_update):
        return (job, poll_disabled, status, *[button_children(k, active) for k in FILTERS],
                *[disabled] * 3, stats)

    @app.callback(
        *outputs,
        Input("raging-all", "n_clicks"), Input("raging-eng", "n_clicks"), Input("raging-ori", "n_clicks"),
        Input("raging-poll", "n_intervals"), Input("raging-key", "value"),
        State("raging-job", "data"), prevent_initial_call=True,
    )
    def drive(_a, _e, _o, _tick, key, job_state):
        trig = ctx.triggered_id
        running = bool(job_state)
        no_key = _key_required() and not (key or "")

        if trig == "raging-key":                       # typing the key only toggles the buttons
            if running:
                raise PreventUpdate
            n = [no_update] * 3
            return (no_update, no_update, no_update, *n, *[no_key] * 3, no_update)

        if trig in ("raging-all", "raging-eng", "raging-ori"):
            if not ctx.triggered or not ctx.triggered[0].get("value") or running:
                raise PreventUpdate
            lang = trig.split("-", 1)[1]
            user, err = auth.write_guard()
            if err:
                return (no_update, no_update, status_view(None, err), *[no_update] * 6, no_update)
            if _key_required() and not hmac.compare_digest((key or "").encode(), C.RAG_ADMIN_KEY.encode()):
                return (no_update, no_update, status_view(None, "Invalid or missing API key."), *[no_update] * 6,
                        no_update)
            if indexer.reindex_running():
                return (no_update, no_update, status_view(None, "A reindex is already running."), *[no_update] * 6,
                        no_update)
            clear = lang != "ori"                      # ORI appends to the existing index; ENG/all clear first
            job_id = jobs.submit("reindex", _run, lang, clear)
            log_audit(user, "reindex", "rag_index", None, f"lang={lang} clear={clear}", auth.client_ip())
            return _ui({"id": job_id, "filter": lang}, False, status_view({"status": "running"}), lang, True)

        # poll tick
        if not job_state:
            raise PreventUpdate
        job = jobs.get(job_state["id"])
        if job is None:                                # job file expired / lost
            return _ui(None, True, status_view(None, "Connection lost. The re-index may still be running on the server."),
                       None, no_key, rag_stats_safe())
        if job["status"] == "running":
            return _ui(no_update, no_update, status_view(job), job_state["filter"], True)
        return _ui(None, True, status_view(job), None, no_key, rag_stats_safe())


def rag_stats_safe():
    return stats_view()

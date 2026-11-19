"""RAG admin console – port of `web/app/raging/{page,RagingClient}.tsx`.

Shows the chunks-table statistics and the AI Search index status.  There are no re-index controls: documents are
chunked once, from the uploaded bytes, when they are uploaded (utils/documents.py), and the app never reads files
back from the volume.  The page requires a signed-in user (Connect SSO or a local account).
Component ids are prefixed `raging-`.  The page is not linked from the nav (curators reach it by URL).
"""

from __future__ import annotations

import logging

from dash import Input, Output, dcc, html
from dash.exceptions import PreventUpdate

from pages.pages_constants import RAGING_TITLE as TITLE
from utils import ai_search, auth, rag
from utils.routes import asset, href

log = logging.getLogger(__name__)

CARD = "rounded-lg border bg-muted/40 p-5"


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


# ── page ────────────────────────────────────────────────────────────────────────────────
def layout(params=None):
    if not auth.current_user():
        # Original: redirect("/auth/signin?callbackUrl=/raging") – done by the `gate` callback below.
        return html.Div([
            dcc.Interval(id="raging-gate", interval=150, max_intervals=1),
            html.P("Redirecting to sign in…", className="text-sm text-muted-foreground"),
        ], className="mx-auto max-w-3xl px-4 py-16")

    return html.Div([
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
        html.P("Every uploaded document (English and original language) is chunked when it is uploaded; the chunks "
               "table is the source of the AI Search index, which is synced after each upload.",
               className="mb-6 text-muted-foreground"),
        html.Div(stats_view(), id="raging-stats"),
    ], className="mx-auto max-w-3xl px-4 py-16")


# ── callbacks ───────────────────────────────────────────────────────────────────────────
def register_callbacks(app):
    @app.callback(Output("url", "search", allow_duplicate=True), Input("raging-gate", "n_intervals"),
                  prevent_initial_call=True)
    def gate(n):
        if not n:
            raise PreventUpdate
        return href("signin", callbackUrl=href("raging"))

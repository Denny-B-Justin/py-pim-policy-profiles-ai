"""
AI Coach chat – port of `components/rag/chat/{ChatInterface,ChatMessage,SourceCards}.tsx` and
`components/rag/filters/MetadataFilters.tsx`.

The original streamed tokens over SSE.  The mAI gateway cannot stream, so a question starts a background job
(`utils.chat.start`) and `dcc.Interval` polls it; the spinner line is shown until the whole answer arrives.
Component ids are prefixed `chat-`.
"""

from __future__ import annotations

import logging
import time

from dash import Input, Output, State, dcc, html, no_update

from utils import chat, jobs, queries
from utils.auth import client_ip
from utils.icons import icon
from utils.ratelimit import rate_limit
from utils.ui import cn, select, spinner

log = logging.getLogger(__name__)

TIER_LABELS = {1: "Legislation", 2: "Regulation", 3: "Guidelines", 4: "Strategy"}
EXAMPLES = [
    "What are best practices for project appraisal?",
    "Compare PIM frameworks in Colombia and Kenya",
    "What legislation governs public investment in Lithuania?",
]
PLACEHOLDER = "Ask about PIM policies, regulations, or best practices..."
PLACEHOLDER_ORI = "Type your question in the original language..."
WORKING = "Searching policies and generating answer..."
GENERIC_ERROR = "Sorry, I encountered an error processing your question. Please try again."
AMBER = "rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800"
BETA_NOTE = ("For credible results, ensure that the relevant documents for your Country Profile for your National "
             "and/or sub-national have been fully curated and validated. This function is in Beta, and depends on "
             "human validation of pertinent input data for the AI.")
_HIDE = {"display": "none"}
_N = {"flex": "0 0 auto"}


def _msg_id() -> str:
    return f"msg-{int(time.time() * 1000)}-{id(object()) % 10000}"


# ── message + sources ────────────────────────────────────────────────────────
def chat_message(m: dict):
    is_user = m["role"] == "user"
    avatar_bot = html.Div(icon("bot", "h-4 w-4"),
                          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary "
                                    "text-primary-foreground")
    avatar_user = html.Div(icon("user", "h-4 w-4"),
                           className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted")
    body = (html.Div(m["content"], className="whitespace-pre-wrap") if is_user else
            dcc.Markdown(m["content"], className="md-prose md-chat"))
    bubble = html.Div(body, className=cn("max-w-[80%] rounded-lg px-4 py-2 text-sm leading-relaxed",
                                         "bg-primary text-primary-foreground" if is_user else "bg-muted"))
    parts = [bubble, avatar_user] if is_user else [avatar_bot, bubble]
    return html.Div(parts, className=cn("flex gap-3 py-4", "justify-end" if is_user else ""))


def source_cards(sources: list[dict]):
    if not sources:
        return None
    cards = []
    for s in sources:
        meta = s.get("metadata") or {}
        tier = meta.get("policy_guidance_tier")
        title = (meta.get("country_name") or meta.get("country") or "Unknown") + (
            f" ({meta['year']})" if meta.get("year") else "")
        cards.append(html.Div([
            html.Div([icon("file-text", "h-3.5 w-3.5 text-primary"),
                      html.Span(title, className="truncate text-xs font-medium")],
                     className="mb-1 flex items-center gap-1.5"),
            html.P(meta["name_eng"], className="mb-1 truncate text-xs text-muted-foreground")
            if meta.get("name_eng") else None,
            html.Span(TIER_LABELS.get(tier) or f"Tier {tier}",
                      className="inline-block rounded-full bg-accent px-2 py-0.5 text-[10px]") if tier else None,
        ], className="shrink-0 rounded-lg border border-border bg-background p-3", style={"maxWidth": "260px"}))
    return html.Div([
        html.P(f"Sources ({len(sources)})", className="mb-2 text-xs font-medium text-muted-foreground"),
        html.Div(cards, className="flex gap-2 overflow-x-auto pb-1"),
    ])


def _welcome():
    return html.Div([
        html.P("Welcome to PIM AI Coach", className="mb-2 text-lg font-medium"),
        html.P("Ask a question about public investment management policies, regulations, or best practices.",
               className="text-sm"),
        html.Div([html.Button(q, type="button", **{"data-chat-q": q},
                              className="rounded-full border border-border px-3 py-1.5 text-xs hover:bg-muted")
                  for q in EXAMPLES], className="mt-6 flex flex-wrap justify-center gap-2"),
    ], className="py-20 text-center text-muted-foreground")


# ── layout ───────────────────────────────────────────────────────────────────
def _filter_label(text: str, control):
    return html.Label([html.Span(text, className="mb-1 block text-xs text-muted-foreground"), control],
                      className="block")


def _filters_panel():
    countries = [("", "All countries")] + [(c["iso3"], c["name"]) for c in queries.list_countries()]
    num = "w-20 rounded border border-border bg-background px-2 py-1 text-xs"
    return html.Div(html.Div([
        _filter_label("Country", select("chat-f-country", countries, value="", size="sm", cls="w-44",
                                        menu_width="14rem", searchable=True)),
        _filter_label("Policy Tier", select("chat-f-tier", [("", "All"), ("1", "1 — Legislation"),
                                                           ("2", "2 — Regulations"), ("3", "3 — Guidelines"),
                                                           ("4", "4 — Strategy")], value="", size="sm", cls="w-36",
                                           menu_width="11rem")),
        _filter_label("Language", select("chat-f-lang", [("", "All"), ("ENG", "English"), ("ORI", "Original")],
                                         value="", size="sm", cls="w-28")),
        _filter_label("Year from", dcc.Input(id="chat-f-yfrom", type="number", placeholder="2000", debounce=True,
                                            className=num)),
        _filter_label("Year to", dcc.Input(id="chat-f-yto", type="number", placeholder="2026", debounce=True,
                                          className=num)),
        html.Button("Clear", id="chat-f-clear", type="button",
                    className="rounded border border-border px-2 py-1 text-xs text-muted-foreground hover:bg-muted"),
    ], className="mx-auto flex max-w-3xl flex-wrap items-end gap-3"),
        id="chat-filters-panel", className="border-t border-border px-4 py-3", style=_HIDE)


def layout():
    return html.Div([
        html.Div(html.Div([html.Div(_welcome(), id="chat-list"), html.Div(id="chat-end")],
                          className="mx-auto max-w-3xl"),
                 className="flex-1 overflow-y-auto px-4 py-6"),
        html.Div(id="chat-sources-wrap"),
        _filters_panel(),
        html.Div(id="chat-ori-wrap"),
        html.Div([
            html.Div([
                html.Button(icon("rotate-ccw", "h-4 w-4"), id="chat-new", type="button", title="New chat",
                            **{"aria-label": "New chat"}, style=_HIDE,
                            className="rounded-lg border border-border p-2 text-muted-foreground hover:bg-muted "
                                      "hover:text-foreground transition-colors"),
                html.Button("Filters", id="chat-filter-btn", type="button", n_clicks=0,
                            **{"aria-label": "Toggle metadata filters"},
                            className="rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground "
                                      "hover:bg-muted"),
                dcc.Input(id="chat-input", type="text", value="", placeholder=PLACEHOLDER, debounce=False,
                          autoComplete="off", n_submit=0,
                          className="flex-1 rounded-lg border border-border bg-background px-4 py-2 text-sm "
                                    "outline-none focus:border-primary disabled:opacity-50"),
                html.Button(icon("send", "h-4 w-4"), id="chat-send", type="button", disabled=True,
                            **{"aria-label": "Send message"},
                            className="rounded-lg bg-primary p-2 text-primary-foreground hover:opacity-90 "
                                      "disabled:opacity-50 disabled:cursor-not-allowed"),
            ], className="mx-auto flex max-w-3xl items-center gap-2"),
            html.Div(BETA_NOTE, role="alert", className=cn("mx-auto mt-2 max-w-3xl", AMBER)),
        ], className="border-t border-border px-4 py-3"),
        dcc.Store(id="chat-msgs", data=[]),
        dcc.Store(id="chat-sources", data=[]),
        dcc.Store(id="chat-filters", data={}),
        dcc.Store(id="chat-job", data=None),
        dcc.Interval(id="chat-poll", interval=1000, disabled=True),
    ], className="flex h-full flex-col")


# ── callbacks ────────────────────────────────────────────────────────────────
def _active_filters(f: dict | None) -> dict | None:
    out = {k: v for k, v in (f or {}).items() if v not in (None, "")}
    return out or None


def register_callbacks(app) -> None:
    if getattr(app, "_chat_registered", False):
        return
    app._chat_registered = True

    # send enabled only with text and no pending job; input locked while the answer is being generated
    app.clientside_callback("function(v, job){return !(v && v.trim()) || !!job;}",
                            Output("chat-send", "disabled"), Input("chat-input", "value"), Input("chat-job", "data"))
    app.clientside_callback("function(job){return !!job;}", Output("chat-input", "disabled"), Input("chat-job", "data"))
    app.clientside_callback("function(n){return (n % 2) ? {} : {display:'none'};}",
                            Output("chat-filters-panel", "style"), Input("chat-filter-btn", "n_clicks"))
    app.clientside_callback("function(a, b){return window.dash_clientside.pimChat.scrollEnd();}",
                            Output("chat-end", "title"), Input("chat-msgs", "data"), Input("chat-job", "data"),
                            prevent_initial_call=True)

    @app.callback(
        Output("chat-msgs", "data"), Output("chat-input", "value"), Output("chat-sources", "data"),
        Output("chat-job", "data"), Output("chat-poll", "disabled"),
        Input("chat-send", "n_clicks"), Input("chat-input", "n_submit"),
        State("chat-input", "value"), State("chat-msgs", "data"), State("chat-filters", "data"),
        State("chat-job", "data"), prevent_initial_call=True)
    def submit(_n, _s, text, msgs, filters, job):
        question = (text or "").strip()
        if not question or job:
            return no_update, no_update, no_update, no_update, no_update
        msgs = list(msgs or [])
        history = [{"role": m["role"], "content": m["content"]} for m in msgs]
        msgs.append({"id": _msg_id(), "role": "user", "content": question})
        allowed, retry = rate_limit(f"chat:{client_ip()}", 30, 60)
        if not allowed:
            msgs.append({"id": _msg_id(), "role": "assistant",
                         "content": f"Too many requests. Please try again in {retry}s."})
            return msgs, question, [], None, True
        try:
            job_id = chat.start(question, history, _active_filters(filters))
        except Exception as exc:  # noqa: BLE001
            log.exception("chat start failed")
            msgs.append({"id": _msg_id(), "role": "assistant", "content": str(exc) or GENERIC_ERROR})
            return msgs, question, [], None, True
        return msgs, "", [], {"id": job_id, "q": question}, False

    @app.callback(
        Output("chat-msgs", "data", allow_duplicate=True), Output("chat-sources", "data", allow_duplicate=True),
        Output("chat-job", "data", allow_duplicate=True), Output("chat-poll", "disabled", allow_duplicate=True),
        Output("chat-input", "value", allow_duplicate=True),
        Input("chat-poll", "n_intervals"), State("chat-job", "data"), State("chat-msgs", "data"),
        State("chat-input", "value"), prevent_initial_call=True)
    def poll(_n, job, msgs, current_input):
        if not job:
            return no_update, no_update, None, True, no_update
        state = jobs.get(job["id"])
        if state is None:
            state = {"status": "error", "error": GENERIC_ERROR}
        if state["status"] == "running":
            return no_update, no_update, no_update, no_update, no_update
        msgs = list(msgs or [])
        if state["status"] == "done":
            res = state.get("result") or {}
            msgs.append({"id": _msg_id(), "role": "assistant", "content": res.get("answer") or ""})
            return msgs, res.get("sources") or [], None, True, no_update
        text = state.get("error") or GENERIC_ERROR
        msgs.append({"id": _msg_id(), "role": "assistant", "content": text})
        # restore the question so the user can retry without retyping
        return msgs, [], None, True, (current_input or job.get("q") or "")

    @app.callback(Output("chat-list", "children"), Output("chat-new", "style"),
                  Input("chat-msgs", "data"), Input("chat-job", "data"))
    def render(msgs, job):
        msgs = msgs or []
        kids = [chat_message(m) for m in msgs] if msgs else [_welcome()]
        if job:
            kids.append(html.Div([spinner("h-4 w-4"), WORKING], **{"aria-live": "polite"},
                                 className="flex items-center gap-2 py-4 text-sm text-muted-foreground"))
        return kids, ({} if msgs else _HIDE)

    @app.callback(Output("chat-sources-wrap", "children"), Input("chat-sources", "data"))
    def render_sources(sources):
        if not sources:
            return None
        return html.Div(html.Div(source_cards(sources), className="mx-auto max-w-3xl"),
                        className="border-t border-border bg-muted px-4 py-3")

    @app.callback(Output("chat-msgs", "data", allow_duplicate=True), Output("chat-sources", "data", allow_duplicate=True),
                  Output("chat-input", "value", allow_duplicate=True), Output("chat-job", "data", allow_duplicate=True),
                  Output("chat-poll", "disabled", allow_duplicate=True),
                  Input("chat-new", "n_clicks"), prevent_initial_call=True)
    def new_chat(n):
        if not n:
            return (no_update,) * 5
        return [], [], "", None, True

    @app.callback(Output("chat-f-country", "value"), Output("chat-f-tier", "value"), Output("chat-f-lang", "value"),
                  Output("chat-f-yfrom", "value"), Output("chat-f-yto", "value"),
                  Input("chat-f-clear", "n_clicks"), prevent_initial_call=True)
    def clear_filters(n):
        return ("", "", "", None, None) if n else (no_update,) * 5

    @app.callback(Output("chat-filters", "data"),
                  Input("chat-f-country", "value"), Input("chat-f-tier", "value"), Input("chat-f-lang", "value"),
                  Input("chat-f-yfrom", "value"), Input("chat-f-yto", "value"))
    def collect_filters(country, tier, lang, yfrom, yto):
        f: dict = {}
        if country:
            f["country"] = country
        if tier:
            f["policy_guidance_tier"] = int(tier)
        if lang:
            f["lang_type"] = lang
        if yfrom:
            f["year_from"] = int(yfrom)
        if yto:
            f["year_to"] = int(yto)
        return f

    @app.callback(Output("chat-filter-btn", "children"), Output("chat-input", "placeholder"),
                  Output("chat-ori-wrap", "children"), Input("chat-filters", "data"))
    def filter_labels(f):
        f = f or {}
        n = len([v for v in f.values() if v])
        ori = f.get("lang_type") == "ORI"
        note = html.Div(html.Div("Original language mode — Please enter your question in the document's original "
                                 "language. Responses will be generated in the same language as the source "
                                 "documents.", className=cn("mx-auto max-w-3xl", AMBER)),
                        className="border-t border-border px-4 py-2") if ori else None
        return f"Filters ({n})" if n else "Filters", (PLACEHOLDER_ORI if ori else PLACEHOLDER), note

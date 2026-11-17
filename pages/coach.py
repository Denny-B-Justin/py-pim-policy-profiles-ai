"""
Coach hub – port of `web/app/coach/page.tsx` + `components/rag/CoachHub.tsx`.

A menu of standard briefings (country profile, transparency brief), the Policy Changes report and the free-form
"Ask the Coach" chatbot on one page.  "Ask the Coach" is the default tab; `?page=coach&tab=<key>` opens another one.
Switching a tab re-renders its content (the original unmounts it too, so each tab starts fresh).
"""

from __future__ import annotations

from dash import ALL, Input, Output, ctx, html, no_update

from pages import policy_changes
from pages.pages_constants import (
    COACH_TITLE as TITLE,
    COACH_SOON_BADGE,
    COACH_SOON_TITLE,
    COACH_SOON_TEXT,
    COACH_SOON_FEATURES,
    COACH_SOON_MEANWHILE,
    COACH_SOON_LINKS,
)
from utils import briefing_panel, briefings, chat_ui, mai_client
from utils.icons import icon
from utils.routes import href
from utils.ui import button_class, cn, link

# Menu order of the original: the briefing registry, then Policy Changes, then the chatbot.
_ICONS = {"profile": "file-text", "transparency": "shield-check"}
TABS = [(b["key"], b["label"], _ICONS.get(b["key"], "file-text")) for b in briefings.BRIEFING_LIST] + [
    ("changes", "Policy Changes", "history"), ("chat", "Ask the Coach", "messages-square")]
_TAB_BASE = ("-mb-px inline-flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-3 text-sm font-medium "
             "transition-colors")


def _tab_class(active: bool) -> str:
    return cn(_TAB_BASE, "border-primary text-foreground" if active else
              "border-transparent text-muted-foreground hover:text-foreground")


def coming_soon():
    """Placeholder for the AI features while the mAI gateway is not configured (also used by the standalone
    country-profile / country-transparency pages)."""
    features = [html.Div([
        html.Span(icon(ico, "h-4 w-4"), className="inline-flex h-8 w-8 items-center justify-center rounded-md "
                                                  "bg-violet-50 text-violet-600"),
        html.P(title, className="mt-3 text-sm font-semibold"),
        html.P(text, className="mt-1 text-xs leading-relaxed text-muted-foreground"),
    ], className="rounded-lg border border-border bg-background p-4 text-left") for ico, title, text in COACH_SOON_FEATURES]
    links = [link(icon(ico, "h-4 w-4"), label, href=href(page), cls=button_class("outline", "sm"))
             for page, label, ico in COACH_SOON_LINKS]
    return html.Div(html.Div([
        html.Span(icon("sparkles", "h-5 w-5"), className="mx-auto flex size-10 items-center justify-center "
                                                          "rounded-xl bg-violet-50 text-violet-600"),
        html.Span(COACH_SOON_BADGE, className="mt-5 inline-block rounded-full border border-amber-200 bg-amber-50 "
                                              "px-3 py-1 text-xs font-medium text-amber-800"),
        html.H2(COACH_SOON_TITLE, className="mt-3 text-2xl font-bold tracking-tight"),
        html.P(COACH_SOON_TEXT, className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-muted-foreground"),
        html.Div(features, className="mt-8 grid gap-3 sm:grid-cols-3"),
        html.P(COACH_SOON_MEANWHILE, className="mt-10 text-xs font-semibold uppercase tracking-wide "
                                               "text-muted-foreground"),
        html.Div(links, className="mt-3 flex flex-wrap justify-center gap-2"),
    ], className="mx-auto max-w-3xl px-4 py-12 text-center"), className="h-full overflow-y-auto")


def _content(tab: str):
    if tab != "changes" and not mai_client.is_configured():
        return coming_soon()
    if briefings.get_briefing(tab):
        return html.Div(briefing_panel.panel(tab), className="h-full overflow-y-auto")
    if tab == "changes":
        return html.Div(policy_changes.panel({}, embedded=True), className="h-full overflow-y-auto")
    return chat_ui.layout()


def layout(params=None):
    tab = (params or {}).get("tab")
    tab = tab if tab in {t[0] for t in TABS} else "chat"
    return html.Div([
        html.Nav(html.Div([
            html.Button([icon(ico, "h-4 w-4"), label], id={"type": "coach-tab", "key": key}, type="button",
                        n_clicks=0, className=_tab_class(key == tab), **{"aria-current": "page"} if key == tab else {})
            for key, label, ico in TABS
        ], className="mx-auto flex max-w-3xl gap-1 overflow-x-auto"),
            className="shrink-0 border-b border-border bg-background px-4"),
        html.Div(_content(tab), id="coach-content", className="min-h-0 flex-1 overflow-hidden"),
    ], className="flex h-[calc(100vh-57px)] flex-col")


def register_callbacks(app) -> None:
    chat_ui.register_callbacks(app)
    briefing_panel.register_callbacks(app)
    policy_changes.register_callbacks(app)

    @app.callback(Output("coach-content", "children"), Output({"type": "coach-tab", "key": ALL}, "className"),
                  Input({"type": "coach-tab", "key": ALL}, "n_clicks"), prevent_initial_call=True)
    def switch(_clicks):
        trig = ctx.triggered_id
        if not trig or not ctx.triggered[0]["value"]:  # pattern-matching ids fire on layout load
            return no_update, [no_update] * len(TABS)
        tab = trig["key"]
        return _content(tab), [_tab_class(k == tab) for k, _l, _i in TABS]

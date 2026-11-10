"""
Coach hub – port of `web/app/coach/page.tsx` + `components/rag/CoachHub.tsx`.

A menu of standard briefings (country profile, transparency brief), the Policy Changes report and the free-form
"Ask the Coach" chatbot on one page.  "Ask the Coach" is the default tab; `?page=coach&tab=<key>` opens another one.
Switching a tab re-renders its content (the original unmounts it too, so each tab starts fresh).
"""

from __future__ import annotations

from dash import ALL, Input, Output, ctx, html, no_update

from pages import policy_changes
from utils import briefing_panel, briefings, chat_ui
from utils.icons import icon
from utils.ui import cn

TITLE = "PIM Policy Profiles AI"

# Menu order of the original: the briefing registry, then Policy Changes, then the chatbot.
_ICONS = {"profile": "file-text", "transparency": "shield-check"}
TABS = [(b["key"], b["label"], _ICONS.get(b["key"], "file-text")) for b in briefings.BRIEFING_LIST] + [
    ("changes", "Policy Changes", "history"), ("chat", "Ask the Coach", "messages-square")]
_TAB_BASE = ("-mb-px inline-flex items-center gap-2 whitespace-nowrap border-b-2 px-3 py-3 text-sm font-medium "
             "transition-colors")


def _tab_class(active: bool) -> str:
    return cn(_TAB_BASE, "border-primary text-foreground" if active else
              "border-transparent text-muted-foreground hover:text-foreground")


def _content(tab: str):
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

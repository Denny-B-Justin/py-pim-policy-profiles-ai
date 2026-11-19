"""
Shared interactive widgets used by several pages.

* Recheck-links button (`components/records/RecheckLinksButton.tsx`) – runs as a background job and polls it.
"""

from __future__ import annotations

import time

from dash import Input, Output, State, dcc, html, no_update
from dash.exceptions import PreventUpdate

from utils import auth, jobs
from utils.icons import icon
from utils.toast import toast
from utils.ui import button, spinner


# ── Recheck links (components/records/RecheckLinksButton.tsx) ─────────────────
def recheck_links_widget():
    return html.Div([
        dcc.Store(id="recheck-job"),
        dcc.Interval(id="recheck-poll", interval=2000, disabled=True),
        button([icon("refresh-cw", "h-3.5 w-3.5"), "Recheck links"], id="recheck-btn", variant="outline", size="sm",
               cls="gap-1.5", title="Re-test every record's external link"),
        html.Span(id="recheck-result", className="text-xs text-muted-foreground"),
    ], className="flex items-center gap-2")


def register_recheck_callbacks(app) -> None:
    from utils import check_link  # noqa: PLC0415

    @app.callback(
        Output("recheck-job", "data"), Output("recheck-poll", "disabled"), Output("recheck-btn", "disabled"),
        Output("recheck-btn", "children"), Output("recheck-result", "children"), Output("toast", "data", allow_duplicate=True),
        Input("recheck-btn", "n_clicks"), prevent_initial_call=True)
    def start(n):
        if not n:
            raise PreventUpdate
        user, err = auth.write_guard()
        if err:
            return no_update, no_update, no_update, no_update, no_update, toast("error", err)

        def job(job_id):
            return check_link.recheck_all(lambda d, t: jobs.update(job_id, progress={"done": d, "total": t}))

        return (jobs.submit("recheck", job), False, True, [spinner("h-3.5 w-3.5"), "Checking…"], "", no_update)

    @app.callback(
        Output("recheck-poll", "disabled", allow_duplicate=True), Output("recheck-btn", "disabled", allow_duplicate=True),
        Output("recheck-btn", "children", allow_duplicate=True), Output("recheck-result", "children", allow_duplicate=True),
        Output("page-refresh", "data", allow_duplicate=True),
        Input("recheck-poll", "n_intervals"), State("recheck-job", "data"), prevent_initial_call=True)
    def poll(_n, job_id):
        job = jobs.get(job_id)
        if not job or job["status"] == "running":
            raise PreventUpdate
        idle = [icon("refresh-cw", "h-3.5 w-3.5"), "Recheck links"]
        if job["status"] == "done":
            r = job["result"]
            return True, False, idle, f"{r['working']} working · {r['broken']} broken", time.time()
        return True, False, idle, job.get("error") or "Recheck failed", no_update

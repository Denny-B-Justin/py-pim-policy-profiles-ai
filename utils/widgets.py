"""
Shared interactive widgets used by several pages.

* Translate-to-English button + confirmation dialog  (`components/records/TranslateButton.tsx`)
  – the translation itself runs as a background job (mAI has no streaming; a long law can take minutes) and the
    dialog polls its progress.
"""

from __future__ import annotations

import time

from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from utils import auth, documents, jobs, mai_client, queries, storage, translate
from utils.icons import icon
from utils.routes import href
from utils.toast import toast
from utils.ui import (DIALOG_HIDE, DIALOG_SHOW, button, button_class, cn, dialog, dialog_description, dialog_footer,
                      dialog_header, dialog_title, spinner)

HIDDEN = {"display": "none"}


def translate_button(record_id: str):
    return html.Button([icon("languages", "h-3.5 w-3.5"), "Translate to English"],
                       id={"type": "translate-open", "id": record_id}, type="button",
                       className=button_class("outline", "sm", "h-7 gap-1.5 px-2 text-xs border-violet-200 "
                                                              "bg-violet-50 text-violet-700 hover:bg-violet-100"))


def translate_dialog():
    """Place once on every page that renders `translate_button`s."""
    return html.Div([
        dcc.Store(id="translate-target"),
        dcc.Store(id="translate-job"),
        dcc.Interval(id="translate-poll", interval=2000, disabled=True),
        dialog("translate-dialog",
               dialog_header(dialog_title("About to translate this policy using mAI"),
                             dialog_description(id="translate-body", cls="pt-1")),
               dialog_footer(
                   button("Cancel", id="translate-cancel", variant="outline"),
                   html.A(button("Sign in"), id="translate-signin", href=href("signin"), style=HIDDEN),
                   button("Translate", id="translate-go", disabled=True),
               ), cls="sm:max-w-md"),
    ])


def _estimating():
    return html.Span([spinner("h-3.5 w-3.5"), "Estimating translation length…"],
                     className="inline-flex items-center gap-1.5 text-muted-foreground")


def register_callbacks(app) -> None:
    @app.callback(
        Output("translate-dialog", "style"), Output("translate-target", "data"), Output("translate-body", "children"),
        Input({"type": "translate-open", "id": ALL}, "n_clicks"), Input("translate-dialog-x", "n_clicks"),
        Input("translate-dialog-overlay", "n_clicks"), Input("translate-cancel", "n_clicks"),
        State("translate-job", "data"), prevent_initial_call=True)
    def toggle(_open, _x, _overlay, _cancel, job_id):
        trig = ctx.triggered_id
        if trig is None or not ctx.triggered or not ctx.triggered[0].get("value"):
            raise PreventUpdate
        if isinstance(trig, dict):
            return DIALOG_SHOW, trig["id"], _estimating()
        job = jobs.get(job_id)
        if job and job.get("status") == "running":      # `onOpenChange={(o) => !translating && setOpen(o)}`
            raise PreventUpdate
        return DIALOG_HIDE, None, no_update

    @app.callback(
        Output("translate-body", "children", allow_duplicate=True), Output("translate-go", "disabled", allow_duplicate=True),
        Output("translate-signin", "style"), Output("translate-signin", "href"), Output("translate-go", "style"),
        Input("translate-target", "data"), State("url", "search"), prevent_initial_call=True)
    def estimate(record_id, search):
        if not record_id:
            raise PreventUpdate
        if not auth.current_user():
            cb = "?" + (search or "").lstrip("?") if search else "?page=home"
            return ("You need to be signed in to use translation.", True, {}, href("signin", callbackUrl=cb), HIDDEN)
        try:
            rec = queries.get_record_with_docs(record_id)
            ori = next((d for d in (rec or {}).get("documents", []) if d["lang_type"] == "ORI"), None)
            if not ori or not ori.get("file_path"):
                raise ValueError("This record has no original-language document to translate.")
            chars = translate.estimate_characters(storage.read(ori["file_path"]), ori.get("file_name"))
            body = html.Span(["Estimated total translation length: ", html.Strong(f"{chars:,} characters"), "."])
            if not mai_client.is_configured():
                body = html.Span(mai_client.NOT_CONFIGURED, className="text-destructive")
                return body, True, HIDDEN, no_update, {}
            return body, False, HIDDEN, no_update, {}
        except Exception as exc:  # noqa: BLE001
            msg = str(exc) or "Could not estimate the translation length."
            return html.Span(msg, className="text-destructive"), True, HIDDEN, no_update, {}

    @app.callback(
        Output("translate-job", "data"), Output("translate-poll", "disabled"),
        Output("translate-go", "children"), Output("translate-go", "disabled", allow_duplicate=True),
        Output("translate-cancel", "disabled"), Output("toast", "data", allow_duplicate=True),
        Input("translate-go", "n_clicks"), State("translate-target", "data"), prevent_initial_call=True)
    def go(n, record_id):
        if not n or not record_id:
            raise PreventUpdate
        user, err = auth.write_guard()
        if err:
            return no_update, no_update, no_update, no_update, no_update, toast("error", err)
        job_id = jobs.submit("translate", documents.translate_record_to_english, record_id)
        return (job_id, False, [spinner("mr-1.5 h-4 w-4"), "Translating…"], True, True, no_update)

    @app.callback(
        Output("translate-body", "children", allow_duplicate=True), Output("translate-dialog", "style", allow_duplicate=True),
        Output("translate-poll", "disabled", allow_duplicate=True), Output("translate-go", "children", allow_duplicate=True),
        Output("translate-go", "disabled", allow_duplicate=True), Output("translate-cancel", "disabled", allow_duplicate=True),
        Output("toast", "data", allow_duplicate=True), Output("page-refresh", "data", allow_duplicate=True),
        Input("translate-poll", "n_intervals"), State("translate-job", "data"), prevent_initial_call=True)
    def poll(_n, job_id):
        job = jobs.get(job_id)
        if not job:
            raise PreventUpdate
        if job["status"] == "running":
            p = job.get("progress") or {}
            label = f"Translating… section {p['done']} of {p['total']}" if p.get("total") else "Translating…"
            return (html.Span([spinner("h-3.5 w-3.5"), label], className="inline-flex items-center gap-1.5 text-muted-foreground"),
                    no_update, no_update, no_update, no_update, no_update, no_update, no_update)
        reset = ("Translate", False, False)
        if job["status"] == "done":
            return (no_update, DIALOG_HIDE, True, *reset, toast("success", "English translation added."),
                    time.time())
        return (html.Span(job.get("error") or "Translation failed.", className="text-destructive"), no_update, True,
                *reset, toast("error", job.get("error") or "Translation failed."), no_update)


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

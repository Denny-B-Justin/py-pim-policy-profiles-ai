"""Batch upload – port of `web/app/repository/batch/{page,BatchUploadClient}.tsx`.

Three steps (Upload → Review → Import), exactly as the original.  Differences forced by the platform:
  * the original's browser loop (`POST /process` once per row) is a background job (`utils.batch.run_batch`) that
    `dcc.Interval` polls – the table updates row by row the same way;
  * non-English originals are machine-translated with the Posit mAI gateway instead of DeepL.
All three step containers stay mounted (like React state) and are shown/hidden, so “Back” keeps the chosen file.
"""

from __future__ import annotations

import csv
import io
import logging

from dash import Input, Output, State, dcc, html, no_update

import constants as C
from pages.pages_constants import (
    BATCH_UPLOAD_TITLE as TITLE,
    BATCH_UPLOAD_STEPS as STEPS,
    BATCH_UPLOAD_STATUS as STATUS,
)
from utils import auth, batch, jobs
from utils.icons import icon
from utils.routes import asset, href
from utils.ui import badge, button, button_class, link, select

log = logging.getLogger(__name__)


TEMPLATE_URL = C.BATCH_TEMPLATE_ASSET
DATADICT_URL = C.BATCH_DATADICT_ASSET
SHOW, HIDE = {}, {"display": "none"}
ALERT = "rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800"
TH = "px-3 py-2 font-medium text-muted-foreground"


def status_badge(status: str):
    cls, label = STATUS.get(status, ("bg-gray-50 text-gray-600 border-gray-200", status))
    return badge(label, variant="outline", cls=f"text-[11px] {cls}")


def stepper(step: str):
    items = []
    for i, (key, label) in enumerate(STEPS):
        active = step == key
        items.append(html.Div([
            html.Span(str(i + 1), className="inline-flex h-6 w-6 items-center justify-center rounded-full border "
                      "text-[11px] font-semibold " + ("bg-blue-600 text-white border-blue-600" if active else "text-muted-foreground")),
            html.Span(label, className="font-medium" if active else "text-muted-foreground"),
            html.Span("→", className="mx-1 text-muted-foreground") if i < 2 else None,
        ], className="flex items-center gap-2"))
    return items


# ── dynamic pieces ──────────────────────────────────────────────────────────────────────
def _validate_label(busy: bool = False):
    return [icon("loader-circle", "h-4 w-4 animate-spin") if busy else icon("upload", "h-4 w-4"), "Validate & preview"]


def _import_label(n: int):
    return [icon("upload", "h-4 w-4"), f"Import {n} record{'' if n == 1 else 's'}"]


def preview_table(rows: list[dict]):
    body = []
    for r in rows:
        docs = []
        if r["has_original"]:
            docs.append(badge((r["original_language"] or "orig").upper(), variant="outline", cls="text-[10px]"))
        if r["has_english"]:
            docs.append(badge("EN", variant="outline", cls="text-[10px]"))
        if r["will_translate"]:
            docs.append(badge([icon("languages", "mr-0.5 h-2.5 w-2.5"), "mAI"], variant="outline",
                              cls="text-[10px] bg-violet-50 text-violet-700"))
        body.append(html.Tr([
            html.Td(str(r["row_number"]), className="px-3 py-2 text-muted-foreground tabular-nums"),
            html.Td(r["country"] or "—", className="px-3 py-2 font-mono text-xs"),
            html.Td([
                html.Div(r["name_eng"] or html.Span("—", className="text-muted-foreground"), className="max-w-md truncate"),
                html.Div("; ".join(r["errors"]), className="mt-0.5 text-xs text-red-600") if r["errors"] else None,
            ], className="px-3 py-2"),
            html.Td(html.Div(docs, className="flex flex-wrap gap-1"), className="px-3 py-2"),
            html.Td(status_badge(r["status"]), className="px-3 py-2"),
        ], className="bg-red-50/40" if r["status"] == "invalid" else None))
    return html.Table([
        html.Thead(html.Tr([html.Th(t, className=TH) for t in ("#", "Country", "English name", "Docs", "Status")]),
                   className="bg-muted/40 text-left"),
        html.Tbody(body, className="divide-y"),
    ], className="w-full text-sm")


def summary_badges(s: dict):
    return [
        badge(f"{s['valid']} ready", variant="outline", cls="bg-blue-50 text-blue-700"),
        badge(f"{s['invalid']} with errors", variant="outline", cls="bg-red-50 text-red-700") if s["invalid"] else None,
        badge(f"{s['skipped']} duplicates", variant="outline", cls="bg-gray-100 text-gray-600") if s["skipped"] else None,
        badge([icon("languages", "mr-1 h-3 w-3"), f"{s['will_translate']} to translate"], variant="outline",
              cls="bg-violet-50 text-violet-700") if s["will_translate"] else None,
    ]


def _result_icon(status: str):
    if status in ("created", "updated"):
        return icon("circle-check", "h-3.5 w-3.5 text-green-600")
    if status == "failed":
        return icon("circle-x", "h-3.5 w-3.5 text-red-600")
    if status == "skipped":
        return icon("skip-forward", "h-3.5 w-3.5 text-gray-500")
    if status == "invalid":
        return icon("triangle-alert", "h-3.5 w-3.5 text-red-500")
    if status == "processing":
        return icon("loader-circle", "h-3.5 w-3.5 animate-spin text-amber-600")
    return None


def run_view(batch_id: str | None, busy: bool, error: bool):
    """→ (status line, results table, show_actions)"""
    _, rows = batch.preview_rows(batch_id) if batch_id else (None, [])
    total = len(rows)
    created = sum(1 for r in rows if r["status"] in ("created", "updated"))
    failed = sum(1 for r in rows if r["status"] == "failed")
    pending = sum(1 for r in rows if r["status"] in ("pending", "processing"))
    if busy:
        head = html.Span([icon("loader-circle", "h-4 w-4 animate-spin text-blue-600"), "Importing…"],
                         className="inline-flex items-center gap-1.5")
    elif error:
        head = html.Span([icon("triangle-alert", "h-4 w-4"),
                          "Import stopped" + (f" — {pending} row{'' if pending == 1 else 's'} unprocessed" if pending else "")],
                         className="inline-flex items-center gap-1.5 font-medium text-red-700")
    else:
        head = html.Span([icon("circle-check", "h-4 w-4 text-green-600"), "Import complete"],
                         className="inline-flex items-center gap-1.5 font-medium")
    status = [head, html.Span(f"{created} created · {failed} failed · {total - pending}/{total} processed",
                              className="text-muted-foreground")]
    body = []
    for r in rows:
        detail = html.Span("; ".join(r["errors"]), className="text-red-600") if r["errors"] else (r["note"] or "")
        body.append(html.Tr([
            html.Td(str(r["row_number"]), className="px-3 py-2 text-muted-foreground tabular-nums"),
            html.Td(html.Div(r["name_eng"], className="max-w-sm truncate"), className="px-3 py-2"),
            html.Td(html.Span([
                _result_icon(r["status"]), status_badge(r["status"]),
                link("view", href=href("record", id=r["record_id"]), cls="text-xs text-blue-600 hover:underline")
                if r["record_id"] and r["status"] in ("created", "updated") else None,
            ], className="inline-flex items-center gap-1.5"), className="px-3 py-2"),
            html.Td(detail, className="px-3 py-2 text-xs text-muted-foreground"),
        ]))
    table = html.Table([
        html.Thead(html.Tr([html.Th(t, className=TH) for t in ("#", "English name", "Result", "Detail")]),
                   className="bg-muted/40 text-left"),
        html.Tbody(body, className="divide-y"),
    ], className="w-full text-sm")
    return status, table, (not busy), error


# ── layout ──────────────────────────────────────────────────────────────────────────────
def layout(params=None):
    return html.Div([
        dcc.Store(id="batch-id", data=None), dcc.Store(id="batch-job", data=None),
        dcc.Store(id="batch-step", data="upload"),
        dcc.Interval(id="batch-poll", interval=1000, disabled=True),
        dcc.Download(id="batch-download"),
        link(icon("arrow-left", "h-4 w-4"), "Repository", href=href("repository"),
             cls="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"),
        html.Div([
            html.H1("Batch upload", className="text-2xl font-bold tracking-tight"),
            html.P("Add many policy records at once from a spreadsheet. Documents are fetched from the links you provide; "
                   "non-English originals can be auto-translated with the Posit mAI gateway.",
                   className="mt-1 text-sm text-muted-foreground"),
        ]),
        html.Div(stepper("upload"), id="batch-stepper", className="flex items-center gap-2 text-xs"),
        html.Div(id="batch-error"),

        # ── Step 1: Upload ──
        html.Div([
            html.Div([
                html.Div([
                    html.A(button(icon("download", "h-4 w-4"), "Download template (.xlsx)", variant="outline", cls="gap-1.5"),
                           href=asset(TEMPLATE_URL), download=""),
                    html.A(button(icon("download", "h-4 w-4"), "Data dictionary (.docx)", variant="ghost", cls="gap-1.5"),
                           href=asset(DATADICT_URL), download=""),
                ], className="flex flex-wrap items-center gap-2"),
                html.P(["Fill in the ", html.Strong("Policies"), " sheet — one row per record — then upload it below. "
                        "CSV with the same headers also works. The template also includes a ",
                        html.Strong("Data Dictionary"), " sheet."], className="mt-2 text-xs text-muted-foreground"),
            ], className="rounded-lg border bg-muted/20 p-4"),
            dcc.Upload(
                html.Div([
                    icon("file-spreadsheet", "h-7 w-7 text-blue-600"),
                    html.P("Click to choose your filled spreadsheet", id="batch-file-name", className="text-sm font-medium"),
                    html.P(".xlsx or .csv, up to 5 MB", className="text-xs text-muted-foreground"),
                ], className="flex flex-col items-center gap-2"),
                id="batch-upload", multiple=False, accept=".xlsx,.csv",
                className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed p-8 "
                          "hover:border-primary/50 hover:bg-muted/20"),
            html.Div([
                dcc.Checklist(id="batch-auto", options=[{"label": "Auto-translate non-English originals (mAI)", "value": "1"}],
                              value=["1"], className="flex items-center gap-2 text-sm",
                              labelClassName="flex items-center gap-2 text-sm", inputClassName="h-4 w-4 m-0",
                              labelStyle={"display": "flex"}),
                html.Div([
                    html.Label("If a record already exists", className="text-sm font-medium"),
                    select("batch-conflict", [("skip", "Skip it"), ("update", "Update it"), ("create", "Create anyway")],
                           value="skip", cls="w-[200px]"),
                ], className="space-y-1"),
            ], className="flex flex-wrap items-end gap-6"),
            button(_validate_label(), id="batch-validate", n_clicks=0, disabled=True, cls="gap-1.5"),
        ], id="batch-upload-step", className="space-y-5"),

        # ── Step 2: Review ──
        html.Div([
            html.Div(id="batch-summary", className="flex flex-wrap items-center gap-2 text-sm"),
            html.Div(id="batch-preview-table", className="overflow-x-auto rounded-lg border"),
            html.Div([
                button("Back", id="batch-back", n_clicks=0, variant="outline"),
                button(_import_label(0), id="batch-import", n_clicks=0, disabled=True, cls="gap-1.5"),
                html.Span("Fix the errors in your sheet and re-upload.", id="batch-import-hint",
                          className="text-xs text-muted-foreground", style=HIDE),
            ], className="flex items-center gap-3"),
        ], id="batch-preview-step", className="space-y-4", style=HIDE),

        # ── Step 3: Import ──
        html.Div([
            html.Div(id="batch-run-status", className="flex flex-wrap items-center gap-3 text-sm"),
            html.Div(id="batch-run-table", className="overflow-x-auto rounded-lg border"),
            html.Div([
                button([icon("rotate-ccw", "h-4 w-4"), "Resume import"], id="batch-resume", n_clicks=0, cls="gap-1.5",
                       style=HIDE),
                button("Import another", id="batch-another", n_clicks=0, variant="outline"),
                button([icon("download", "h-4 w-4"), "Download results (CSV)"], id="batch-csv", n_clicks=0,
                       variant="outline", cls="gap-1.5"),
                link("Back to repository", href=href("repository"), cls=button_class("ghost")),
            ], id="batch-run-actions", className="flex flex-wrap items-center gap-3"),
        ], id="batch-run-step", className="space-y-4", style=HIDE),
    ], className="space-y-6")


# ── callbacks ───────────────────────────────────────────────────────────────────────────
def _alert(msg: str | None):
    return html.Div(msg, role="alert", className=ALERT) if msg else None


def _start_job(batch_id: str):
    return jobs.submit("batch", lambda job_id, bid: batch.run_batch(job_id, bid), batch_id)


def register_callbacks(app):
    # file picked → show its name, enable "Validate & preview", clear any error
    @app.callback(Output("batch-file-name", "children"), Output("batch-validate", "disabled"),
                  Output("batch-error", "children", allow_duplicate=True),
                  Input("batch-upload", "filename"), prevent_initial_call=True)
    def picked(name):
        return (name or "Click to choose your filled spreadsheet"), not name, None

    # step 1 → 2: parse + validate + persist, then fill the review table
    @app.callback(
        Output("batch-id", "data"), Output("batch-step", "data"), Output("batch-error", "children"),
        Output("batch-summary", "children"), Output("batch-preview-table", "children"),
        Output("batch-import", "children"), Output("batch-import", "disabled"), Output("batch-import-hint", "style"),
        Input("batch-validate", "n_clicks"),
        State("batch-upload", "contents"), State("batch-upload", "filename"), State("batch-auto", "value"),
        State("batch-conflict", "value"), State("batch-step", "data"),
        running=[(Output("batch-validate", "disabled"), True, False),
                 (Output("batch-validate", "children"), _validate_label(True), _validate_label(False))],
        prevent_initial_call=True,
    )
    def validate(n, contents, filename, auto, conflict, step):
        if not n or not contents or not filename:
            return (no_update,) * 8
        fail = lambda msg: (no_update, no_update, _alert(msg), no_update, no_update, no_update, no_update, no_update)  # noqa: E731
        user, err = auth.write_guard()
        if err:
            return fail(err)
        try:
            data = _decode(contents)
        except ValueError as exc:
            return fail(str(exc))
        if len(data) > C.MAX_BATCH_SHEET_BYTES:
            return fail(f"That file is {len(data) / 1048576:.1f} MB — the limit is 5 MB.")
        try:
            res = batch.parse_and_store(data, filename, bool(auto), conflict or "skip", (user or {}).get("email"))
        except batch.BatchError as exc:
            return fail(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("batch parse failed")
            return fail(str(exc) or "Could not read the file.")
        _, rows = batch.preview_rows(res["batch_id"])
        s = res["summary"]
        return (res["batch_id"], "preview", None, summary_badges(s), preview_table(rows), _import_label(s["valid"]),
                s["valid"] == 0, SHOW if s["valid"] == 0 else HIDE)

    # which step is visible + the stepper
    @app.callback(Output("batch-upload-step", "style"), Output("batch-preview-step", "style"),
                  Output("batch-run-step", "style"), Output("batch-stepper", "children"), Input("batch-step", "data"))
    def show_step(step):
        step = step or "upload"
        return (SHOW if step == "upload" else HIDE, SHOW if step == "preview" else HIDE,
                SHOW if step == "run" else HIDE, stepper(step))

    @app.callback(Output("batch-step", "data", allow_duplicate=True), Output("batch-error", "children", allow_duplicate=True),
                  Input("batch-back", "n_clicks"), prevent_initial_call=True)
    def back(n):
        return ("upload", None) if n else (no_update, no_update)

    # step 2 → 3 (and “Resume import”): start / continue the background job
    def outs(primary: bool):
        return [Output("batch-job", "data", allow_duplicate=not primary),
                Output("batch-step", "data", allow_duplicate=True),
                Output("batch-poll", "disabled", allow_duplicate=True),
                Output("batch-error", "children", allow_duplicate=True),
                Output("batch-run-status", "children", allow_duplicate=True),
                Output("batch-run-table", "children", allow_duplicate=True),
                Output("batch-run-actions", "style", allow_duplicate=True),
                Output("batch-resume", "style", allow_duplicate=True)]

    def start(batch_id):
        _, err = auth.write_guard()
        if err:
            return (no_update, no_update, no_update, _alert(err), no_update, no_update, no_update, no_update)
        if not batch.get_batch(batch_id):
            return (no_update, no_update, no_update, _alert("Batch not found"), no_update, no_update, no_update, no_update)
        job_id = _start_job(batch_id)
        status, table, *_ = run_view(batch_id, True, False)
        return {"id": job_id}, "run", False, None, status, table, HIDE, HIDE

    @app.callback(*outs(True), Input("batch-import", "n_clicks"), State("batch-id", "data"), prevent_initial_call=True)
    def import_click(n, batch_id):
        return start(batch_id) if n and batch_id else (no_update,) * 8

    @app.callback(*outs(False), Input("batch-resume", "n_clicks"), State("batch-id", "data"), prevent_initial_call=True)
    def resume_click(n, batch_id):
        return start(batch_id) if n and batch_id else (no_update,) * 8

    # poll the job → live table
    @app.callback(
        Output("batch-run-status", "children"), Output("batch-run-table", "children"),
        Output("batch-run-actions", "style"), Output("batch-resume", "style"), Output("batch-poll", "disabled"),
        Output("batch-error", "children", allow_duplicate=True),
        Input("batch-poll", "n_intervals"), State("batch-id", "data"), State("batch-job", "data"),
        prevent_initial_call=True,
    )
    def poll(_n, batch_id, job_state):
        if not batch_id or not job_state:
            return (no_update,) * 5 + (no_update,)
        job = jobs.get(job_state["id"])
        busy = bool(job) and job["status"] == "running"
        error = (job is None) or job["status"] == "error"
        status, table, show_actions, show_resume = run_view(batch_id, busy, error)
        msg = None
        if job and job["status"] == "error":
            msg = _alert(job.get("error") or "Processing failed.")
        elif job is None:
            msg = _alert("Processing failed.")
        return (status, table, SHOW if show_actions else HIDE, SHOW if show_resume else HIDE, not busy,
                msg if not busy else no_update)

    # “Import another” → back to a clean step 1
    @app.callback(
        Output("batch-step", "data", allow_duplicate=True), Output("batch-id", "data", allow_duplicate=True),
        Output("batch-job", "data", allow_duplicate=True), Output("batch-error", "children", allow_duplicate=True),
        Output("batch-upload", "contents"), Output("batch-upload", "filename"),
        Output("batch-poll", "disabled", allow_duplicate=True),
        Input("batch-another", "n_clicks"), prevent_initial_call=True)
    def another(n):
        if not n:
            return (no_update,) * 7
        return "upload", None, None, None, None, None, True

    # results CSV (downloadResultsCsv)
    @app.callback(Output("batch-download", "data"), Input("batch-csv", "n_clicks"), State("batch-id", "data"),
                  prevent_initial_call=True)
    def results_csv(n, batch_id):
        if not n or not batch_id:
            return no_update
        _, rows = batch.preview_rows(batch_id)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_NONNUMERIC)
        w.writerow(["row", "country", "name_eng", "result", "record_id", "detail"])
        for r in rows:
            detail = "; ".join(r["errors"]) if r["errors"] else (r["note"] or "")
            w.writerow([r["row_number"], r["country"], r["name_eng"], r["status"], r["record_id"] or "", detail])
        return dcc.send_string("﻿" + buf.getvalue(), f"batch-results-{batch_id}.csv", type="text/csv;charset=utf-8")


def _decode(contents: str) -> bytes:
    import base64

    if not contents or "," not in contents:
        raise ValueError("The file could not be read.")
    try:
        return base64.b64decode(contents.split(",", 1)[1])
    except Exception as exc:  # noqa: BLE001
        raise ValueError("The file could not be read.") from exc

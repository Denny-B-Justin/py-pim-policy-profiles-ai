"""
Record create / edit form – port of `components/records/RecordForm.tsx`, `DocAttachment.tsx`, `CountryCombobox.tsx`
and the zod-validated save flow (`app/api/records` POST / PATCH).

The three form pages (`record_new`, `record_edit`) only differ by `mode`; the callbacks are registered once.
React-hook-form state becomes plain Dash component state, validation is `utils.record_schema.validate_record`.
Edits only write the fields the curator actually changed (same rule as the original: never touch columns the form
does not show, e.g. `tokens`).
"""

from __future__ import annotations

import logging
import time

from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

import constants as C
from utils import auth, check_link, documents, indexer, jobs, queries
from utils.audit import log_audit
from utils.components import fmt_size
from utils.icons import icon
from utils.record_schema import validate_record
from utils.routes import href
from utils.toast import toast
from utils.ui import (badge, button, cn, combobox, label, select, separator, text_input, textarea)

log = logging.getLogger(__name__)

LANG_LABELS = dict(C.NATIVE_LANGUAGES)
SECTION_H2 = "text-sm font-semibold uppercase tracking-wide text-muted-foreground"
ERR_FIELDS = ["country", "name_eng", "year", "year_revised", "jurisdiction_name", "superseded_by_link",
              "superseded_by_note", "link"]
HIDDEN = {"display": "none"}
SHOWN = {}

TIER_OPTS = [("none", "None")] + [(str(t["value"]), t["label"]) for t in C.TIERS]
STRATEGY_OPTS = [("none", "None")] + [(str(s["value"]), s["label"]) for s in C.STRATEGY_TYPES]
LEVEL_OPTS = [("national", "National"), ("subnational", "Sub-national"), ("supranational", "Supranational")]
STATUS_OPTS = [("latest", "Latest — current / in force"), ("archived", "Archived — superseded or repealed")]
MACHINE_OPTS = [("unknown", "Unknown — no files checked yet"), ("Y", "Yes — files have a text layer"),
                ("N", "No — image-only scan, needs OCR")]


# ── small pieces ─────────────────────────────────────────────────────────────
def field_error(field: str):
    return html.P(id=f"rf-err-{field}", role="alert", className="text-xs text-destructive mt-1 empty:hidden")


def file_icon(file_name: str | None):
    ext = (file_name or "").rsplit(".", 1)[-1].lower() if file_name and "." in file_name else ""
    if ext == "pdf":
        return icon("file-text", "h-3.5 w-3.5 text-red-500")
    if ext in ("doc", "docx"):
        return icon("file-type", "h-3.5 w-3.5 text-blue-500")
    if ext == "txt":
        return icon("file-code", "h-3.5 w-3.5 text-gray-500")
    return icon("paperclip", "h-3.5 w-3.5 text-gray-400")


_TYPE_COLORS = {"PDF": "bg-red-50 text-red-700 border-red-200", "DOC": "bg-blue-50 text-blue-700 border-blue-200",
                "DOCX": "bg-blue-50 text-blue-700 border-blue-200", "TXT": "bg-gray-100 text-gray-600 border-gray-200"}


def file_type_badge(file_name: str | None):
    if not file_name or "." not in file_name:
        return None
    ext = file_name.rsplit(".", 1)[-1].upper()
    return badge(ext, variant="outline", cls=cn("text-[10px] px-1.5 py-0", _TYPE_COLORS.get(ext, "bg-gray-50")))


def _icon_btn(*children, cls: str = "", **props):
    return html.Button(list(children), type="button",
                       className=cn("inline-flex items-center justify-center rounded-md text-sm font-medium "
                                    "transition-all hover:bg-accent hover:text-accent-foreground h-6 w-6 shrink-0 "
                                    "cursor-pointer disabled:opacity-50", cls), **props)


# ── DocAttachment (edit mode) ────────────────────────────────────────────────
def attachment_slot(lang_type: str, doc: dict | None):
    """The attach / replace / remove row below a document-name input."""
    upl_id = {"type": "rf-att-upl", "slot": lang_type}
    if doc:
        return html.Div([
            file_icon(doc.get("file_name")),
            html.Span(doc.get("file_name") or "", className="text-xs text-foreground font-medium flex-1 truncate min-w-0"),
            file_type_badge(doc.get("file_name")),
            html.Span(fmt_size(doc.get("file_size")), className="text-[10px] text-muted-foreground hidden sm:inline")
            if fmt_size(doc.get("file_size")) else None,
            dcc.Upload(_icon_btn(icon("refresh-cw", "h-3 w-3"), title="Replace file"), id=upl_id, multiple=False,
                       accept=".pdf,.doc,.docx,.txt", max_size=C.MAX_UPLOAD_BYTES, className="inline-flex shrink-0"),
            _icon_btn(icon("trash-2", "h-3 w-3"), title="Remove attachment", id={"type": "rf-att-del", "id": doc["id"]},
                      cls="text-destructive hover:text-destructive"),
        ], className="flex items-center gap-2 mt-1.5 px-3 py-2 rounded-md border bg-muted/30 group")
    return dcc.Upload(
        html.Div([icon("paperclip", "h-3.5 w-3.5 text-muted-foreground"),
                  html.Span("Attach file (PDF, Word, or Text)", className="text-xs text-muted-foreground")],
                 className="flex items-center gap-2"),
        id=upl_id, multiple=False, accept=".pdf,.doc,.docx,.txt", max_size=C.MAX_UPLOAD_BYTES,
        className="flex items-center gap-2 mt-1.5 px-3 py-2 rounded-md border border-dashed cursor-pointer "
                  "hover:border-primary/40 hover:bg-muted/20 transition-colors")


def _lang_options():
    return [("none", "Select language…")] + [(c, l) for c, l in C.NATIVE_LANGUAGES]


def _attachment_block(lang_type: str, mode: str, record: dict | None, docs: list[dict]):
    if mode != "edit" or not record:
        return html.P("📎 File attachment available after saving the record",
                      className="text-xs text-muted-foreground mt-1.5 flex items-center gap-1")
    doc = next((d for d in docs if d["lang_type"] == lang_type), None)
    blocks = [html.Div(attachment_slot(lang_type, doc), id=f"rf-att-{lang_type}")]
    if lang_type == "ORI":
        # A new native-language file needs its language (the original form omitted it, so the upload was always
        # rejected with "Select the document's original language…" – here it is asked for inline).
        blocks.append(html.Div([
            html.Span("Language of the file", className="text-xs text-muted-foreground shrink-0"),
            select("rf-ori-lang", _lang_options(), value="none", size="sm", cls="w-[180px]", searchable=True,
                   menu_width="14rem"),
        ], id="rf-ori-lang-wrap", className="flex items-center gap-2 mt-1.5", style=HIDDEN if doc else SHOWN))
    return html.Div(blocks)


# ── the form ────────────────────────────────────────────────────────────────
def _section(title: str, *children):
    return html.Section([html.H2(title, className=SECTION_H2), *children], className="space-y-4")


def _field(lbl, control, *extra, cls: str = ""):
    return html.Div([lbl, control, *extra], className=cn("space-y-1.5", cls))


def record_form(mode: str, record: dict | None = None, documents_: list[dict] | None = None):
    rec = record or {}
    docs = documents_ or []
    level = rec.get("jurisdiction_level") or "national"
    status = rec.get("record_status") or "latest"
    tier = rec.get("policy_guidance_tier")
    mr = rec.get("machine_readable")
    countries = [(c["iso3"], c["name"], c["iso3"]) for c in queries.list_all_countries()]
    strategy_on = tier == 4
    return html.Div([
        dcc.Store(id="rf-mode", data={"mode": mode, "id": rec.get("id")}),
        dcc.Store(id="rf-doc-version"),
        html.Div(id="rf-noop", hidden=True),

        _section(
            "Document Identity",
            html.Div([
                _field(label("Country *"), combobox("rf-country", countries, rec.get("country"), "Select country…",
                                                    "Search country…", "No country found."), field_error("country")),
                _field(label("Source / Issuing Body", html_for="rf-source"),
                       text_input("rf-source", value=rec.get("source") or "", placeholder="e.g. Ministry of Finance",
                                  debounce=False)),
                _field(label("Document Name (Native Language)", html_for="rf-name-orig"),
                       text_input("rf-name-orig", value=rec.get("name_orig") or "",
                                  placeholder="Title in original language (if different)", debounce=False),
                       _attachment_block("ORI", mode, record, docs), cls="sm:col-span-2"),
                _field(label("Document Name (English) *", html_for="rf-name-eng"),
                       text_input("rf-name-eng", value=rec.get("name_eng") or "", placeholder="Full title in English",
                                  debounce=False),
                       field_error("name_eng"), _attachment_block("ENG", mode, record, docs), cls="sm:col-span-2"),
            ], className="grid grid-cols-1 sm:grid-cols-2 gap-4"),
        ),
        separator(),
        _section(
            "Publication",
            html.Div([
                _field(label("Year", html_for="rf-year"),
                       text_input("rf-year", type="number", value=rec.get("year"), placeholder="e.g. 2020", debounce=False),
                       field_error("year")),
                _field(label("Year Revised", html_for="rf-year-revised"),
                       text_input("rf-year-revised", type="number", value=rec.get("year_revised"),
                                  placeholder="e.g. 2023", debounce=False), field_error("year_revised")),
                _field(label("Pages", html_for="rf-pages"),
                       text_input("rf-pages", type="number", value=rec.get("pages"), placeholder="e.g. 64", debounce=False)),
            ], className="grid grid-cols-2 sm:grid-cols-3 gap-4"),
        ),
        separator(),
        _section(
            "Classification",
            html.Div([
                _field(label("Policy Guidance Tier"),
                       select("rf-ptier", TIER_OPTS, value=str(tier) if tier else "none", placeholder="Select tier…",
                              cls="w-full", menu_width="30rem")),
                html.Div([
                    label("Strategy Classification", id="rf-stier-label",
                          cls=None if strategy_on else "text-muted-foreground"),
                    select("rf-stier", STRATEGY_OPTS, value=str(rec["strategy_tier"]) if rec.get("strategy_tier") else "none",
                           placeholder="Select strategy type…", cls=cn("w-full", "" if strategy_on else "opacity-50 cursor-not-allowed"),
                           disabled=not strategy_on),
                    html.P(["Only available for ",
                            html.Span("Tier 4 — Strategies for Project Prioritization & Alignment", className="font-medium"),
                            " records."], id="rf-stier-help", className="text-xs text-muted-foreground",
                           style=HIDDEN if strategy_on else SHOWN),
                ], className="space-y-1.5"),
            ], className="grid grid-cols-1 gap-4"),
        ),
        separator(),
        _section(
            "Jurisdiction",
            html.Div([
                _field(label("Level"), select("rf-level", LEVEL_OPTS, value=level, cls="w-full")),
                html.Div([
                    label("Entity Name", html.Span(" *", id="rf-jname-star", style=SHOWN if level != "national" else HIDDEN),
                          id="rf-jname-label", html_for="rf-jname", cls="text-muted-foreground" if level == "national" else None),
                    text_input("rf-jname", value=rec.get("jurisdiction_name") or "",
                               placeholder="e.g. Bangkok Metropolitan Administration, European Union",
                               disabled=level == "national", debounce=False),
                    field_error("jurisdiction_name"),
                ], className="space-y-1.5"),
            ], className="grid grid-cols-1 sm:grid-cols-2 gap-4"),
        ),
        separator(),
        _section(
            "Status",
            html.Div([
                _field(label("Record Status"), select("rf-status", STATUS_OPTS, value=status, cls="w-full max-w-xs"),
                       html.P("Archived records stay in the repository but are excluded from AI policy briefs and "
                              "Ask-the-Coach answers.", className="text-xs text-muted-foreground")),
                _field(label("Machine Readable (Y/N)"),
                       select("rf-machine", MACHINE_OPTS, value="Y" if mr is True else "N" if mr is False else "unknown",
                              cls="w-full max-w-xs", menu_width="20rem"),
                       html.P("Detected automatically from uploaded files (image-only PDFs are not sent to the AI index "
                              "and the title shows in red). Set to Yes after an OCR pass has been validated and the "
                              "searchable file uploaded.", className="text-xs text-muted-foreground")),
                html.Div([
                    _field(label("Superseded By (link)", html_for="rf-sup-link"),
                           text_input("rf-sup-link", value=rec.get("superseded_by_link") or "", debounce=False,
                                      placeholder="/records/… or https://… — the current policy that replaces this one"),
                           field_error("superseded_by_link")),
                    _field(label("Superseded By (note)", html_for="rf-sup-note"),
                           textarea("rf-sup-note", value=rec.get("superseded_by_note") or "", rows=3,
                                    placeholder="e.g. Replaced by the Decree on Capital Projects adopted 28 Aug 2026, "
                                                "in force 5 Sep 2026."),
                           field_error("superseded_by_note")),
                ], id="rf-archived-panel", className="space-y-4 rounded-md border border-amber-200 bg-amber-50/60 p-4",
                    style=SHOWN if status == "archived" else HIDDEN),
            ], className="grid grid-cols-1 gap-4"),
        ),
        separator(),
        _section(
            "Content",
            html.Div([
                _field(label("Overview", html_for="rf-overview"),
                       textarea("rf-overview", value=rec.get("overview") or "", rows=5,
                                placeholder="Brief description of the document's scope and purpose…")),
                _field(label("Internal Comment", html_for="rf-comment"),
                       textarea("rf-comment", value=rec.get("comment") or "", rows=3,
                                placeholder="Internal notes (not shown publicly)…")),
                _field(label("External Link", html_for="rf-link"),
                       text_input("rf-link", type="url", value=rec.get("link") or "", placeholder="https://…",
                                  debounce=False), field_error("link")),
            ], className="space-y-4"),
        ),
        html.Div([
            button("Cancel", id="rf-cancel", variant="outline"),
            button(icon("save", "h-4 w-4 mr-2", id="rf-save-icon"),
                   html.Span("Create Record" if mode == "create" else "Save Changes", id="rf-save-label"),
                   id="rf-save"),
        ], className="flex gap-3 justify-end pt-2"),
    ], id="rf-form", className="space-y-8")


# ── callbacks ───────────────────────────────────────────────────────────────
def _tier(v):
    return None if v in (None, "none", "") else int(v)


def _normalise_for_diff(v):
    return None if v == "" else v


def register_callbacks(app) -> None:
    if getattr(app, "_rf_registered", False):
        return
    app._rf_registered = True

    # Cancel: confirm discarding unsaved edits, then history.back() (or → repository), exactly like handleCancel()
    app.clientside_callback("function(n){return window.dash_clientside.pim.cancelForm(n);}",
                            Output("rf-noop", "children"), Input("rf-cancel", "n_clicks"), prevent_initial_call=True)

    # Strategy select is only available for Tier 4 records; moving away from Tier 4 clears it
    @app.callback(Output("rf-stier", "disabled"), Output("rf-stier", "value"), Output("rf-stier", "className"),
                  Output("rf-stier-label", "className"), Output("rf-stier-help", "style"),
                  Input("rf-ptier", "value"), State("rf-stier", "value"), prevent_initial_call=True)
    def tier_changed(tier, stier):
        on = tier == "4"
        return (not on, stier if on else "none",
                cn("ui-select ui-select-default", "w-full", "" if on else "opacity-50 cursor-not-allowed"),
                cn("flex items-center gap-2 text-sm leading-none font-medium select-none", "" if on else "text-muted-foreground"),
                HIDDEN if on else SHOWN)

    # National records have no entity name
    @app.callback(Output("rf-jname", "disabled"), Output("rf-jname", "value"), Output("rf-jname-star", "style"),
                  Output("rf-jname-label", "className"), Input("rf-level", "value"), State("rf-jname", "value"),
                  prevent_initial_call=True)
    def level_changed(level, name):
        national = level == "national"
        return (national, "" if national else name, HIDDEN if national else SHOWN,
                cn("flex items-center gap-2 text-sm leading-none font-medium select-none", "text-muted-foreground" if national else ""))

    # Archived: show the superseded-by panel; back to Latest clears it
    @app.callback(Output("rf-archived-panel", "style"), Output("rf-sup-link", "value"), Output("rf-sup-note", "value"),
                  Input("rf-status", "value"), State("rf-sup-link", "value"), State("rf-sup-note", "value"),
                  prevent_initial_call=True)
    def status_changed(status, link, note):
        if status == "archived":
            return SHOWN, no_update, no_update
        return HIDDEN, "", ""

    # ── save ──
    err_outputs = [Output(f"rf-err-{f}", "children") for f in ERR_FIELDS]

    @app.callback(
        *err_outputs, Output("url", "search", allow_duplicate=True), Output("toast", "data", allow_duplicate=True),
        Input("rf-save", "n_clicks"),
        State("rf-mode", "data"), State("rf-country", "data"), State("rf-source", "value"), State("rf-name-orig", "value"),
        State("rf-name-eng", "value"), State("rf-year", "value"), State("rf-year-revised", "value"),
        State("rf-pages", "value"), State("rf-ptier", "value"), State("rf-stier", "value"), State("rf-level", "value"),
        State("rf-jname", "value"), State("rf-status", "value"), State("rf-machine", "value"),
        State("rf-sup-link", "value"), State("rf-sup-note", "value"), State("rf-overview", "value"),
        State("rf-comment", "value"), State("rf-link", "value"),
        running=[(Output("rf-save", "disabled"), True, False), (Output("rf-cancel", "disabled"), True, False),
                 (Output("rf-save-icon", "className"), "lucide lucide-loader-circle h-4 w-4 mr-2 animate-spin",
                  "lucide lucide-save h-4 w-4 mr-2")],
        prevent_initial_call=True)
    def save(n, mode_d, country, source, name_orig, name_eng, year, year_rev, pages, ptier, stier, level, jname,
             status, machine, sup_link, sup_note, overview, comment, link):
        if not n:
            raise PreventUpdate
        def out(errors: dict | None = None, search=no_update, msg=no_update):
            e = [(errors or {}).get(f, "") for f in ERR_FIELDS]
            return (*e, search, msg)

        user, err = auth.write_guard()
        if err:
            return out(msg=toast("error", err))
        mode = (mode_d or {}).get("mode", "create")
        rid = (mode_d or {}).get("id")
        data = {
            "country": country or "", "source": source, "name_orig": name_orig, "name_eng": name_eng or "",
            "year": year, "year_revised": year_rev, "pages": pages, "policy_guidance_tier": _tier(ptier),
            "strategy_tier": _tier(stier) if _tier(ptier) == 4 else None, "jurisdiction_level": level or "national",
            "jurisdiction_name": None if level == "national" else jname, "record_status": status or "latest",
            "superseded_by_link": sup_link if status == "archived" else None,
            "superseded_by_note": sup_note if status == "archived" else None,
            "machine_readable": {"Y": True, "N": False}.get(machine), "comment": comment, "overview": overview,
            "link": link,
        }
        # blank text boxes are stored as NULL (never a mix of "" and NULL for a "not set" value)
        for k in ("source", "name_orig", "overview", "comment", "link"):
            if isinstance(data[k], str) and not data[k].strip():
                data[k] = None
        clean, errors = validate_record(data)
        if errors:
            hidden = {k: m for k, m in errors.items() if k not in ERR_FIELDS}
            msg = toast("error", "Please check the form", "; ".join(hidden.values())) if hidden else no_update
            return out(errors, msg=msg)
        try:
            if mode == "edit":
                existing = queries.get_record(rid) if rid else None
                if not existing:
                    return out(msg=toast("error", "Record not found"))
                changed = {k: v for k, v in clean.items() if _normalise_for_diff(v) != _normalise_for_diff(existing.get(k))}
                record = queries.update_record(rid, changed) if changed else existing
                if "link" in changed:
                    jobs.run_background(check_link.apply_link_status, rid, record.get("link"))
                if {"record_status", "name_eng", "name_orig", "year", "source", "policy_guidance_tier", "strategy_tier",
                    "country"} & set(changed):
                    jobs.run_background(indexer.sync_record_metadata, rid)
                log_audit(user, "update", "record", rid, ",".join(sorted(changed)))
            else:
                record = queries.create_record(clean)
                if record.get("link"):
                    jobs.run_background(check_link.apply_link_status, record["id"], record["link"])
                log_audit(user, "create", "record", record["id"])
            queries.db.invalidate("")
        except Exception:  # noqa: BLE001
            log.exception("record save failed")
            return out(msg=toast("error", "Save failed."))
        return out(search=href("record", id=record["id"]),
                   msg=toast("success", "Record created!" if mode == "create" else "Record updated!"))

    # ── attachments (edit mode) ──
    @app.callback(
        Output("rf-doc-version", "data"), Output("toast", "data", allow_duplicate=True),
        Input({"type": "rf-att-upl", "slot": ALL}, "contents"), State({"type": "rf-att-upl", "slot": ALL}, "filename"),
        State("rf-mode", "data"), State("rf-ori-lang", "value"), prevent_initial_call=True)
    def upload(_contents, _names, mode_d, ori_lang):
        if not ctx.triggered_id or not ctx.triggered or not ctx.triggered[0].get("value"):
            raise PreventUpdate
        slot = ctx.triggered_id["slot"]
        contents = {i["id"]["slot"]: i.get("value") for i in ctx.inputs_list[0]}.get(slot)
        names = {i["id"]["slot"]: i.get("value") for i in ctx.states_list[0]}
        rid = (mode_d or {}).get("id")
        user, err = auth.write_guard()
        if err:
            return no_update, toast("error", "Please sign in to upload." if not user and "Sign in" in err else err)
        try:
            lang_code = lang_label = None
            if slot == "ORI":
                existing = next((d for d in queries.get_documents_for_record(rid) if d["lang_type"] == "ORI"), None)
                if existing and existing.get("lang_code"):
                    lang_code, lang_label = existing["lang_code"], existing.get("lang_label")
                elif ori_lang and ori_lang != "none":
                    lang_code, lang_label = ori_lang, LANG_LABELS.get(ori_lang, ori_lang)
                else:
                    return no_update, toast("error", "Select the document’s language before uploading.")
            doc = documents.attach_document(rid, slot, names.get(slot) or "file", documents.decode_data_url(contents),
                                            lang_code=lang_code, lang_label=lang_label)
            log_audit(user, "upload", "document", doc["id"], names.get(slot))
        except documents.DocumentError as exc:
            return no_update, toast("error", str(exc))
        except Exception:  # noqa: BLE001
            log.exception("attachment upload failed")
            return no_update, toast("error", "Upload failed")
        return time.time(), toast("success", "File attached successfully")

    @app.callback(
        Output("rf-doc-version", "data", allow_duplicate=True), Output("toast", "data", allow_duplicate=True),
        Input({"type": "rf-att-del", "id": ALL}, "n_clicks"), prevent_initial_call=True)
    def remove(_n):
        if not ctx.triggered_id or not ctx.triggered or not ctx.triggered[0].get("value"):
            raise PreventUpdate
        user, err = auth.write_guard()
        if err:
            return no_update, toast("error", err)
        try:
            doc = documents.remove_document(ctx.triggered_id["id"])
        except Exception:  # noqa: BLE001
            return no_update, toast("error", "Failed to remove attachment")
        if not doc:
            return no_update, toast("error", "Failed to remove attachment")
        log_audit(user, "delete", "document", doc["id"], doc.get("file_name"))
        return time.time(), toast("success", "Attachment removed")

    @app.callback(Output("rf-att-ORI", "children"), Output("rf-att-ENG", "children"), Output("rf-ori-lang-wrap", "style"),
                  Input("rf-doc-version", "data"), State("rf-mode", "data"), prevent_initial_call=True)
    def refresh_slots(_v, mode_d):
        rid = (mode_d or {}).get("id")
        docs = queries.get_documents_for_record(rid) if rid else []
        ori = next((d for d in docs if d["lang_type"] == "ORI"), None)
        eng = next((d for d in docs if d["lang_type"] == "ENG"), None)
        return attachment_slot("ORI", ori), attachment_slot("ENG", eng), HIDDEN if ori else SHOWN

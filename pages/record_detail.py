"""Record detail – port of `web/app/records/[id]/page.tsx`, `DocumentUpload.tsx`, `DocumentSection.tsx`,
`DeleteRecordButton.tsx`."""

from __future__ import annotations

import logging
import re
import time

from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

import constants as C
from pages import not_found
from pages.pages_constants import RECORD_DETAIL_TITLE as TITLE
from utils import auth, documents, queries
from utils.audit import log_audit
from utils.components import (machine_readable_badge, original_link_button, record_status_badge, superseded_href,
                              superseded_link_kind, tier_badge, title_class)
from utils.countries_data import resolve_country_name
from utils.icons import icon
from utils.record_form import HIDDEN, SHOWN, LANG_LABELS
from utils.routes import api_url, href
from utils.toast import toast
from utils.ui import (DIALOG_HIDE, DIALOG_SHOW, badge, button, button_class, cn, dialog, dialog_description,
                      dialog_footer, dialog_header, dialog_title, label, link, select, separator, text_input)

log = logging.getLogger(__name__)

H2 = "text-sm font-semibold uppercase tracking-wide text-muted-foreground"


def title(params) -> str:
    rec = queries.get_record((params or {}).get("id") or "")
    return f"{rec['name_eng']} — PIM Repository" if rec else "Record Not Found"


# ── DocumentUpload (two slots) ───────────────────────────────────────────────
def _doc_slot(label_text: str, lang_type: str, existing: dict | None):
    upl_id = {"type": "doc-upl", "slot": lang_type}
    head = html.Div([
        label(label_text, cls="text-sm font-medium"),
        badge("Uploaded", variant="outline", cls="text-xs bg-green-50 text-green-700 border-green-200") if existing else None,
    ], className="flex items-center justify-between")

    controls = []
    if lang_type == "ORI":
        code = (existing or {}).get("lang_code") or ""
        controls.append(html.Div([
            label(["Document language ", html.Span("*", className="text-destructive"),
                   html.Span("(choose “English” if the original is in English)",
                             className="ml-1 font-normal text-muted-foreground")], cls="text-xs font-medium"),
            html.Div([
                select("doc-ori-lang", [("none", "Select language…")] + list(C.NATIVE_LANGUAGES), value=code or "none",
                       placeholder="Select language…", size="sm", cls="w-[180px]", searchable=True, menu_width="14rem"),
                text_input("doc-ori-lang-other", value=(existing or {}).get("lang_label") if code == "other" else "",
                           placeholder="Language name", debounce=False, cls="h-8 text-xs",
                           style=SHOWN if code == "other" else HIDDEN),
            ], className="flex gap-2"),
        ], className="space-y-1"))
    else:
        src = (existing or {}).get("translation_source") or "none"
        controls.append(html.Div([
            label(["English translation source",
                   html.Span("(how the English version was produced)", className="ml-1 font-normal text-muted-foreground")],
                  cls="text-xs font-medium"),
            select("doc-source", [("none", "Not specified")] + [(s["value"], s["label"]) for s in C.TRANSLATION_SOURCES],
                   value=src, placeholder="Select source…", size="sm", cls="w-[200px]"),
        ], className="space-y-1"))

    if existing:
        size = existing.get("file_size")
        size_txt = f"{size / 1024:.0f} KB" if size else "Size unknown"
        if existing.get("lang_label"):
            size_txt += f" · {existing['lang_label']}"
        body = html.Div([
            icon("file-text", "h-5 w-5 text-blue-600 shrink-0"),
            html.Div([
                html.P(existing.get("file_name") or "", className="text-sm font-medium truncate"),
                html.P(size_txt, className="text-xs text-muted-foreground"),
            ], className="flex-1 min-w-0"),
            html.Div([
                html.A(html.Span(icon("external-link", "h-3.5 w-3.5"), title="Open document",
                                 className=button_class("ghost", "icon", "h-7 w-7")),
                       href=api_url(f"/api/download/{existing['id']}"), target="_blank", rel="noopener noreferrer"),
                html.Button(icon("trash-2", "h-3.5 w-3.5"), type="button", title="Delete document",
                            id={"type": "doc-del", "id": existing["id"]},
                            className=button_class("ghost", "icon", "h-7 w-7 text-destructive hover:text-destructive")),
            ], className="flex gap-1 shrink-0"),
        ], className="flex items-center gap-3 p-3 bg-muted/40 rounded-md")
        replace = dcc.Upload(html.Span("Replace with a new file"), id=upl_id, multiple=False, accept="application/pdf",
                             max_size=C.MAX_UPLOAD_BYTES,
                             className="text-xs text-muted-foreground underline cursor-pointer w-fit")
        tail = [body, replace]
    else:
        tail = [dcc.Upload(
            html.Div([icon("upload", "h-6 w-6 text-muted-foreground"),
                      html.P("Click to upload PDF", className="text-xs text-muted-foreground text-center")],
                     className="flex flex-col items-center gap-2"),
            id=upl_id, multiple=False, accept="application/pdf", max_size=C.MAX_UPLOAD_BYTES,
            className="border-2 border-dashed rounded-md p-4 flex flex-col items-center gap-2 cursor-pointer "
                      "hover:border-primary/50 hover:bg-muted/30 transition-colors")]
    return html.Div([head, *controls, *tail], className="border rounded-lg p-4 space-y-3")


def _document_upload(docs: list[dict]):
    eng = next((d for d in docs if d["lang_type"] == "ENG"), None)
    ori = next((d for d in docs if d["lang_type"] == "ORI"), None)
    return [_doc_slot("Native Language Document", "ORI", ori), _doc_slot("English Document", "ENG", eng)]


# ── delete record ────────────────────────────────────────────────────────────
def _delete_dialog():
    return dialog(
        "rec-del-dialog",
        dialog_header(dialog_title("Delete this record?"),
                      dialog_description("This will permanently delete the policy record and any uploaded document "
                                         "files. This action cannot be undone.")),
        dialog_footer(
            button("Cancel", id="rec-del-cancel", variant="outline"),
            button(icon("loader-circle", "h-4 w-4 mr-2 animate-spin", id="rec-del-spin", style=HIDDEN),
                   html.Span("Delete Record", id="rec-del-label"), id="rec-del-confirm", variant="destructive"),
        ))


def _meta_item(lbl: str, value):
    return html.Div([
        html.P(lbl, className="text-xs text-muted-foreground uppercase tracking-wide font-medium"),
        html.P(value if value else "—", className="mt-0.5 font-medium"),
    ])


def layout(params=None):
    rid = (params or {}).get("id") or ""
    r = queries.get_record_with_docs(rid)
    if not r:
        return not_found.layout(params)
    sup_kind = superseded_link_kind(r.get("superseded_by_link"))
    created = r.get("created_at")
    return html.Div([
        dcc.Store(id="doc-record", data=rid),
        dcc.Store(id="doc-version"),
        # breadcrumb
        html.Div([
            link(icon("arrow-left", "h-3.5 w-3.5"), "Back to Repository", href=href("repository"),
                 cls="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"),
            html.Div([
                original_link_button(r.get("link"), r.get("link_ok"), r.get("link_last_ok_at") is not None),
                link(button(icon("pencil", "h-3.5 w-3.5 mr-1.5"), "Edit", variant="outline", size="sm"),
                     href=href("record-edit", id=rid)),
                button(icon("trash-2", "h-3.5 w-3.5 mr-1.5"), "Delete", id="rec-del-open", variant="outline", size="sm",
                       cls="text-destructive hover:text-destructive border-destructive/30 hover:border-destructive/60"),
            ], className="flex items-center gap-2"),
        ], className="flex items-center justify-between"),

        # header
        html.Div([
            html.Div([
                badge(icon("map-pin", "h-3 w-3"), resolve_country_name(r["country"]),
                      html.Span(f"({r['country']})", className="text-[10px] font-mono opacity-60 ml-0.5"),
                      variant="secondary", cls="gap-1"),
                tier_badge(r.get("policy_guidance_tier"), "policy"),
                tier_badge(r.get("strategy_tier"), "strategy"),
                record_status_badge(r.get("record_status"), show_latest=True),
                machine_readable_badge(r.get("machine_readable")),
            ], className="flex items-center gap-2 flex-wrap"),
            html.H1(r["name_eng"], className=title_class(r, "text-xl font-bold leading-snug")),
            html.P(r["name_orig"], className="text-base text-muted-foreground italic") if r.get("name_orig") else None,
        ], className="space-y-3"),

        html.Div([
            html.P("Not machine-readable", className="text-xs font-semibold uppercase tracking-wide text-red-700"),
            html.P("At least one attached file is an image-only scan. It is not sent to the AI index; run OCR, validate "
                   "the result, re-upload the searchable file (or set Machine Readable to Yes on the record once "
                   "validated).", className="text-sm text-red-900 leading-relaxed"),
        ], className="bg-red-50 border border-red-200 rounded-md p-4 space-y-1") if r.get("machine_readable") is False else None,

        html.Div([
            html.P([icon("archive", "h-3.5 w-3.5"), "Superseded"],
                   className="text-xs font-semibold uppercase tracking-wide text-gray-500 flex items-center gap-1.5"),
            html.P(r["superseded_by_note"], className="text-sm text-gray-700 leading-relaxed") if r.get("superseded_by_note") else None,
            link("View the current policy record", icon("arrow-left", "h-3.5 w-3.5 rotate-180"),
                 href=superseded_href(r["superseded_by_link"]),
                 cls="flex items-center gap-1.5 text-sm text-blue-700 hover:underline w-fit")
            if sup_kind == "internal" else None,
            html.A(["View the current policy", icon("external-link", "h-3.5 w-3.5")], href=r["superseded_by_link"],
                   target="_blank", rel="noopener noreferrer",
                   className="flex items-center gap-1.5 text-sm text-blue-700 hover:underline w-fit")
            if sup_kind == "external" else None,
        ], className="bg-gray-50 border border-gray-200 rounded-md p-4 space-y-1.5")
        if r.get("record_status") == "archived" and (r.get("superseded_by_note") or r.get("superseded_by_link")) else None,

        separator(),

        html.Div([
            _meta_item("Year", str(r["year"]) if r.get("year") else None),
            _meta_item("Year Revised", str(r["year_revised"]) if r.get("year_revised") else None),
            _meta_item("Source", r.get("source")),
            _meta_item("Pages", str(r["pages"]) if r.get("pages") else None),
        ], className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm"),

        html.Div([
            html.H2("Overview", className=H2),
            html.P(r["overview"], className="text-sm leading-relaxed text-foreground/90 whitespace-pre-line"),
        ], className="space-y-2") if r.get("overview") else None,

        html.Div([
            html.P("Internal Comment", className="text-xs font-semibold uppercase tracking-wide text-amber-700"),
            html.P(r["comment"], className="text-sm text-amber-900 leading-relaxed"),
        ], className="bg-amber-50 border border-amber-200 rounded-md p-4 space-y-1") if r.get("comment") else None,

        html.A([icon("external-link", "h-3.5 w-3.5"), "View source document"], href=r["link"], target="_blank",
               rel="noopener noreferrer", className="flex items-center gap-2 text-sm text-blue-700 hover:underline w-fit")
        if r.get("link") and re.match(r"^https?://", r["link"], re.I) else None,

        separator(),

        html.Div([
            html.H2("Document Files", className=H2),
            html.Div([
                html.P([icon("info", "h-3.5 w-3.5 shrink-0"), "Recommendation"],
                       className="text-xs font-semibold uppercase tracking-wide text-blue-700 flex items-center gap-1.5"),
                html.P("When adding documents, please follow this final naming convention:",
                       className="text-sm text-blue-900 leading-relaxed"),
                html.P("[Country] [Agency] [Year] [Name] [Language: Original Language (OL) or English (ENG)]",
                       className="text-xs font-mono text-blue-800 bg-blue-100 rounded px-2.5 py-1.5 border border-blue-200"),
                html.Div([
                    html.P("Serbia Cabinet 2023 O KAPITALNIM PROJEKTIMA Decree on Public Investment Management No 23 (OL)"),
                    html.P("Serbia Cabinet 2023 Decree on Public Investment Management No 23 (ENG)"),
                ], className="text-xs font-mono text-blue-800 space-y-1 leading-relaxed"),
                html.P("To allow for formal citation of policies, please provide formal designation of the policy "
                       "(e.g., Cabinet Decision 9/2026).", className="text-sm text-blue-900 leading-relaxed"),
            ], className="border border-blue-200 bg-blue-50 rounded-md p-4 space-y-2.5"),
            html.Div(_document_upload(r["documents"]), id="doc-section", className="space-y-4"),
        ], className="space-y-4"),

        html.Div([
            html.Span(f"ID: {r['id']}"),
            html.Span(f"Tokens: {r['tokens']:,}") if r.get("tokens") else None,
            html.Span(f"Added: {created.month}/{created.day}/{created.year}") if created else None,
        ], className="text-xs text-muted-foreground flex gap-4 pt-2"),

        _delete_dialog(),
    ], className="max-w-3xl mx-auto space-y-8")


# ── callbacks ───────────────────────────────────────────────────────────────
def register_callbacks(app):
    @app.callback(Output("doc-ori-lang-other", "style"), Input("doc-ori-lang", "value"), prevent_initial_call=True)
    def other_visible(code):
        return SHOWN if code == "other" else HIDDEN

    @app.callback(
        Output("doc-version", "data"), Output("toast", "data", allow_duplicate=True),
        Input({"type": "doc-upl", "slot": ALL}, "contents"), State({"type": "doc-upl", "slot": ALL}, "filename"),
        State("doc-record", "data"), State("doc-ori-lang", "value"), State("doc-ori-lang-other", "value"),
        State("doc-source", "value"), prevent_initial_call=True)
    def upload(_c, _n, rid, ori_code, ori_other, source):
        if not ctx.triggered_id or not ctx.triggered or not ctx.triggered[0].get("value"):
            raise PreventUpdate
        slot = ctx.triggered_id["slot"]
        contents = {i["id"]["slot"]: i.get("value") for i in ctx.inputs_list[0]}.get(slot)
        names = {i["id"]["slot"]: i.get("value") for i in ctx.states_list[0]}
        # Original-language documents must have a language selected first.
        if slot == "ORI" and (not ori_code or ori_code == "none"):
            return no_update, toast("error", "Select the document’s language before uploading.")
        user, err = auth.write_guard()
        if err:
            return no_update, toast("error", "Please sign in to upload." if not user and "Sign in" in err else err)
        label_text = "Native Language Document" if slot == "ORI" else "English Document"
        try:
            kw = {}
            if slot == "ORI":
                lab = (ori_other or "").strip() if ori_code == "other" else LANG_LABELS.get(ori_code, ori_code)
                kw = dict(lang_code=ori_code, lang_label=lab or LANG_LABELS.get(ori_code, ori_code))
            else:
                kw = dict(translation_source=None if source in (None, "none") else source)
            doc = documents.attach_document(rid, slot, names.get(slot) or "file", documents.decode_data_url(contents), **kw)
            log_audit(user, "upload", "document", doc["id"], names.get(slot))
        except documents.DocumentError as exc:
            return no_update, toast("error", str(exc))
        except Exception:  # noqa: BLE001
            log.exception("document upload failed")
            return no_update, toast("error", "Upload failed")
        return time.time(), toast("success", f"{label_text} uploaded successfully")

    @app.callback(
        Output("doc-version", "data", allow_duplicate=True), Output("toast", "data", allow_duplicate=True),
        Input({"type": "doc-del", "id": ALL}, "n_clicks"), prevent_initial_call=True)
    def remove(_n):
        if not ctx.triggered_id or not ctx.triggered or not ctx.triggered[0].get("value"):
            raise PreventUpdate
        user, err = auth.write_guard()
        if err:
            return no_update, toast("error", err)
        try:
            doc = documents.remove_document(ctx.triggered_id["id"])
        except Exception:  # noqa: BLE001
            return no_update, toast("error", "Failed to delete document")
        if not doc:
            return no_update, toast("error", "Failed to delete document")
        log_audit(user, "delete", "document", doc["id"], doc.get("file_name"))
        return time.time(), toast("success", "Document removed")

    @app.callback(Output("toast", "data", allow_duplicate=True), Input("doc-source", "value"), State("doc-record", "data"),
                  prevent_initial_call=True)
    def source_changed(value, rid):
        eng = next((d for d in queries.get_documents_for_record(rid) if d["lang_type"] == "ENG"), None)
        new = None if value in (None, "none") else value
        if not eng or (eng.get("translation_source") or None) == new:
            raise PreventUpdate          # not uploaded yet — the choice is applied at upload time
        user, err = auth.write_guard()
        if err:
            return toast("error", err)
        try:
            documents.set_translation_source(eng["id"], new)
        except Exception:  # noqa: BLE001
            return toast("error", "Could not update translation source")
        raise PreventUpdate

    @app.callback(Output("doc-section", "children"), Input("doc-version", "data"), State("doc-record", "data"),
                  prevent_initial_call=True)
    def refresh(_v, rid):
        return _document_upload(queries.get_documents_for_record(rid))

    # delete record
    @app.callback(Output("rec-del-dialog", "style"), Input("rec-del-open", "n_clicks"), Input("rec-del-dialog-x", "n_clicks"),
                  Input("rec-del-dialog-overlay", "n_clicks"), Input("rec-del-cancel", "n_clicks"), prevent_initial_call=True)
    def toggle(_o, _x, _ov, _c):
        if not ctx.triggered or not ctx.triggered[0].get("value"):
            raise PreventUpdate
        return DIALOG_SHOW if ctx.triggered_id == "rec-del-open" else DIALOG_HIDE

    @app.callback(
        Output("rec-del-dialog", "style", allow_duplicate=True), Output("url", "search", allow_duplicate=True),
        Output("toast", "data", allow_duplicate=True), Input("rec-del-confirm", "n_clicks"), State("doc-record", "data"),
        running=[(Output("rec-del-confirm", "disabled"), True, False), (Output("rec-del-cancel", "disabled"), True, False),
                 (Output("rec-del-spin", "style"), SHOWN, HIDDEN), (Output("rec-del-label", "children"), "Deleting…", "Delete Record")],
        prevent_initial_call=True)
    def confirm_delete(n, rid):
        if not n:
            raise PreventUpdate
        user, err = auth.write_guard()
        if err:
            return DIALOG_HIDE, no_update, toast("error", err)
        try:
            ok = documents.delete_record(rid)
        except Exception:  # noqa: BLE001
            log.exception("delete record failed")
            ok = False
        if not ok:
            return DIALOG_HIDE, no_update, toast("error", "Failed to delete record")
        log_audit(user, "delete", "record", rid)
        return DIALOG_HIDE, href("home"), toast("success", "Record deleted")

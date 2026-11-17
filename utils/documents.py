"""
Document service: the one place that stores / replaces / removes a record's files.

Used by the upload callbacks, the “Translate to English” job and the batch importer.
Ports `app/api/documents/{upload,save,[id]}`, `modules/documents/queries.ts` and the `after(() => syncDocumentToRag…)`
hooks: after every change the search index is updated in a background thread.
"""

from __future__ import annotations

import base64
import logging

import constants as C
from utils import extract, indexer, jobs, queries, storage

log = logging.getLogger(__name__)

ALLOWED_EXT = tuple(e.lstrip(".") for e in C.ALLOWED_DOC_EXTENSIONS)
TRANSLATION_SOURCE_VALUES = [s["value"] for s in C.TRANSLATION_SOURCES]


def decode_data_url(contents: str | None) -> bytes:
    """`dcc.Upload.contents` ("data:<mime>;base64,<payload>") → raw bytes."""
    if not contents or "," not in contents:
        raise DocumentError("The file could not be read.")
    try:
        return base64.b64decode(contents.split(",", 1)[1])
    except Exception as exc:  # noqa: BLE001
        raise DocumentError("The file could not be read.") from exc


class DocumentError(ValueError):
    """User-facing validation error."""


def is_translation_source(v) -> bool:
    return isinstance(v, str) and v in TRANSLATION_SOURCE_VALUES


def attach_document(record_id: str, lang_type: str, file_name: str, data: bytes, lang_code: str | None = None,
                    lang_label: str | None = None, translation_source: str | None = None,
                    text_layer: bool | None = ..., index: bool = True) -> dict:
    """Store `data` as the record's ENG/ORI document (replacing the previous file of that slot)."""
    if lang_type not in ("ENG", "ORI"):
        raise DocumentError("langType must be ENG or ORI")
    if lang_type == "ORI" and not lang_code:
        raise DocumentError("Select the document's original language before uploading.")
    ext = extract.ext_of(file_name)
    if ext not in ALLOWED_EXT:
        raise DocumentError(f"File type not allowed. Accepted: {', '.join(ALLOWED_EXT)}")
    if len(data) > C.MAX_UPLOAD_BYTES:
        raise DocumentError(f"File is too large (max {C.MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")
    rec = queries.get_record(record_id)
    if not rec:
        raise DocumentError("Record not found.")
    if lang_type == "ENG":
        lang_code, lang_label = lang_code or "en", lang_label or "English"
    key = storage.make_key(rec["country"], queries.db.uid(), file_name)
    size = storage.save(key, data)
    if text_layer is ...:
        try:
            text_layer = extract.detect_text_layer(data, file_name)
        except Exception:  # noqa: BLE001 - best effort; leaves the flag unknown
            text_layer = None
    doc = queries.upsert_document(
        record_id, lang_type, key, lang_code=lang_code, lang_label=lang_label, file_name=file_name, file_size=size,
        translation_source=translation_source if is_translation_source(translation_source) else None,
        text_layer=text_layer)
    replaced = doc.pop("_replaced_path", None)
    if replaced:
        try:
            storage.delete(replaced)
        except Exception:  # noqa: BLE001
            log.warning("could not delete replaced file %s", replaced, exc_info=True)
    queries.db.invalidate("")
    if index:
        jobs.run_background(indexer.index_document, doc["id"])
    return doc


def remove_document(doc_id: str) -> dict | None:
    doc = queries.delete_document(doc_id)
    if not doc:
        return None
    try:
        storage.delete(doc["file_path"])
    except Exception:  # noqa: BLE001 - idempotent on “not found”
        log.warning("file deletion failed: %s", doc["file_path"], exc_info=True)
    jobs.run_background(indexer.delete_chunks, doc["record_id"], doc["lang_type"])
    queries.db.invalidate("")
    return doc


def set_translation_source(doc_id: str, source: str | None) -> dict | None:
    if source is not None and not is_translation_source(source):
        raise DocumentError("Invalid translation source")
    doc = queries.update_document_source(doc_id, source)
    queries.db.invalidate("")
    return doc


def translate_record_to_english(job_id: str, record_id: str, progress_fn=None) -> dict:
    """Job body for “Translate to English”: original document → mAI translation → ENG slot (provenance `mai`)."""
    from utils import translate  # local import keeps module import light

    rec = queries.get_record_with_docs(record_id)
    if not rec:
        raise DocumentError("Record not found")
    ori = next((d for d in rec["documents"] if d["lang_type"] == "ORI"), None)
    eng = next((d for d in rec["documents"] if d["lang_type"] == "ENG"), None)
    if not ori or not ori.get("file_path"):
        raise DocumentError("This record has no original-language document to translate.")
    if eng:
        raise DocumentError("This record already has an English document.")
    data = storage.read(ori["file_path"])
    out, name = translate.translate_document_to_english(
        data, ori.get("file_name"), ori.get("lang_label"),
        progress=lambda done, total: jobs.update(job_id, progress={"done": done, "total": total}))
    attach_document(record_id, "ENG", name, out, lang_code="en", lang_label="English", translation_source="mai",
                    text_layer=True)
    return {"file_name": name}


def delete_record(record_id: str) -> bool:
    """Delete a record with its documents, stored files and search-index chunks."""
    rec = queries.get_record(record_id)
    if not rec:
        return False
    for d in queries.delete_record(record_id):
        try:
            storage.delete(d["file_path"])
        except Exception:  # noqa: BLE001
            log.warning("file deletion failed: %s", d.get("file_path"), exc_info=True)
    queries.db.invalidate("")
    from utils import rag  # noqa: PLC0415

    rag.invalidate()
    return True

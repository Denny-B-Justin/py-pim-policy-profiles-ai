"""
Document service: the one place that stores / replaces / removes a record's files.

Used by the upload callbacks and the batch importer.  An upload does two things in parallel with the same bytes:
    1. stores the file in the UC volume (utils/storage.py – upload only, never read back)
    2. chunks it in the app and writes the chunks to the chunks table, then requests an index sync (utils/indexer.py)
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
    # the slot keeps its id on replacement; the file is filed in the volume by country / national-subnational
    current = queries.get_slot_document(record_id, lang_type)
    doc_id = current["id"] if current else queries.db.uid()
    country_name = queries.country_name_map().get(rec["country"], rec["country"])
    key = storage.upload_key(rec, country_name, doc_id, file_name)
    if text_layer is ...:
        try:
            text_layer = extract.detect_text_layer(data, file_name)
        except Exception:  # noqa: BLE001 - best effort; leaves the flag unknown
            text_layer = None
    doc = queries.upsert_document(
        doc_id, record_id, lang_type, key, lang_code=lang_code, lang_label=lang_label, file_name=file_name,
        file_size=len(data), translation_source=translation_source if is_translation_source(translation_source) else None,
        text_layer=text_layer)
    queries.db.invalidate("")
    # chunking runs in the background from these bytes while the file is uploaded to the volume
    if index:
        jobs.run_background(indexer.index_document, doc["id"], data)
    try:
        storage.save(key, data)
    except Exception as exc:  # noqa: BLE001
        log.exception("volume upload failed for %s", storage.location(key))
        raise DocumentError("The file could not be uploaded to the document volume.") from exc
    return doc


def remove_document(doc_id: str) -> dict | None:
    """Remove a document row and its chunks (the uploaded file stays in the volume)."""
    doc = queries.delete_document(doc_id)
    if not doc:
        return None
    jobs.run_background(indexer.delete_document_chunks, doc)
    queries.db.invalidate("")
    return doc


def set_translation_source(doc_id: str, source: str | None) -> dict | None:
    if source is not None and not is_translation_source(source):
        raise DocumentError("Invalid translation source")
    doc = queries.update_document_source(doc_id, source)
    queries.db.invalidate("")
    return doc


def delete_record(record_id: str) -> bool:
    """Delete a record with its documents and search-index chunks (uploaded files stay in the volume)."""
    rec = queries.get_record(record_id)
    if not rec:
        return False
    for d in queries.delete_record(record_id):
        indexer.delete_document_chunks(d)
    queries.db.invalidate("")
    from utils import rag  # noqa: PLC0415

    rag.invalidate()
    return True

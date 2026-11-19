"""
Search-index maintenance (port of `web/lib/rag-sync.ts` + `rag/app/ingestion/pipeline.py`).

The index lives in the Delta table **`policy_repo_document_chunks`** (one row per chunk) – the source of the
Databricks AI Search Delta Sync index (utils/ai_search.py).

    upload / replace  → index_document()          the uploaded bytes (in memory, never read back from the volume)
                                                   → extract → chunk (utils/chunking.py) → MERGE rows → delete the
                                                   document's surplus rows → ingest log → request an index sync
    delete            → delete_document_chunks()
    record edited     → sync_record_metadata()     rewrites the record fields copied onto its chunks

Write semantics: rows are upserted by `chunk_id` (= `<document_id>:<chunk_index>`) and only rewritten when their
`row_hash` changes, then the document's rows past the new last chunk are deleted – a document is never missing
from the index while it is being re-chunked, and unchanged passages are not re-embedded.
"""

from __future__ import annotations

import json
import logging
import time

import constants as C
from utils import ai_search, db, extract, queries, rag
from utils.chunking import CHUNK_COLUMNS, CHUNKER_VERSION, apply_record_metadata, chunk_pages, chunk_rows
from utils.db import table

log = logging.getLogger(__name__)

_CHUNKS = "document_chunks"
_LOG = "document_ingest_log"

# Spark SQL types of CHUNK_COLUMNS (for from_json in the MERGE) – keep in step with utils/schema.py
_SPARK_TYPES = {"chunk_index": "INT", "page": "INT", "page_end": "INT", "char_count": "INT", "year": "INT",
                "policy_guidance_tier": "INT", "strategy_tier": "INT"}
_ROWS_SCHEMA = "ARRAY<STRUCT<" + ", ".join(f"`{c}`: {_SPARK_TYPES.get(c, 'STRING')}" for c in CHUNK_COLUMNS) + ">>"


def _sqlite() -> bool:
    return C.DB_BACKEND == "sqlite"


def _retry(fn, *args, attempts: int = 4):
    """Delta has optimistic concurrency: a concurrent writer can make a MERGE / DELETE fail."""
    for i in range(attempts):
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if i + 1 < attempts and ("concurrent" in msg or "conflict" in msg):
                time.sleep(2 * (i + 1))
                continue
            raise


def _after_write() -> None:
    rag.invalidate()
    ai_search.request_sync()


# ── table writes ─────────────────────────────────────────────────────────────
def _merge_rows(rows: list[dict]) -> None:
    """Upsert chunk rows (rewritten only when row_hash changed)."""
    if not rows:
        return
    if _sqlite():
        now = db.now()
        for r in rows:
            db.execute(f"DELETE FROM {table(_CHUNKS)} WHERE chunk_id = :i", {"i": r["chunk_id"]})
            cols = CHUNK_COLUMNS + ["created_at", "updated_at"]
            db.execute(f"INSERT INTO {table(_CHUNKS)} ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})",
                       {**r, "created_at": now, "updated_at": now})
        return
    sets = ", ".join(f"t.`{c}` = s.`{c}`" for c in CHUNK_COLUMNS if c != "chunk_id")
    cols = ", ".join(f"`{c}`" for c in CHUNK_COLUMNS)
    vals = ", ".join(f"s.`{c}`" for c in CHUNK_COLUMNS)
    sql = (f"MERGE INTO {table(_CHUNKS)} AS t "
           f"USING (SELECT inline(from_json(:rows, '{_ROWS_SCHEMA}'))) AS s "
           f"ON t.chunk_id = s.chunk_id "
           f"WHEN MATCHED AND t.row_hash <> s.row_hash THEN UPDATE SET {sets}, t.updated_at = current_timestamp() "
           f"WHEN NOT MATCHED THEN INSERT ({cols}, created_at, updated_at) "
           f"VALUES ({vals}, current_timestamp(), current_timestamp())")
    for i in range(0, len(rows), C.CHUNK_WRITE_BATCH):
        payload = json.dumps(rows[i:i + C.CHUNK_WRITE_BATCH], ensure_ascii=False)
        _retry(db.execute, sql, {"rows": payload})


def _write_document_rows(document_id: str, rows: list[dict]) -> None:
    """Replace a document's chunks: upsert the new rows, then drop rows past the new last chunk."""
    _merge_rows(rows)
    _retry(db.execute, f"DELETE FROM {table(_CHUNKS)} WHERE document_id = :d AND chunk_index >= :n",
           {"d": document_id, "n": len(rows)})
    db.invalidate(_CHUNKS)


def _delete_rows(where: str, params: dict) -> None:
    _retry(db.execute, f"DELETE FROM {table(_CHUNKS)} WHERE {where}", params)
    db.invalidate(_CHUNKS)


def _log(doc: dict, status: str, detail: str | None, chunk_count: int, source: str,
         file_path: str | None = None, file_size: int | None = None) -> None:
    """One row per document with its last chunking outcome (best effort – never fails the indexing)."""
    try:
        db.execute(f"DELETE FROM {table(_LOG)} WHERE document_id = :d", {"d": doc["id"]})
        db.insert(_LOG, {
            "document_id": doc["id"], "record_id": doc.get("record_id"), "lang_type": doc.get("lang_type"),
            "file_path": file_path, "file_size": file_size if file_size is not None else doc.get("file_size"),
            "status": status, "detail": (detail or "")[:2000] or None, "chunk_count": int(chunk_count),
            "chunker_version": CHUNKER_VERSION, "source": source, "processed_at": db.now()})
    except Exception:  # noqa: BLE001
        log.warning("ingest log write failed for %s", doc.get("id"), exc_info=True)


# ── public API (unchanged signatures) ────────────────────────────────────────
def delete_document_chunks(doc: dict) -> None:
    _delete_rows("document_id = :d", {"d": doc["id"]})
    try:
        db.execute(f"DELETE FROM {table(_LOG)} WHERE document_id = :d", {"d": doc["id"]})
    except Exception:  # noqa: BLE001
        log.warning("ingest log cleanup failed for document %s", doc.get("id"), exc_info=True)
    _after_write()


def index_document(doc_id: str, data: bytes, source: str = "app_upload") -> int:
    """(Re-)index one document from its uploaded bytes.  Returns the number of chunks written (0 = nothing indexable)."""
    doc = queries.get_document(doc_id)
    if not doc:
        return 0
    rec = queries.get_record(doc["record_id"])
    if not rec:
        return 0
    path = doc["file_path"]
    # Image-only files have nothing to index – they are flagged on the record (machine_readable = false).  Any
    # chunks of a previous (text) file in the same slot are removed so they cannot be cited any more.
    if doc["text_layer"] is False:
        log.info("index skip %s/%s: no text layer (needs OCR)", rec["id"], doc["lang_type"])
        _delete_rows("document_id = :d", {"d": doc_id})
        _log(doc, "no_text_layer", "Scanned / image-only file – needs OCR before it can be searched.", 0, source, path)
        _after_write()
        return 0
    try:
        pages = extract.load_pages(data, doc["file_name"])
    except ValueError as exc:
        if str(exc).startswith("Unsupported file type"):
            _delete_rows("document_id = :d", {"d": doc_id})
            _log(doc, "unsupported", str(exc), 0, source, path)
            _after_write()
            return 0
        _log(doc, "failed", f"{type(exc).__name__}: {exc}", 0, source, path)
        raise
    except Exception as exc:  # noqa: BLE001
        _log(doc, "failed", f"{type(exc).__name__}: {exc}", 0, source, path)
        raise
    chunks = chunk_pages(pages)
    country_name = queries.country_name_map().get(rec["country"], rec["country"])
    rows = chunk_rows(chunks, doc, rec, country_name, path)
    _write_document_rows(doc_id, rows)            # zero rows → the document's old rows are removed
    _log(doc, "indexed" if rows else "empty", None if rows else "No extractable text.", len(rows), source, path,
         len(data))
    _after_write()
    log.info("indexed %s/%s → %d chunks", rec["id"], doc["lang_type"], len(rows))
    return len(rows)


def sync_record_metadata(record_id: str) -> int:
    """A record was edited: copy its new fields (country, names, year, tiers, status …) onto its chunks.

    Only rows whose content actually changes are rewritten, so AI Search re-embeds just those.  Returns the number
    of rows updated."""
    rec = queries.get_record(record_id)
    if not rec:
        return 0
    cols = ", ".join(CHUNK_COLUMNS)
    rows = db.query(f"SELECT {cols} FROM {table(_CHUNKS)} WHERE record_id = :r", {"r": record_id})
    if not rows:
        return 0
    country_name = queries.country_name_map().get(rec["country"], rec["country"])
    changed = []
    for r in rows:
        new = apply_record_metadata(r, rec, country_name)
        if new["row_hash"] != r.get("row_hash"):
            changed.append(new)
    if changed:
        _merge_rows(changed)
        db.invalidate(_CHUNKS)
        _after_write()
    else:
        rag.invalidate()
    return len(changed)

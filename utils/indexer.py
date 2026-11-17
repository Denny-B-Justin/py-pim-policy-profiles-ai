"""
Search-index maintenance (port of `web/lib/rag-sync.ts` + `rag/app/ingestion/pipeline.py`).

Instead of POSTing to a separate RAG service, the Dash app extracts + chunks the stored file itself.  The chunks
are stored as **files in the volume** (no table): one JSON file per document in the `chunks/` area,

    <record-id>__<ENG|ORI>__<document-id>.json   {"document_id", "record_id", "lang_type", "lang_code",
                                                  "created_at", "chunks": [{section_heading, page,
                                                  chunk_index, content}, …]}

The file holds only what comes from the document; the record's descriptive fields (country, names, year, tiers,
status) are joined from the records table when the search index is loaded (utils/rag.py), so editing a record
needs no index update.  Hooks are called (in background threads) after every document upload / delete;
`reconcile()` repairs drift (the nightly cron of the original).
"""

from __future__ import annotations

import json
import logging
import threading

import constants as C
from utils import db, extract, queries, rag, storage
from utils.chunking import chunk_pages
from utils.db import table

log = logging.getLogger(__name__)
_AREA = C.CHUNKS_DIR


# ── chunk files ──────────────────────────────────────────────────────────────
def chunk_file_name(record_id: str, lang_type: str, document_id: str) -> str:
    return f"{record_id}__{lang_type}__{document_id}.json"


def parse_chunk_file_name(name: str) -> tuple[str, str, str] | None:
    """→ (record_id, lang_type, document_id), or None for a file that is not a chunk file."""
    if not name.endswith(".json"):
        return None
    parts = name[:-5].split("__")
    return (parts[0], parts[1], parts[2]) if len(parts) == 3 else None


def chunk_files() -> list[dict]:
    """[{name, size, modified, record_id, lang_type, document_id}] of every chunk file."""
    out = []
    for f in storage.list_files(_AREA):
        parsed = parse_chunk_file_name(f["name"])
        if parsed:
            out.append({**f, "record_id": parsed[0], "lang_type": parsed[1], "document_id": parsed[2]})
    return out


def read_chunk_file(name: str) -> dict:
    return json.loads(storage.read(name, area=_AREA).decode("utf-8"))


def delete_chunks(record_id: str, lang_type: str | None = None) -> None:
    """Remove the index files of a record (optionally only its ENG or ORI document)."""
    for f in chunk_files():
        if f["record_id"] == record_id and (lang_type is None or f["lang_type"] == lang_type):
            storage.delete(f["name"], area=_AREA)
    rag.invalidate()


def delete_document_chunks(doc: dict) -> None:
    storage.delete(chunk_file_name(doc["record_id"], doc["lang_type"], doc["id"]), area=_AREA)
    rag.invalidate()


def index_document(doc_id: str) -> int:
    """(Re-)index one document.  Returns the number of chunks written (0 = skipped)."""
    doc = queries.get_document(doc_id)
    if not doc:
        return 0
    rec = queries.get_record(doc["record_id"])
    if not rec:
        return 0
    # Image-only files have nothing to index – they are flagged on the record (machine_readable = false).
    if doc["text_layer"] is False:
        log.info("index skip %s/%s: no text layer (needs OCR)", rec["id"], doc["lang_type"])
        return 0
    if not doc["file_path"]:
        raise FileNotFoundError(storage.NOT_IN_REPOSITORY)
    data = storage.read(doc["file_path"])
    pages = extract.load_pages(data, doc["file_name"])
    chunks = chunk_pages(pages)
    name = chunk_file_name(rec["id"], doc["lang_type"], doc["id"])

    # replace semantics: only after the new content loaded + chunked successfully
    if chunks:
        payload = {
            "document_id": doc["id"], "record_id": rec["id"], "lang_type": doc["lang_type"],
            "lang_code": doc["lang_code"], "created_at": db.now().isoformat(sep=" "),
            "chunks": [{"section_heading": c["section_heading"], "page": c["page"], "chunk_index": c["chunk_index"],
                        "content": c["content"]} for c in chunks],
        }
        storage.save(name, json.dumps(payload, ensure_ascii=False).encode("utf-8"), area=_AREA)
    else:
        storage.delete(name, area=_AREA)
    # (a replaced file keeps its slot's document id, so it overwrites the same chunk file; orphans of deleted
    #  documents are removed by delete_document_chunks() and, as a safety net, by reconcile())
    rag.invalidate()
    log.info("indexed %s/%s → %d chunks", rec["id"], doc["lang_type"], len(chunks))
    return len(chunks)


def index_record(record_id: str) -> int:
    return sum(index_document(d["id"]) for d in queries.get_documents_for_record(record_id))


def sync_record_metadata(record_id: str) -> None:
    """Record fields are joined into the index when it loads – just make the next search reload it."""
    rag.invalidate()


def reconcile(max_ingests: int = 12) -> dict:
    """Diff documents ↔ chunk files and repair the drift (what the nightly cron did in the original)."""
    files = chunk_files()
    indexed = {f["document_id"] for f in files}
    docs = db.query(f"SELECT id, record_id, lang_type, text_layer, blob_url FROM {table('documents')}")
    present = {d["id"] for d in docs}
    # documents whose file is not in the repository yet are left for after the transfer
    missing = [d for d in docs if d["id"] not in indexed and db.to_bool(d["text_layer"]) is not False
               and storage.document_path(d)]
    ghosts = [f for f in files if f["document_id"] not in present]
    ingested = 0
    for d in missing[:max_ingests]:
        try:
            if index_document(d["id"]):
                ingested += 1
        except Exception:  # noqa: BLE001
            log.exception("reconcile: indexing %s failed", d["id"])
    for f in ghosts:
        storage.delete(f["name"], area=_AREA)
    rag.invalidate()
    return {"missing": len(missing), "ingested": ingested, "ghosts": len(ghosts), "ghosts_deleted": len(ghosts),
            "remaining": len(missing) - ingested}


_reindex_lock = threading.Lock()     # one rebuild at a time (a 2nd run would interleave with – and re-clear – the first)


def reindex_running() -> bool:
    return _reindex_lock.locked()


def reindex(lang_filter: str = "eng", clear_existing: bool = True, status=None, progress=None) -> dict:
    """Port of `rag/app/api/reindex.py` – rebuild the chunk files from the stored documents.

    lang_filter     'all' | 'eng' | 'ori'.  The RAG console clears the index for all/eng and *appends* for ori.
    status(msg)     human-readable phase text ("Found 42 documents to index.")
    progress(dict)  {current, total, document, status: "done"|"error", chunks?, error?} after every document

    Fails fast – BEFORE the destructive clear – when document storage is unreachable, so a dead dependency can't
    leave the index empty.  Returns {succeeded, failed, total_chunks[, message]}.
    """
    say = status or (lambda _m: None)
    tick = progress or (lambda _p: None)
    lang = {"eng": "ENG", "ori": "ORI", "all": None}.get((lang_filter or "").lower(), "ENG")
    if not _reindex_lock.acquire(blocking=False):
        raise RuntimeError("A reindex is already running.")
    try:
        say("Collecting documents from the repository…")
        sql = (f"SELECT d.id, d.blob_url, d.lang_type, r.country, r.name_eng FROM {table('documents')} d "
               f"JOIN {table('policy_records')} r ON r.id = d.record_id")
        params: dict = {}
        if lang:
            sql += " WHERE d.lang_type = :l"
            params["l"] = lang
        items = db.query(sql + " ORDER BY r.country, r.name_eng, d.lang_type", params)
        total = len(items)
        if total == 0:
            return {"succeeded": 0, "failed": 0, "total_chunks": 0, "message": "No documents matched the filter."}
        say(f"Found {total} documents to index.")

        if clear_existing:
            say("Preflight: checking document access…")
            try:
                probe = next((p for p in map(storage.document_path, items) if p), None)
                if probe is None:
                    raise FileNotFoundError("none of the documents is in the repository yet")
                storage.read(probe)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Preflight failed – document storage is unreachable ({exc}). "
                                   "The existing index was left untouched.") from exc
            say("Clearing existing index…")
            files = chunk_files()
            for f in files:
                storage.delete(f["name"], area=_AREA)
            rag.invalidate()
            say(f"Cleared {len(files)} existing index files.")

        ok = failed = chunks = 0
        for i, d in enumerate(items, 1):
            label = f"{d['country']} — {d['name_eng']} ({d['lang_type']})"
            try:
                n = index_document(d["id"])
                ok += 1
                chunks += n
                tick({"current": i, "total": total, "document": label, "status": "done", "chunks": n})
            except Exception as exc:  # noqa: BLE001
                log.exception("reindex: %s failed", label)
                failed += 1
                tick({"current": i, "total": total, "document": label, "status": "error",
                      "error": str(exc) or type(exc).__name__})
        rag.invalidate()
        return {"succeeded": ok, "failed": failed, "total_chunks": chunks}
    finally:
        _reindex_lock.release()


def reindex_all(progress=None) -> dict:
    """Rebuild the whole index (kept for scripts / cron)."""
    res = reindex("all", True, progress=(lambda p: progress(p["current"], p["total"])) if progress else None)
    return {"documents": res["succeeded"] + res["failed"], "indexed": res["succeeded"], "failed": res["failed"]}

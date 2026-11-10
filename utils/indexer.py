"""
Search-index maintenance (port of `web/lib/rag-sync.ts` + `rag/app/ingestion/pipeline.py`).

Instead of POSTing to a separate RAG service, the Dash app extracts + chunks the stored file itself and
writes the chunk rows to `pim_document_chunks`.  Hooks are called (in background threads) after every
document upload / delete / record status change; `reconcile()` repairs drift (the nightly cron of the
original).
"""

from __future__ import annotations

import logging
import threading

from utils import db, extract, queries, rag, storage
from utils.chunking import chunk_pages
from utils.db import table

log = logging.getLogger(__name__)
_BATCH = 20


def delete_chunks(record_id: str, lang_type: str | None = None) -> None:
    sql = f"DELETE FROM {table('document_chunks')} WHERE record_id = :r"
    params = {"r": record_id}
    if lang_type:
        sql += " AND lang_type = :l"
        params["l"] = lang_type
    db.execute(sql, params)
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
    data = storage.read(doc["file_path"])
    pages = extract.load_pages(data, doc["file_name"])
    chunks = chunk_pages(pages)
    country_name = queries.country_name_map().get(rec["country"], rec["country"])
    ts = db.now()

    # replace semantics: only after the new content loaded + chunked successfully
    delete_chunks(rec["id"], doc["lang_type"])
    rows = []
    for c in chunks:
        rows.append({
            "id": db.uid(), "record_id": rec["id"], "document_id": doc["id"], "country": rec["country"],
            "country_name": country_name, "name_eng": rec["name_eng"], "name_orig": rec["name_orig"],
            "year": rec["year"], "source": rec["source"], "policy_guidance_tier": rec["policy_guidance_tier"],
            "strategy_tier": rec["strategy_tier"], "lang_type": doc["lang_type"], "lang_code": doc["lang_code"],
            "record_status": rec["record_status"] or "latest", "section_heading": c["section_heading"],
            "page": c["page"], "chunk_index": c["chunk_index"], "content": c["content"], "created_at": ts,
        })
    for i in range(0, len(rows), _BATCH):
        _insert_many(rows[i:i + _BATCH])
    rag.invalidate()
    log.info("indexed %s/%s → %d chunks", rec["id"], doc["lang_type"], len(rows))
    return len(rows)


def _insert_many(rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0])
    params: dict = {}
    groups = []
    for i, r in enumerate(rows):
        names = []
        for c in cols:
            p = f"{c}_{i}"
            params[p] = r[c]
            names.append(f":{p}")
        groups.append("(" + ",".join(names) + ")")
    db.execute(f"INSERT INTO {table('document_chunks')} ({', '.join(cols)}) VALUES {','.join(groups)}", params)


def index_record(record_id: str) -> int:
    return sum(index_document(d["id"]) for d in queries.get_documents_for_record(record_id))


def sync_record_metadata(record_id: str) -> None:
    """Copy the record's descriptive fields + lifecycle status onto its indexed chunks."""
    rec = queries.get_record(record_id)
    if not rec:
        return
    db.execute(
        f"UPDATE {table('document_chunks')} SET name_eng = :n, name_orig = :no, year = :y, source = :s, "
        f"policy_guidance_tier = :pt, strategy_tier = :st, record_status = :rs, country = :c WHERE record_id = :r",
        {"n": rec["name_eng"], "no": rec["name_orig"], "y": rec["year"], "s": rec["source"],
         "pt": rec["policy_guidance_tier"], "st": rec["strategy_tier"], "rs": rec["record_status"] or "latest",
         "c": rec["country"], "r": record_id})
    rag.invalidate()


def reconcile(max_ingests: int = 12) -> dict:
    """Diff documents ↔ chunks and repair the drift (what the nightly cron did in the original)."""
    indexed = {(r["record_id"], r["lang_type"]) for r in
               db.query(f"SELECT DISTINCT record_id, lang_type FROM {table('document_chunks')}")}
    docs = [d for d in db.query(f"SELECT id, record_id, lang_type, text_layer FROM {table('documents')}")]
    present = {(d["record_id"], d["lang_type"]) for d in docs}
    missing = [d for d in docs if (d["record_id"], d["lang_type"]) not in indexed and db.to_bool(d["text_layer"]) is not False]
    ghosts = [g for g in indexed if g not in present]
    ingested = 0
    for d in missing[:max_ingests]:
        try:
            if index_document(d["id"]):
                ingested += 1
        except Exception:  # noqa: BLE001
            log.exception("reconcile: indexing %s failed", d["id"])
    ghosts_deleted = 0
    for rid, lt in ghosts:
        delete_chunks(rid, lt)
        ghosts_deleted += 1
    # status drift
    resynced = 0
    status = {r["id"]: r["record_status"] for r in db.query(f"SELECT id, record_status FROM {table('policy_records')}")}
    for r in db.query(f"SELECT DISTINCT record_id, record_status FROM {table('document_chunks')}"):
        want = status.get(r["record_id"])
        if want and want != r["record_status"]:
            db.execute(f"UPDATE {table('document_chunks')} SET record_status = :s WHERE record_id = :r",
                       {"s": want, "r": r["record_id"]})
            resynced += 1
    rag.invalidate()
    return {"missing": len(missing), "ingested": ingested, "ghosts": len(ghosts), "ghosts_deleted": ghosts_deleted,
            "remaining": len(missing) - ingested, "status_resynced": resynced}


_reindex_lock = threading.Lock()     # one rebuild at a time (a 2nd run would interleave with – and re-clear – the first)


def reindex_running() -> bool:
    return _reindex_lock.locked()


def reindex(lang_filter: str = "eng", clear_existing: bool = True, status=None, progress=None) -> dict:
    """Port of `rag/app/api/reindex.py` – rebuild the chunk table from the stored documents.

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
        sql = (f"SELECT d.id, d.file_path, d.lang_type, r.country, r.name_eng FROM {table('documents')} d "
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
                storage.read(items[0]["file_path"])
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Preflight failed – document storage is unreachable ({exc}). "
                                   "The existing index was left untouched.") from exc
            say("Clearing existing index…")
            n = int(db.query(f"SELECT COUNT(*) AS n FROM {table('document_chunks')}")[0]["n"] or 0)
            db.execute(f"DELETE FROM {table('document_chunks')}")
            rag.invalidate()
            say(f"Cleared {n} existing chunks.")

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

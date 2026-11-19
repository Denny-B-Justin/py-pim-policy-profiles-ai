"""
Search-index maintenance (port of `web/lib/rag-sync.ts` + `rag/app/ingestion/pipeline.py`).

The index lives in the Delta table **`policy_repo_document_chunks`** (one row per chunk) – the source of the
Databricks AI Search Delta Sync index (utils/ai_search.py).  It replaces the v1 JSON chunk files in the volume.

    upload / replace  → index_document()          extract → chunk (utils/chunking.py) → MERGE rows → delete the
                                                   document's surplus rows → ingest log → request an index sync
    delete            → delete_document_chunks()   / delete_chunks(record_id)
    record edited     → sync_record_metadata()     rewrites the record fields copied onto its chunks
    nightly           → reconcile()                missing / stale / orphan chunks + metadata drift
    admin page        → reindex()                  rebuild in place (the index stays queryable throughout)

Old documents are chunked once by the Databricks notebook notebooks/01_backfill_document_chunks.py, which uses
the same chunker and writes the same rows, so both paths feed one table and one index.

Write semantics: rows are upserted by `chunk_id` (= `<document_id>:<chunk_index>`) and only rewritten when their
`row_hash` changes, then the document's rows past the new last chunk are deleted – a document is never missing
from the index while it is being re-chunked, and unchanged passages are not re-embedded.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from itertools import zip_longest
import threading
import time

import constants as C
from utils import ai_search, db, extract, queries, storage
from utils.chunking import CHUNK_COLUMNS, CHUNKER_VERSION, apply_record_metadata, chunk_pages, chunk_rows
from utils.db import table

log = logging.getLogger(__name__)

_CHUNKS = "document_chunks"
_LOG = "document_ingest_log"

# Spark SQL types of CHUNK_COLUMNS (for from_json in the MERGE) – keep in step with utils/schema.py
_SPARK_TYPES = {"chunk_index": "INT", "page": "INT", "page_end": "INT", "char_count": "INT", "year": "INT",
                "policy_guidance_tier": "INT", "strategy_tier": "INT"}
_ROWS_SCHEMA = "ARRAY<STRUCT<" + ", ".join(f"`{c}`: {_SPARK_TYPES.get(c, 'STRING')}" for c in CHUNK_COLUMNS) + ">>"
_RECORD_COMPARE = ["country", "name_eng", "name_orig", "year", "source", "policy_guidance_tier", "strategy_tier",
                   "jurisdiction_level", "jurisdiction_name", "record_status"]


def _sqlite() -> bool:
    return C.DB_BACKEND == "sqlite"


def _retry(fn, *args, attempts: int = 4):
    """Delta has optimistic concurrency: a concurrent writer (e.g. the notebook) can make a MERGE / DELETE fail."""
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
    invalidate()
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
def delete_chunks(record_id: str, lang_type: str | None = None) -> None:
    """Remove the index rows of a record (optionally only its ENG or ORI document)."""
    where, params = "record_id = :r", {"r": record_id}
    if lang_type:
        where += " AND lang_type = :l"
        params["l"] = lang_type
    _delete_rows(where, params)
    try:
        db.execute(f"DELETE FROM {table(_LOG)} WHERE {where}", params)
    except Exception:  # noqa: BLE001
        log.warning("ingest log cleanup failed for record %s", record_id, exc_info=True)
    _after_write()


def delete_document_chunks(doc: dict) -> None:
    _delete_rows("document_id = :d", {"d": doc["id"]})
    try:
        db.execute(f"DELETE FROM {table(_LOG)} WHERE document_id = :d", {"d": doc["id"]})
    except Exception:  # noqa: BLE001
        log.warning("ingest log cleanup failed for document %s", doc.get("id"), exc_info=True)
    _after_write()


def index_document(doc_id: str, source: str = "app_upload") -> int:
    """(Re-)index one document.  Returns the number of chunks written (0 = nothing indexable)."""
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
    if not path:
        _log(doc, "missing_file", storage.NOT_IN_REPOSITORY, 0, source)
        raise FileNotFoundError(storage.NOT_IN_REPOSITORY)
    try:
        data = storage.read(path)
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


def index_record(record_id: str) -> int:
    return sum(index_document(d["id"]) for d in queries.get_documents_for_record(record_id))


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
        invalidate()
    return len(changed)


# ── reconcile (nightly) ──────────────────────────────────────────────────────
_TERMINAL = ("no_text_layer", "empty", "unsupported")      # outcomes that re-chunking cannot change


def _null_safe_eq(a: str, b: str) -> str:
    return f"{a} IS {b}" if _sqlite() else f"{a} <=> {b}"


def reconcile(max_ingests: int = 12) -> dict:
    """Diff documents ↔ chunks and repair the drift (what the nightly cron did in the original):

    * documents with a file but no chunks (and no terminal outcome in the ingest log)  → index (≤ max_ingests)
    * documents chunked by an older CHUNKER_VERSION                                    → re-index (same budget)
    * chunks of documents that no longer exist                                         → delete
    * chunks whose copied record fields differ from the record                         → sync_record_metadata
    then asks AI Search to sync.
    """
    docs = db.query(f"SELECT id, record_id, lang_type, text_layer, blob_url, uploaded_at FROM {table('documents')}")
    present = {d["id"] for d in docs}
    indexed = {r["document_id"]: r["v"] for r in db.query(
        f"SELECT document_id, MIN(chunker_version) AS v FROM {table(_CHUNKS)} GROUP BY document_id")}
    done = {r["document_id"]: r for r in db.query(
        f"SELECT document_id, status, chunker_version, processed_at FROM {table(_LOG)}")}

    def settled(d) -> bool:
        """Scanned / empty / unsupported with this chunker, and the file has not been replaced since."""
        lg = done.get(d["id"])
        if not (lg and lg["status"] in _TERMINAL and lg["chunker_version"] == CHUNKER_VERSION):
            return False
        processed, uploaded = db.to_dt(lg["processed_at"]), db.to_dt(d["uploaded_at"])
        return bool(processed and (uploaded is None or processed >= uploaded))

    missing = [d for d in docs if d["id"] not in indexed and db.to_bool(d["text_layer"]) is not False
               and not settled(d) and storage.document_path(d)]
    stale = [d for d in docs if d["id"] in indexed and indexed[d["id"]] != CHUNKER_VERSION]
    ghosts = [doc_id for doc_id in indexed if doc_id not in present]

    # never-attempted documents first; ones that failed before are retried after them
    missing.sort(key=lambda d: (done.get(d["id"]) or {}).get("status") == "failed")
    ingested = failed = 0
    for d in (missing + stale)[:max_ingests]:
        try:
            index_document(d["id"], source="app_reconcile")
            ingested += 1
        except Exception:  # noqa: BLE001
            failed += 1
            log.exception("reconcile: indexing %s failed", d["id"])
    for i in range(0, len(ghosts), 200):
        clause, params = db.in_clause("g", ghosts[i:i + 200])
        _delete_rows(f"document_id IN {clause}", params)

    cmp = " AND ".join(_null_safe_eq(f"c.{k}", f"r.{k}") for k in _RECORD_COMPARE)
    drift = [r["record_id"] for r in db.query(
        f"SELECT DISTINCT c.record_id FROM {table(_CHUNKS)} c JOIN {table('policy_records')} r ON r.id = c.record_id "
        f"WHERE NOT ({cmp})")]
    meta_updates = 0
    for rid in drift:
        try:
            meta_updates += sync_record_metadata(rid)
        except Exception:  # noqa: BLE001
            log.exception("reconcile: metadata sync of %s failed", rid)

    invalidate()
    synced = ai_search.sync_best_effort()
    return {"missing": len(missing), "stale": len(stale), "ingested": ingested, "failed": failed,
            "ghosts": len(ghosts), "ghosts_deleted": len(ghosts), "metadata_records": len(drift),
            "metadata_rows_updated": meta_updates, "remaining": max(0, len(missing) + len(stale) - ingested - failed),
            "index_sync_requested": synced}


# ── full rebuild (RAG admin page / scripts/reindex.py) ───────────────────────
_reindex_lock = threading.Lock()     # one rebuild at a time


def reindex_running() -> bool:
    return _reindex_lock.locked()


def reindex(lang_filter: str = "eng", clear_existing: bool = True, status=None, progress=None) -> dict:
    """Port of `rag/app/api/reindex.py` – re-chunk the stored documents into the chunks table.

    lang_filter     'all' | 'eng' | 'ori'
    clear_existing  also remove chunks (of that language) whose document no longer exists.  The table is NOT
                    emptied first any more: each document is rewritten in place, so the AI Search index keeps
                    answering during the rebuild and unchanged passages are not re-embedded.
    status(msg)     human-readable phase text
    progress(dict)  {current, total, document, status: "done"|"error", chunks?, error?} after every document

    Fails fast when document storage is unreachable.  Returns {succeeded, failed, total_chunks[, message]}.
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

        say("Preflight: checking document access…")
        try:
            probe = next((p for p in map(storage.document_path, items) if p), None)
            if probe is None:
                raise FileNotFoundError("none of the documents is in the repository yet")
            storage.read(probe)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Preflight failed – document storage is unreachable ({exc}). "
                               "The existing index was left untouched.") from exc

        if clear_existing:
            say("Removing chunks of deleted documents…")
            where, p2 = "1 = 1", {}
            if lang:
                where, p2 = "lang_type = :l", {"l": lang}
            have = {r["document_id"] for r in db.query(
                f"SELECT DISTINCT document_id FROM {table(_CHUNKS)} WHERE {where}", p2)}
            live = {r["id"] for r in db.query(f"SELECT id FROM {table('documents')}")}
            ghosts = sorted(have - live)
            for i in range(0, len(ghosts), 200):
                clause, params = db.in_clause("g", ghosts[i:i + 200])
                _delete_rows(f"document_id IN {clause}", params)
            say(f"Removed chunks of {len(ghosts)} deleted documents.")

        ok = failed = chunks = 0
        for i, d in enumerate(items, 1):
            label = f"{d['country']} — {d['name_eng']} ({d['lang_type']})"
            try:
                n = index_document(d["id"], source="app_reindex")
                ok += 1
                chunks += n
                tick({"current": i, "total": total, "document": label, "status": "done", "chunks": n})
            except Exception as exc:  # noqa: BLE001
                log.exception("reindex: %s failed", label)
                failed += 1
                tick({"current": i, "total": total, "document": label, "status": "error",
                      "error": str(exc) or type(exc).__name__})
        invalidate()
        if ai_search.enabled():
            say("Asking AI Search to sync the index…")
            ai_search.sync_best_effort()
        return {"succeeded": ok, "failed": failed, "total_chunks": chunks}
    finally:
        _reindex_lock.release()


def reindex_all(progress=None) -> dict:
    """Rebuild the whole index (kept for scripts / cron)."""
    res = reindex("all", True, progress=(lambda p: progress(p["current"], p["total"])) if progress else None)
    return {"documents": res["succeeded"] + res["failed"], "indexed": res["succeeded"], "failed": res["failed"]}


# ── retrieval (AI Search) ────────────────────────────────────────────────────
_TIER_LABELS = {1: "Primary Legislation", 2: "Secondary Regulations", 3: "Procedural Guidelines", 4: "Strategies"}
_detail_cache: dict = {"data": None, "expires": 0.0}


def invalidate() -> None:
    """Drop cached aggregates (the search itself is served by AI Search, which has no in-process index)."""
    _detail_cache.update(data=None, expires=0.0)


def with_default_exclusions(filters: dict | None) -> dict:
    out = {k: v for k, v in (filters or {}).items() if k != "include_archived"}
    if "record_status" not in out and not (filters or {}).get("include_archived"):
        out["record_status"] = {"$ne": "archived"}
    return out


def _merge_query_results(result_lists: list[list[dict]], k: int) -> list[dict]:
    """Round-robin merge of per-query lists, de-duplicated, capped at k."""
    seen: set = set()
    merged: list[dict] = []
    for group in zip_longest(*result_lists):
        for d in group:
            if d is None:
                continue
            key = (d["metadata"].get("record_id"), hash(d["content"]))
            if key in seen:
                continue
            seen.add(key)
            merged.append(d)
            if len(merged) >= k:
                return merged
    return merged


def retrieve(query: str | list[str], filters: dict | None = None, k: int | None = None,
             pad_to_k: bool = False) -> list[dict]:
    """Top-k chunks → [{"content": str, "metadata": {...}}] via Databricks AI Search.

    pad_to_k is accepted for briefings; AI Search always returns up to k nearest chunks, so nothing is padded.
    """
    if not ai_search.enabled():
        raise ai_search.AISearchError("Search is unavailable: set PIM_SEARCH_BACKEND=ai_search on a Databricks "
                                      "(non-sqlite) backend.")
    k = k or C.RETRIEVER_K
    queries_ = [query] if isinstance(query, str) else [q for q in query if q]
    if not queries_:
        return []
    f = with_default_exclusions(filters)
    return _merge_query_results([ai_search.query(q, f, k) for q in queries_], k)


# ── statistics ───────────────────────────────────────────────────────────────
def stats_detail(force: bool = False) -> dict:
    """Aggregates for the Statistics page, computed from the chunks table (cached 60 s)."""
    now = time.monotonic()
    if not force and _detail_cache["data"] is not None and now < _detail_cache["expires"]:
        return _detail_cache["data"]
    rows = db.query(f"SELECT document_id, record_id, country, lang_type, year, policy_guidance_tier, created_at "
                    f"FROM {table(_CHUNKS)}")
    records: set = set()
    langs: dict[str, set] = defaultdict(set)
    country_docs: dict[str, set] = defaultdict(set)
    country_chunks: Counter = Counter()
    tier_docs: dict[int, set] = defaultdict(set)
    year_docs: dict[int, set] = defaultdict(set)
    last = None
    for r in rows:
        rid = r["record_id"]
        records.add(rid)
        langs[r["lang_type"]].add(rid)
        country_docs[r["country"]].add(rid)
        country_chunks[r["country"]] += 1
        tier, year = db.to_int(r.get("policy_guidance_tier")), db.to_int(r.get("year"))
        if tier is not None:
            tier_docs[tier].add(rid)
        if year is not None:
            year_docs[year].add(rid)
        if r.get("created_at") and (last is None or str(r["created_at"]) > last):
            last = str(r["created_at"])
    tok = sum(db.to_int(t["tokens"]) or 0 for t in db.query(f"SELECT id, tokens FROM {table('policy_records')}")
              if t["id"] in records)
    names = queries.country_name_map()
    by_country = [{"country": c or "Unknown", "country_name": names.get(c), "documents": len(d),
                   "chunks": country_chunks[c]} for c, d in country_docs.items()]
    by_country.sort(key=lambda x: (-x["documents"], x["country"] or ""))
    by_tier = [{"tier": t, "label": _TIER_LABELS.get(t, f"Tier {t}"), "documents": len(d)}
               for t, d in sorted(tier_docs.items())]
    by_year = [{"year": str(y), "documents": len(d)} for y, d in sorted(year_docs.items())]
    data = {
        "summary": {
            "total_chunks": len(rows), "total_documents": len(records), "total_tokens": tok or None,
            "lang_eng_documents": len(langs.get("ENG", ())), "lang_ori_documents": len(langs.get("ORI", ())),
            "last_updated": last[:10] if last else None, "collection_name": C.AI_SEARCH_INDEX,
        },
        "by_country": by_country, "by_tier": by_tier, "by_year": by_year,
        "vector_store": "Delta table + AI Search index",
        "search_engine": f"AI Search · {C.AI_SEARCH_QUERY_TYPE}" + (" + rerank" if C.AI_SEARCH_RERANK else ""),
        "chunk_size": C.CHUNK_PARENT_CHARS, "chunk_overlap": 0,
        "retriever_type": f"ai_search · {C.AI_SEARCH_QUERY_TYPE.lower()}",
    }
    _detail_cache.update(data=data, expires=now + 60)
    return data

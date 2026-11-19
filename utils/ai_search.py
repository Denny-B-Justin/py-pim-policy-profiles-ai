"""
Databricks AI Search (formerly Vector Search) client for the search index.

The index is a **Delta Sync index with Databricks-computed embeddings** over `policy_repo_document_chunks`
(created once by notebooks/01_backfill_document_chunks.py):

    source table   prd_mega.sgpbpi163.policy_repo_document_chunks      (Change Data Feed on)
    index          prd_mega.sgpbpi163.policy_repo_document_chunks_index (constants.AI_SEARCH_INDEX)
    endpoint       constants.AI_SEARCH_ENDPOINT  (STANDARD)
    embeddings     `embedding_text` column → databricks-qwen3-embedding-0-6b (multilingual)
    sync           TRIGGERED – the app asks for a sync after it writes chunks (debounced, see request_sync)

Everything goes through the Databricks SDK WorkspaceClient that utils/storage.py already creates for the volume
(same OAuth M2M service principal) – no extra package.  The service principal needs USE CATALOG / USE SCHEMA and
SELECT on the index to query it, and must own (or be allowed to manage) the index to trigger syncs; see the
notebook's "Grants" cell.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import constants as C

log = logging.getLogger(__name__)

# Columns returned by a query (all synced to the index).  `score` is appended by AI Search.
RESULT_COLUMNS = ["chunk_id", "record_id", "document_id", "country", "country_name", "name_eng", "name_orig", "year",
                  "source", "policy_guidance_tier", "strategy_tier", "lang_type", "lang_code", "record_status",
                  "section_heading", "page", "page_end", "chunk_index", "content"]
_INT_COLS = {"year", "policy_guidance_tier", "strategy_tier", "page", "page_end", "chunk_index"}


class AISearchError(RuntimeError):
    pass


def enabled() -> bool:
    return C.SEARCH_BACKEND == "ai_search" and C.DB_BACKEND != "sqlite"


def _w():
    from utils import storage  # noqa: PLC0415 - one WorkspaceClient per process (M2M OAuth)

    return storage._w()


# ── filters ──────────────────────────────────────────────────────────────────
def build_filters(filters: dict) -> dict:
    """App filter dict (utils/rag.py semantics) → AI Search standard-endpoint filter dict.

    country / lang_type / source / tiers : value or list  → {"col": v} / {"col": [..]}
    {"$ne": v}  → {"col NOT": v};   {"$in": [..]} → {"col": [..]}
    year {"$gte","$lte"} | year_from / year_to | exact year → {"year >=": a, "year <=": b} / {"year": y}
    """
    out: dict = {}
    f = dict(filters or {})
    lo, hi = f.pop("year_from", None), f.pop("year_to", None)
    y = f.pop("year", None)
    if isinstance(y, dict):
        lo, hi = y.get("$gte", lo), y.get("$lte", hi)
    elif y is not None:
        out["year"] = int(y)
    if lo is not None:
        out["year >="] = int(lo)
    if hi is not None:
        out["year <="] = int(hi)
    for key, cond in f.items():
        if isinstance(cond, list):
            out[key] = list(cond) if cond else ["__none__"]        # empty list = match nothing (same as BM25)
        elif isinstance(cond, dict):
            if "$ne" in cond:
                out[f"{key} NOT"] = cond["$ne"]
            if "$in" in cond:
                out[key] = list(cond["$in"]) or ["__none__"]
        else:
            out[key] = cond
    return out


# ── query ────────────────────────────────────────────────────────────────────
def query(text: str, filters: dict | None, k: int) -> list[dict]:
    """Top-k chunks for one query → [{"content", "metadata"}] (best first)."""
    from databricks.sdk.service.vectorsearch import RerankerConfig, RerankerConfigRerankerParameters  # noqa: PLC0415

    kwargs = dict(index_name=C.AI_SEARCH_INDEX, columns=RESULT_COLUMNS, query_text=text, num_results=int(k),
                  query_type=C.AI_SEARCH_QUERY_TYPE)
    vs_filters = build_filters(filters or {})
    if vs_filters:
        kwargs["filters_json"] = json.dumps(vs_filters, ensure_ascii=False)
    if C.AI_SEARCH_RERANK:
        kwargs["reranker"] = RerankerConfig(model="databricks_reranker", parameters=RerankerConfigRerankerParameters(
            columns_to_rerank=["content", "section_heading", "name_eng"]))
    try:
        res = _w().vector_search_indexes.query_index(**kwargs)
    except Exception as exc:  # noqa: BLE001
        raise AISearchError(f"AI Search query failed: {exc}") from exc
    cols = [c.name for c in (res.manifest.columns or [])] if res.manifest else []
    rows = (res.result.data_array or []) if res.result else []
    out = []
    for values in rows:
        rec = dict(zip(cols, values))
        meta = {}
        for key in RESULT_COLUMNS:
            if key == "content":
                continue
            v = rec.get(key)
            if v is None:
                continue
            if key in _INT_COLS:
                try:
                    v = int(float(v))
                except (TypeError, ValueError):
                    pass
            meta[key] = v
        meta["id"] = meta.get("chunk_id")
        if rec.get("score") is not None:
            meta["score"] = float(rec["score"])
        meta["retrieved_via"] = f"ai_search:{C.AI_SEARCH_QUERY_TYPE.lower()}" + ("+rerank" if C.AI_SEARCH_RERANK else "")
        out.append({"content": rec.get("content") or "", "metadata": meta})
    return out


# ── index status / sync ──────────────────────────────────────────────────────
def status() -> dict:
    """{"ready", "indexed_row_count", "message", "index", "endpoint"} – never raises."""
    info = {"index": C.AI_SEARCH_INDEX, "endpoint": C.AI_SEARCH_ENDPOINT, "ready": None, "indexed_row_count": None,
            "message": None}
    if not enabled():
        info["message"] = "AI Search is not enabled (PIM_SEARCH_BACKEND)."
        return info
    try:
        idx = _w().vector_search_indexes.get_index(index_name=C.AI_SEARCH_INDEX)
        st = idx.status
        if st:
            info.update(ready=st.ready, indexed_row_count=st.indexed_row_count, message=st.message)
    except Exception as exc:  # noqa: BLE001
        info["message"] = f"{type(exc).__name__}: {exc}"
    return info


def sync_now() -> bool:
    """Ask the index to pick up the latest table changes.  True = accepted.  Raises on permission problems."""
    if not enabled():
        return False
    _w().vector_search_indexes.sync_index(index_name=C.AI_SEARCH_INDEX)
    log.info("AI Search sync requested for %s", C.AI_SEARCH_INDEX)
    return True


def sync_best_effort() -> bool:
    try:
        return sync_now()
    except Exception as exc:  # noqa: BLE001
        log.warning("AI Search sync not started (%s) – it will be picked up by the next sync", exc)
        return False


# Debounced background sync: uploads mark the index dirty; one daemon thread syncs at most every
# AI_SEARCH_SYNC_DEBOUNCE_SECONDS and retries while a previous sync is still running.
_dirty = threading.Event()
_worker_lock = threading.Lock()
_worker: threading.Thread | None = None
_denied = {"flag": False}


def _sync_loop() -> None:
    while True:
        _dirty.wait()
        time.sleep(max(1, C.AI_SEARCH_SYNC_DEBOUNCE_SECONDS))
        _dirty.clear()
        for attempt in range(6):
            try:
                sync_now()
                break
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if any(s in msg for s in ("permission", "forbidden", "not authorized", "unauthorized", "403")):
                    if not _denied["flag"]:
                        log.error("AI Search: the service principal may not sync %s (%s). Grant it ownership / "
                                  "MANAGE on the index or schedule the sync in Databricks.", C.AI_SEARCH_INDEX, exc)
                    _denied["flag"] = True
                    break
                log.info("AI Search sync busy/failed (%s) – retry %d", exc, attempt + 1)
                time.sleep(60)


def request_sync() -> None:
    """Non-blocking: schedule a sync of the index (no-op when AI Search is off or syncing is disabled)."""
    global _worker
    if not (enabled() and C.AI_SEARCH_SYNC_ON_WRITE):
        return
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_sync_loop, name="ai-search-sync", daemon=True)
            _worker.start()
    _dirty.set()

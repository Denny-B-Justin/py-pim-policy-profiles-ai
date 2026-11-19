"""
Retrieval for the AI Coach and the country briefings, plus the index statistics.

Search runs on **Databricks AI Search** only: a Delta Sync index over the Delta table
`policy_repo_document_chunks` (written by utils/indexer.py when documents are uploaded), with
Databricks-computed embeddings (multilingual `databricks-qwen3-embedding-0-6b`), queried in HYBRID mode
(vector similarity + keyword) – see utils/ai_search.py.  There is no in-process fallback: with 245k chunks a
local keyword index would have to load the whole table into every app process.

Filter semantics are those of `rag/app/retrieval/filters.py`:
    country (str | list), record_id (list), lang_type, policy_guidance_tier, strategy_tier, source,
    year {$gte,$lte} | year_from / year_to, and archived records are excluded unless the caller
    filters on record_status or passes include_archived.
"""

from __future__ import annotations

import logging
import time
from itertools import zip_longest

import constants as C
from utils import ai_search, db
from utils.db import table

log = logging.getLogger(__name__)

_CHUNKS = "document_chunks"


def invalidate() -> None:
    """Drop cached aggregates (the search itself is served by AI Search – there is no in-process index)."""
    _detail_cache.update(data=None, expires=0.0)


# ── retrieval (AI Search) ────────────────────────────────────────────────────
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
            key = d["metadata"].get("chunk_id") or (d["metadata"].get("record_id"), hash(d["content"]))
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

    query     one string, or several (e.g. a translated + the English briefing query) – results are merged
    pad_to_k  accepted for the briefings; AI Search already returns up to k nearest chunks.
    Raises ai_search.AISearchError (with a readable message) when search is off or the query fails; the
    reason – including missing permissions – is logged by utils/ai_search.py.
    """
    if not ai_search.enabled():
        raise ai_search.AISearchError(ai_search.disabled_reason())
    k = k or C.RETRIEVER_K
    queries_ = [query] if isinstance(query, str) else [q for q in query if q]
    if not queries_:
        return []
    f = with_default_exclusions(filters)
    return _merge_query_results([ai_search.query(q, f, k) for q in queries_], k)


# ── statistics (Statistics page, RAG admin page) ─────────────────────────────
_TIER_LABELS = {1: "Primary Legislation", 2: "Secondary Regulations", 3: "Procedural Guidelines", 4: "Strategies"}
_detail_cache: dict = {"data": None, "expires": 0.0}


def stats_detail(force: bool = False) -> dict:
    """Aggregates computed in SQL from the chunks table (cached 60 s).  Never loads the chunk rows themselves."""
    now = time.monotonic()
    if not force and _detail_cache["data"] is not None and now < _detail_cache["expires"]:
        return _detail_cache["data"]
    from utils import chunking, queries  # noqa: PLC0415

    t = table(_CHUNKS)
    tot = db.query(f"SELECT COUNT(*) AS n, COUNT(DISTINCT record_id) AS r, MAX(updated_at) AS m FROM {t}")[0]
    langs = {r["lang_type"]: int(r["n"]) for r in
             db.query(f"SELECT lang_type, COUNT(DISTINCT record_id) AS n FROM {t} GROUP BY lang_type")}
    names = queries.country_name_map()
    by_country = [{"country": r["country"] or "Unknown", "country_name": names.get(r["country"]),
                   "documents": int(r["d"]), "chunks": int(r["n"])} for r in
                  db.query(f"SELECT country, COUNT(DISTINCT record_id) AS d, COUNT(*) AS n FROM {t} GROUP BY country")]
    by_country.sort(key=lambda x: (-x["documents"], x["country"]))
    by_tier = [{"tier": int(r["t"]), "label": _TIER_LABELS.get(int(r["t"]), f"Tier {r['t']}"), "documents": int(r["d"])}
               for r in db.query(f"SELECT policy_guidance_tier AS t, COUNT(DISTINCT record_id) AS d FROM {t} "
                                 f"WHERE policy_guidance_tier IS NOT NULL GROUP BY policy_guidance_tier "
                                 f"ORDER BY policy_guidance_tier")]
    by_year = [{"year": str(r["y"]), "documents": int(r["d"])}
               for r in db.query(f"SELECT year AS y, COUNT(DISTINCT record_id) AS d FROM {t} WHERE year IS NOT NULL "
                                 f"GROUP BY year ORDER BY year")]
    tok = db.query(f"SELECT SUM(tokens) AS s FROM {table('policy_records')} WHERE id IN "
                   f"(SELECT DISTINCT record_id FROM {t})")[0]["s"]
    last = str(tot["m"]) if tot["m"] is not None else None
    on = ai_search.enabled()
    data = {
        "summary": {
            "total_chunks": int(tot["n"] or 0),
            "total_documents": int(tot["r"] or 0),
            "total_tokens": db.to_int(tok) or None,
            "lang_eng_documents": langs.get("ENG", 0),
            "lang_ori_documents": langs.get("ORI", 0),
            "last_updated": last[:10] if last else None,
            "collection_name": C.AI_SEARCH_INDEX,
        },
        "by_country": by_country, "by_tier": by_tier, "by_year": by_year,
        "vector_store": f"Delta table + Databricks AI Search ({C.AI_SEARCH_INDEX})",
        "search_engine": (f"AI Search · {C.AI_SEARCH_QUERY_TYPE}" + (" + rerank" if C.AI_SEARCH_RERANK else "")
                          + f" · {C.AI_SEARCH_EMBEDDING_MODEL}") if on else "off – " + ai_search.disabled_reason(),
        "chunk_size": chunking.PARENT_SIZE, "chunk_overlap": chunking.OVERLAP_CHARS,
        "retriever_type": f"ai_search · {C.AI_SEARCH_QUERY_TYPE.lower()}",
        "chunker_version": chunking.CHUNKER_VERSION,
    }
    _detail_cache.update(data=data, expires=now + 60)
    return data

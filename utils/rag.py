"""
Retrieval for the AI Coach and the country briefings.

The original service searched a pgvector index with OpenAI embeddings.  The Posit mAI gateway exposes
no embedding endpoint, so retrieval is **lexical BM25** over the chunk table `pim_document_chunks`
(see docs/DATABRICKS_MIGRATION.md).  The chunk rows are loaded into a process-local, in-memory
SQLite **FTS5** index (Porter stemming + unicode folding, built-in `bm25()` ranking); if FTS5 is not
compiled into the host's SQLite, a pure-Python BM25 over the filtered candidates is used instead.

Filter semantics are those of `rag/app/retrieval/filters.py`:
    country (str | list), record_id (list), lang_type, policy_guidance_tier, strategy_tier, source,
    year {$gte,$lte} | year_from / year_to, and archived records are excluded unless the caller
    filters on record_status or passes include_archived.
"""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import threading
import time
from collections import Counter
from itertools import zip_longest
from typing import Any

from utils import constants as C
from utils import db
from utils.db import table

log = logging.getLogger(__name__)

_COLS = ("id", "record_id", "document_id", "country", "country_name", "name_eng", "name_orig", "year", "source",
         "policy_guidance_tier", "strategy_tier", "lang_type", "lang_code", "record_status", "section_heading",
         "page", "chunk_index", "content")
_META_KEYS = [c for c in _COLS if c != "content"]

_STOP = set("""a an and are as at be been but by can could did do does for from had has have how i if in into is it its
may might more most must no not of on or our shall should so than that the their them then there these they this to
under was we were what when where which who whom why will with would you your about above after all also any because
before between both each few other over same such through up very""".split())
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

_lock = threading.RLock()
_state: dict[str, Any] = {"conn": None, "version": None, "checked": 0.0, "fts": None, "rows": None}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) > 1 and t not in _STOP]


def _fts5_available() -> bool:
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        c.close()
        return True
    except sqlite3.Error:
        return False


# ── index lifecycle ──────────────────────────────────────────────────────────
def _db_version() -> tuple:
    r = db.query(f"SELECT COUNT(*) AS n, MAX(created_at) AS m, "
                 f"SUM(CASE WHEN record_status = 'archived' THEN 1 ELSE 0 END) AS a FROM {table('document_chunks')}")[0]
    return (int(r["n"] or 0), str(r["m"]), int(r["a"] or 0))


def invalidate() -> None:
    with _lock:
        _state.update(version=None, checked=0.0)
    _detail_cache.update(data=None, expires=0.0)


def _load_rows() -> list[dict]:
    return db.query(f"SELECT {', '.join(_COLS)} FROM {table('document_chunks')}")


def _build(rows: list[dict], use_fts: bool):
    if not use_fts:
        return None
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("CREATE VIRTUAL TABLE fts USING fts5(content, tokenize='porter unicode61 remove_diacritics 2')")
    conn.execute("CREATE TABLE meta (rid INTEGER PRIMARY KEY, record_id TEXT, country TEXT, lang_type TEXT, "
                 "tier INTEGER, strat INTEGER, year INTEGER, status TEXT, source TEXT)")
    conn.executemany("INSERT INTO fts(rowid, content) VALUES (?, ?)", ((i, r["content"]) for i, r in enumerate(rows)))
    conn.executemany(
        "INSERT INTO meta VALUES (?,?,?,?,?,?,?,?,?)",
        ((i, r["record_id"], r["country"], r["lang_type"], r["policy_guidance_tier"], r["strategy_tier"], r["year"],
          r["record_status"], r["source"]) for i, r in enumerate(rows)))
    conn.execute("CREATE INDEX meta_country ON meta(country)")
    conn.execute("CREATE INDEX meta_record ON meta(record_id)")
    conn.commit()
    return conn


def _ensure() -> None:
    now = time.monotonic()
    with _lock:
        if _state["rows"] is not None and now - _state["checked"] < 30:
            return
        try:
            version = _db_version()
        except Exception:  # noqa: BLE001 - keep serving the stale index if the DB hiccups
            if _state["rows"] is not None:
                return
            raise
        _state["checked"] = now
        if version == _state["version"] and _state["rows"] is not None:
            return
        t0 = time.perf_counter()
        rows = _load_rows()
        use_fts = _fts5_available()
        conn = _build(rows, use_fts)
        _state.update(rows=rows, conn=conn, version=version, fts=use_fts)
        log.info("search index built: %d chunks, fts5=%s, %.1fs", len(rows), use_fts, time.perf_counter() - t0)


def index_size() -> int:
    _ensure()
    return len(_state["rows"] or [])


# ── filters ──────────────────────────────────────────────────────────────────
def with_default_exclusions(filters: dict | None) -> dict:
    out = {k: v for k, v in (filters or {}).items() if k != "include_archived"}
    if "record_status" not in out and not (filters or {}).get("include_archived"):
        out["record_status"] = {"$ne": "archived"}
    return out


def _match(row: dict, f: dict) -> bool:
    for key, cond in f.items():
        if key in ("year_from", "year_to"):
            continue
        if key == "year":
            continue
        val = row.get(key)
        if isinstance(cond, list):
            if val not in cond:
                return False
        elif isinstance(cond, dict):
            if "$ne" in cond and val == cond["$ne"]:
                return False
            if "$in" in cond and val not in cond["$in"]:
                return False
        elif val != cond:
            return False
    yr = row.get("year")
    lo = f.get("year_from"); hi = f.get("year_to")
    y = f.get("year")
    if isinstance(y, dict):
        lo = y.get("$gte", lo); hi = y.get("$lte", hi)
    elif y is not None:
        if yr != y:
            return False
    if lo is not None and (yr is None or yr < int(lo)):
        return False
    if hi is not None and (yr is None or yr > int(hi)):
        return False
    return True


# ── scoring ──────────────────────────────────────────────────────────────────
def _fts_query(tokens: list[str]) -> str:
    uniq = list(dict.fromkeys(tokens))[:40]
    return " OR ".join('"' + t.replace('"', "") + '"' for t in uniq)


def _search_fts(tokens: list[str], f: dict, k: int) -> list[int]:
    conn: sqlite3.Connection = _state["conn"]
    where, params = ["fts MATCH ?"], [_fts_query(tokens)]
    f = dict(f)
    simple = {"country": "m.country", "lang_type": "m.lang_type", "policy_guidance_tier": "m.tier",
              "strategy_tier": "m.strat", "source": "m.source", "record_status": "m.status",
              "record_id": "m.record_id"}
    residual: dict = {}
    for key, cond in f.items():
        col = simple.get(key)
        if col is None:
            residual[key] = cond
            continue
        if isinstance(cond, list):
            where.append(f"{col} IN ({','.join('?' * len(cond))})" if cond else "0")
            params.extend(cond)
        elif isinstance(cond, dict):
            if "$ne" in cond:
                where.append(f"({col} IS NULL OR {col} <> ?)"); params.append(cond["$ne"])
            if "$in" in cond:
                where.append(f"{col} IN ({','.join('?' * len(cond['$in']))})" if cond["$in"] else "0")
                params.extend(cond["$in"])
        else:
            where.append(f"{col} = ?"); params.append(cond)
    lo = residual.pop("year_from", None); hi = residual.pop("year_to", None)
    y = residual.pop("year", None)
    if isinstance(y, dict):
        lo = y.get("$gte", lo); hi = y.get("$lte", hi)
    elif y is not None:
        lo = hi = y
    if lo is not None:
        where.append("m.year >= ?"); params.append(int(lo))
    if hi is not None:
        where.append("m.year <= ?"); params.append(int(hi))
    sql = (f"SELECT fts.rowid AS rid FROM fts JOIN meta m ON m.rid = fts.rowid WHERE {' AND '.join(where)} "
           f"ORDER BY bm25(fts) LIMIT {int(k)}")
    try:
        return [r[0] for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:  # malformed MATCH (rare) → fall back to python scoring
        return _search_py(tokens, f, k)


def _search_py(tokens: list[str], f: dict, k: int) -> list[int]:
    rows = _state["rows"]
    cand = [i for i, r in enumerate(rows) if _match(r, f)]
    if not cand or not tokens:
        return []
    q = list(dict.fromkeys(tokens))
    docs = {i: Counter(tokenize(rows[i]["content"])) for i in cand}
    n = len(cand)
    avgdl = sum(sum(c.values()) for c in docs.values()) / max(1, n)
    df = {t: sum(1 for c in docs.values() if t in c) for t in q}
    k1, b = 1.5, 0.75
    scored = []
    for i, c in docs.items():
        dl = sum(c.values()) or 1
        s = 0.0
        for t in q:
            tf = c.get(t, 0)
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))
        if s > 0:
            scored.append((s, i))
    scored.sort(reverse=True)
    return [i for _, i in scored[:k]]


def _to_doc(row: dict) -> dict:
    meta = {k: row[k] for k in _META_KEYS if row.get(k) is not None}
    meta["retrieved_via"] = "bm25"
    return {"content": row["content"], "metadata": meta}


def _merge_query_results(result_lists: list[list[dict]], k: int) -> list[dict]:
    """Round-robin merge of per-query lists, de-duplicated, capped at k (same as the original)."""
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
    """Top-k chunks → [{"content": str, "metadata": {...}}].

    pad_to_k: when lexical matching finds fewer than k chunks (e.g. a generic briefing query), top up with
    the opening chunks of each in-scope record so a briefing always receives some document context.
    """
    k = k or C.RETRIEVER_K
    queries = [query] if isinstance(query, str) else [q for q in query if q]
    if not queries:
        return []
    _ensure()
    rows = _state["rows"]
    if not rows:
        return []
    f = with_default_exclusions(filters)
    lists: list[list[dict]] = []
    for q in queries:
        tokens = tokenize(q)
        if not tokens:
            lists.append([])
            continue
        idx = _search_fts(tokens, f, k) if _state["fts"] else _search_py(tokens, f, k)
        lists.append([_to_doc(rows[i]) for i in idx])
    docs = _merge_query_results(lists, k)
    if pad_to_k and len(docs) < k:
        have = {(d["metadata"].get("id")) for d in docs}
        by_record: dict[str, list[dict]] = {}
        for r in rows:
            if _match(r, f):
                by_record.setdefault(r["record_id"], []).append(r)
        for rs in by_record.values():
            rs.sort(key=lambda r: r["chunk_index"] or 0)
        for group in zip_longest(*by_record.values()):
            for r in group:
                if r is None or r["id"] in have:
                    continue
                docs.append(_to_doc(r)); have.add(r["id"])
                if len(docs) >= k:
                    return docs
    return docs


# ── stats for the RAG admin console (`/api/stats` of the original service) ──
def stats() -> dict:
    _ensure()
    rows = _state["rows"] or []
    by_country: Counter = Counter()
    by_lang: Counter = Counter()
    records: set = set()
    for r in rows:
        by_country[r["country"]] += 1
        by_lang[r["lang_type"] or "?"] += 1
        records.add(r["record_id"])
    return {"total_chunks": len(rows), "total_records": len(records), "by_country": dict(by_country),
            "by_lang_type": dict(by_lang), "engine": "sqlite-fts5 bm25" if _state["fts"] else "python bm25"}


# ── detailed statistics (`/api/stats/detail` of the original service; cached 60 s) ──
_TIER_LABELS = {1: "Primary Legislation", 2: "Secondary Regulations", 3: "Procedural Guidelines", 4: "Strategies"}
_detail_cache: dict = {"data": None, "expires": 0.0}


def stats_detail(force: bool = False) -> dict:
    """Aggregates for the Statistics page, computed with plain SQL so they work on UC tables and SQLite alike."""
    now = time.monotonic()
    if not force and _detail_cache["data"] is not None and now < _detail_cache["expires"]:
        return _detail_cache["data"]
    from utils import queries  # local import: queries imports rag lazily elsewhere

    chunks = table("document_chunks")
    head = db.query(f"SELECT COUNT(*) AS n, COUNT(DISTINCT record_id) AS d, MAX(created_at) AS m FROM {chunks}")[0]
    langs = {r["lang_type"]: int(r["d"] or 0) for r in
             db.query(f"SELECT lang_type, COUNT(DISTINCT record_id) AS d FROM {chunks} GROUP BY lang_type")}
    tok = db.query(f"SELECT SUM(tokens) AS t FROM {table('policy_records')} "
                   f"WHERE id IN (SELECT DISTINCT record_id FROM {chunks})")[0]["t"]
    names = queries.country_name_map()
    by_country = [
        {"country": r["country"] or "Unknown", "country_name": r["country_name"] or names.get(r["country"]),
         "documents": int(r["d"] or 0), "chunks": int(r["n"] or 0)}
        for r in db.query(f"SELECT country, MAX(country_name) AS country_name, COUNT(DISTINCT record_id) AS d, "
                          f"COUNT(*) AS n FROM {chunks} GROUP BY country")]
    by_country.sort(key=lambda x: (-x["documents"], x["country"]))
    by_tier = []
    for r in db.query(f"SELECT policy_guidance_tier AS t, COUNT(DISTINCT record_id) AS d FROM {chunks} "
                      f"WHERE policy_guidance_tier IS NOT NULL GROUP BY policy_guidance_tier"):
        t = int(r["t"])
        by_tier.append({"tier": t, "label": _TIER_LABELS.get(t, f"Tier {t}"), "documents": int(r["d"] or 0)})
    by_tier.sort(key=lambda x: x["tier"])
    by_year = [{"year": str(int(r["y"])), "documents": int(r["d"] or 0)} for r in
               db.query(f"SELECT year AS y, COUNT(DISTINCT record_id) AS d FROM {chunks} "
                        f"WHERE year IS NOT NULL GROUP BY year")]
    by_year.sort(key=lambda x: x["year"])
    last = head["m"]
    data = {
        "summary": {
            "total_chunks": int(head["n"] or 0),
            "total_documents": int(head["d"] or 0),
            "total_tokens": int(tok) if tok else None,
            "lang_eng_documents": langs.get("ENG", 0),
            "lang_ori_documents": langs.get("ORI", 0),
            "last_updated": str(last)[:10] if last else None,
            "collection_name": table("document_chunks"),
        },
        "by_country": by_country, "by_tier": by_tier, "by_year": by_year,
        "vector_store": "SQLite" if C.DB_BACKEND == "sqlite" else "Unity Catalog (Delta)",
        "search_engine": "BM25 · SQLite FTS5" if _fts5_available() else "BM25 · in-process",
        "chunk_size": C.CHUNK_PARENT_CHARS, "chunk_overlap": 0,
        "retriever_type": "bm25 · parent-sections",
    }
    _detail_cache.update(data=data, expires=now + 60)
    return data

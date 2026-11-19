"""
Databricks AI Search (formerly Vector Search) client for the search index.

The index is a **Delta Sync index with Databricks-computed embeddings** over `policy_repo_document_chunks`:

    source table   prd_mega.sgpbpi163.policy_repo_document_chunks      (Change Data Feed on)
    index          prd_mega.sgpbpi163.policy_repo_document_chunks_index (constants.AI_SEARCH_INDEX)
    endpoint       pim_policy_profiles_ai_search (STANDARD)              (constants.AI_SEARCH_ENDPOINT, informational)
    embeddings     `embedding_text` column → databricks-qwen3-embedding-0-6b (multilingual)
    sync           TRIGGERED – the app asks for a sync after it writes chunks (debounced, see request_sync)

Everything goes through the Databricks SDK WorkspaceClient that utils/storage.py already creates for the volume
(same OAuth M2M service principal, DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET) – no extra package or secret.
Queries and syncs address the index by its full name; neither needs the endpoint name or id.

Access the service principal needs (see docs/AI_SEARCH.md):
    query   USE CATALOG prd_mega, USE SCHEMA prd_mega.sgpbpi163, SELECT on the index (no endpoint permission)
    sync    not documented by Databricks as a privilege; the index owner can sync (the sync pipeline runs as the
            owner) → make the service principal the owner of the index, or sync outside the app.
Every refused call is logged once per hour as an "AI SEARCH ACCESS NEEDED" block naming the principal, the
object, the exact Databricks error and what to ask for.  `python scripts/check_ai_search.py` runs all checks.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

import constants as C

log = logging.getLogger(__name__)

# Columns returned by a query (all synced to the index).  `score` is appended by AI Search.
RESULT_COLUMNS = ["chunk_id", "record_id", "document_id", "country", "country_name", "name_eng", "name_orig", "year",
                  "source", "policy_guidance_tier", "strategy_tier", "lang_type", "lang_code", "record_status",
                  "section_heading", "page", "page_end", "chunk_index", "content"]
_INT_COLS = {"year", "policy_guidance_tier", "strategy_tier", "page", "page_end", "chunk_index"}
_VALID_BACKENDS = ("ai_search",)


class AISearchError(RuntimeError):
    pass


def enabled() -> bool:
    return C.SEARCH_BACKEND in _VALID_BACKENDS and C.DB_BACKEND != "sqlite" and bool(C.AI_SEARCH_INDEX)


def disabled_reason() -> str:
    if C.DB_BACKEND == "sqlite":
        return "Search is unavailable in the local SQLite preview (AI Search needs the Databricks backend)."
    if C.SEARCH_BACKEND not in _VALID_BACKENDS:
        return (f"Search is switched off: PIM_SEARCH_BACKEND is '{C.SEARCH_BACKEND}' – set it to 'ai_search' "
                f"(or remove it; ai_search is the default).")
    if not C.AI_SEARCH_INDEX:
        return "Search is switched off: PIM_AI_SEARCH_INDEX is empty."
    return "Search is enabled."


def _w():
    from utils import storage  # noqa: PLC0415 - one WorkspaceClient per process (M2M OAuth)

    return storage._w()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ── error classification + access logging ───────────────────────────────────
def classify(exc: BaseException) -> str:
    """'permission' | 'auth' | 'not_found' | 'busy' | 'other' for an SDK / HTTP error."""
    try:
        from databricks.sdk import errors as E  # noqa: PLC0415

        if isinstance(exc, E.PermissionDenied):
            return "permission"
        if isinstance(exc, E.Unauthenticated):
            return "auth"
        if isinstance(exc, E.NotFound):
            return "not_found"
    except Exception:  # noqa: BLE001 - SDK missing (local preview)
        pass
    msg = str(exc).lower()
    if any(s in msg for s in ("insufficient permission", "permission_denied", "permission denied", "forbidden",
                              "not authorized", "403")):
        return "permission"
    if any(s in msg for s in ("invalid_client", "unauthenticated", "401", "invalid client secret",
                              "cannot configure default credentials")):
        return "auth"
    if any(s in msg for s in ("does not exist", "not found", "404")):
        return "not_found"
    if any(s in msg for s in ("already", "in progress", "currently", "busy", "conflict", "429", "too many")):
        return "busy"
    return "other"


_READ_FIX = ("Grant the service principal USE CATALOG on catalog {catalog}, USE SCHEMA on schema {catalog}.{schema} "
             "and SELECT on the index {index} (querying needs no permission on the AI Search endpoint). If the "
             "service principal's OAuth secret was created with restricted scopes, it must include 'vector-search' "
             "and 'model-serving' (or 'all-apis'). If the error names the embedding model, the account restricts "
             "foundation models: give the service principal CAN QUERY on the serving endpoint {model}.")
_SYNC_FIX = ("The service principal may not trigger a sync of {index} (index owner today: {owner}). Databricks does not "
             "document a separate 'sync' privilege; the index owner can sync (the sync pipeline runs as the owner). "
             "Ask the index owner or a metastore admin to make the service principal {principal} the OWNER of the "
             "index (Catalog Explorer → {index} → Owner) – it already reads the source table – or to grant whatever "
             "privilege the workspace requires. Until then new uploads are written to the chunks table but are NOT "
             "searchable: click 'Sync now' on the index page in Catalog Explorer, or run a scheduled Databricks job "
             "that syncs the index.")
_AUTH_FIX = ("Databricks rejected the app's credentials. Check DATABRICKS_SERVER_HOSTNAME / DATABRICKS_CLIENT_ID / "
             "DATABRICKS_CLIENT_SECRET on Posit Connect: the OAuth secret may have expired or been rotated, or the "
             "service principal is not assigned to this workspace.")
_NOT_FOUND_FIX = ("The index {index} was not found – or the service principal cannot see it (Unity Catalog answers "
                  "'does not exist' when USE CATALOG / USE SCHEMA / SELECT are missing). Check PIM_AI_SEARCH_INDEX and "
                  "the grants above.")

_owner_cache = {"owner": None}


def _fix_text(operation: str, kind: str) -> str:
    fmt = dict(catalog=C.DATABRICKS_CATALOG, schema=C.DATABRICKS_SCHEMA, index=C.AI_SEARCH_INDEX,
               model=C.AI_SEARCH_EMBEDDING_MODEL, principal=C.DATABRICKS_CLIENT_ID or "<DATABRICKS_CLIENT_ID>",
               owner=_owner_cache["owner"] or "unknown")
    if kind == "auth":
        return _AUTH_FIX
    if kind == "not_found":
        return (_NOT_FOUND_FIX + " " + _READ_FIX).format(**fmt)
    if operation == "sync_index":
        return _SYNC_FIX.format(**fmt)
    return _READ_FIX.format(**fmt)


_EFFECT = {"query_index": "chat answers and briefings cannot use the policy documents",
           "get_index": "the index status cannot be read",
           "sync_index": "new / changed documents are stored but are not searchable until the index is synced"}
_problems: dict[str, dict] = {}          # operation → last access problem (for status / the admin page)
_last_logged: dict[tuple, float] = {}
_LOG_EVERY_SECONDS = 3600


def access_problem(operation: str, exc: BaseException) -> dict:
    """Record (and log, at most hourly per operation + kind) a refused call.  Returns the problem dict."""
    kind = classify(exc)
    problem = {"operation": operation, "kind": kind, "error": f"{type(exc).__name__}: {exc}"[:1500],
               "principal": C.DATABRICKS_CLIENT_ID or None, "workspace": C.DATABRICKS_SERVER_HOSTNAME or None,
               "index": C.AI_SEARCH_INDEX, "endpoint": C.AI_SEARCH_ENDPOINT, "at": _now(),
               "effect": _EFFECT.get(operation, ""), "fix": _fix_text(operation, kind)}
    if kind in ("permission", "auth", "not_found"):
        _problems[operation] = problem
        key = (operation, kind)
        if time.monotonic() - _last_logged.get(key, -1e9) >= _LOG_EVERY_SECONDS:
            _last_logged[key] = time.monotonic()
            log.error(
                "AI SEARCH ACCESS NEEDED\n"
                "  operation : %s\n  problem   : %s\n  principal : %s (OAuth M2M service principal of the app)\n"
                "  workspace : %s\n  index     : %s\n  endpoint  : %s\n  error     : %s\n  effect    : %s\n"
                "  fix       : %s",
                operation, kind, problem["principal"], problem["workspace"], problem["index"], problem["endpoint"],
                problem["error"], problem["effect"], problem["fix"])
    return problem


def _clear_problem(operation: str) -> None:
    if _problems.pop(operation, None) is not None:
        log.info("AI Search: %s works again for %s", operation, C.AI_SEARCH_INDEX)


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
            out[key] = list(cond) if cond else ["__none__"]        # empty list = match nothing
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
    """Top-k chunks for one query → [{"content", "metadata"}] (best first).  Raises AISearchError."""
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
        p = access_problem("query_index", exc)
        if p["kind"] in ("permission", "auth", "not_found"):
            raise AISearchError("Document search is not available: the app is not allowed to read the search "
                                "index (details are in the app log under 'AI SEARCH ACCESS NEEDED').") from exc
        log.warning("AI Search query failed (%s): %s", p["kind"], exc)
        raise AISearchError(f"Document search failed: {exc}") from exc
    _clear_problem("query_index")
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
_sync = {"last_attempt": None, "last_ok": None, "last_error": None, "writes_pending": 0, "notified_off": False}


def sync_state() -> dict:
    return {**{k: v for k, v in _sync.items() if k != "notified_off"}, "sync_on_write": C.AI_SEARCH_SYNC_ON_WRITE,
            "problems": list(_problems.values())}


def status() -> dict:
    """{"ready", "indexed_row_count", "message", "index", "endpoint", "owner", "sync", …} – never raises."""
    info = {"index": C.AI_SEARCH_INDEX, "endpoint": C.AI_SEARCH_ENDPOINT, "ready": None, "indexed_row_count": None,
            "message": None, "owner": None, "enabled": enabled()}
    if not enabled():
        info["message"] = disabled_reason()
        info["sync"] = sync_state()
        return info
    try:
        idx = _w().vector_search_indexes.get_index(index_name=C.AI_SEARCH_INDEX)
        _clear_problem("get_index")
        info["owner"] = _owner_cache["owner"] = getattr(idx, "creator", None) or _owner_cache["owner"]
        info["endpoint"] = getattr(idx, "endpoint_name", None) or info["endpoint"]
        st = idx.status
        if st:
            info.update(ready=st.ready, indexed_row_count=st.indexed_row_count, message=st.message)
    except Exception as exc:  # noqa: BLE001
        access_problem("get_index", exc)
        info["message"] = f"{type(exc).__name__}: {exc}"
    info["sync"] = sync_state()
    return info


def sync_now() -> bool:
    """Ask the index to pick up the latest table changes.  True = accepted.  Raises on any refusal."""
    if not enabled():
        return False
    _sync["last_attempt"] = _now()
    try:
        _w().vector_search_indexes.sync_index(index_name=C.AI_SEARCH_INDEX)
    except Exception as exc:  # noqa: BLE001
        _sync["last_error"] = f"{_now()} · {type(exc).__name__}: {exc}"[:1000]
        if classify(exc) in ("permission", "auth", "not_found"):
            access_problem("sync_index", exc)
        raise
    _sync.update(last_ok=_now(), last_error=None, writes_pending=0)
    _clear_problem("sync_index")
    log.info("AI Search sync requested for %s", C.AI_SEARCH_INDEX)
    return True


def sync_best_effort() -> bool:
    try:
        return sync_now()
    except Exception as exc:  # noqa: BLE001
        if classify(exc) not in ("permission", "auth", "not_found"):     # those were logged by access_problem
            log.warning("AI Search sync not started (%s) – it will be picked up by the next sync", exc)
        return False


# Debounced background sync: uploads mark the index dirty; one daemon thread syncs at most every
# AI_SEARCH_SYNC_DEBOUNCE_SECONDS and retries while a previous sync is still running.
_dirty = threading.Event()
_worker_lock = threading.Lock()
_worker: threading.Thread | None = None


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
                kind = classify(exc)
                if kind in ("permission", "auth", "not_found"):
                    break                               # logged by access_problem; retried on the next upload
                log.info("AI Search sync busy/failed (%s: %s) – retry %d/5 in 60 s", kind, exc, attempt + 1)
                time.sleep(60)


def request_sync() -> None:
    """Non-blocking: schedule a sync of the index after a chunk write (no-op when AI Search is off)."""
    global _worker
    if not enabled():
        return
    _sync["writes_pending"] += 1
    if not C.AI_SEARCH_SYNC_ON_WRITE:
        if not _sync["notified_off"]:
            _sync["notified_off"] = True
            log.warning("AI Search: chunks were written but PIM_AI_SEARCH_SYNC=0, so the app does not sync %s. "
                        "New / changed documents become searchable only after 'Sync now' on the index page or a "
                        "scheduled sync.", C.AI_SEARCH_INDEX)
        return
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_sync_loop, name="ai-search-sync", daemon=True)
            _worker.start()
    _dirty.set()


# ── self-check (startup log + scripts/check_ai_search.py) ────────────────────
def diagnose(include_sync: bool = False) -> dict:
    """Run every access check the app depends on → {"ok", "checks": [{name, ok, detail, fix}], "context"}.

    include_sync=True really triggers a sync (harmless: it only picks up pending table changes)."""
    checks: list[dict] = []
    ctx = {"backend": C.SEARCH_BACKEND, "db_backend": C.DB_BACKEND, "workspace": C.DATABRICKS_SERVER_HOSTNAME,
           "principal": C.DATABRICKS_CLIENT_ID, "index": C.AI_SEARCH_INDEX, "endpoint": C.AI_SEARCH_ENDPOINT,
           "query_type": C.AI_SEARCH_QUERY_TYPE, "sync_on_write": C.AI_SEARCH_SYNC_ON_WRITE, "at": _now()}

    def add(name, ok, detail="", fix=""):
        checks.append({"name": name, "ok": ok, "detail": detail, "fix": fix})

    if not enabled():
        add("configuration", False, disabled_reason(), "Set PIM_SEARCH_BACKEND=ai_search (or leave it unset) and "
            "PIM_AI_SEARCH_INDEX to the full index name (or leave it unset for the default).")
        return {"ok": False, "checks": checks, "context": ctx}
    add("configuration", True, f"index={C.AI_SEARCH_INDEX} query_type={C.AI_SEARCH_QUERY_TYPE} "
                               f"sync_on_write={C.AI_SEARCH_SYNC_ON_WRITE}")
    try:
        w = _w()
    except Exception as exc:  # noqa: BLE001
        add("credentials", False, f"{type(exc).__name__}: {exc}", _AUTH_FIX)
        return {"ok": False, "checks": checks, "context": ctx}
    try:
        me = w.current_user.me()
        add("identity", True, f"{getattr(me, 'display_name', None) or ''} ({getattr(me, 'user_name', None)})")
    except Exception as exc:  # noqa: BLE001
        kind = classify(exc)
        add("identity", kind not in ("auth",), f"{type(exc).__name__}: {exc}"[:500],
            _AUTH_FIX if kind == "auth" else "Informational only – not needed by the app.")
        if kind == "auth":
            return {"ok": False, "checks": checks, "context": ctx}
    st = status()
    if st["ready"] is None and st["message"]:
        p = _problems.get("get_index")
        add("read index", False, st["message"][:500], p["fix"] if p else _fix_text("get_index", "other"))
    else:
        add("read index", bool(st["ready"]), f"ready={st['ready']} rows={st['indexed_row_count']} "
                                             f"owner={st['owner']} endpoint={st['endpoint']} {st['message'] or ''}"[:500],
            "" if st["ready"] else "The index exists but is not ready – check its page in Catalog Explorer.")
    try:
        hits = query("public investment management", {"record_status": {"$ne": "archived"}}, 1)
        add("query", True, f"{len(hits)} result(s)")
    except AISearchError as exc:
        p = _problems.get("query_index")
        add("query", False, (p["error"] if p else str(exc))[:500], p["fix"] if p else "")
    if include_sync:
        try:
            sync_now()
            add("sync", True, "sync accepted")
        except Exception as exc:  # noqa: BLE001
            kind = classify(exc)
            if kind == "busy":
                add("sync", True, f"a sync is already running ({exc})"[:500])
            else:
                add("sync", False, f"{type(exc).__name__}: {exc}"[:500], _fix_text("sync_index", kind))
    else:
        add("sync", None, "not tested (run scripts/check_ai_search.py --sync)")
    return {"ok": all(c["ok"] is not False for c in checks), "checks": checks, "context": ctx}


def format_report(report: dict) -> str:
    c = report["context"]
    lines = [f"AI Search access report · {c['at']}",
             f"  principal {c['principal'] or '-'} on {c['workspace'] or '-'}",
             f"  index {c['index']} (endpoint {c['endpoint']})"]
    for chk in report["checks"]:
        mark = {True: "OK  ", False: "FAIL", None: "--  "}[chk["ok"]]
        lines.append(f"  [{mark}] {chk['name']:<13} {chk['detail']}")
        if chk["ok"] is False and chk["fix"]:
            lines.append(f"         fix: {chk['fix']}")
    return "\n".join(lines)


_startup = {"done": False}


def start_startup_check() -> None:
    """Once per process, in the background: run the read-only checks and log the report (ERROR if one fails)."""
    if _startup["done"] or not enabled():
        return
    _startup["done"] = True

    def run():
        try:
            rep = diagnose(include_sync=False)
            (log.info if rep["ok"] else log.error)("%s", format_report(rep))
            if not C.AI_SEARCH_SYNC_ON_WRITE:
                log.warning("AI Search: PIM_AI_SEARCH_SYNC=0 – uploads are not synced to the index by the app.")
        except Exception:  # noqa: BLE001
            log.exception("AI Search startup check failed")

    threading.Thread(target=run, name="ai-search-check", daemon=True).start()

"""
Database access layer.

Production  : Azure Databricks SQL warehouse → Unity Catalog `prd_mega.sgpbpi163`
              (OAuth machine-to-machine with a service principal client id/secret).
Local demo  : SQLite file (PIM_DB_BACKEND=sqlite) – only for previewing the UI
              without Databricks credentials.

All SQL in this project is written in the common subset of both dialects
(named `:param` markers, no vendor functions) so the same statements run on both.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

import constants as C

log = logging.getLogger(__name__)

_lock = threading.RLock()
_conn: Any = None

# ── tiny TTL cache (read-mostly tables, mirrors Next.js `revalidate`) ────────
_cache: dict[str, tuple[float, Any]] = {}


def cached(key: str, ttl: int | None, fn):
    ttl = C.QUERY_CACHE_SECONDS if ttl is None else ttl
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def invalidate(prefix: str = "") -> None:
    for k in [k for k in _cache if k.startswith(prefix)]:
        _cache.pop(k, None)


# ── helpers ──────────────────────────────────────────────────────────────────
def uid() -> str:
    return str(uuid.uuid4())


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def table(name: str) -> str:
    """Fully-qualified table name for a logical table key (see constants.TABLES)."""
    physical = C.TABLES[name]
    if C.DB_BACKEND == "sqlite":
        return physical
    return f"`{C.DATABRICKS_CATALOG}`.`{C.DATABRICKS_SCHEMA}`.`{physical}`"


def db_configured() -> bool:
    if C.DB_BACKEND == "sqlite":
        return True
    return all(
        [C.DATABRICKS_SERVER_HOSTNAME, C.DATABRICKS_HTTP_PATH, C.DATABRICKS_CLIENT_ID, C.DATABRICKS_CLIENT_SECRET]
    )


# ── connections ──────────────────────────────────────────────────────────────
def _connect_databricks():
    from databricks import sql
    from databricks.sdk.core import Config, oauth_service_principal

    if not db_configured():
        raise RuntimeError(
            "Databricks is not configured. Set DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH, "
            "DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET (see .env.sample)."
        )

    def credentials_provider():
        cfg = Config(
            host=f"https://{C.DATABRICKS_SERVER_HOSTNAME}",
            client_id=C.DATABRICKS_CLIENT_ID,
            client_secret=C.DATABRICKS_CLIENT_SECRET,
        )
        return oauth_service_principal(cfg)

    return sql.connect(
        server_hostname=C.DATABRICKS_SERVER_HOSTNAME,
        http_path=C.DATABRICKS_HTTP_PATH,
        credentials_provider=credentials_provider,
        catalog=C.DATABRICKS_CATALOG,
        schema=C.DATABRICKS_SCHEMA,
    )


def _connect_sqlite():
    import os

    os.makedirs(os.path.dirname(C.SQLITE_PATH), exist_ok=True)
    conn = sqlite3.connect(C.SQLITE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _get_conn():
    global _conn
    if _conn is None:
        _conn = _connect_sqlite() if C.DB_BACKEND == "sqlite" else _connect_databricks()
    return _conn


def _reset_conn():
    global _conn
    try:
        if _conn is not None:
            _conn.close()
    except Exception:  # noqa: BLE001
        pass
    _conn = None


def _norm_params(params: dict | None) -> dict:
    out = {}
    for k, v in (params or {}).items():
        if C.DB_BACKEND == "sqlite":
            if isinstance(v, datetime):
                v = v.isoformat(sep=" ")
            elif isinstance(v, bool):
                v = int(v)
        out[k] = v
    return out


def _run(sql_text: str, params: dict | None, fetch: bool):
    last: Exception | None = None
    for attempt in range(2):
        try:
            with _lock:
                conn = _get_conn()
                cur = conn.cursor()
                try:
                    cur.execute(sql_text, _norm_params(params))
                    if fetch and cur.description:
                        cols = [d[0] for d in cur.description]
                        return [dict(zip(cols, tuple(r))) for r in cur.fetchall()]
                    if C.DB_BACKEND == "sqlite":
                        conn.commit()
                    return []
                finally:
                    cur.close()
        except Exception as exc:  # noqa: BLE001 - reconnect once on transport errors
            last = exc
            msg = str(exc).lower()
            transient = any(s in msg for s in ("session", "connection", "closed", "timeout", "expired", "token"))
            if attempt == 0 and transient and C.DB_BACKEND != "sqlite":
                log.warning("DB call failed (%s) — reconnecting once", exc)
                with _lock:
                    _reset_conn()
                continue
            raise
    raise last  # pragma: no cover


def query(sql_text: str, params: dict | None = None) -> list[dict]:
    """Run a SELECT and return a list of dict rows."""
    return _run(sql_text, params, fetch=True)


def execute(sql_text: str, params: dict | None = None) -> None:
    """Run INSERT / UPDATE / DELETE / DDL."""
    _run(sql_text, params, fetch=False)


def in_clause(prefix: str, values: Iterable[Any]) -> tuple[str, dict]:
    """Build `(:p0,:p1,..)` + params for an IN (...) list."""
    vals = list(values)
    if not vals:
        return "(NULL)", {}
    names = [f"{prefix}{i}" for i in range(len(vals))]
    return "(" + ",".join(f":{n}" for n in names) + ")", dict(zip(names, vals))


def insert(logical_table: str, row: dict) -> None:
    cols = list(row)
    sql_text = (
        f"INSERT INTO {table(logical_table)} ({', '.join(cols)}) "
        f"VALUES ({', '.join(':' + c for c in cols)})"
    )
    execute(sql_text, row)
    invalidate(logical_table)


def update(logical_table: str, row_id: str, values: dict, id_col: str = "id") -> None:
    if not values:
        return
    sets = ", ".join(f"{c} = :v_{c}" for c in values)
    params = {f"v_{c}": v for c, v in values.items()}
    params["row_id"] = row_id
    execute(f"UPDATE {table(logical_table)} SET {sets} WHERE {id_col} = :row_id", params)
    invalidate(logical_table)


def delete(logical_table: str, row_id: str, id_col: str = "id") -> None:
    execute(f"DELETE FROM {table(logical_table)} WHERE {id_col} = :row_id", {"row_id": row_id})
    invalidate(logical_table)


# ── value coercion shared by the query layer ─────────────────────────────────
_FRACTION = re.compile(r"\.(\d+)")


def to_dt(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.replace(tzinfo=None)
    # Databricks returns timestamps as strings with trailing zeros trimmed ("05:00:41.14"), but Python 3.10's
    # fromisoformat only accepts 3- or 6-digit fractions → pad / cut the fraction to 6 digits.
    s = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), str(v).strip().replace("Z", ""), count=1)
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        return None


def to_bool(v: Any) -> bool | None:
    if v is None:
        return None
    if isinstance(v, str):
        return v.lower() in ("1", "true", "t", "y", "yes")
    return bool(v)


def to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

"""
One-off migration:  Neon / PostgreSQL (the Next.js app's database)  →  Azure Databricks Unity Catalog
                    + Vercel Blob files                             →  UC Volume.

    pip install "psycopg[binary]"                       # only needed for this script
    export PIM_SOURCE_DATABASE_URL="postgresql://user:pass@host/db?sslmode=require"
    export DATABRICKS_SERVER_HOSTNAME=… DATABRICKS_HTTP_PATH=… DATABRICKS_CLIENT_ID=… DATABRICKS_CLIENT_SECRET=…

    python scripts/create_tables.py                     # 1. create the policy_repo_* Delta tables + (see docs) the volume
    python scripts/migrate_from_postgres.py --dry-run   # 2. row counts only
    python scripts/migrate_from_postgres.py             # 3. copy everything (idempotent – rows already present are skipped)
    python scripts/reindex.py --lang all                 # 4. rebuild the search index (chunk files in the volume) from the documents

What is copied (source table → UC table)
    countries            → policy_repo_countries            policy_records       → policy_repo_policy_records
    documents            → policy_repo_documents            (blob_url → file_path; the file is copied to the UC Volume)
    users                → policy_repo_users                audit_logs           → policy_repo_audit_logs
    partner_api_keys     → policy_repo_partner_api_keys     batch_imports        → policy_repo_batch_imports
    batch_import_rows    → policy_repo_batch_import_rows    (jsonb raw / errors → JSON text in STRING columns)
    briefings_cache      → policy_repo_briefings_cache      imf_pima_assessments → policy_repo_imf_pima_assessments

What is NOT copied (and why – see docs/DATABRICKS_MIGRATION.md)
    langchain_pg_collection / langchain_pg_embedding / chunk_parents   pgvector index – the mAI gateway has no embedding
                                                                        endpoint; retrieval is BM25 over chunk files in the volume,
                                                                        which `scripts/reindex.py` rebuilds from the documents.
    accounts / sessions / verification_tokens                          Auth.js (Google OAuth) tables – replaced by Posit Connect
                                                                        sign-in or the local policy_repo_users accounts.

Credentials are read from the environment only – nothing is stored in this file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

import constants as C  # noqa: E402
from utils import db, storage  # noqa: E402
from utils.db import table  # noqa: E402
from utils.schema import SCHEMA  # noqa: E402

ORDER = ["countries", "policy_records", "documents", "users", "audit_logs", "partner_api_keys",
         "batch_imports", "batch_import_rows", "briefings_cache", "imf_pima_assessments"]
PK = {"countries": "iso3"}                       # every other table is keyed by `id`
JSON_COLS = {"batch_import_rows": ("raw", "errors")}
INSERT_BATCH = 50


# ── value conversion ────────────────────────────────────────────────────────────────────
def convert(v):
    """psycopg value → something the Databricks SQL connector (or sqlite, for tests) accepts."""
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, Decimal):
        return v if C.DB_BACKEND != "sqlite" else float(v)
    if isinstance(v, date) and C.DB_BACKEND == "sqlite":
        return v.isoformat()
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return v


def transform(logical: str, src: dict, now: datetime, file_path: str | None = None) -> dict:
    """One source row → one UC row, in the column order of utils/schema.py."""
    out = {}
    for col, typ in SCHEMA[logical]["cols"]:
        if logical == "documents" and col == "file_path":
            out[col] = file_path
            continue
        if col == "created_at" and src.get(col) is None:      # users.created_at did not exist in the Auth.js table
            out[col] = now
            continue
        v = src.get(col)
        if col in JSON_COLS.get(logical, ()) and v is not None and not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)             # jsonb → JSON text
        v = convert(v)
        if isinstance(v, bool) and typ.split()[0] != "BOOLEAN":
            v = int(v)
        if col in ("iso3", "country") and isinstance(v, str):  # char(3) columns are space padded in Postgres
            v = v.strip()
        out[col] = v
    return out


# ── sources / targets ───────────────────────────────────────────────────────────────────
def read_table(conn, name: str):
    """Yield dict rows of a Postgres table (server-side cursor, so big tables don't fill memory)."""
    from psycopg.rows import dict_row

    with conn.cursor(name=f"mig_{name}", row_factory=dict_row) as cur:
        cur.itersize = 500
        cur.execute(f'SELECT * FROM "{name}"')
        yield from cur


def count_source(conn, name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{name}"')
        return cur.fetchone()[0]


def existing_keys(logical: str) -> set:
    col = PK.get(logical, "id")
    return {r[col] for r in db.query(f"SELECT {col} FROM {table(logical)}")}


def bulk_insert(logical: str, rows: list[dict]) -> None:
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
    db.execute(f"INSERT INTO {table(logical)} ({', '.join(cols)}) VALUES {','.join(groups)}", params)


def download(url: str) -> bytes:
    res = requests.get(url, timeout=120)
    res.raise_for_status()
    return res.content


# ── main ────────────────────────────────────────────────────────────────────────────────
def migrate(conn, tables: list[str], dry_run: bool, skip_files: bool, read=read_table, count=count_source,
            fetch=download) -> dict:
    now = db.now()
    report: dict[str, dict] = {}
    country_of_record: dict[str, str] = {}
    for logical in ORDER:
        if logical == "documents" and "policy_records" not in tables:
            # the country (storage folder) of each record is needed to build the volume path
            country_of_record = {r["id"]: r["country"] for r in db.query(f"SELECT id, country FROM {table('policy_records')}")}
        if logical not in tables:
            continue
        n_src = count(conn, logical)
        if dry_run:
            report[logical] = {"source": n_src}
            print(f"{logical:<22} {n_src:>8} rows in source")
            continue
        have = {str(k).strip() for k in existing_keys(logical)}
        key = PK.get(logical, "id")
        batch, copied, skipped, failed = [], 0, 0, 0
        for src in read(conn, logical):
            src = {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in src.items()}
            if logical == "policy_records":
                country_of_record[src["id"]] = src["country"]
            if str(src.get(key)).strip() in have:
                skipped += 1
                continue
            file_path = None
            if logical == "documents":
                file_path = storage.make_key(country_of_record.get(src["record_id"], "XXX"), src["id"], src.get("file_name"))
                if not skip_files:
                    try:
                        if not storage.exists(file_path):
                            storage.save(file_path, fetch(src["blob_url"]))
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        print(f"  ! file copy failed for document {src['id']}: {exc}")
                        continue
            batch.append(transform(logical, src, now, file_path))
            if len(batch) >= INSERT_BATCH:
                bulk_insert(logical, batch)
                copied += len(batch)
                batch = []
        bulk_insert(logical, batch)
        copied += len(batch)
        n_dst = int(db.query(f"SELECT COUNT(*) AS n FROM {table(logical)}")[0]["n"])
        report[logical] = {"source": n_src, "copied": copied, "already_present": skipped, "failed": failed, "target": n_dst}
        flag = "" if n_dst == n_src else "   <-- counts differ"
        print(f"{logical:<22} source {n_src:>7}  copied {copied:>7}  present {skipped:>7}  failed {failed:>4}  target {n_dst:>7}{flag}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source-url", default=os.environ.get("PIM_SOURCE_DATABASE_URL"),
                    help="PostgreSQL URL of the old database (or env PIM_SOURCE_DATABASE_URL)")
    ap.add_argument("--tables", help=f"comma-separated subset of: {', '.join(ORDER)}")
    ap.add_argument("--dry-run", action="store_true", help="only count source rows")
    ap.add_argument("--skip-files", action="store_true", help="do not copy the files from Vercel Blob (metadata only)")
    args = ap.parse_args()
    if not args.source_url:
        ap.error("--source-url or PIM_SOURCE_DATABASE_URL is required")
    tables = [t.strip() for t in args.tables.split(",")] if args.tables else list(ORDER)
    bad = [t for t in tables if t not in ORDER]
    if bad:
        ap.error(f"unknown table(s): {', '.join(bad)}")

    import psycopg

    print(f"target: {C.DATABRICKS_CATALOG}.{C.DATABRICKS_SCHEMA} (backend={C.DB_BACKEND})")
    with psycopg.connect(args.source_url) as conn:
        report = migrate(conn, tables, args.dry_run, args.skip_files)
    if not args.dry_run:
        print("\nNext: python scripts/reindex.py --lang all   (rebuilds the chunk files – the pgvector index is not migrated)")
        bad = [t for t, r in report.items() if r["target"] != r["source"] or r["failed"]]
        if bad:
            print("Check:", ", ".join(bad))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

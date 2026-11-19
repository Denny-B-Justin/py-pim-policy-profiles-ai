# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Backfill policy-document chunks → Databricks AI Search
# MAGIC
# MAGIC **One-time (re-runnable) job** that turns the documents already stored in the Unity Catalog volume into the
# MAGIC search index used by the PIM Policy Profiles AI Dash app (AI Coach + country briefings).
# MAGIC
# MAGIC ```
# MAGIC  UC Volume  …/upload-files/documents/country-files/  (_manifest.json + pdf|docx|xlsx)   policy_repo_documents + policy_records + countries
# MAGIC        │                                                                          │  (metadata: country, names, year, tiers, status)
# MAGIC        └──────────────► extract text ─► chunk (shared chunker) ◄──────────────────┘
# MAGIC                                    │
# MAGIC                                    ▼
# MAGIC        policy_repo_document_chunks   (Delta, Change Data Feed on, 1 row per chunk)   +   policy_repo_document_ingest_log (1 row per document)
# MAGIC                                    │  Delta Sync (TRIGGERED), embeddings computed by Databricks from `embedding_text`
# MAGIC                                    ▼
# MAGIC        policy_repo_document_chunks_index   on AI Search endpoint  pim_policy_profiles_ai_search  (STANDARD)
# MAGIC                                    ▲
# MAGIC  Dash app: new upload ─► same chunker ─► MERGE into the same table ─► asks the index to sync
# MAGIC ```
# MAGIC
# MAGIC | Object | Kind | Why |
# MAGIC |---|---|---|
# MAGIC | `policy_repo_document_chunks` | Delta table (**required**) | The chunks + the record fields the index filters on. Source of the index. Written by this notebook (old documents) **and** by the app (new uploads). |
# MAGIC | `policy_repo_document_ingest_log` | Delta table (recommended) | Last outcome per document (indexed / scanned / empty / missing file / failed). Makes re-runs incremental and shows what could not be indexed. |
# MAGIC | `policy_repo_document_chunks_index` | AI Search Delta Sync index | Vector + keyword (hybrid) search. Created from the chunks table – not a table you write to. |
# MAGIC | `pim_policy_profiles_ai_search` | AI Search endpoint (compute) | Serves the index. **This is the part with a running cost** – agree it with ITS first. |
# MAGIC
# MAGIC Not needed any more: `policy_repo_langchain_pg_collection` (pgvector bookkeeping of the old app), the old
# MAGIC `chunk_parents` / `langchain_pg_embedding` tables (their Voyage/OpenAI vectors cannot be reused), and the v1 JSON files in `…/upload-files/chunks/`.
# MAGIC
# MAGIC **Files:** `_manifest.json` in `…/upload-files/documents/country-files/` maps every document id to its file (step 3). PDF, DOCX and XLSX are read; scanned PDFs without a text layer are reported, not indexed.
# MAGIC
# MAGIC **Run order:** 0 Config → 1 Tables → 2 Shared code → 3 Inventory → 4 Select → 5 Chunk → 6 Write → 7 Checks → 8 AI Search → 9 Test query → 10 Grants.
# MAGIC Steps 1–7 need no AI Search endpoint, so you can build and inspect the chunks **before** ITS approves the endpoint (set `create_ai_search = no`).
# MAGIC
# MAGIC **Re-running is safe.** Rows are upserted by `chunk_id` (`<document_id>:<chunk_index>`) and only rewritten when their content changes, so an unchanged
# MAGIC document costs nothing to re-run and is never re-embedded. `run_mode = incremental` skips documents already processed with the same chunker version and file size.
# MAGIC
# MAGIC **Compute:** a Databricks Runtime 15.4 LTS+ cluster (dedicated or standard access mode) or serverless notebook compute.

# COMMAND ----------

# MAGIC %pip install --quiet pypdf==7.0.0 python-docx==1.2.0 openpyxl==3.1.5 databricks-sdk==0.163.0

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0 · Configuration

# COMMAND ----------

dbutils.widgets.text("catalog", "prd_mega", "Catalog")
dbutils.widgets.text("schema", "sgpbpi163", "Schema")
dbutils.widgets.text("files_root", "/Volumes/prd_mega/sgpbpi163/vgpbpi163/PIMPAM-AI/policy-profiles/upload-files", "Files root (volume folder)")
dbutils.widgets.text("documents_dir", "documents/country-files", "Document repository (relative to files root)")
dbutils.widgets.text("manifest_path", "", "Manifest path (empty = <repository>/_manifest.json)")
dbutils.widgets.text("scan_root", "", "Folder to scan for files (empty = the repository)")
dbutils.widgets.dropdown("run_mode", "incremental", ["incremental", "force", "sync_only"], "Run mode")
dbutils.widgets.dropdown("lang_filter", "all", ["all", "eng", "ori"], "Languages")
dbutils.widgets.dropdown("processing", "spark", ["spark", "driver"], "Processing")
dbutils.widgets.dropdown("dry_run", "no", ["no", "yes"], "Dry run (no writes)")
dbutils.widgets.dropdown("update_manifest", "no", ["no", "yes"], "Add scan-matched files to _manifest.json")
dbutils.widgets.dropdown("recreate_chunks_table", "no", ["no", "yes"], "Recreate chunks table if its schema is wrong")
dbutils.widgets.dropdown("create_ai_search", "no", ["no", "yes"], "Create / sync AI Search endpoint + index")
dbutils.widgets.text("ai_search_endpoint", "pim_policy_profiles_ai_search", "AI Search endpoint")
dbutils.widgets.text("embedding_model", "databricks-qwen3-embedding-0-6b", "Embedding model endpoint")
dbutils.widgets.dropdown("ai_search_identity", "notebook_user", ["notebook_user", "service_principal"], "Create AI Search objects as")
dbutils.widgets.text("secret_scope", "", "Secret scope (service principal)")
dbutils.widgets.text("secret_key_client_id", "pim-client-id", "Secret key: client id")
dbutils.widgets.text("secret_key_client_secret", "pim-client-secret", "Secret key: client secret")
dbutils.widgets.text("app_principal", "", "App service principal application id (for GRANTs)")

W = {k: dbutils.widgets.get(k).strip() for k in [
    "catalog", "schema", "files_root", "documents_dir", "manifest_path", "scan_root", "run_mode", "lang_filter", "processing", "dry_run",
    "update_manifest", "recreate_chunks_table", "create_ai_search", "ai_search_endpoint", "embedding_model",
    "ai_search_identity", "secret_scope", "secret_key_client_id", "secret_key_client_secret", "app_principal"]}

CATALOG, SCHEMA = W["catalog"], W["schema"]
FQ = lambda name: f"`{CATALOG}`.`{SCHEMA}`.`{name}`"                       # noqa: E731
CHUNKS_TABLE = f"{CATALOG}.{SCHEMA}.policy_repo_document_chunks"
LOG_TABLE = f"{CATALOG}.{SCHEMA}.policy_repo_document_ingest_log"
STAGE_TABLE = f"{CATALOG}.{SCHEMA}.policy_repo_document_chunks_backfill_stage"
INDEX_NAME = f"{CHUNKS_TABLE}_index"
T_DOCS, T_RECS, T_COUNTRIES = FQ("policy_repo_documents"), FQ("policy_repo_policy_records"), FQ("policy_repo_countries")
FILES_ROOT = W["files_root"].rstrip("/")
REPO_ROOT = f"{FILES_ROOT}/{W['documents_dir'].strip('/')}"                # = the app's storage.location("")
SCAN_ROOT = (W["scan_root"] or REPO_ROOT).rstrip("/")
MANIFEST_PATH = W["manifest_path"] or f"{REPO_ROOT}/_manifest.json"
DRY_RUN = W["dry_run"] == "yes"
LANG = {"eng": "ENG", "ori": "ORI", "all": None}[W["lang_filter"]]

for k, v in W.items():
    if "secret" not in k:
        print(f"{k:24s} {v}")
print(f"{'chunks table':24s} {CHUNKS_TABLE}\n{'index':24s} {INDEX_NAME}\n{'repository':24s} {REPO_ROOT}\n"
      f"{'manifest':24s} {MANIFEST_PATH}\n{'scan folder':24s} {SCAN_ROOT}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1 · Tables
# MAGIC Same DDL as the app (`utils/schema.py` / `sql/uc_tables.sql`). If `policy_repo_document_chunks` already exists with a different
# MAGIC layout (an earlier experiment), the cell stops and tells you; set **Recreate chunks table = yes** to drop and recreate it
# MAGIC (only the chunks table – it is fully rebuildable from the documents).

# COMMAND ----------

DDL_CHUNKS = """@@DDL_CHUNKS@@"""
DDL_LOG = """@@DDL_LOG@@"""
EXPECTED_CHUNK_COLUMNS = @@EXPECTED_COLUMNS@@


def _columns(table_name):
    return {r["col_name"]: r["data_type"].lower() for r in spark.sql(f"DESCRIBE TABLE {table_name}").collect()
            if r["col_name"] and not r["col_name"].startswith("#")}


def ensure_tables():
    exists = spark.catalog.tableExists(CHUNKS_TABLE)
    if exists:
        have = _columns(CHUNKS_TABLE)
        problems = [f"{c} {t} (found: {have.get(c, 'missing')})" for c, t in EXPECTED_CHUNK_COLUMNS.items()
                    if have.get(c) != t]
        if problems:
            n = spark.table(CHUNKS_TABLE).count()
            msg = (f"{CHUNKS_TABLE} exists ({n} rows) but does not have the expected columns:\n  - "
                   + "\n  - ".join(problems))
            if W["recreate_chunks_table"] != "yes":
                raise RuntimeError(msg + "\nSet 'Recreate chunks table' = yes to drop and recreate it "
                                   "(an AI Search index on the old table must be deleted first).")
            print(msg + "\n→ dropping and recreating it")
            if not DRY_RUN:
                spark.sql(f"DROP TABLE {CHUNKS_TABLE}")
                exists = False
    if not DRY_RUN:
        spark.sql(DDL_CHUNKS)
        spark.sql(DDL_LOG)
        # AI Search syncs from the Change Data Feed – make sure it is on even for a pre-existing table
        spark.sql(f"ALTER TABLE {CHUNKS_TABLE} SET TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')")
    print("tables ready:", CHUNKS_TABLE, "·", LOG_TABLE, "(dry run – nothing created)" if DRY_RUN else "")


if W["run_mode"] != "sync_only":
    ensure_tables()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2 · Shared code — extraction + chunker
# MAGIC **Do not edit here.** This cell is a verbatim copy of the app's `utils/extract.py` (loader part) and `utils/chunking.py`
# MAGIC (generated by `tools/build_notebook.py`), so old documents chunked here and new uploads chunked by the app produce identical rows.
# MAGIC Change the chunker in the app, bump `CHUNKER_VERSION`, regenerate this notebook and re-run with `run_mode = incremental`.

# COMMAND ----------

# @@SHARED_CODE@@

# COMMAND ----------

# Self-test of the shared chunker (fast, no data access)
_t = chunk_pages([(1, "LAW ON PUBLIC INVESTMENT\nPreamble text.\nArticle 1. Scope\n" + "The ministry shall appraise projects. " * 80),
                  (2, "Article 2. Appraisal\n" + "Projects above the threshold need a cost-benefit analysis. " * 40)])
assert _t and all(c["char_count"] <= PARENT_SIZE for c in _t) and _t[0]["page"] == 1 and _t[-1]["page_end"] == 2
assert "LAW ON PUBLIC INVESTMENT" in _t[0]["content"], "text before the first heading must be kept"
print(CHUNKER_VERSION, "→", len(_t), "chunks;", [(c["section_heading"], c["page"], c["page_end"], c["char_count"]) for c in _t])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3 · Inventory — which file belongs to which document row
# MAGIC **3a** reads and checks the repository manifest `_manifest.json` (`{"generated_at", "upload_to", "documents": {document id: relative path}}`).
# MAGIC Paths in it are relative to **the folder that holds the manifest** (`…/documents/country-files/`) and look like
# MAGIC `AGO_Angola/National/2019/Tier1_Primary-Legislation/ORI_Portuguese/b1fda989_05_Public_Private_Partnerships_Law.pdf`
# MAGIC (`<ISO3_Country>/<level>/<year>/<tier>/<ENG | ORI_language>/<id8>_<name>`). The manifest's `upload_to` field is informational only and is **ignored**.
# MAGIC
# MAGIC **3b** links every `policy_repo_documents` row to its file:
# MAGIC 1. the manifest entry for the document id (primary – should cover everything)
# MAGIC 2. `blob_url` holding a repository path (documents uploaded later through the Dash app)
# MAGIC 3. fallback scan of the folder: a file named `<first 8 characters of the document id>_…`
# MAGIC 4. fallback scan: a file whose name equals the document's `file_name` (only when unique)
# MAGIC
# MAGIC and cross-checks each manifest match against the record (country folder ↔ `country`, `ENG`/`ORI_*` folder ↔ `lang_type`, file name ↔ document id).

# COMMAND ----------

import json
import os
import re
from collections import Counter
from urllib.parse import unquote

import pandas as pd


def _clean_pdf(pdf: pd.DataFrame) -> pd.DataFrame:
    return pdf.astype(object).where(pd.notnull(pdf), None)


# ── 3a · the manifest ────────────────────────────────────────────────────────
MANIFEST_RAW = None
if not os.path.exists(MANIFEST_PATH):
    raise FileNotFoundError(f"Manifest not found: {MANIFEST_PATH} – check the 'Document repository' / 'Manifest path' widgets.")
with open(MANIFEST_PATH, "r", encoding="utf-8") as fh:
    MANIFEST_RAW = json.load(fh)
if isinstance(MANIFEST_RAW, dict) and isinstance(MANIFEST_RAW.get("documents"), dict):
    MANIFEST = MANIFEST_RAW["documents"]
elif isinstance(MANIFEST_RAW, dict) and all(isinstance(v, str) for v in MANIFEST_RAW.values()):
    MANIFEST = MANIFEST_RAW                                   # flat {id: path} variant
else:
    raise ValueError(f"Unexpected manifest layout in {MANIFEST_PATH} – expected {{'documents': {{id: path}}}}.")
MANIFEST = {str(k).strip().lower(): str(v).strip().replace("\\", "/").lstrip("/") for k, v in MANIFEST.items() if v}
MANIFEST_BASE = os.path.dirname(MANIFEST_PATH)                # paths are relative to the manifest's folder
print(f"manifest            {MANIFEST_PATH}")
print(f"entries             {len(MANIFEST)}")
if isinstance(MANIFEST_RAW, dict):
    print(f"generated_at        {MANIFEST_RAW.get('generated_at')}")
    if MANIFEST_RAW.get("upload_to") and MANIFEST_RAW["upload_to"].rstrip("/") != MANIFEST_BASE:
        print(f"upload_to           {MANIFEST_RAW['upload_to']}   ← differs from the manifest's folder; ignored, "
              f"paths are resolved against {MANIFEST_BASE}")

# scan of the folder (driver – /Volumes is mounted on the driver): what is really there
SCANNED = {}                                   # abs path → size
for root, _dirs, files in os.walk(SCAN_ROOT):
    for f in files:
        if f.startswith("_manifest") or f.startswith("."):
            continue
        p = os.path.join(root, f)
        try:
            SCANNED[p] = os.path.getsize(p)
        except OSError:
            pass
print(f"files under {SCAN_ROOT}: {len(SCANNED)}")


def _abs(rel):
    return f"{MANIFEST_BASE}/{rel}"


def _exists(p):
    return p in SCANNED if p.startswith(SCAN_ROOT + "/") else os.path.exists(p)


man_pdf = pd.DataFrame([{"document_id": k, "rel": v, "abs_path": _abs(v)} for k, v in MANIFEST.items()])
man_pdf["exists"] = man_pdf["abs_path"].map(_exists)
man_pdf["ext"] = man_pdf["rel"].map(lambda r: os.path.splitext(r)[1].lower())
_parts = man_pdf["rel"].str.split("/")
man_pdf["country_dir"] = _parts.map(lambda x: x[0] if len(x) >= 6 else None)
man_pdf["lang_dir"] = _parts.map(lambda x: x[4] if len(x) >= 6 else None)
print(f"entries whose file exists: {int(man_pdf['exists'].sum())} / {len(man_pdf)}")
display(man_pdf.groupby(["ext", "exists"]).size().reset_index(name="entries"))
missing_files = man_pdf[~man_pdf["exists"]]
if len(missing_files):
    print(f"{len(missing_files)} manifest entries point to a file that is not in the volume:")
    display(missing_files[["document_id", "rel"]].head(200))
_layout_bad = man_pdf[man_pdf["country_dir"].isna()]
if len(_layout_bad):
    print(f"{len(_layout_bad)} entries do not follow <country>/<level>/<year>/<tier>/<lang>/<file> (still used):")
    display(_layout_bad[["document_id", "rel"]].head(50))
_dupes = [p for p, n in Counter(MANIFEST.values()).items() if n > 1]
if _dupes:
    print(f"WARNING: {len(_dupes)} paths are listed for more than one document id, e.g. {_dupes[:3]}")
in_manifest = set(man_pdf["abs_path"])
not_in_manifest = sorted(p for p in SCANNED if p not in in_manifest)
print(f"{len(not_in_manifest)} files in the folder are not in the manifest" + (":" if not_in_manifest else ""))
for p in not_in_manifest[:25]:
    print("   ", p)

# COMMAND ----------

# ── 3b · link document rows to files ─────────────────────────────────────────
docs_pdf = _clean_pdf(spark.sql(f"""
    SELECT d.id AS document_id, d.record_id, d.lang_type, d.lang_code, d.lang_label, d.text_layer, d.blob_url,
           d.file_name, d.file_size AS db_file_size,
           r.id AS rec_id, r.country, r.name_eng, r.name_orig, r.year, r.source, r.policy_guidance_tier, r.strategy_tier,
           r.jurisdiction_level, r.jurisdiction_name, r.record_status, c.name AS country_name
    FROM {T_DOCS} d
    LEFT JOIN {T_RECS} r ON r.id = d.record_id
    LEFT JOIN {T_COUNTRIES} c ON c.iso3 = r.country
""").toPandas())
print(f"{len(docs_pdf)} rows in policy_repo_documents")
if docs_pdf.empty:
    raise RuntimeError(f"{T_DOCS} is empty – migrate the documents table first (scripts/migrate_from_postgres.py).")

_by_prefix, _by_name = {}, {}
for p in SCANNED:
    base = os.path.basename(p)
    if len(base) > 9 and base[8] == "_":
        _by_prefix.setdefault(base[:8].lower(), []).append(p)
    _by_name.setdefault(base, []).append(p)


def _safe_part(file_name):                     # = storage._file_part (what the repository names files)
    base = (file_name or "document").replace("\\", "/").split("/")[-1]
    stem, dot, ext = base.rpartition(".")
    if not dot or len(ext) > 5:
        stem, ext = base, ""
    seg = lambda s, lim, fb: (re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9.-]+", "_", (s or "").strip())).strip("_.")[:lim].rstrip("_.") or fb)  # noqa: E731
    return seg(stem, 80, "document") + (f".{seg(ext, 5, '')}" if ext else "")


def manifest_checks(row, rel):
    """Consistency of a manifest path with the document row (warnings only – the file is still used)."""
    notes = []
    parts = rel.split("/")
    if len(parts) >= 6:
        cdir, ldir = parts[0], parts[4]
        if row["country"] and cdir.split("_")[0].upper() != str(row["country"]).upper():
            notes.append(f"country folder {cdir} ≠ record country {row['country']}")
        if row["lang_type"] == "ENG" and ldir != "ENG":
            notes.append(f"ENG document in folder {ldir}")
        if row["lang_type"] == "ORI" and not ldir.startswith("ORI"):
            notes.append(f"ORI document in folder {ldir}")
    if not os.path.basename(rel).lower().startswith(str(row["document_id"])[:8].lower() + "_"):
        notes.append("file name does not start with the document id")
    want, got = os.path.splitext(row["file_name"] or "")[1].lower(), os.path.splitext(rel)[1].lower()
    if want and got and want != got:
        notes.append(f"extension {got} ≠ file_name {want}")
    return "; ".join(notes) or None


def resolve(row):
    """→ (abs path | None, how, note)"""
    rel = MANIFEST.get(str(row["document_id"]).lower())
    if rel:
        p = _abs(rel)
        if _exists(p):
            return p, "manifest", manifest_checks(row, rel)
        manifest_note = f"manifest path not in the volume: {rel}"
    else:
        manifest_note = "not in manifest"
    blob = (row["blob_url"] or "").strip()
    if blob.startswith(REPO_ROOT + "/") and os.path.exists(blob):
        return blob, "blob_url", None
    cands = _by_prefix.get(str(row["document_id"])[:8].lower(), [])
    if cands:
        lang_dir = "/ENG/" if row["lang_type"] == "ENG" else "/ORI_"
        pref = [p for p in cands if lang_dir in p] or cands
        exact = [p for p in pref if os.path.basename(p)[9:] == _safe_part(row["file_name"])]
        pick = sorted(exact or pref)
        return pick[0], "scan_id_prefix", "; ".join(filter(None, [manifest_note, f"{len(pick)} candidates" if len(pick) > 1 else None]))
    if row["file_name"]:
        same = _by_name.get(row["file_name"], []) or _by_name.get(_safe_part(row["file_name"]), [])
        if len(same) == 1:
            return same[0], "scan_file_name", manifest_note
        if len(same) > 1:
            return None, "ambiguous", f"{manifest_note}; {len(same)} files named {row['file_name']}"
    return None, "not_found", manifest_note


res = docs_pdf.apply(resolve, axis=1, result_type="expand")
docs_pdf["abs_path"], docs_pdf["found_by"], docs_pdf["resolve_note"] = res[0], res[1], res[2]
docs_pdf["file_size"] = pd.Series([SCANNED.get(p) if p in SCANNED else (os.path.getsize(p) if p else None)
                                    for p in docs_pdf["abs_path"]], index=docs_pdf.index, dtype=object)
docs_pdf["file_path"] = docs_pdf["abs_path"].map(
    lambda p: (p[len(REPO_ROOT) + 1:] if p and p.startswith(REPO_ROOT + "/") else p))      # = the app's doc["file_path"]

display(docs_pdf.groupby(["found_by", "lang_type"], dropna=False).size().reset_index(name="documents"))
_db_ids = set(docs_pdf["document_id"].astype(str).str.lower())
orphan_entries = man_pdf[~man_pdf["document_id"].isin(_db_ids)]
print(f"{len(orphan_entries)} manifest entries have no policy_repo_documents row (cannot be indexed without a record)"
      + (":" if len(orphan_entries) else ""))
if len(orphan_entries):
    display(orphan_entries[["document_id", "rel", "exists"]].head(200))
used = set(docs_pdf["abs_path"].dropna())
unreferenced = sorted(p for p in SCANNED if p not in used)
print(f"{len(unreferenced)} files in the volume are not linked to any policy_repo_documents row")
for p in unreferenced[:25]:
    print("   ", p)
problems = docs_pdf[docs_pdf["abs_path"].isna() | docs_pdf["rec_id"].isna() | docs_pdf["resolve_note"].notna()]
if len(problems):
    print(f"{len(problems)} documents need attention (missing file, no record, or a manifest / record mismatch):")
    display(problems[["document_id", "record_id", "lang_type", "file_name", "found_by", "resolve_note", "country", "name_eng"]])

# COMMAND ----------

# Optional: make the app able to find files that were placed in the volume by hand (downloads + future re-indexing)
if W["update_manifest"] == "yes" and W["run_mode"] != "sync_only":
    add = {r.document_id: r.abs_path[len(MANIFEST_BASE) + 1:] for r in docs_pdf.itertuples()
           if r.found_by in ("scan_id_prefix", "scan_file_name") and r.abs_path and r.abs_path.startswith(MANIFEST_BASE + "/")}
    if add and not DRY_RUN:
        import datetime as _dt
        if os.path.exists(MANIFEST_PATH):
            stamp = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            with open(MANIFEST_PATH, "rb") as src, open(f"{MANIFEST_BASE}/_manifest.backup-{stamp}.json", "wb") as dst:
                dst.write(src.read())
        merged = {**add, **MANIFEST}                       # never overwrite an existing entry
        if isinstance(MANIFEST_RAW, dict) and "documents" in MANIFEST_RAW:
            payload = {**MANIFEST_RAW, "documents": merged}
        else:
            payload = merged
        with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, ensure_ascii=False)
        print(f"manifest: +{len(add)} entries (backup written)")
    else:
        print(f"manifest: {len(add)} entries would be added" + (" (dry run)" if DRY_RUN else ""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4 · Select the documents to process

# COMMAND ----------

TERMINAL = ("no_text_layer", "empty", "unsupported")
if W["run_mode"] == "sync_only":
    todo = docs_pdf.iloc[0:0]
    print("sync_only: skipping chunking")
else:
    scope = docs_pdf if LANG is None else docs_pdf[docs_pdf["lang_type"] == LANG]
    done = {}
    if spark.catalog.tableExists(LOG_TABLE):
        done = {r["document_id"]: r for r in spark.table(LOG_TABLE).collect()}
    have_chunks = set()
    if spark.catalog.tableExists(CHUNKS_TABLE):
        have_chunks = {r[0] for r in spark.sql(f"SELECT DISTINCT document_id FROM {CHUNKS_TABLE} "
                                               f"WHERE chunker_version = '{CHUNKER_VERSION}'").collect()}

    def up_to_date(row):
        lg = done.get(row["document_id"])
        if W["run_mode"] == "force" or lg is None or lg["chunker_version"] != CHUNKER_VERSION:
            return False
        if row["file_size"] is None or lg["file_size"] is None or int(lg["file_size"]) != int(row["file_size"]):
            return False
        return (lg["status"] == "indexed" and row["document_id"] in have_chunks) or lg["status"] in TERMINAL

    todo = scope[~scope.apply(up_to_date, axis=1)] if len(scope) else scope
    print(f"in scope: {len(scope)} · already up to date: {len(scope) - len(todo)} · to process: {len(todo)}")

# documents decided without reading the file
pre = []
for r in todo.itertuples():
    if r.rec_id is None:
        pre.append((r.document_id, "orphan_document", "policy_repo_documents row without a policy record", 0, r.file_size))
    elif r.abs_path is None:
        pre.append((r.document_id, "missing_file", f"file not found ({r.found_by}{': ' + r.resolve_note if r.resolve_note else ''})", 0, None))
    elif r.text_layer is False:
        pre.append((r.document_id, "no_text_layer", "Scanned / image-only file (text_layer = false) – needs OCR", 0, r.file_size))
pre_ids = {p[0] for p in pre}
work = todo[~todo["document_id"].isin(pre_ids)]
print(f"to read and chunk: {len(work)} · decided without reading: {len(pre)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5 · Extract + chunk
# MAGIC `processing = spark` reads the files with Spark's `binaryFile` source and chunks them in parallel on the workers;
# MAGIC any document Spark could not read is retried on the driver. `processing = driver` does everything on the driver (simplest, slower).
# MAGIC Results go to a staging table first, so nothing is computed twice and nothing touches the chunks table until step 6.

# COMMAND ----------

RECORD_KEYS = ["country", "name_eng", "name_orig", "year", "source", "policy_guidance_tier", "strategy_tier",
               "jurisdiction_level", "jurisdiction_name", "record_status"]
META = {}
for r in work.to_dict("records"):
    META[r["abs_path"]] = {
        "document": {"id": r["document_id"], "record_id": r["record_id"], "lang_type": r["lang_type"],
                     "lang_code": r["lang_code"], "file_name": r["file_name"]},
        "record": {k: r[k] for k in RECORD_KEYS},
        "country_name": r["country_name"] or r["country"],
        "file_path": r["file_path"],
    }


def process_document(meta, data):
    """bytes of one file → (status, detail, n_chunks, rows_json).  Never raises."""
    try:
        pages = load_pages(data, meta["document"]["file_name"])
    except ValueError as exc:
        if str(exc).startswith("Unsupported file type"):
            return "unsupported", str(exc), 0, "[]"
        return "failed", f"{type(exc).__name__}: {exc}"[:2000], 0, "[]"
    except Exception as exc:  # noqa: BLE001
        return "failed", f"{type(exc).__name__}: {exc}"[:2000], 0, "[]"
    try:
        chunks = chunk_pages(pages)
        rows = chunk_rows(chunks, meta["document"], meta["record"], meta["country_name"], meta["file_path"])
    except Exception as exc:  # noqa: BLE001
        return "failed", f"chunking: {type(exc).__name__}: {exc}"[:2000], 0, "[]"
    if not rows:
        return "empty", "No extractable text (scanned PDF without a text layer? needs OCR)", 0, "[]"
    return "indexed", None, len(rows), json.dumps(rows, ensure_ascii=False)


STAGE_SCHEMA = "document_id string, status string, detail string, n_chunks int, file_size long, rows_json string"


def _norm_path(p):
    """binaryFile `path` (dbfs:/Volumes/…, file:/…, URL-encoded) → the /Volumes/… path used as META key."""
    p = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*:(?=/)", "", unquote(p))
    return "/" + p.lstrip("/")


def _spark_chunk(batches):
    for pdf in batches:
        out = []
        for path, content, length in zip(pdf["path"], pdf["content"], pdf["length"]):
            meta = META.get(_norm_path(path))
            if meta is None:
                continue
            st, detail, n, rows = process_document(meta, bytes(content))
            out.append((meta["document"]["id"], st, detail, n, int(length), rows))
        yield pd.DataFrame(out, columns=["document_id", "status", "detail", "n_chunks", "file_size", "rows_json"])


def _glob_escape(p):
    return re.sub(r"([\[\]{}*?\\])", r"\\\1", p)


if not DRY_RUN and W["run_mode"] != "sync_only":
    spark.sql(f"DROP TABLE IF EXISTS {STAGE_TABLE}")

processed = set()
if len(work) and W["processing"] == "spark":
    try:
        spark.conf.set("spark.sql.execution.arrow.maxRecordsPerBatch", "2")
    except Exception:  # noqa: BLE001 - not settable on some compute; the default only costs memory
        pass
    paths = [_glob_escape(p) for p in META]
    try:
        bin_df = spark.read.format("binaryFile").load(paths)
        n_parts = max(8, min(256, len(paths) // 4))
        stage_df = bin_df.repartition(n_parts).mapInPandas(_spark_chunk, schema=STAGE_SCHEMA)
        if DRY_RUN:
            stage_pdf = stage_df.drop("rows_json").toPandas()
            processed = set(stage_pdf["document_id"])
            display(stage_pdf.groupby("status").agg(documents=("document_id", "count"), chunks=("n_chunks", "sum")).reset_index())
        else:
            stage_df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(STAGE_TABLE)
            processed = {r[0] for r in spark.table(STAGE_TABLE).select("document_id").collect()}
    except Exception as exc:  # noqa: BLE001 - e.g. a file removed since the inventory: do everything on the driver
        print(f"spark processing failed ({type(exc).__name__}: {str(exc)[:300]}) → falling back to the driver")
        processed = set()
        if not DRY_RUN:
            spark.sql(f"DROP TABLE IF EXISTS {STAGE_TABLE}")
    print(f"spark: processed {len(processed)} of {len(META)} files")

# driver: everything (processing = driver) or what Spark could not read
left = [(p, m) for p, m in META.items() if m["document"]["id"] not in processed]
driver_rows = []
for i, (p, m) in enumerate(left, 1):
    try:
        with open(p, "rb") as fh:
            data = fh.read()
        st, detail, n, rows = process_document(m, data)
        driver_rows.append((m["document"]["id"], st, detail, n, len(data), rows))
    except Exception as exc:  # noqa: BLE001
        driver_rows.append((m["document"]["id"], "failed", f"read: {type(exc).__name__}: {exc}"[:2000], 0, None, "[]"))
    if i % 25 == 0:
        print(f"driver: {i}/{len(left)}")
driver_rows += [(d, st, detail, n, size, "[]") for d, st, detail, n, size in pre]


def _int_or_none(v):
    try:
        return None if v is None or pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return None


if driver_rows:
    driver_rows = [(str(d), st, detail, int(n or 0), _int_or_none(size), rows) for d, st, detail, n, size, rows in driver_rows]
    extra = spark.createDataFrame(driver_rows, schema=STAGE_SCHEMA)
    if DRY_RUN:
        display(extra.drop("rows_json").groupBy("status").count())
    else:
        extra.write.mode("append").option("mergeSchema", "true").saveAsTable(STAGE_TABLE)
print(f"driver: processed {len(left)} files + {len(pre)} pre-decided documents")

if not DRY_RUN and spark.catalog.tableExists(STAGE_TABLE):
    display(spark.sql(f"SELECT status, COUNT(*) AS documents, SUM(n_chunks) AS chunks FROM {STAGE_TABLE} "
                      f"GROUP BY status ORDER BY documents DESC"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6 · Write to `policy_repo_document_chunks` + ingest log
# MAGIC * upsert chunk rows by `chunk_id` (rewritten only when `row_hash` changes → no needless re-embedding)
# MAGIC * delete a processed document's rows past its new last chunk (or all of them when it is now scanned / empty / unsupported)
# MAGIC * failed / missing-file documents keep whatever chunks they had
# MAGIC * delete chunks of documents that no longer exist in `policy_repo_documents`

# COMMAND ----------

ROWS_SCHEMA = "ARRAY<STRUCT<" + ", ".join(
    f"`{c}`: {'INT' if c in ('chunk_index', 'page', 'page_end', 'char_count', 'year', 'policy_guidance_tier', 'strategy_tier') else 'STRING'}"
    for c in CHUNK_COLUMNS) + ">>"

if not DRY_RUN and W["run_mode"] != "sync_only" and spark.catalog.tableExists(STAGE_TABLE):
    spark.sql(f"""
        CREATE OR REPLACE TEMPORARY VIEW backfill_chunks AS
        SELECT inline(from_json(rows_json, '{ROWS_SCHEMA}')) FROM {STAGE_TABLE} WHERE status = 'indexed'
    """)
    dupes = spark.sql("SELECT chunk_id, COUNT(*) n FROM backfill_chunks GROUP BY chunk_id HAVING n > 1").count()
    assert dupes == 0, f"{dupes} duplicate chunk ids in the staging data"

    sets = ", ".join(f"t.`{c}` = s.`{c}`" for c in CHUNK_COLUMNS if c != "chunk_id")
    cols = ", ".join(f"`{c}`" for c in CHUNK_COLUMNS)
    vals = ", ".join(f"s.`{c}`" for c in CHUNK_COLUMNS)
    m1 = spark.sql(f"""
        MERGE INTO {CHUNKS_TABLE} AS t USING backfill_chunks AS s ON t.chunk_id = s.chunk_id
        WHEN MATCHED AND t.row_hash <> s.row_hash THEN UPDATE SET {sets}, t.updated_at = current_timestamp()
        WHEN NOT MATCHED THEN INSERT ({cols}, created_at, updated_at) VALUES ({vals}, current_timestamp(), current_timestamp())
    """)
    print("upsert:", m1.collect()[0].asDict())
    m2 = spark.sql(f"""
        MERGE INTO {CHUNKS_TABLE} AS t
        USING (SELECT document_id, n_chunks FROM {STAGE_TABLE}
               WHERE status IN ('indexed', 'empty', 'no_text_layer', 'unsupported')) AS s
        ON t.document_id = s.document_id AND t.chunk_index >= s.n_chunks
        WHEN MATCHED THEN DELETE
    """)
    print("surplus rows removed:", m2.collect()[0].asDict())
    m3 = spark.sql(f"""
        MERGE INTO {LOG_TABLE} AS t
        USING (SELECT s.document_id, d.record_id, d.lang_type, s.file_size, s.status, s.detail, s.n_chunks
               FROM {STAGE_TABLE} s LEFT JOIN {T_DOCS} d ON d.id = s.document_id) AS s
        ON t.document_id = s.document_id
        WHEN MATCHED THEN UPDATE SET t.record_id = s.record_id, t.lang_type = s.lang_type, t.file_size = s.file_size,
             t.status = s.status, t.detail = s.detail, t.chunk_count = s.n_chunks, t.chunker_version = '{CHUNKER_VERSION}',
             t.source = 'notebook_backfill', t.processed_at = current_timestamp()
        WHEN NOT MATCHED THEN INSERT (document_id, record_id, lang_type, file_path, file_size, status, detail, chunk_count,
             chunker_version, source, processed_at)
             VALUES (s.document_id, s.record_id, s.lang_type, NULL, s.file_size, s.status, s.detail, s.n_chunks,
                     '{CHUNKER_VERSION}', 'notebook_backfill', current_timestamp())
    """)
    path_rows = [(str(r.document_id), str(r.file_path)) for r in docs_pdf.itertuples() if r.file_path]
    if path_rows:
        spark.createDataFrame(path_rows, "document_id string, file_path string").createOrReplaceTempView("backfill_paths")
        spark.sql(f"MERGE INTO {LOG_TABLE} t USING backfill_paths s ON t.document_id = s.document_id "
                  f"WHEN MATCHED THEN UPDATE SET t.file_path = s.file_path")
    print("ingest log:", m3.collect()[0].asDict())
    m4 = spark.sql(f"""
        MERGE INTO {CHUNKS_TABLE} AS t
        USING (SELECT DISTINCT c.document_id FROM {CHUNKS_TABLE} c LEFT ANTI JOIN {T_DOCS} d ON d.id = c.document_id) AS s
        ON t.document_id = s.document_id
        WHEN MATCHED THEN DELETE
    """)
    print("orphan chunks removed:", m4.collect()[0].asDict())
    spark.sql(f"DROP TABLE IF EXISTS {STAGE_TABLE}")
else:
    print("nothing written (dry run / sync_only / nothing to process)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7 · Checks

# COMMAND ----------

if spark.catalog.tableExists(CHUNKS_TABLE):
    display(spark.sql(f"""
        SELECT COUNT(*) AS chunks, COUNT(DISTINCT document_id) AS documents, COUNT(DISTINCT record_id) AS records,
               ROUND(AVG(char_count)) AS avg_chars, MAX(char_count) AS max_chars,
               SUM(CASE WHEN char_count < 200 THEN 1 ELSE 0 END) AS tiny_chunks,
               COUNT(DISTINCT chunker_version) AS chunker_versions, MAX(updated_at) AS last_write
        FROM {CHUNKS_TABLE}"""))
    display(spark.sql(f"SELECT lang_type, record_status, COUNT(*) AS chunks, COUNT(DISTINCT document_id) AS documents "
                      f"FROM {CHUNKS_TABLE} GROUP BY ALL ORDER BY ALL"))
    display(spark.sql(f"SELECT country, country_name, COUNT(DISTINCT document_id) AS documents, COUNT(*) AS chunks "
                      f"FROM {CHUNKS_TABLE} GROUP BY ALL ORDER BY documents DESC LIMIT 50"))
    n_dupe = spark.sql(f"SELECT chunk_id FROM {CHUNKS_TABLE} GROUP BY chunk_id HAVING COUNT(*) > 1").count()
    cdf = spark.sql(f"SHOW TBLPROPERTIES {CHUNKS_TABLE} ('delta.enableChangeDataFeed')").collect()[0]["value"]
    print(f"duplicate chunk ids: {n_dupe} · change data feed: {cdf}")
    assert n_dupe == 0 and cdf == "true"
    display(spark.sql(f"SELECT chunk_id, country, name_eng, lang_type, page, page_end, section_heading, char_count, "
                      f"LEFT(embedding_text, 300) AS embedding_text_start FROM {CHUNKS_TABLE} ORDER BY rand() LIMIT 10"))
if spark.catalog.tableExists(LOG_TABLE):
    display(spark.sql(f"SELECT status, source, COUNT(*) AS documents, SUM(chunk_count) AS chunks FROM {LOG_TABLE} "
                      f"GROUP BY ALL ORDER BY documents DESC"))
    display(spark.sql(f"""SELECT l.status, l.detail, d.file_name, r.country, r.name_eng, l.file_path
                          FROM {LOG_TABLE} l LEFT JOIN {T_DOCS} d ON d.id = l.document_id
                          LEFT JOIN {T_RECS} r ON r.id = l.record_id
                          WHERE l.status <> 'indexed' ORDER BY l.status, r.country"""))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8 · AI Search endpoint + Delta Sync index
# MAGIC * **Endpoint** `STANDARD` – low latency, the right size for a few thousand documents. It runs (and bills) continuously once created → ITS / charge code first.
# MAGIC * **Index** Delta Sync, **TRIGGERED** (synced on demand – by this notebook and by the app after each upload; no always-on pipeline),
# MAGIC   embeddings computed by Databricks from `embedding_text` with **`databricks-qwen3-embedding-0-6b`** (multilingual – the repository has original-language documents;
# MAGIC   `databricks-gte-large-en` is English-only). Queried in **HYBRID** mode (vectors + keywords).
# MAGIC * **Identity** `service_principal` creates both as the app's service principal (it then owns the index and can trigger syncs from the app).
# MAGIC   With `notebook_user`, grant the SP `SELECT` on the index (step 10) and, if the app may not trigger syncs, schedule this notebook with `run_mode = sync_only`.

# COMMAND ----------

import time
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.vectorsearch import (DeltaSyncVectorIndexSpecRequest, EmbeddingSourceColumn, EndpointType,
                                                 PipelineType, VectorIndexType)

SYNC_COLUMNS = ["chunk_id", "document_id", "record_id", "lang_type", "lang_code", "chunk_index", "page", "page_end",
                "section_heading", "content", "embedding_text", "country", "country_name", "name_eng", "name_orig", "year",
                "source", "policy_guidance_tier", "strategy_tier", "jurisdiction_level", "jurisdiction_name",
                "record_status", "file_name", "updated_at"]


def _client():
    if W["ai_search_identity"] == "service_principal":
        if not W["secret_scope"]:
            raise ValueError("Set 'Secret scope' (with the service principal's client id / secret) for identity = service_principal.")
        host = WorkspaceClient().config.host
        return WorkspaceClient(host=host,
                               client_id=dbutils.secrets.get(W["secret_scope"], W["secret_key_client_id"]),
                               client_secret=dbutils.secrets.get(W["secret_scope"], W["secret_key_client_secret"]))
    return WorkspaceClient()


def wait_endpoint(w, name, minutes=60):
    t0 = time.time()
    while True:
        ep = w.vector_search_endpoints.get_endpoint(name)
        state = ep.endpoint_status.state.value if ep.endpoint_status and ep.endpoint_status.state else "?"
        if state == "ONLINE":
            return ep
        if state in ("OFFLINE", "RED_STATE", "DELETED"):
            raise RuntimeError(f"endpoint {name} is {state}: {ep.endpoint_status.message}")
        if time.time() - t0 > minutes * 60:
            raise TimeoutError(f"endpoint {name} still {state} after {minutes} min")
        print(f"  endpoint {state} … ({int(time.time() - t0)} s)")
        time.sleep(30)


def wait_index(w, name, target_rows, minutes=120):
    t0, last = time.time(), None
    while True:
        st = w.vector_search_indexes.get_index(name).status
        rows = st.indexed_row_count or 0
        msg = (st.message or "")[:160]
        if (rows, msg) != last:
            print(f"  ready={st.ready} indexed_rows={rows}/{target_rows} {msg}")
            last = (rows, msg)
        if st.ready and rows >= target_rows:
            return st
        if time.time() - t0 > minutes * 60:
            print("  still syncing – check the index page in Catalog Explorer; queries work while it finishes.")
            return st
        time.sleep(30)


if W["create_ai_search"] == "yes" or W["run_mode"] == "sync_only":
    w = _client()
    target = spark.table(CHUNKS_TABLE).count()
    if W["create_ai_search"] == "yes":
        try:
            w.serving_endpoints.get(W["embedding_model"])
        except NotFound:
            raise RuntimeError(f"Embedding endpoint {W['embedding_model']} is not available in this workspace / region. "
                               f"Pick another (e.g. databricks-gte-large-en, English only) or ask ITS to enable it.")
        try:
            w.vector_search_endpoints.get_endpoint(W["ai_search_endpoint"])
            print("endpoint exists:", W["ai_search_endpoint"])
        except NotFound:
            print("creating endpoint", W["ai_search_endpoint"], "(STANDARD) – this can take 10–20 minutes")
            w.vector_search_endpoints.create_endpoint(name=W["ai_search_endpoint"], endpoint_type=EndpointType.STANDARD)
        wait_endpoint(w, W["ai_search_endpoint"])
        try:
            idx = w.vector_search_indexes.get_index(INDEX_NAME)
            print("index exists:", INDEX_NAME, "→ sync")
            spec = idx.delta_sync_index_spec
            if spec and spec.source_table and spec.source_table.lower() != CHUNKS_TABLE.lower():
                print("  WARNING: the existing index reads", spec.source_table)
            w.vector_search_indexes.sync_index(INDEX_NAME)
        except NotFound:
            print("creating index", INDEX_NAME)
            w.vector_search_indexes.create_index(
                name=INDEX_NAME, endpoint_name=W["ai_search_endpoint"], primary_key="chunk_id",
                index_type=VectorIndexType.DELTA_SYNC,
                delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
                    source_table=CHUNKS_TABLE, pipeline_type=PipelineType.TRIGGERED, columns_to_sync=SYNC_COLUMNS,
                    embedding_source_columns=[EmbeddingSourceColumn(name="embedding_text",
                                                                    embedding_model_endpoint_name=W["embedding_model"])]))
    else:
        print("sync_only → sync", INDEX_NAME)
        w.vector_search_indexes.sync_index(INDEX_NAME)
    wait_index(w, INDEX_NAME, target)
else:
    print("AI Search step skipped (create_ai_search = no). The chunks table is ready; run again with 'yes' once ITS has approved the endpoint.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9 · Test query (same call the app makes)

# COMMAND ----------

if W["create_ai_search"] == "yes" or W["run_mode"] == "sync_only":
    q = "How are major public investment projects appraised before they enter the budget?"
    res = w.vector_search_indexes.query_index(
        index_name=INDEX_NAME, query_text=q, query_type="HYBRID", num_results=8,
        columns=["chunk_id", "country", "name_eng", "lang_type", "year", "section_heading", "page", "content"],
        filters_json=json.dumps({"record_status NOT": "archived"}))
    cols = [c.name for c in res.manifest.columns]
    out = pd.DataFrame(res.result.data_array or [], columns=cols)
    if "content" in out:
        out["content"] = out["content"].str.slice(0, 160)
    display(out)
    one_country = docs_pdf["country"].dropna().mode()
    if len(one_country):
        c = one_country.iloc[0]
        res = w.vector_search_indexes.query_index(index_name=INDEX_NAME, query_text=q, query_type="HYBRID", num_results=3,
                                                  columns=["chunk_id", "country", "name_eng"],
                                                  filters_json=json.dumps({"country": c, "record_status NOT": "archived"}))
        got = {r[1] for r in (res.result.data_array or [])}
        print(f"country filter {c}: {got}")
        assert got <= {c}

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10 · Grants for the Dash app's service principal
# MAGIC The app (Posit Connect, OAuth M2M) reads + writes the chunk tables and queries the index. If the schema-level grant from
# MAGIC `sql/uc_tables.sql` (`USE SCHEMA, SELECT, MODIFY, CREATE TABLE ON SCHEMA`) is in place, nothing more is needed for the tables
# MAGIC or for querying. Endpoint permission (*Can use*) is set on the endpoint page: **Compute → AI Search → endpoint → Permissions**.

# COMMAND ----------

sp = W["app_principal"]
grants = [
    f"GRANT USE CATALOG ON CATALOG `{CATALOG}` TO `{sp or '<app-sp-application-id>'}`",
    f"GRANT USE SCHEMA ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `{sp or '<app-sp-application-id>'}`",
    f"GRANT SELECT, MODIFY ON TABLE {CHUNKS_TABLE} TO `{sp or '<app-sp-application-id>'}`",
    f"GRANT SELECT, MODIFY ON TABLE {LOG_TABLE} TO `{sp or '<app-sp-application-id>'}`",
    f"GRANT SELECT ON TABLE {INDEX_NAME} TO `{sp or '<app-sp-application-id>'}`",
]
for g in grants:
    if sp and not DRY_RUN:
        try:
            spark.sql(g)
            print("OK  ", g)
        except Exception as exc:  # noqa: BLE001
            print("FAIL", g, "→", str(exc)[:200])
    else:
        print(g + ";")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11 · Dash app settings (Posit Connect → Content → Vars)
# MAGIC ```
# MAGIC PIM_SEARCH_BACKEND=ai_search                 # bm25 = keyword search over the same table (no endpoint needed)
# MAGIC PIM_AI_SEARCH_ENDPOINT=pim_policy_profiles_ai_search
# MAGIC PIM_AI_SEARCH_INDEX=prd_mega.sgpbpi163.policy_repo_document_chunks_index
# MAGIC PIM_AI_SEARCH_QUERY_TYPE=HYBRID
# MAGIC PIM_AI_SEARCH_RERANK=0                       # 1 = Databricks reranker (better ordering, more latency)
# MAGIC PIM_AI_SEARCH_SYNC=1                         # app triggers a sync after uploads (0 if the SP may not sync)
# MAGIC ```
# MAGIC **Ongoing:** new uploads are chunked by the app into the same table and synced automatically. Re-run this notebook only after a bulk copy of files into the
# MAGIC volume, or after a chunker change (`run_mode = incremental` picks up only what changed). To keep the index fresh without app-triggered syncs,
# MAGIC schedule this notebook as a Job with `run_mode = sync_only`.

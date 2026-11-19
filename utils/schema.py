"""
Single source of truth for the Unity Catalog table definitions.

`ddl(logical_name, dialect)` renders CREATE TABLE statements for
  * "databricks" – Delta tables in prd_mega.sgpbpi163  (production)
  * "sqlite"     – local demo database                 (UI preview only)

`python scripts/create_tables.py` executes them; `sql/uc_tables.sql` is the same
DDL as a plain file you can paste into the Databricks SQL editor.
"""

from __future__ import annotations

import constants as C

# (column, databricks_type)   – NOT NULL is enforced by Delta, PK/FK are informational
SCHEMA: dict[str, dict] = {
    "countries": {
        "comment": "ISO3-keyed country lookup with 2024 GDP / public-investment estimates (IMF WEO Oct 2024).",
        "cols": [
            ("iso3", "STRING NOT NULL"),
            ("name", "STRING NOT NULL"),
            ("gdp_2024_bn", "DECIMAL(12,3)"),
            ("pub_inv_pct_gdp", "DECIMAL(6,4)"),
            ("pub_inv_2024_bn", "DECIMAL(12,3)"),
        ],
        "pk": ["iso3"],
    },
    "policy_records": {
        "comment": "One row per curated PIM policy / strategy document (metadata only).",
        "cols": [
            ("id", "STRING NOT NULL"),
            ("country", "STRING NOT NULL"),
            ("name_eng", "STRING NOT NULL"),
            ("name_orig", "STRING"),
            ("year", "SMALLINT"),
            ("source", "STRING"),
            ("year_revised", "SMALLINT"),
            ("overview", "STRING"),
            ("policy_guidance_tier", "SMALLINT"),
            ("strategy_tier", "SMALLINT"),
            ("jurisdiction_level", "STRING NOT NULL"),   # national | subnational | supranational
            ("jurisdiction_name", "STRING"),
            ("record_status", "STRING NOT NULL"),        # latest | archived
            ("superseded_by_link", "STRING"),
            ("superseded_by_note", "STRING"),
            ("machine_readable", "BOOLEAN"),
            ("comment", "STRING"),
            ("link", "STRING"),
            ("pages", "SMALLINT"),
            ("tokens", "INT"),
            ("link_ok", "SMALLINT"),                     # NULL unchecked, 0 broken, 1 working
            ("link_checked_at", "TIMESTAMP"),
            ("link_last_ok_at", "TIMESTAMP"),
            ("created_at", "TIMESTAMP NOT NULL"),
            ("updated_at", "TIMESTAMP NOT NULL"),
        ],
        "pk": ["id"],
        "fks": [("country", "countries", "iso3")],
    },
    "documents": {
        "comment": "Attached files (max 1 ENG + 1 ORI per record). The file lives in the volume repository (path via its _manifest.json or blob_url).",
        "cols": [
            ("id", "STRING NOT NULL"),
            ("record_id", "STRING NOT NULL"),
            ("lang_type", "STRING NOT NULL"),            # ENG | ORI
            ("lang_code", "STRING"),
            ("lang_label", "STRING"),
            ("translation_source", "STRING"),
            ("text_layer", "BOOLEAN"),
            ("blob_url", "STRING"),                      # volume path the upload was written to – informational
            ("file_name", "STRING"),
            ("file_size", "INT"),
            ("uploaded_at", "TIMESTAMP NOT NULL"),
        ],
        "pk": ["id"],
        "fks": [("record_id", "policy_records", "id")],
    },
    "users": {
        "comment": "Local accounts (email + bcrypt hash). Posit Connect identities are used when present.",
        "cols": [
            ("id", "STRING NOT NULL"),
            ("name", "STRING"),
            ("email", "STRING"),
            ("email_verified", "TIMESTAMP"),
            ("image", "STRING"),
            ("password_hash", "STRING"),
        ],
        "pk": ["id"],
    },
    "audit_logs": {
        "comment": "Create / update / delete audit trail.",
        "cols": [
            ("id", "STRING NOT NULL"),
            ("user_id", "STRING"),
            ("user_email", "STRING"),
            ("action", "STRING NOT NULL"),
            ("entity", "STRING NOT NULL"),
            ("entity_id", "STRING"),
            ("detail", "STRING"),
            ("ip", "STRING"),
            ("created_at", "TIMESTAMP NOT NULL"),
        ],
        "pk": ["id"],
    },
    "partner_api_keys": {
        "comment": "X-API-Key credentials for the plug-in /api/v1 endpoints (sha256 hashes only).",
        "cols": [
            ("id", "STRING NOT NULL"),
            ("key_hash", "STRING NOT NULL"),
            ("key_prefix", "STRING NOT NULL"),
            ("name", "STRING NOT NULL"),
            ("contact_email", "STRING"),
            ("rate_limit_per_minute", "INT NOT NULL"),
            ("active", "SMALLINT NOT NULL"),
            ("created_at", "TIMESTAMP NOT NULL"),
            ("last_used_at", "TIMESTAMP"),
            ("revoked_at", "TIMESTAMP"),
            ("notes", "STRING"),
        ],
        "pk": ["id"],
    },
    "batch_imports": {
        "comment": "Spreadsheet batch uploads (one row per upload).",
        "cols": [
            ("id", "STRING NOT NULL"),
            ("file_name", "STRING"),
            ("status", "STRING NOT NULL"),
            ("total_rows", "INT NOT NULL"),
            ("valid_rows", "INT NOT NULL"),
            ("created_rows", "INT NOT NULL"),
            ("failed_rows", "INT NOT NULL"),
            ("auto_translate", "SMALLINT NOT NULL"),
            ("conflict_policy", "STRING NOT NULL"),
            ("created_by_email", "STRING"),
            ("created_at", "TIMESTAMP NOT NULL"),
            ("updated_at", "TIMESTAMP NOT NULL"),
        ],
        "pk": ["id"],
    },
    "batch_import_rows": {
        "comment": "Per-row progress of a batch upload. raw / errors hold JSON text (was Postgres jsonb).",
        "cols": [
            ("id", "STRING NOT NULL"),
            ("batch_id", "STRING NOT NULL"),
            ("row_number", "INT NOT NULL"),
            ("raw", "STRING NOT NULL"),
            ("status", "STRING NOT NULL"),
            ("action", "STRING"),
            ("record_id", "STRING"),
            ("errors", "STRING"),
            ("note", "STRING"),
            ("created_at", "TIMESTAMP NOT NULL"),
            ("updated_at", "TIMESTAMP NOT NULL"),
        ],
        "pk": ["id"],
        "fks": [("batch_id", "batch_imports", "id")],
    },
    "briefings_cache": {
        "comment": "Latest generated briefing per (brief_key, country, lang_type, scope).",
        "cols": [
            ("id", "STRING NOT NULL"),
            ("brief_key", "STRING NOT NULL"),
            ("country", "STRING NOT NULL"),
            ("lang_type", "STRING NOT NULL"),
            ("scope", "STRING NOT NULL"),
            ("content", "STRING NOT NULL"),
            ("generated_at", "TIMESTAMP NOT NULL"),
        ],
        "pk": ["id"],
    },
    "imf_pima_assessments": {
        "comment": "IMF PIMA / C-PIMA assessments (portal + curated references).",
        "cols": [
            ("id", "STRING NOT NULL"),
            ("iso3", "STRING NOT NULL"),
            ("assessment_type", "STRING NOT NULL"),
            ("title", "STRING NOT NULL"),
            ("status", "STRING NOT NULL"),
            ("mission_date", "DATE NOT NULL"),
            ("year", "SMALLINT NOT NULL"),
            ("published_at", "DATE"),
            ("report_url", "STRING"),
            ("summary_url", "STRING"),
            ("source_url", "STRING NOT NULL"),
            ("imf_country_name", "STRING"),
            ("source", "STRING NOT NULL"),
            ("reference_url", "STRING"),
            ("note", "STRING"),
            ("fetched_at", "TIMESTAMP NOT NULL"),
            ("created_at", "TIMESTAMP NOT NULL"),
            ("updated_at", "TIMESTAMP NOT NULL"),
        ],
        "pk": ["id"],
        "fks": [("iso3", "countries", "iso3")],
    },
    # ── search index ────────────────────────────────────────────────────────
    # Source table of the Databricks AI Search Delta Sync index.  Written by utils/indexer.py at upload time
    # with the chunker in utils/chunking.py.  Column types are those AI Search can sync and filter on (INT, not SMALLINT).
    # Change Data Feed must stay on: the index syncs from it.
    "document_chunks": {
        "comment": "Search-index chunks of the policy documents (one row per chunk). Source of the AI Search index "
                   "<table>_index; record fields are copied in so the index can filter on them.",
        "cols": [
            ("chunk_id", "STRING NOT NULL"),             # <document_id>:<chunk_index>  (index primary key)
            ("document_id", "STRING NOT NULL"),
            ("record_id", "STRING NOT NULL"),
            ("lang_type", "STRING NOT NULL"),            # ENG | ORI
            ("lang_code", "STRING"),
            ("chunk_index", "INT NOT NULL"),
            ("page", "INT"),                             # first page of the chunk (PDF); NULL for DOCX / TXT
            ("page_end", "INT"),
            ("section_heading", "STRING"),
            ("content", "STRING NOT NULL"),              # passage shown to the LLM / in citations
            ("embedding_text", "STRING NOT NULL"),       # context header + content – the column AI Search embeds
            ("char_count", "INT"),
            ("country", "STRING"),
            ("country_name", "STRING"),
            ("name_eng", "STRING"),
            ("name_orig", "STRING"),
            ("year", "INT"),
            ("source", "STRING"),
            ("policy_guidance_tier", "INT"),
            ("strategy_tier", "INT"),
            ("jurisdiction_level", "STRING"),
            ("jurisdiction_name", "STRING"),
            ("record_status", "STRING NOT NULL"),        # latest | archived  (archived is filtered out by default)
            ("file_name", "STRING"),
            ("file_path", "STRING"),                     # path relative to the document repository
            ("chunker_version", "STRING NOT NULL"),
            ("row_hash", "STRING NOT NULL"),             # MERGE rewrites a row (→ re-embedding) only when it changes
            ("created_at", "TIMESTAMP NOT NULL"),
            ("updated_at", "TIMESTAMP NOT NULL"),
        ],
        "pk": ["chunk_id"],
        "tblproperties": {"delta.enableChangeDataFeed": "true"},
    },
    "document_ingest_log": {
        "comment": "Last chunking outcome per document (indexed / no_text_layer / empty / missing_file / unsupported / "
                   "failed) – written by the app.",
        "cols": [
            ("document_id", "STRING NOT NULL"),
            ("record_id", "STRING"),
            ("lang_type", "STRING"),
            ("file_path", "STRING"),
            ("file_size", "BIGINT"),
            ("status", "STRING NOT NULL"),
            ("detail", "STRING"),
            ("chunk_count", "INT"),
            ("chunker_version", "STRING"),
            ("source", "STRING"),                        # app_upload
            ("processed_at", "TIMESTAMP NOT NULL"),
        ],
        "pk": ["document_id"],
    },
}

_SQLITE_TYPES = {"STRING": "TEXT", "DECIMAL(12,3)": "NUMERIC", "DECIMAL(6,4)": "NUMERIC", "TIMESTAMP": "TEXT", "DATE": "TEXT",
                 "BIGINT": "INTEGER"}


def _qualified(logical: str, dialect: str) -> str:
    phys = C.TABLES[logical]
    if dialect == "sqlite":
        return phys
    return f"{C.DATABRICKS_CATALOG}.{C.DATABRICKS_SCHEMA}.{phys}"


def ddl(logical: str, dialect: str = "databricks") -> str:
    spec = SCHEMA[logical]
    lines = []
    for col, typ in spec["cols"]:
        if dialect == "sqlite":
            head, _, rest = typ.partition(" ")
            head = _SQLITE_TYPES.get(head, head)
            typ = f"{head} {rest}".strip()
        lines.append(f"  {col} {typ}")
    pk = ", ".join(spec["pk"])
    lines.append(f"  CONSTRAINT pk_{C.TABLES[logical]} PRIMARY KEY ({pk})")
    for col, ref_t, ref_c in spec.get("fks", []):
        lines.append(
            f"  CONSTRAINT fk_{C.TABLES[logical]}_{col} FOREIGN KEY ({col}) "
            f"REFERENCES {_qualified(ref_t, dialect)}({ref_c})"
        )
    body = ",\n".join(lines)
    if dialect == "sqlite":
        return f"CREATE TABLE IF NOT EXISTS {_qualified(logical, dialect)} (\n{body}\n)"
    comment = spec["comment"].replace("'", "''")
    props = spec.get("tblproperties")
    tblprops = ("\nTBLPROPERTIES (" + ", ".join(f"'{k}' = '{v}'" for k, v in props.items()) + ")") if props else ""
    return (
        f"CREATE TABLE IF NOT EXISTS {_qualified(logical, dialect)} (\n{body}\n)\n"
        f"USING DELTA\nCOMMENT '{comment}'{tblprops}"
    )


CREATE_ORDER = [
    "countries", "policy_records", "documents", "users", "audit_logs", "partner_api_keys",
    "batch_imports", "batch_import_rows", "briefings_cache", "imf_pima_assessments",
    "document_chunks", "document_ingest_log",
]


def all_ddl(dialect: str = "databricks") -> list[str]:
    return [ddl(t, dialect) for t in CREATE_ORDER]

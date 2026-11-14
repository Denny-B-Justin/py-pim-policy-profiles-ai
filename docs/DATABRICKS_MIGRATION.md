# Moving the data to Azure Databricks Unity Catalog

Target: catalog **`prd_mega`**, schema **`sgpbpi163`**.
The Next.js app kept everything in one Neon PostgreSQL database plus Vercel Blob (files). This app keeps the same data in Delta tables named `pim_*` and the files in a Unity Catalog **Volume** named `pim_documents`.

> The Databricks code path (`utils/db.py`, `utils/storage.py`, `scripts/*`) has been exercised end to end against the local SQLite demo backend, but not against your workspace. Run the steps below on a copy of the data first and compare the counts printed by `migrate_from_postgres.py`.

---

## 1. Is any NoSQL / non-relational store in use?

There is **no dedicated NoSQL database** (no MongoDB, Redis, DynamoDB, Firestore, Cosmos DB). The original uses PostgreSQL (Neon) throughout. It does, however, use four non-relational *patterns* that need an explicit equivalent in Unity Catalog:

| Original (Next.js / FastAPI app) | Where | In this Dash app | What you do |
|---|---|---|---|
| **`jsonb` columns** – `batch_import_rows.raw`, `batch_import_rows.errors` (and `cmetadata` on the vector table) | Drizzle schema, LangChain PGVector | `STRING` columns holding JSON text (`json.dumps`). `cmetadata` is flattened into real columns of `pim_document_chunks` (`record_id`, `country`, `lang_type`, `year`, …) | Nothing – `migrate_from_postgres.py` serialises them. Query with `from_json(raw, 'map<string,string>')` or `raw:Country` (Databricks JSON path syntax). |
| **pgvector embeddings** – `langchain_pg_collection`, `langchain_pg_embedding` (`vector` column + `cmetadata jsonb`), `chunk_parents` (`jsonb metadata`) | Python RAG service | **`pim_document_chunks`** – plain text chunks, retrieved with BM25 (SQLite FTS5 in memory). The mAI gateway exposes chat models only – no embedding endpoint – so vectors can't be produced. | Not copied. Run `python scripts/reindex.py --lang all` after migrating; it rebuilds the chunks from the stored documents. |
| **Object storage** – Vercel Blob (`documents.blob_url`) | Document upload / download / RAG ingest | UC **Volume** `/Volumes/prd_mega/sgpbpi163/pim_documents/<ISO3>/<document-id>-<file name>`; `pim_documents.file_path` holds the key | `migrate_from_postgres.py` downloads each blob and writes it to the volume. |
| **Auth.js tables** – `accounts`, `sessions`, `verification_tokens` | Google OAuth | Not migrated. Sign-in is Posit Connect SSO (headers) or a local email + password account stored in `pim_users`. | Users re-register once, or sign in through Connect. |

Also in the original: in-process rate limiters (no Redis) → kept as in-process limiters here, so nothing to migrate.

Pgvector → SQL, if you ever want it back: add an `embedding ARRAY<FLOAT>` column to `pim_document_chunks` and use Databricks Vector Search (or `vector_search()`), fed by an embedding model served in your workspace. The mAI gateway can't provide one today.

---

## 2. Table names (use exactly these)

| Postgres table (old) | Unity Catalog table (new) | Notes |
|---|---|---|
| `countries` | `prd_mega.sgpbpi163.pim_countries` | key `iso3`; `char(3)` → `STRING` (trimmed) |
| `policy_records` | `prd_mega.sgpbpi163.pim_policy_records` | key `id` (UUID kept as text) |
| `documents` | `prd_mega.sgpbpi163.pim_documents` | **`blob_url` → `file_path`** (path inside the volume) |
| `users` | `prd_mega.sgpbpi163.pim_users` | gains `created_at` |
| `audit_logs` | `prd_mega.sgpbpi163.pim_audit_logs` | |
| `partner_api_keys` | `prd_mega.sgpbpi163.pim_partner_api_keys` | only SHA-256 hashes are stored |
| `batch_imports` | `prd_mega.sgpbpi163.pim_batch_imports` | |
| `batch_import_rows` | `prd_mega.sgpbpi163.pim_batch_import_rows` | `raw`, `errors`: jsonb → JSON `STRING` |
| `briefings_cache` | `prd_mega.sgpbpi163.pim_briefings_cache` | |
| `imf_pima_assessments` | `prd_mega.sgpbpi163.pim_imf_pima_assessments` | |
| `langchain_pg_collection`, `langchain_pg_embedding`, `chunk_parents` | `prd_mega.sgpbpi163.pim_document_chunks` | **new shape, re-indexed – not copied** |
| `accounts`, `sessions`, `verification_tokens` | – | not migrated |
| Vercel Blob | volume `prd_mega.sgpbpi163.pim_documents` | |

The prefix and the schema are set in `utils/constants.py` (`TABLES`, `DATABRICKS_CATALOG`, `DATABRICKS_SCHEMA`). Credentials are **not** in the code – only the variable names; values come from Posit Connect environment variables (or `.env` locally).

### Type mapping

| PostgreSQL | Delta |
|---|---|
| `uuid`, `text`, `varchar`, `char(n)` | `STRING` |
| `smallint` / `integer` | `SMALLINT` / `INT` |
| `numeric(p,s)` | `DECIMAL(p,s)` |
| `boolean` | `BOOLEAN` |
| `timestamp` | `TIMESTAMP` (stored as naive UTC) |
| `date` | `DATE` |
| `jsonb` | `STRING` (JSON text) |
| `vector(n)` | – (see above) |

The full DDL is in [`sql/uc_tables.sql`](../sql/uc_tables.sql) (generated from `utils/schema.py`).

---

## 3. One-time setup in Databricks

1. **Service principal.** Use the OAuth M2M service principal whose *client id* and *secret* you already have. Put them in the Connect content's environment variables as `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET`, together with `DATABRICKS_SERVER_HOSTNAME` and `DATABRICKS_HTTP_PATH` (SQL warehouse → *Connection details*).
2. **Grants** (run as a catalog/schema owner):
   ```sql
   GRANT USE CATALOG ON CATALOG prd_mega TO `<client-id>`;
   GRANT USE SCHEMA, SELECT, MODIFY, CREATE TABLE, CREATE VOLUME ON SCHEMA prd_mega.sgpbpi163 TO `<client-id>`;
   -- after the volume exists:
   GRANT READ VOLUME, WRITE VOLUME ON VOLUME prd_mega.sgpbpi163.pim_documents TO `<client-id>`;
   ```
   The service principal also needs **CAN USE** on the SQL warehouse.
3. **Create tables and volume** – either paste `sql/uc_tables.sql` into the SQL editor, or run
   ```bash
   python scripts/create_tables.py
   ```
   Re-running is safe (`CREATE … IF NOT EXISTS`).
4. **Load the country reference list** (needed on a brand-new setup – the app reads country names from `pim_countries`):
   ```bash
   python scripts/seed_countries.py        # 196 countries from data/countries.json; safe to re-run
   ```
   Skip this if you copy the data from Postgres in section 4 below – the `countries` table comes across with it.

---

## 4. Copy the data (only when you have existing data in the old Postgres database)

```bash
pip install -r requirements.txt
pip install "psycopg[binary]"                       # only for the migration
export PIM_SOURCE_DATABASE_URL="postgresql://USER:PASS@HOST/DB?sslmode=require"   # the Neon connection string
export DATABRICKS_SERVER_HOSTNAME=… DATABRICKS_HTTP_PATH=… DATABRICKS_CLIENT_ID=… DATABRICKS_CLIENT_SECRET=…

python scripts/migrate_from_postgres.py --dry-run   # row counts in the source
python scripts/migrate_from_postgres.py             # copy
python scripts/reindex.py --lang all                # rebuild the search index
```

What the script does:

* copies the 10 relational tables in dependency order (countries → records → documents → users → …), 50 rows per `INSERT`;
* **idempotent** – rows whose key already exists are skipped, so an interrupted run can simply be repeated (`--tables documents` re-runs one table);
* keeps all UUIDs (as strings), so links such as `/?page=record&id=<uuid>` keep working;
* for every `documents` row it downloads `blob_url`, writes the file to the volume and stores the new path in `file_path` (`--skip-files` copies metadata only);
* turns `jsonb` into JSON text, trims `char(3)` country codes, fills `users.created_at`;
* prints `source / copied / present / failed / target` per table and exits non-zero when counts differ.

Credentials come from the environment only. Use a read-only Postgres role for the source.

### Alternative without Python (metadata tables only)

Export each table with `psql`, upload the CSVs to `/Volumes/prd_mega/sgpbpi163/pim_documents/_import/`, then load them with `COPY INTO`:

```sql
COPY INTO prd_mega.sgpbpi163.pim_policy_records
FROM '/Volumes/prd_mega/sgpbpi163/pim_documents/_import/policy_records.csv'
FILEFORMAT = CSV
FORMAT_OPTIONS ('header' = 'true', 'multiLine' = 'true', 'escape' = '"');
```

Export with explicit casts so Spark can read the values: `SELECT …, machine_readable::text, raw::text …` (`t`/`f` booleans are not read as BOOLEAN). `documents` needs the file copy and the new `file_path`, so use the script for that table (`--tables documents`).

---

## 5. Check the result

```sql
SELECT 'countries' t, COUNT(*) n FROM prd_mega.sgpbpi163.pim_countries
UNION ALL SELECT 'policy_records', COUNT(*) FROM prd_mega.sgpbpi163.pim_policy_records
UNION ALL SELECT 'documents',      COUNT(*) FROM prd_mega.sgpbpi163.pim_documents
UNION ALL SELECT 'document_chunks',COUNT(*) FROM prd_mega.sgpbpi163.pim_document_chunks;

-- every document must have a file in the volume
LIST '/Volumes/prd_mega/sgpbpi163/pim_documents/';

-- records without chunks after re-indexing (expected: only files without a text layer)
SELECT r.id, r.name_eng FROM prd_mega.sgpbpi163.pim_policy_records r
LEFT JOIN (SELECT DISTINCT record_id FROM prd_mega.sgpbpi163.pim_document_chunks) c ON c.record_id = r.id
WHERE c.record_id IS NULL;
```

Then open the app: `/?page=statistics` shows the index size, `/?page=repository` the records.

---

## 6. Behavioural differences to know about

* **No enforced constraints.** Delta primary / foreign keys are informational. Uniqueness (user e-mail, API-key hash, one ENG + one ORI document per record) is checked by the app before writing (select-then-insert).
* **No row locks.** The original used `FOR UPDATE SKIP LOCKED` to claim batch rows; here a batch runs in one background job guarded by an in-process lock. Run Connect with the default single process for this content, or accept that two simultaneous runs of the *same* batch aren't prevented.
* **Latency.** A serverless / auto-stopping SQL warehouse needs a cold start on the first query. The app caches read-heavy lookups for `PIM_CACHE_SECONDS` (default 60 s).
* **Housekeeping.** Run `OPTIMIZE prd_mega.sgpbpi163.pim_document_chunks` occasionally after large re-indexes.
* **Time zones.** Timestamps are stored as naive UTC (the source columns were `timestamp without time zone`).

---

## 7. Cut-over checklist

1. Freeze edits in the old app (or accept a short read-only window).
2. Run steps 3 → 4 above.
3. Compare the printed counts; spot-check 3–4 records and open a PDF from each language.
4. Set the Connect environment variables from `.env.sample`, deploy, open `/?page=home`.
5. Create the first account (there are no roles: any signed-in user can edit) or sign in through Connect, then visit `/?page=raging` and check the index.
6. Decommission the Neon database and the Blob store once you are happy.

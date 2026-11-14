# Architecture — PIM Policy Profiles AI (Dash on Posit Connect)

> **In plain words.** This application is a searchable library of public-investment-management (PIM) policy documents — laws, regulations, guidelines and strategies — from many countries. Staff add a *policy record* (its name, year, country, type of instrument, a link to the official source) and attach the document itself, in its original language and, where available, in English. The application reads each document, cuts it into short passages and keeps an index of them, a bit like the index at the back of a book. When someone asks a question in the **AI Coach**, or asks for a **country briefing**, the application looks up the passages that best match, hands them together with the question to an AI service (Posit's *mAI* service) and shows the answer with the sources it used. Everything that matters — the records, the documents, the index, the saved briefings, the user accounts — is kept in your organisation's Databricks workspace: a set of tables for the information and a folder (a *volume*) for the files. The web application itself keeps nothing that cannot be rebuilt, so it can be redeployed or restarted at any time without losing data.
>
> **Technically.** A single Python 3.11 **Dash/Flask** application runs on Posit Connect (entrypoint `app:app`); every page is a module in `pages/` selected by the `?page=` query parameter, and all JSON/file endpoints are Flask routes on the same server. All structured data lives in **Delta tables `prd_mega.sgpbpi163.pim_*`** (Unity Catalog), read and written through the `databricks-sql-connector` with an OAuth machine-to-machine service principal (client id/secret from environment variables, one shared connection per process). Uploaded PDF/DOCX/TXT files live in the **UC Volume `pim_documents`** (written with the Databricks SDK); `pim_documents.file_path` points at them. When a document is attached, replaced or deleted, the app saves/removes the file, upserts the `pim_documents` row and — in a background thread — extracts the text (`pypdf`/`python-docx`), splits it into section-aware chunks of ≤ 2,000 characters and replaces that document's rows in **`pim_document_chunks`**. For retrieval each process loads the chunk table into an in-memory SQLite **FTS5/BM25** index (rebuilt when the table changes, checked every ≤ 30 s) — there are no embeddings because the **mAI gateway** only offers chat models. The AI Coach, the briefings and the partner `/api/v1/coach/query` endpoint run *condense → retrieve top passages → one mAI call* (plain HTTP/SDK with `X-Posit-App-Id` = `CONNECT_CONTENT_GUID`, no API key); since the gateway cannot stream, long work runs as **background jobs** whose progress is written to small JSON files and polled by the browser every second or two; finished briefings are cached in `pim_briefings_cache`. Authentication is the Posit Connect identity header or a local e-mail/password account (`pim_users`, signed Flask cookie); every write goes through `auth.write_guard()` and is recorded in `pim_audit_logs`.

---

## Contents

1. [Big picture](#1-big-picture)
2. [Where everything is stored](#2-where-everything-is-stored)
3. [The data model](#3-the-data-model)
4. [Code map](#4-code-map)
5. [How a page request works](#5-how-a-page-request-works)
6. [Workflows, step by step](#6-workflows-step-by-step)
7. [The AI part (mAI, retrieval, prompts)](#7-the-ai-part-mai-retrieval-prompts)
8. [Users, sign-in and permissions](#8-users-sign-in-and-permissions)
9. [Background jobs, caches and concurrency](#9-background-jobs-caches-and-concurrency)
10. [HTTP API](#10-http-api)
11. [How to update data (cookbook)](#11-how-to-update-data-cookbook)
12. [How to change the application](#12-how-to-change-the-application)
13. [Operations: configuration, scheduling, backups, monitoring](#13-operations)
14. [Troubleshooting](#14-troubleshooting)
15. [Known limits and differences from the Next.js original](#15-known-limits-and-differences-from-the-nextjs-original)
16. [Glossary](#16-glossary)

---

## 1. Big picture

```
                                   ┌──────────────────────────────────────────────┐
   Browser                         │             Posit Connect (one app)          │
 ┌──────────────┐   HTTPS          │                                              │
 │  Dash UI     │◀────────────────▶│  app.py  ── Dash router  (?page=…)           │
 │  (React in   │  callbacks +     │             │                                │
 │   the page)  │  /api/* routes   │             ├── pages/*.py   one module/page │
 └──────────────┘                  │             ├── utils/*.py   logic + UI parts │
                                   │             └── Flask routes (utils/api_routes.py)
                                   │                                              │
                                   │  per-process memory:                         │
                                   │   • TTL query cache      • BM25 (FTS5) index │
                                   │   • rate limiters        • job thread pool   │
                                   │  local temp files: job progress (JSON)       │
                                   └───────┬───────────────────────┬──────────────┘
                                           │                       │
                         SQL + files       │                       │  HTTPS, header X-Posit-App-Id
                    (service principal)    │                       │  (no API key)
                                           ▼                       ▼
                     ┌─────────────────────────────────┐   ┌──────────────────────┐
                     │ Azure Databricks · Unity Catalog│   │  Posit mAI Gateway   │
                     │  catalog prd_mega               │   │  (Claude / OpenAI /  │
                     │   schema sgpbpi163              │   │   Gemini endpoint)   │
                     │   ├─ Delta tables  pim_*        │   └──────────────────────┘
                     │   └─ Volume  pim_documents      │
                     │        (the PDF/DOCX/TXT files) │   ┌──────────────────────┐
                     └─────────────────────────────────┘   │ imf.org (scraped for │
                                                           │ the PIMA overview)   │
                                                           └──────────────────────┘
```

Design rules that explain most of the code:

* **One source of truth: Databricks.** The app process is disposable. Anything in memory or in the temp folder can be recreated from the tables and the volume.
* **Pages are query-string routes.** `/?page=record&id=<uuid>` rather than `/record/<uuid>`; the home page is `/` or `/?page=home`.
* **Reads are plain SQL, cached for a minute.** Writes are Dash callbacks guarded by sign-in and a rate limit, and each write also refreshes whatever depends on it (search index, link status, caches).
* **No streaming, no embeddings.** The mAI gateway offers neither, so long AI work is a background job that the page polls, and retrieval is keyword (BM25) search.
* **No secrets in code.** Databricks credentials and mAI settings come from environment variables; the app never sends an `Authorization` header to mAI.

---

## 2. Where everything is stored

| What | Where | Written by | Read by | Survives a restart / redeploy? |
|---|---|---|---|---|
| Policy records (name, year, tier, status, link, …) | Delta table `pim_policy_records` | record form, batch import, link checker, migration | almost every page, API | **Yes** |
| Document *metadata* (language, file name, size, translation source, `text_layer`, path) | `pim_documents` | upload / replace / delete, translation, batch import | record pages, downloads, indexer | **Yes** |
| The **document files** (PDF, DOCX, TXT; up to 250 MB each) | **UC Volume** `/Volumes/prd_mega/sgpbpi163/pim_documents/<ISO3>/<document-id>-<file name>` | `utils/storage.py` | download routes, indexer, translator | **Yes** |
| **Search index** (text passages with their metadata) | `pim_document_chunks` | `utils/indexer.py` | `utils/rag.py` | **Yes** (the table) — the in-memory BM25 copy is rebuilt on start |
| Saved AI briefings (profile, transparency; per country/language/scope) | `pim_briefings_cache` | briefing generation | briefing panels, `/api/briefings` | **Yes** |
| Countries reference (name, GDP, public investment 2024) | `pim_countries` | `scripts/seed_countries.py` / migration | everywhere | **Yes** |
| IMF PIMA / C-PIMA assessments | `pim_imf_pima_assessments` | `utils/imf_pima.py` (refresh) | Countries page, Excel export, `/api/imf-pima` | **Yes** |
| Local user accounts (e-mail, bcrypt hash) | `pim_users` | register page | sign-in | **Yes** |
| Partner API keys (SHA-256 hash only) | `pim_partner_api_keys` | `scripts/issue_api_key.py` | `/api/v1/*` | **Yes** |
| Audit trail (who changed what) | `pim_audit_logs` | every write | — (query it in SQL) | **Yes** |
| Batch-upload jobs and their rows | `pim_batch_imports`, `pim_batch_import_rows` | batch upload | batch upload page | **Yes** |
| Taxonomy, labels, languages, limits, prompts | Python source: `utils/constants.py`, `utils/prompts.py` | developers | everywhere | In the code (redeploy to change) |
| Static reference files | `data/countries.json`, `data/imf-pima-references.json`, `data/openapi.json`, `data/release_notes.json`; `assets/` (CSS, JS, map polygons, templates, fonts) | developers | app | In the code |
| TTL query cache, BM25 index, rate-limit counters, job thread pool | **process memory** | — | — | **No** — rebuilt on demand |
| Job progress files | temp folder (`PIM_JOBS_DIR`, default system temp) | `utils/jobs.py` | polling pages | No — deleted after 6 h anyway |
| Session cookie of a local sign-in | browser cookie `pim_session` (signed with `PIM_SECRET_KEY`) | sign-in | every request | Until it expires (30 days) |
| Secrets and endpoints | Connect environment variables (or `.env` locally) | administrator | `utils/constants.py` | Stored by Connect |

There is **no other database**: no NoSQL store, no Redis, no separate vector database. (The Next.js original used Neon PostgreSQL, pgvector and Vercel Blob — see `docs/DATABRICKS_MIGRATION.md`.)

### Layout of the volume

```
/Volumes/prd_mega/sgpbpi163/pim_documents/
   LTU/
      8c1e…-Law_on_the_Budget_Structure_ENG.pdf
      f6a1…-Law_on_the_Budget_Structure_ORI.pdf
   EST/
      …
```

`documents.file_path` stores the part after the volume root (`LTU/8c1e…-….pdf`). A record has at most **one `ENG` and one `ORI` document**; attaching a new file to an occupied slot replaces the old file (the old file is deleted).

---

## 3. The data model

All tables carry the prefix `pim_` and live in `prd_mega.sgpbpi163`. The DDL is in `sql/uc_tables.sql` (generated from `utils/schema.py`). Ids are UUID strings. Delta primary/foreign keys are informational only — the app enforces uniqueness itself.

```
 pim_countries ──┐ iso3
                 ├──< pim_policy_records >──┬──< pim_documents  (ENG / ORI, one file each)
                 │       (id)               └──< pim_document_chunks  (search passages)
                 │                                   ▲ record_id, document_id
 pim_imf_pima_assessments (iso3)        pim_briefings_cache (country, lang, scope)
 pim_batch_imports ──< pim_batch_import_rows ──> record_id
 pim_users · pim_partner_api_keys · pim_audit_logs
```

| Table | Key columns and meaning |
|---|---|
| `pim_countries` | `iso3` (key), `name`, `gdp_2024_bn`, `pub_inv_pct_gdp`, `pub_inv_2024_bn` — 196 rows from `data/countries.json` |
| `pim_policy_records` | `id`, `country` (ISO-3), `name_eng`, `name_orig`, `year`, `year_revised`, `source`, `overview`, `policy_guidance_tier` (1–4), `strategy_tier` (1–6, only when tier = 4), `jurisdiction_level` (`national` / `subnational` / `supranational`), `jurisdiction_name`, `record_status` (`latest` / `archived`), `superseded_by_link/_note`, `machine_readable`, `comment`, `link` + `link_ok` / `link_checked_at` / `link_last_ok_at`, `pages`, `tokens`, `created_at`, `updated_at` |
| `pim_documents` | `id`, `record_id`, `lang_type` (`ENG` / `ORI`), `lang_code`, `lang_label`, `translation_source` (`official`, `mai`, `deepl`, `google`, `wbg`, `other`), `text_layer` (false = scanned image, cannot be indexed), `file_path`, `file_name`, `file_size`, `uploaded_at` |
| `pim_document_chunks` | one row per passage: `record_id`, `document_id`, `country`, `country_name`, `name_eng`, `name_orig`, `year`, `source`, `policy_guidance_tier`, `strategy_tier`, `lang_type`, `lang_code`, `record_status`, `section_heading`, `page`, `chunk_index`, `content`, `created_at` — descriptive fields are **copied from the record** so a search never needs a join |
| `pim_briefings_cache` | `brief_key` (`profile` / `transparency`), `country`, `lang_type`, `scope` (`all` / `national` / `sub:<entity>`), `content` (Markdown), `generated_at` |
| `pim_imf_pima_assessments` | per country: assessment type (PIMA / C-PIMA), title, status, mission date, report/summary URLs, `source` (`portal`, `imf.org`, curated reference), `fetched_at` |
| `pim_users` | `email`, `name`, `password_hash` (bcrypt), `created_at` |
| `pim_partner_api_keys` | `key_hash` (SHA-256), `key_prefix`, `name`, `contact_email`, `rate_limit_per_minute`, `active`, `last_used_at`, `revoked_at` |
| `pim_audit_logs` | `user_email`, `action` (`create`, `update`, `delete`, `upload`, `reindex`), `entity` (`record`, `document`, `batch`, `rag_index`), `entity_id`, `detail`, `ip`, `created_at` |
| `pim_batch_imports` / `pim_batch_import_rows` | one row per upload and per spreadsheet row; `raw` (the original row) and `errors` hold JSON text |

Two fields worth knowing:

* **`record_status = 'archived'`** hides a record from "current" views, the AI Coach (unless the question explicitly filters for archived) and the briefings.
* **`tokens`** is an optional number you may fill yourself; the app never computes it. The Statistics page sums it for "Total Tokens".

---

## 4. Code map

```
app.py                      Creates the Dash app, the router callback, registers every page's callbacks and the Flask API routes.
manifest.json               Posit Connect bundle manifest (rsconnect-python). requirements.txt / .env.sample alongside.

pages/                      ONE FILE PER PAGE.  Each exposes  layout(params)  [+ TITLE or title(params)] [+ register_callbacks(app)]
  __init__.py               ROUTES table:  ?page=<name>  →  module
  home, countries, country_detail, map_page, repository, record_detail, record_new, record_edit,
  batch_upload, coach, country_profile, country_transparency, policy_changes, benchmark, statistics,
  raging (index admin), download, api_docs, about, release_notes, signin, register, not_found, error_page

utils/                      Everything the pages share
  constants.py              Settings (from env), table names, taxonomy (tiers, languages…), limits, mAI model lists
  db.py  schema.py          SQL access (Databricks or SQLite), TTL cache, DDL for every table
  queries.py                All SQL for records, documents, countries, stats, briefings cache
  storage.py                Files: UC Volume (production) / local folder (demo) — save, read, stream, delete
  documents.py              Attach / replace / delete a record's documents + "translate to English" job
  extract.py chunking.py    Text extraction (PDF/DOCX/TXT) and section-aware chunking
  indexer.py                Keeps pim_document_chunks in step with documents (index, delete, reconcile, full re-index)
  rag.py                    BM25 retrieval over the chunk table (in-memory SQLite FTS5), index statistics
  mai_client.py             The only place that talks to the mAI gateway (Claude Converse / OpenAI Responses / Gemini)
  prompts.py                System prompts for the Coach, briefings, translation, query rewriting
  chat.py  chat_ui.py       AI Coach: condense → retrieve → answer;  the chat widget
  briefings.py briefing_panel.py   Country profile / transparency briefs: generate, cache, export;  the panel
  translate.py              Document → English translation through mAI, DOCX builder
  batch.py                  Spreadsheet batch import engine (validate → preview → import)
  jobs.py                   Background job runner with file-persisted progress
  record_form.py record_schema.py  The create/edit form and its validation
  changes.py                "Policy changes" reports (pure SQL/date logic — no AI)
  imf_pima.py               Scrapes imf.org PIMA pages + curated references
  check_link.py             Link reachability checker (with SSRF protection)
  auth.py api_keys.py ratelimit.py audit.py   Sign-in, partner keys, in-process rate limits, audit log
  api_routes.py             Flask routes: downloads, JSON API, cron routes, partner API v1
  export.py xlsx_exports.py Word/PDF export of briefings; Excel exports
  ui.py components.py widgets.py layout.py icons.py routes.py toast.py   UI building blocks (Tailwind classes), navigation, helpers

assets/                     style.css (compiled Tailwind), app.js (client helpers), map.js (Leaflet map), fonts, logo, countries.geojson,
                            vendor/ (Leaflet, ReDoc — loaded on demand), templates/ (batch-upload xlsx + data dictionary docx)
data/                       countries.json, imf-pima-references.json, openapi.json, release_notes.json
scripts/                    create_tables · seed_countries · migrate_from_postgres · reindex · cron_reconcile · cron_refresh_pima ·
                            issue_api_key · seed_demo (local preview only)
sql/uc_tables.sql           DDL for the Unity Catalog tables and the volume
docs/DATABRICKS_MIGRATION.md   Table names, data migration, NoSQL/JSONB/pgvector conversion
tools/                      CSS and icon build sources (not deployed)
```

---

## 5. How a page request works

1. The browser requests `/?page=record&id=…`. Dash serves the shell (`utils/layout.py`: header, footer, `dcc.Location`, an empty content area).
2. The **router callback** in `app.py` receives the query string, looks the page up in `pages.ROUTES`, and calls that module's `layout(params)`. If the page raises an exception, an error page is shown and the traceback is logged.
3. `layout()` calls functions in `utils/queries.py`. These run SQL through `utils/db.py`. Read results are kept in a **60-second in-memory cache** (country list: 5 minutes), so repeated page views do not hit the SQL warehouse.
4. Interactions (clicking a filter, saving a form) are **Dash callbacks** registered by the page's `register_callbacks(app)`. Callbacks that change data first call `auth.write_guard()` (must be signed in, ≤ 30 writes per minute per IP), perform the change, write an audit-log row, invalidate the cache and show a toast.
5. Navigation uses ordinary links (`?page=…`); Dash swaps the content without reloading the browser.

Page ↔ URL reference:

| URL | Module | Notes |
|---|---|---|
| `/?page=home` (or `/`) | `home.py` | counts, shortcuts |
| `?page=countries`, `?page=country&iso3=LTU` | `countries.py`, `country_detail.py` | country list with IMF PIMA status; policies of one country |
| `?page=map` | `map_page.py` + `assets/map.js` | Leaflet map coloured by original language |
| `?page=repository` | `repository.py` | search / filter / sort all records; "Recheck links" |
| `?page=record&id=…`, `record-new`, `record-edit&id=…` | `record_detail.py`, `record_new.py`, `record_edit.py` | view, create, edit (documents, translation) |
| `?page=batch` | `batch_upload.py` | bulk import from XLSX/CSV |
| `?page=coach`, `country-profile`, `country-transparency` | `coach.py` … | AI Coach and the two briefings |
| `?page=policy-changes` | `policy_changes.py` | what changed in a country's profile |
| `?page=statistics` | `statistics.py` | search-index analytics (Plotly) |
| `?page=raging` | `raging.py` | RAG admin: rebuild the index (sign-in required) |
| `?page=download` | `download.py` | download one country's documents |
| `?page=api-docs`, `about`, `release-notes`, `benchmark` | | static content |
| `?page=signin`, `register` | | local accounts |

---

## 6. Workflows, step by step

### 6.1 Add a policy record (form)
1. Signed-in user opens `?page=record-new`, fills in the form (`utils/record_form.py`).
2. Validation (`utils/record_schema.py`): country (ISO-3 code) and English name are required; numbers must be numbers; tier 1–4; a strategy type (1–6) is kept only for tier 4; sub-national and supranational records need an entity name; status must be `latest` or `archived`; links must be `http(s)` URLs.
3. `queries.create_record()` inserts a row into `pim_policy_records` (`created_at = updated_at = now`).
4. If a `link` was given, a background task checks it (see 6.9) and stores `link_ok` / `link_checked_at`.
5. An audit row (`create`/`record`) is written; the user lands on the record page, where documents can be attached.

### 6.2 Edit a record
Same form with `mode=edit`. Only the **changed** fields are written (`updated_at = now`). Then:
* if `link` changed → reachability is reset and re-checked in the background;
* if any of `record_status`, `name_eng`, `name_orig`, `year`, `source`, `policy_guidance_tier`, `strategy_tier`, `country` changed → `indexer.sync_record_metadata()` copies the new values onto the record's rows in `pim_document_chunks`, so filters and citations stay right without re-reading the files.

### 6.3 Attach, replace or delete a document
1. Upload (`dcc.Upload` → base64 → bytes). Allowed: `.pdf .doc .docx .txt`, ≤ 250 MB (in practice limited by Connect's maximum request size).
2. `documents.attach_document()` builds the key `<ISO3>/<new-uuid>-<safe file name>`, **saves the file to the volume**, detects whether the file has a text layer, and **upserts the `pim_documents` row** for that record + `ENG`/`ORI` slot. A replaced file is deleted from the volume.
3. `queries.recompute_machine_readable()` updates the record's `machine_readable` flag from its documents' `text_layer`.
4. **In a background thread** `indexer.index_document()`:
   `read file` → `extract.load_pages()` (PDF pages / DOCX / TXT) → `chunking.chunk_pages()` → delete that document's old chunks → insert the new rows into `pim_document_chunks` (20 rows per statement) → `rag.invalidate()`.
   Scanned PDFs (`text_layer = false`) are skipped and flagged — they need OCR first.
5. Deleting a document removes the file, the `pim_documents` row and its chunks.

### 6.4 "Translate to English"
Available on a record that has an original-language document but no English one.
1. A job (`documents.translate_record_to_english`) reads the original file from the volume and extracts its text.
2. The text is split into pieces of ≤ 6,000 characters (`TRANSLATE_CHUNK_CHARS`); each piece is translated with one mAI call (a few in parallel, `PIM_TRANSLATE_WORKERS`); the page shows progress (done/total). The size is estimated first and refused above `PIM_MAX_TRANSLATE_CHARS` (default 600,000 characters).
3. The result is written as a **.docx** and attached as the record's `ENG` document with `translation_source = 'mai'` — which then follows 6.3 (stored, indexed).

### 6.5 Batch upload (`?page=batch`)
1. The user downloads the template from the page (`assets/templates/…xlsx`, with a data dictionary), fills the **Policies** sheet and uploads it (XLSX or CSV).
2. `batch.parse_and_store()` validates every row (country, English name, 4-digit year, tier and strategy rules, jurisdiction, status, URLs, translation-source values — the original language is required whenever an original document link is given), stores the batch in `pim_batch_imports` / `pim_batch_import_rows`, and shows a preview: valid / invalid / will-be-skipped counts.
3. The user chooses what to do when a record with the same country + English name already exists — **skip**, **update** or **create anyway** — and whether to **auto-translate** missing English documents.
4. *Import* starts a background job (`batch.run_batch`). For each valid row: download the document URL(s) (https only, public hosts only — private/internal addresses are refused, 25 MB cap), then create/update the record, attach the files (→ 6.3), optionally translate (→ 6.4). Documents are fetched **before** the record is written, so a failed download never leaves an empty record.
5. Progress is polled; at the end the user can download a results CSV. An interrupted import can be resumed — rows that are already done are not repeated.

### 6.6 AI Coach question
`?page=coach`:
1. The question starts a job (`chat.start`).
2. If there is chat history, mAI rewrites the follow-up into a standalone question (`condense_question`).
3. `rag.retrieve()` returns the **6 best passages** (BM25) for the standalone question, filtered by the user's selections on the page (country, policy tier, language, year range); archived records are excluded by default. The Coach needs no sign-in.
4. `prompts.build_chat_system()` puts those passages into a system prompt that asks for an answer grounded only in them, with numbered citations.
5. One mAI call (`max 2,048` output tokens) produces the answer; the page shows it with the sources (first 300 characters of each passage, plus metadata linking to the record).
6. If the question is about original-language documents (`lang_type = ORI`), the answer is requested in that language.

### 6.7 Country briefings (profile / transparency)
1. The user picks a country, language (English or original), and scope (all / national / one sub-national jurisdiction) and presses *Generate*.
2. The job selects the country's **current** records (those that have the chosen language), builds a table-like "policy records" context from the database, and retrieves up to **20 passages** (padded with the opening chunks of each in-scope record if the keyword match returns too few). For original-language briefings the search query is first translated into that language.
3. One long mAI call (up to 8,192 tokens; 12,288 for the profile) writes the Markdown brief.
4. The result is **saved to `pim_briefings_cache`** (one row per brief type + country + language + scope — a new run overwrites it). Anyone can generate and download a briefing, but it is only archived in the cache when the person is signed in. The panel always shows the saved version with its date, and offers Word/PDF download (`utils/export.py` builds the file on the fly with `python-docx` / `fpdf2`).
5. The cache is **not** invalidated automatically when records change; regenerate when the data has changed.

### 6.8 Policy changes (`?page=policy-changes`)
No AI. `utils/changes.py` reads `created_at` / `updated_at` of the country's records, groups days of activity that are ≤ 2 days apart into "profile updates", and reports what was added, changed or archived — either the latest update or everything since a chosen date. It can be exported as Markdown. **Consequence:** if you change records directly in SQL, also set `updated_at`, otherwise the change will not appear.

### 6.9 Link check
`check_link.check_link()` sends `HEAD` (falls back to `GET`), follows up to a few redirects, and re-validates every hop against private/loopback/link-local address ranges (SSRF protection). It runs after a record is created or its link edited, and for all records when someone presses **Recheck links** on the repository page (6 at a time). Results: `link_ok` (1/0/NULL), `link_checked_at`, `link_last_ok_at` (kept when a link breaks, so you can see when it last worked).

### 6.10 IMF PIMA data
`imf_pima.refresh()` reads the IMF's public PIMA/C-PIMA pages, matches each country name to an ISO-3 code, upserts `pim_imf_pima_assessments`, and then applies the curated entries in `data/imf-pima-references.json` (reports the IMF site does not list). Run it on a schedule (see §13.2). The Countries page and an Excel export read the stored rows.

### 6.11 Search-index maintenance
* **Automatic**: 6.2 and 6.3 keep the index in step.
* **Reconcile** (`indexer.reconcile`): compares `pim_documents` with `pim_document_chunks` and repairs drift — indexes documents with no chunks (max 12 per run), removes chunks of documents that no longer exist, re-syncs `record_status`. Meant for a nightly schedule.
* **Full re-index** (`?page=raging` or `scripts/reindex.py`): *Re-index All Documents* / *Re-index English Only* clear the index first and rebuild from the stored files; *Add Original Language Docs* appends without clearing. A pre-flight reads the first file before clearing so a storage problem cannot leave you with an empty index. Only one re-index can run at a time.

### 6.12 Downloads and exports
* One document: `/api/download/<document-id>` streams the file from the volume.
* A country's documents: `/api/download/country/<ISO3>?lang=ENG|ORI|all` returns a zip (also reachable from `?page=download`).
* Corpus export for other pipelines: `/api/export/documents` (CORS + 10 requests/min/IP).
* IMF PIMA table: `/api/imf-pima/export` (Excel).

---

## 7. The AI part (mAI, retrieval, prompts)

### 7.1 Talking to mAI (`utils/mai_client.py`)
* Needs only `MAI_BASE_URL` and `MAI_ENDPOINT` (and optionally `MAI_MODEL`). The provider is derived from `MAI_ENDPOINT` (`…/bedrock/…` → Claude, `…/openai/…` → OpenAI, `…/gemini/…` → Gemini).
* Every request carries the query parameter `mai-endpoint` and the headers `X-Posit-App-Id` (= `CONNECT_CONTENT_GUID`, injected by Posit Connect), `Content-Type: application/json`, `x-source-type: interactive`. **No `Authorization` header, no key.**
* Claude: plain `requests.post(MAI_BASE_URL + "converse", …)` (Bedrock Converse). OpenAI: `client.responses.create`. Gemini: `client.interactions.create` (the SDK clients get the literal api key `"not-used"`).
* One public function, `generate(system, messages, max_tokens, temperature)`. Rate-limit (429), server (5xx) and timeout errors are retried up to 3 times with a short back-off; other errors (400 / 403 / 404 …) fail immediately with a plain-language hint.
* The gateway accepts calls **only from apps running on Posit Connect**. Locally you get 403 — use `PIM_MAI_MOCK=1` to preview the UI with canned answers.

### 7.2 Retrieval (`utils/rag.py`)
* The chunk table is loaded into an **in-memory SQLite FTS5 table** (Porter stemming, Unicode folding, diacritics removed) with a side table of filter columns. Ranking is BM25. If FTS5 is missing from the host's SQLite, a pure-Python BM25 is used.
* The index is **per process** and refreshed lazily: at most every 30 seconds the app asks the database for `(count, latest created_at, archived count)` of the chunk table and reloads only if it changed. Writes made by the same process invalidate it immediately.
* Filters: `country`, `record_id`, `lang_type`, tiers, source, year range; archived records are excluded unless asked for.
* Quality note: keyword search matches the *words* of the question, not their meaning. Questions phrased with the vocabulary of the documents work best; mixed-language corpora are searched per language slot (English and original-language documents are separate `lang_type` values).

### 7.3 Chunking (`utils/chunking.py`)
Text is first split at legal headings (Chapter, Part, Title, Article, Section, §, numbered ALL-CAPS headings in several languages), then recursively at paragraph → line → sentence → word boundaries into ≈ 1,000-character pieces, which are packed into blocks of **≤ 2,000 characters**. Each block keeps its page number and section heading, which the Coach shows as the citation.

### 7.4 Prompts (`utils/prompts.py`)
All instructions given to the model live here: Coach system prompt, condense prompt, the two briefing prompts, the translation prompt, and the original-language directive. Changing wording is a code change (redeploy).

---

## 8. Users, sign-in and permissions

* **Who is "signed in"?**
  1. On Posit Connect, the `RStudio-Connect-Credentials` header identifies the Connect user (`connect:<username>`) — no extra login.
  2. Otherwise a **local account** (`pim_users`): e-mail + password (bcrypt), stored in a signed Flask cookie `pim_session` (30 days). Set `PIM_SECRET_KEY` so the cookie stays valid across restarts and processes.
* **Registering**: the very first account can be created freely; after that registration requires `ADMIN_INVITE_CODE`.
* **Roles**: there are **no roles**. Anyone signed in can create, edit and delete records and documents, run imports, translations and re-indexes. Anonymous visitors can read everything, use the AI Coach and generate briefings (a briefing is only archived in the cache when the person is signed in). On Connect, control *who may open the content at all* (and therefore who can sign in) with the content's access list.
* **Rate limits** (in-process, per client IP): 30 writes/min; 10/min on the public export route; per-key limits on the partner API.
* **Audit**: every create/update/delete/upload/re-index/batch action writes a row to `pim_audit_logs` with user, entity, and the names of changed fields.
* **Partner API keys**: `python scripts/issue_api_key.py --name … --email …` prints the key **once**; only its SHA-256 hash is stored. Revoke with `--revoke <key id>`.

---

## 9. Background jobs, caches and concurrency

| Mechanism | Purpose | Detail |
|---|---|---|
| **Job runner** (`utils/jobs.py`) | Anything slow: briefing generation, Coach answers, translation, batch import, re-index | A thread pool (`PIM_JOB_WORKERS`, default 6) runs the work; progress and result are written to `<PIM_JOBS_DIR>/<job-id>.json`; the page polls with `dcc.Interval` and shows a progress bar. Files older than 6 h are deleted. |
| **Fire-and-forget tasks** | Indexing after an upload, link checks, metadata sync | Same pool, errors are logged and never shown to the user. |
| **Query cache** | Fewer SQL round trips | `db.cached(key, ttl)`; default 60 s (`PIM_CACHE_SECONDS`). Every write calls `db.invalidate()`, so the *same* process sees changes immediately; *other* processes within the TTL. |
| **BM25 index** | Retrieval | See §7.2. |
| **One DB connection per process** | | `utils/db.py` keeps a single connection guarded by a lock and reconnects once on transport errors, so SQL statements of one process run one after another. |
| **Locks** | Re-index and batch import | A process-local lock stops two re-indexes (or two runs of the same batch) from interleaving. They do not coordinate *between* processes — hence the recommendation of **Max processes = 1** on Connect. |

Because job state is a file on the server's disk, a job survives page reloads but **not** a server restart; a restarted app simply shows the job as unknown and the user can start it again.

---

## 10. HTTP API

Dash callbacks handle all writes, so there are **no POST/PATCH/DELETE endpoints for records** (the original had them). Flask routes in `utils/api_routes.py`:

| Route | Purpose | Access |
|---|---|---|
| `GET /api/health` | liveness | public |
| `GET /api/records`, `/api/records/<id>`, `/api/records/language-breakdown`, `/api/countries`, `/api/jurisdictions` | read data | public |
| `GET /api/download/<document-id>`, `/api/download/country/<ISO3>` | files / zip | public |
| `GET /api/countries/<ISO3>/changes?mode=latest\|since&format=json\|md` | policy-change report | public |
| `GET/POST /api/briefings` | read / save cached briefings | POST needs sign-in |
| `GET /api/export/documents?since=&lang=` | corpus export | public, CORS, 10/min |
| `GET /api/imf-pima`, `/api/imf-pima/export` | IMF PIMA data | public |
| `GET/POST /api/rag-sync/reconcile`, `/api/imf-pima/refresh` | maintenance (cron) | `Authorization: Bearer $CRON_SECRET` or signed-in user |
| `GET /api/v1/countries`, `/records`, `/records/<id>`, `POST /api/v1/coach/query`, `GET /api/v1/openapi.json` | **partner API** for ePIM-CBA | header `X-API-Key`, per-key rate limit, documented at `?page=api-docs` |

---

## 11. How to update data (cookbook)

Prefer the web interface: it keeps the search index, link status, audit log and caches consistent. Use SQL only for corrections the UI cannot make, and follow the "afterwards" column.

| I want to… | Do this | Afterwards |
|---|---|---|
| **Add one policy record** | Sign in → `?page=record-new` | Attach documents on the record page — indexing is automatic. |
| **Add many records (and documents)** | `?page=batch` — fill the template, upload, review, import | Automatic. Download the results CSV to see failures. |
| **Edit a record's fields** | `?page=record-edit&id=…` | Automatic (chunk metadata and link status are refreshed). |
| **Archive a record that has been replaced** | Edit → set *Status* to `archived`, optionally *superseded by* link/note | Automatic; it disappears from "current" views and AI answers. |
| **Replace a document** | Edit the record → upload a new file in the same slot (ENG or ORI) | Old file deleted, new one indexed. |
| **Delete a document / a record** | Buttons on the record page | File, row and chunks are removed. |
| **Add an English version of a foreign-language document** | Record page → *Translate to English* | Stored with provenance "mAI Translate" and indexed. Or upload your own file and pick its translation source. |
| **Change a field directly in SQL** | `UPDATE prd_mega.sgpbpi163.pim_policy_records SET year = 2021, updated_at = current_timestamp() WHERE id = '…'` | (1) always set `updated_at` (Policy changes uses it); (2) if you changed `name_eng`, `name_orig`, `year`, `source`, tiers, `country` or `record_status`, run the same change on `pim_document_chunks … WHERE record_id = '…'` (or re-index) so search and citations match; (3) the app shows it within ~60 s. |
| **Replace a file straight in the volume** | Not recommended. If you must: overwrite the same path, update `pim_documents.file_size`, then in a Python shell run `from utils import indexer; indexer.index_document('<document id>')` | The index for that document is rebuilt. |
| **Rebuild the whole search index** | `?page=raging` (sign in) → *Re-index*, or `python scripts/reindex.py --lang all` | Check `?page=statistics`. |
| **Repair drift after manual DB work** | `python scripts/cron_reconcile.py` (or `GET /api/rag-sync/reconcile`) | Indexes documents that lack chunks, removes orphan chunks, re-syncs status. |
| **Update country names / GDP / public-investment figures** | Edit `data/countries.json`, then `python scripts/seed_countries.py --update` (or `UPDATE pim_countries …`) | Visible within 5 minutes (cache). |
| **Refresh IMF PIMA data** | `python scripts/cron_refresh_pima.py` (or `GET /api/imf-pima/refresh` with the cron secret) | Countries page updates. Add curated entries in `data/imf-pima-references.json` first if needed. |
| **Regenerate a briefing** | Open the briefing page → *Generate* | The cache row is overwritten. To drop stale ones: `DELETE FROM pim_briefings_cache WHERE country = 'LTU'`. |
| **Add a person who may edit** | Connect: give them access to the content. Local account: they register with the invite code (or you insert a row with a bcrypt hash) | — |
| **Remove / reset a local account** | `DELETE FROM pim_users WHERE email = '…'`; for a password reset set `password_hash` to a new bcrypt hash (`python -c "import bcrypt;print(bcrypt.hashpw(b'NewPass123',bcrypt.gensalt()).decode())"`) | — |
| **Issue / revoke a partner API key** | `python scripts/issue_api_key.py --name "Org" --email x@y --rate-limit 120` / `--revoke <id>` | The plaintext key is shown once. |
| **Fill the optional `tokens` or `pages` numbers** | `UPDATE … SET tokens = …` or the *Pages* field in the form | Statistics → *Total Tokens*. |
| **Bring in data from the old Postgres database** | `scripts/migrate_from_postgres.py` (see `docs/DATABRICKS_MIGRATION.md`) | Then `scripts/reindex.py --lang all`. |
| **Load the country list on a brand-new setup** | `python scripts/seed_countries.py` | — |

Handy SQL (replace the catalog/schema if you moved them):

```sql
-- how many records / documents / chunks?
SELECT (SELECT COUNT(*) FROM prd_mega.sgpbpi163.pim_policy_records) AS records,
       (SELECT COUNT(*) FROM prd_mega.sgpbpi163.pim_documents)      AS documents,
       (SELECT COUNT(*) FROM prd_mega.sgpbpi163.pim_document_chunks) AS chunks;

-- documents that are not searchable yet (no chunks) – scanned files are expected here
SELECT d.id, d.file_name, d.text_layer FROM prd_mega.sgpbpi163.pim_documents d
LEFT JOIN (SELECT DISTINCT document_id FROM prd_mega.sgpbpi163.pim_document_chunks) c ON c.document_id = d.id
WHERE c.document_id IS NULL;

-- who changed what recently?
SELECT created_at, user_email, action, entity, entity_id, detail
FROM prd_mega.sgpbpi163.pim_audit_logs ORDER BY created_at DESC LIMIT 50;

-- links that stopped working
SELECT country, name_eng, link, link_last_ok_at FROM prd_mega.sgpbpi163.pim_policy_records WHERE link_ok = 0;
```

---

## 12. How to change the application

| Change | Where |
|---|---|
| Add a **new page** | New module in `pages/` with `layout(params)` (and `register_callbacks(app)` if interactive) → add `"<name>": "<module>"` to `pages/__init__.py` `ROUTES` → rebuild the CSS (below) |
| Change **text/labels, tiers, languages, limits** | `utils/constants.py` |
| Change **AI wording** | `utils/prompts.py` |
| Change **styling** | Tailwind classes in the page/`utils/ui.py`; rebuild `assets/style.css`: `cd tools/css && npx tailwindcss -i input.css -o ../../assets/style.css` |
| Add a **column or table** | Edit `utils/schema.py` (`SCHEMA`, `CREATE_ORDER`), add the table name to `constants.TABLES`, run `ALTER TABLE … ADD COLUMN …` (or `python scripts/create_tables.py` for new tables), update `utils/queries.py` and the migration script |
| Change the **mAI model/endpoint** | Connect environment variables `MAI_ENDPOINT` / `MAI_MODEL` (allowed models only) → restart the content |
| Change **chunk size / retrieval depth** | `utils/chunking.py` (`CHUNK_SIZE`, `PARENT_SIZE`), `constants.RETRIEVER_K` / `BRIEFING_RETRIEVER_K` → then re-index |
| Edit **About / release notes** | `pages/about.py`, `data/release_notes.json` |
| Update **dependencies** | `requirements.txt` (pinned) → regenerate the manifest: `rsconnect write-manifest dash -e app:app -x "tools/**" -x "docs/**" .` |

After any code change: redeploy with `rsconnect deploy manifest manifest.json` (regenerate `manifest.json` first so its file checksums are current).

---

## 13. Operations

### 13.1 Configuration (Connect → Content → Vars)
`MAI_BASE_URL`, `MAI_ENDPOINT` (optional `MAI_MODEL`); `DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET`; recommended `PIM_SECRET_KEY`, `ADMIN_INVITE_CODE`; optional `CRON_SECRET`, `RAG_ADMIN_KEY`, `EXPORT_ALLOWED_ORIGINS`, `PUBLIC_BASE_URL`, `PIM_CACHE_SECONDS`, `PIM_JOB_WORKERS`, `PIM_JOBS_DIR`, `PIM_MAX_TRANSLATE_CHARS`, `PIM_TRANSLATE_WORKERS`. All are listed with explanations in `.env.sample`. Leave **Integrations** empty (mAI needs none). Never set `CONNECT_CONTENT_GUID` — Connect does.

Databricks permissions for the service principal: `USE CATALOG`, `USE SCHEMA`, `SELECT`, `MODIFY`, `CREATE TABLE` on the schema, `READ VOLUME` / `WRITE VOLUME` on `pim_documents`, and *Can use* on the SQL warehouse (see `docs/DATABRICKS_MIGRATION.md`).

### 13.2 Scheduled work
Two jobs are worth scheduling (they replace the Vercel cron jobs of the original):

| Job | Suggested frequency | How |
|---|---|---|
| Search-index reconcile | nightly | `GET /api/rag-sync/reconcile` with `Authorization: Bearer $CRON_SECRET`, or run `scripts/cron_reconcile.py` |
| IMF PIMA refresh | weekly | `GET /api/imf-pima/refresh` with the bearer token, or `scripts/cron_refresh_pima.py` |

### 13.3 Backups and recovery
* **Tables** are Delta tables: `DESCRIBE HISTORY <table>` lists versions; `RESTORE TABLE <table> TO VERSION AS OF n` (or `TIMESTAMP AS OF …`) undoes a mistake within the retention window configured on your workspace.
* **Files** in the volume have no version history. Keep a periodic copy elsewhere if the originals are not stored in another system. Records' `link` field points to the official source in most cases.
* **The search index** is derived data — `python scripts/reindex.py --lang all` rebuilds it from the files.
* **Saved briefings** are derived but cost AI calls to recreate; they are in `pim_briefings_cache`.

### 13.4 Monitoring
Logs go to standard output (Connect → Logs); `LOG_LEVEL` sets the verbosity. Look for `search index built: N chunks`, `job … failed`, `mAI gateway error`, `DB call failed … reconnecting`. `GET /api/health` returns 200 when the app is up. `?page=statistics` shows chunks, documents and tokens per country/tier/year.

### 13.5 Sizing
* Memory: the BM25 index holds all chunk text once per process (roughly the size of all extracted text).
* First request after the SQL warehouse was idle is slow (cold start); later requests are served from cache for up to a minute.
* Use **one Connect process** for this content (locks and the job runner are per process; job progress files are local to the server).

---

## 14. Troubleshooting

| Symptom | Likely cause → fix |
|---|---|
| AI Coach answers "I don't have information…" or shows no sources | Index empty or the document is not indexed. Check `?page=statistics`; run a re-index; scanned PDFs have no text layer (OCR them, re-upload). |
| "mAI is not configured" | `MAI_BASE_URL` / `MAI_ENDPOINT` missing in Connect Vars. |
| "mAI gateway error 403" | Not running on Posit Connect (local machine), or a model outside the allowed list in `MAI_MODEL`. |
| "mAI gateway error 404" | Wrong `MAI_BASE_URL` (must end with `/`) or endpoint path. |
| "mAI gateway error 400" | `MAI_ENDPOINT` empty, or `CONNECT_CONTENT_GUID` missing (not on Connect). |
| Pages say "Databricks is not configured" / very slow first load | The four `DATABRICKS_*` variables are missing, or the warehouse is starting. |
| "table or view not found" | Tables not created: run `scripts/create_tables.py`; check catalog/schema names. |
| Country lists are empty | `pim_countries` not loaded: run `scripts/seed_countries.py`. |
| Upload fails for a big file | Connect's maximum request size is smaller than the file; raise it or use the batch importer with a download URL. |
| A change made in SQL isn't visible | Cache (60 s) — wait or restart; and see §11 for chunk metadata and `updated_at`. |
| Signed out after every restart | `PIM_SECRET_KEY` not set. |
| "Registration is closed" | An account exists; supply `ADMIN_INVITE_CODE`, or have the administrator add you in Connect. |
| Re-index appears stuck | It runs in a background thread; progress is shown on `?page=raging`. After a server restart an interrupted run is gone — start it again. |
| Map has no background tiles | The server/browser cannot reach `basemaps.cartocdn.com`; polygons still work. |

---

## 15. Known limits and differences from the Next.js original

| Topic | Original | This app |
|---|---|---|
| Database | Neon PostgreSQL (Drizzle) | Unity Catalog Delta tables `pim_*` — no enforced keys or row locks |
| Files | Vercel Blob (public URLs) | UC Volume, served through the app |
| Search | pgvector embeddings (semantic) | BM25 keyword search — questions should use the documents' vocabulary |
| AI | OpenAI/Anthropic/DeepL, streamed answers | mAI gateway only; answers appear when finished (progress shown) |
| Translation | DeepL | mAI (`translation_source = mai`) |
| Sign-in | Google (Auth.js) | Connect identity or local accounts; no roles |
| Writes | REST POST/PATCH/DELETE + UI | UI callbacks only |
| Upload size | Vercel limits | Bounded by Connect's maximum request size |
| Index location | separate FastAPI service | inside the app (per process, in memory) |

The Databricks and mAI paths could be exercised only against a local SQLite stand-in and canned AI replies during development; run the first deployment on QA and walk through: sign in → add a record → attach a PDF → ask the Coach → generate a briefing → `?page=statistics`.

---

## 16. Glossary

| Term | Meaning |
|---|---|
| **PIM** | Public Investment Management |
| **Policy record** | One law, regulation, guideline or strategy (a row in `pim_policy_records`) |
| **Tier 1–4** | Primary legislation · Secondary regulations · Procedural guidelines · Strategies (strategy types 1–6 apply to tier 4) |
| **ENG / ORI** | English / original-language document of a record |
| **Text layer** | A PDF that contains real text (not just a picture of a page) — required for indexing |
| **Chunk / passage** | A ≤ 2,000-character piece of a document, the unit of search |
| **BM25 / FTS5** | The ranking method / SQLite full-text engine used for keyword search |
| **RAG** | Retrieval-augmented generation: find passages first, then let the AI answer from them |
| **mAI gateway** | Posit's service that gives apps on Posit Connect access to approved AI models without API keys |
| **Unity Catalog (UC)** | Databricks' governance layer: catalogs → schemas → tables / volumes |
| **Volume** | A UC-governed folder for files |
| **Service principal** | A non-human Databricks identity (client id + secret) the app uses to log in |
| **Delta table** | Databricks' table format (versioned, supports `RESTORE`) |
| **PIMA / C-PIMA** | IMF's (Climate) Public Investment Management Assessment |
| **Job** | A background task whose progress the page polls |

# PIM Policy Profiles AI — Python Dash

Dash port of the Next.js + FastAPI "PIM Policy Profiles AI" application, built to run on **Posit Connect** (Python 3.12.12).

* Data: **Azure Databricks Unity Catalog** — `prd_mega.sgpbpi163.policy_repo_*` tables, documents in the UC Volume folder `/Volumes/prd_mega/sgpbpi163/vgpbpi163/PIMPAM-AI/policy-profiles/upload-files`
* Search: **Databricks AI Search** (formerly Vector Search) index over the `policy_repo_document_chunks` table – see [docs/AI_SEARCH.md](docs/AI_SEARCH.md)
* AI: **Posit mAI Gateway** — `MAI_BASE_URL` + one endpoint per provider (`MAI_ENDPOINT_OPENAI` / `_CLAUDE` / `_GEMINI`), no API keys
* Pages: one site, routed by query string — `/?page=<name>` (home is `/` or `/?page=home`)

## Pages

| URL | Page |
|---|---|
| `/?page=home` | Home |
| `/?page=countries` · `/?page=country&iso3=LTU` | Countries and country detail |
| `/?page=map` | Global map |
| `/?page=repository` | Policy repository (search, filters, sort, link re-check) |
| `/?page=record&id=<id>` · `record-new` · `record-edit&id=<id>` | Record detail, create, edit (document upload, link check) |
| `/?page=batch` | Batch upload (XLSX / CSV) |
| `/?page=coach` · `country-profile` · `country-transparency` | AI Coach hub, country profile briefing, transparency briefing |
| `/?page=policy-changes` · `benchmark` · `statistics` | Policy changes, benchmark, search-index statistics |
| `/?page=raging` | Search-index (RAG) status |
| `/?page=api-docs` · `about` · `release-notes` | API docs (ReDoc), About, Release notes |
| `/?page=signin` · `register` | Local accounts (Connect sign-in is picked up automatically) |

JSON / file endpoints (`/api/...`, partner API under `/api/v1`) are listed in `utils/api_routes.py` and on the API docs page.

## Layout

How the pieces fit together (data flow, storage, how to update data): **[ARCHITECTURE.md](ARCHITECTURE.md)**.

```
app.py                  Dash app + router (?page=…) + Flask API routes
manifest.json           Posit Connect bundle manifest (rsconnect-python)
requirements.txt        pinned dependencies
.env.sample             every environment variable, grouped
assets/                 style.css (compiled Tailwind), app.js, map.js, fonts, logo, vendor/ (Leaflet, ReDoc), templates/
pages/                  one module per page
utils/                  constants, db, schema, storage, mai_client, rag, indexer, batch, exports, UI building blocks …
scripts/                create_tables · seed_countries · cron_refresh_pima · seed_demo · issue_api_key · check_ai_search
sql/uc_tables.sql       DDL for the Unity Catalog tables + volume
docs/AI_SEARCH.md       search index: design, setup, operations, cost drivers
tools/                  CSS + icon build sources (not deployed)
```

## Configuration

Copy `.env.sample` to `.env` locally; on Connect set the same names under **Content ▸ Vars**.

| Variable | Needed | Purpose |
|---|---|---|
| `MAI_BASE_URL`, `MAI_ENDPOINT_OPENAI` / `MAI_ENDPOINT_CLAUDE` / `MAI_ENDPOINT_GEMINI` | **yes** (at least one endpoint) | mAI gateway – QA/Production values in `.env.sample`; `MAI_MODEL` (default model) is optional. Without them the Coach shows "coming soon" |
| `DATABRICKS_SERVER_HOSTNAME`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_CLIENT_ID`, `DATABRICKS_CLIENT_SECRET` | **yes** | SQL warehouse + OAuth M2M service principal for `prd_mega.sgpbpi163` |
| `PIM_SECRET_KEY` | recommended | signs the cookie of local accounts (shared by all Connect processes) |
| `ADMIN_INVITE_CODE` | recommended | required to register after the first account |
| `CRON_SECRET`, `EXPORT_ALLOWED_ORIGINS`, `PUBLIC_BASE_URL` | optional | see `.env.sample` |

`CONNECT_CONTENT_GUID` is set by Posit Connect itself and is sent to the gateway as `X-Posit-App-Id`; never define it. The app never sends an `Authorization` header to the gateway. Real Databricks credentials are read from the environment only — `constants.py` holds the variable names, catalog and schema, not secrets.

## Set up the data (once)

```bash
python scripts/create_tables.py                 # or paste sql/uc_tables.sql into the SQL editor
python scripts/seed_countries.py                # the 196-country reference list
# search index: create the AI Search index on policy_repo_document_chunks in Databricks (docs/AI_SEARCH.md)
```

Uploaded documents are only written to the UC volume (`…/upload-files/documents/upload-documents/<ISO3>_<Country>/<National|Subnational>/`);
at the same time the app chunks them into `policy_repo_document_chunks` and asks the AI Search index to sync. The app never reads files
back from the volume.

## Deploy to Posit Connect

```bash
pip install rsconnect-python
rsconnect add --server https://<connect-host> --api-key <key> --name connect
rsconnect deploy manifest manifest.json --name connect        # or:  rsconnect deploy dash . -e app:app
```

Then, in the content's settings: set the variables above, and (recommended) keep **Max processes = 1** and a generous *Read timeout* / *Idle timeout*, because document indexing and AI generation run as background jobs inside the process. Regenerate the manifest after changing files: `rsconnect write-manifest dash -e app:app .` (the exclusions used for this repo are in the command in "Maintenance").

Cron job that was a Vercel cron route: either call `GET /api/imf-pima/refresh` with `Authorization: Bearer $CRON_SECRET`, or schedule `scripts/cron_refresh_pima.py`.

## Run locally

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.sample .env        # fill in Databricks + MAI values
python app.py              # http://localhost:8050/?page=home
```

UI preview without Databricks or the gateway (SQLite + local files + canned AI replies; the gateway only accepts calls from Connect):

```bash
export PIM_DB_BACKEND=sqlite PIM_MAI_MOCK=1
python scripts/seed_demo.py --reset     # demo records with PDFs and a search index
python app.py                           # demo login: demo-user@example.org / longenough1
```

## How it differs from the Next.js app

| Original | Dash app |
|---|---|
| Neon PostgreSQL (Drizzle) | Unity Catalog Delta tables `policy_repo_*` |
| Vercel Blob | UC Volume folder `…/upload-files/documents/upload-documents/`, filed by country / national-subnational; upload only (files are not downloadable from the app) |
| pgvector + embeddings (LangChain) | Databricks AI Search Delta Sync index (Databricks-computed multilingual embeddings, hybrid search) over the `policy_repo_document_chunks` table; |
| OpenAI / Anthropic / DeepL calls, streamed | mAI gateway only; no streaming, so long jobs run in the background and the page polls for progress |
| DeepL translation | translation through mAI (`translation_source` = `mai`) |
| Google sign-in (Auth.js) | Posit Connect identity, or local e-mail + password accounts (first account bootstraps; later ones need `ADMIN_INVITE_CODE`) |
| Recharts | Plotly |
| Tailwind typography plugin (never installed in the original) | equivalent `.md-prose` styles for briefings |
| Statistics "Vector Database Analytics" | renamed "Search Index Analytics" (shows the AI Search engine) |
| REST POST / PATCH / DELETE for records | Dash callbacks (no write endpoints; the read/export/partner API is unchanged) |

## Limits and notes

* `dcc.Upload` sends files as base64 in one request, so the practical upload size is bounded by Connect's *max request size* (and the app caps batch downloads at 25 MB per file).
* Delta has no enforced unique constraints or row locks; the app checks before it writes.
* Local accounts and partner API keys live in `policy_repo_users` / `policy_repo_partner_api_keys` (the latter not created yet – the partner API `/api/v1/*` is off until `PIM_PARTNER_API=1`); API keys are stored as SHA-256 hashes and shown once (`python scripts/issue_api_key.py`).
* A cold SQL warehouse makes the first request slow; read queries are cached for `PIM_CACHE_SECONDS` (60 s).

## Maintenance

Rebuild the stylesheet after changing classes in a page (needs Node):

```bash
cd tools/css && npm install && npx tailwindcss -i input.css -o ../../assets/style.css
```

Regenerate the Connect manifest:

```bash
rsconnect write-manifest dash -e app:app -x "tools/*" -x "docs/*" -x ".local/*" -x ".venv/*" .
```

# Search index on Databricks AI Search

The AI Coach and the country briefings retrieve passages from the policy documents. Retrieval now runs on
**Databricks AI Search** (the product formerly called *Vector Search*) over a Delta table of chunks. The v1 design
(JSON chunk files in the volume + in-process BM25) is gone; BM25 stays as a fallback over the same table.

## 1. Design

```
                      ┌──────────── one-time / bulk ────────────┐        ┌──────── every upload (Dash app) ────────┐
UC Volume documents ─►│ notebooks/01_backfill_document_chunks.py │        │ utils/documents.attach_document          │
                      │   Spark: extract → chunk → rows          │        │   → utils/indexer.index_document         │
                      └───────────────────┬──────────────────────┘        └───────────────────┬─────────────────────┘
                                          │   same code: utils/extract.py + utils/chunking.py  │
                                          ▼                                                    ▼
             prd_mega.sgpbpi163.policy_repo_document_chunks   (Delta, Change Data Feed, 1 row per chunk, MERGE by chunk_id)
             prd_mega.sgpbpi163.policy_repo_document_ingest_log (1 row per document: indexed / no_text_layer / empty / failed …)
                                          │  Delta Sync, TRIGGERED, embeddings computed by Databricks from `embedding_text`
                                          ▼
             prd_mega.sgpbpi163.policy_repo_document_chunks_index   on endpoint pim_policy_profiles_ai_search (STANDARD)
                                          │  HYBRID query (vector + keyword) + filters (country, record, language, tier, year, status)
                                          ▼
                     utils/rag.retrieve → AI Coach (utils/chat.py) · briefings (utils/briefings.py) · /api/v1/coach/query
```

Why this shape:

| Choice | Reason |
|---|---|
| **Delta Sync index with Databricks-computed embeddings** | The mAI gateway has no embedding endpoint; Databricks embeds the `embedding_text` column itself, both when syncing and at query time. The app only writes rows and sends query text – no vectors to manage. |
| **`databricks-qwen3-embedding-0-6b`** | Multilingual (the repository holds original-language laws), 32k context. `databricks-gte-large-en` is English-only. Configurable in the notebook. |
| **HYBRID queries** | Legal text is full of exact terms (article numbers, law names) that keyword search finds and pure vectors miss; vectors find paraphrases. |
| **One chunk table, written by both the notebook and the app** | Old and new documents end up in the same rows, same format, same index. `chunk_id = <document_id>:<chunk_index>` is deterministic, so re-running is idempotent and an unchanged chunk is never re-embedded (`row_hash`). |
| **Record fields copied onto each chunk** | AI Search can only filter on columns of the index. When a record is edited the app rewrites its chunks' fields (`indexer.sync_record_metadata`); the nightly reconcile repairs any drift. |
| **TRIGGERED sync, STANDARD endpoint** | No always-on streaming pipeline; the app asks for a sync after uploads (debounced). A STANDARD endpoint gives low latency and fits a corpus of a few thousand documents. |
| **~2,000-character section-aware chunks with a context header** | The passage the LLM reads is the passage that was embedded; `embedding_text` adds "Country / Document / Year / Section" so a question naming a law or article finds it. |
| **BM25 fallback over the same table** | The app keeps working before ITS has approved the endpoint (`PIM_SEARCH_BACKEND=bm25`) and if an AI Search call fails. |

Alternatives considered: *Direct Vector Access index* (the app would have to compute and upsert vectors – no embedding API available to it); *self-managed embeddings column* (same problem); *parent/child chunks* as in the original service (more tables and a lookup per query for a small gain once hybrid search and a context header are used – can be added later without changing the app's interface); *storage-optimized endpoint* (cheaper per vector at large scale, but TRIGGERED-only with partial rebuilds and a different filter syntax – not needed at this size).

## 2. Objects

| Object | Created by | Notes |
|---|---|---|
| `policy_repo_document_chunks` | notebook step 1 / `scripts/create_tables.py` / `sql/uc_tables.sql` | Must keep `delta.enableChangeDataFeed = true`. Column types are AI-Search compatible (`INT`, not `SMALLINT`). |
| `policy_repo_document_ingest_log` | same | Makes the notebook incremental; shows what could not be indexed and why. |
| `policy_repo_document_chunks_index` | notebook step 8 | Delta Sync index, primary key `chunk_id`, embedding source `embedding_text`. A column change in the table requires a **new** index. |
| `pim_policy_profiles_ai_search` | notebook step 8 | The billed compute. |

No longer needed: `policy_repo_langchain_pg_collection` (and the old `langchain_pg_embedding` / `chunk_parents` – their Voyage/OpenAI vectors cannot be reused), the JSON files in `…/upload-files/chunks/`.

## 3. First-time setup

1. **Before ITS approval** – import `notebooks/01_backfill_document_chunks.py` into the workspace (Workspace → Import → File) and run it with
   `create_ai_search = no`. It creates the two tables, reads `…/upload-files/documents/country-files/_manifest.json`
   (`{"documents": {document id: "<ISO3_Country>/<level>/<year>/<tier>/<ENG|ORI_lang>/<id8>_<name>"}}`, paths relative to that folder;
   its `upload_to` field is ignored), checks every entry against the files and the document rows, links each `policy_repo_documents` row
   to its file (manifest → `blob_url` → scan by `<id8>_` file name → scan by file name), chunks PDF / DOCX / XLSX and writes the tables. Check step 7: documents that are scanned
   (`no_text_layer` / `empty`), `.doc` files (`unsupported`), missing or broken files are listed.
   Deploy the app with `PIM_SEARCH_BACKEND=bm25` – it already searches the new table.
2. **After ITS approval** – run the notebook again with `create_ai_search = yes` (and `ai_search_identity = service_principal` +
   the secret scope holding the app's client id / secret, so the app's service principal owns the index and may trigger syncs).
   It creates the endpoint and the index, waits for the first sync and runs a test query.
3. Grants (notebook step 10) – usually already covered by the schema-level grant in `sql/uc_tables.sql`. Give the service principal
   *Can use* on the endpoint (Compute → AI Search → endpoint → Permissions).
4. Set on Posit Connect: `PIM_SEARCH_BACKEND=ai_search` (the default), and the other `PIM_AI_SEARCH_*` values if you changed names.
   The app's document folder now defaults to `documents/country-files` (`PIM_DOCUMENTS_FOLDER`), so it reads the same manifest and
   computes the same file paths as the notebook (identical rows, nothing re-embedded when the app re-indexes).

## 4. Day-to-day behaviour

| Event | What happens |
|---|---|
| Upload / replace a document | background job: extract → chunk → MERGE rows (only changed ones) → delete surplus rows → ingest log → sync requested (debounced 20 s). A replaced file that turns out to be scanned removes the old chunks. |
| Delete a document / record | its rows are deleted → sync. |
| Edit a record (country, names, year, tiers, status …) | its chunks' copied fields are rewritten → sync (only rows that change are re-embedded). Archived records stay in the index and are filtered out by default. |
| Nightly `GET /api/rag-sync/reconcile` or `scripts/cron_reconcile.py` | indexes missing documents, re-chunks those made by an older `CHUNKER_VERSION`, deletes orphan chunks, repairs metadata drift, syncs. |
| RAG admin page / `scripts/reindex.py` | re-chunks in place; the index keeps serving. |
| Chunker change | edit `utils/chunking.py`, bump `CHUNKER_VERSION`, run `python tools/build_notebook.py` (regenerates the notebook's shared-code cell), re-run the notebook with `run_mode = incremental`. |
| Bulk copy of files into the volume | re-run the notebook (`incremental`). `update_manifest = yes` also adds scan-matched files to `_manifest.json` so the app can serve and re-index them. |

If the service principal may not trigger syncs, set `PIM_AI_SEARCH_SYNC=0` and schedule the notebook as a Job with `run_mode = sync_only`
(e.g. every 15–30 minutes); uploads then become searchable at the next run.

## 5. Cost drivers (for the ITS discussion)

* **Endpoint** – a STANDARD endpoint is billed for every hour it exists, whether or not it is queried. This is the main recurring cost.
  One endpoint can serve several indexes.
* **Embeddings** – billed per token through Foundation Model APIs: once for the backfill (≈ number of chunks × ~600 tokens; step 7 of the
  notebook shows the chunk count) and then only for new or changed chunks and for each query text.
* **Sync** – TRIGGERED syncs on a STANDARD endpoint process only changed rows; no continuous pipeline is running.
* **Reranker** (`PIM_AI_SEARCH_RERANK=1`) – optional, adds per-query cost and latency.
* Ask ITS for a budget / usage policy to tag the endpoint (`budget_policy_id`) with the charge code.

## 6. Troubleshooting

| Symptom | Check |
|---|---|
| Coach answers "no indexed documents" | `SELECT COUNT(*) FROM policy_repo_document_chunks`; Statistics page → engine; app log for "AI Search failed – falling back". |
| New upload not found | ingest log row of the document (`status`, `detail`); index page in Catalog Explorer (last sync); `PIM_AI_SEARCH_SYNC`. |
| `PERMISSION_DENIED` on query | `SELECT` on the index + `USE CATALOG / USE SCHEMA` for the service principal; endpoint *Can use*. |
| `PERMISSION_DENIED` on sync | the index is owned by a user → recreate it as the service principal, or use the scheduled `sync_only` job. |
| Notebook stops in step 1 | an older `policy_repo_document_chunks` with another layout exists → `recreate_chunks_table = yes` (delete any index on it first). |
| Scanned PDFs (`no_text_layer` / `empty`) | need OCR before they can be searched (e.g. a translated / OCR'd copy uploaded as the ENG document). |

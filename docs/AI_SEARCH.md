# Search index on Databricks AI Search

The AI Coach and the country briefings retrieve passages from the policy documents. Retrieval now runs on
**Databricks AI Search** (the product formerly called *Vector Search*) over a Delta table of chunks.

The app only does three things with Databricks storage, all when a document is uploaded – nothing is ever read back
from the volume, and there is no migration, backfill or re-index from it:

1. **upload** the file to the UC volume `…/upload-files/documents/upload-documents/<ISO3>_<Country>/<National|Subnational>/`
2. in parallel, **chunk** the uploaded bytes in the app and **insert** the chunk rows into `policy_repo_document_chunks`
3. ask the index `policy_repo_document_chunks_index` to **sync** with that table

## 1. Design

```
  user upload (record page / batch import)
        │  utils/documents.attach_document – the same bytes go two ways at once
        ├──────────────────────────────────────────┐
        ▼                                          ▼
  utils/storage.save                      utils/indexer.index_document (background)
  UC Volume …/documents/upload-documents/   extract → chunk (utils/extract.py + utils/chunking.py)
  <ISO3>_<Country>/<National|Subnational>/   │
  (write only)                               ▼
             prd_mega.sgpbpi163.policy_repo_document_chunks   (Delta, Change Data Feed, 1 row per chunk, MERGE by chunk_id)
             prd_mega.sgpbpi163.policy_repo_document_ingest_log (1 row per document: indexed / no_text_layer / empty / failed …)
                                          │  sync requested by the app (TRIGGERED), embeddings computed by Databricks from `embedding_text`
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
| **`databricks-qwen3-embedding-0-6b`** | Multilingual (the repository holds original-language laws), 32k context. `databricks-gte-large-en` is English-only. Chosen when the index is created in Databricks. |
| **HYBRID queries** | Legal text is full of exact terms (article numbers, law names) that keyword search finds and pure vectors miss; vectors find paraphrases. |
| **Chunks written at upload time, from the uploaded bytes** | No second pass over the volume. `chunk_id = <document_id>:<chunk_index>` is deterministic, so replacing a file is idempotent and an unchanged chunk is never re-embedded (`row_hash`). |
| **Record fields copied onto each chunk** | AI Search can only filter on columns of the index. When a record is edited the app rewrites its chunks' fields (`indexer.sync_record_metadata`). |
| **TRIGGERED sync, STANDARD endpoint** | No always-on streaming pipeline; the app asks for a sync after uploads (debounced). A STANDARD endpoint gives low latency and fits a corpus of a few thousand documents. |
| **~2,000-character section-aware chunks with a context header** | The passage the LLM reads is the passage that was embedded; `embedding_text` adds "Country / Document / Year / Section" so a question naming a law or article finds it. |

Alternatives considered: *Direct Vector Access index* (the app would have to compute and upsert vectors – no embedding API available to it); *self-managed embeddings column* (same problem); *parent/child chunks* as in the original service (more tables and a lookup per query for a small gain once hybrid search and a context header are used – can be added later without changing the app's interface); *storage-optimized endpoint* (cheaper per vector at large scale, but TRIGGERED-only with partial rebuilds and a different filter syntax – not needed at this size).

## 2. Objects

| Object | Created by | Notes |
|---|---|---|
| `policy_repo_document_chunks` | `scripts/create_tables.py` / `sql/uc_tables.sql` | Must keep `delta.enableChangeDataFeed = true`. Column types are AI-Search compatible (`INT`, not `SMALLINT`). |
| `policy_repo_document_ingest_log` | same | Shows what could not be indexed and why. |
| `policy_repo_document_chunks_index` | created in Databricks (Catalog Explorer) | Delta Sync index, primary key `chunk_id`, embedding source `embedding_text`. A column change in the table requires a **new** index. |
| `pim_policy_profiles_ai_search` | created in Databricks (Compute → AI Search) | The billed compute. |

No longer needed: `policy_repo_langchain_pg_collection` (and the old `langchain_pg_embedding` / `chunk_parents` – their Voyage/OpenAI vectors cannot be reused), the JSON files in `…/upload-files/chunks/`.

## 3. First-time setup

1. Create the tables: `python scripts/create_tables.py` (or `sql/uc_tables.sql`).
2. In Databricks, create the endpoint and a Delta Sync index on `policy_repo_document_chunks` (primary key `chunk_id`, embedding
   source `embedding_text`, TRIGGERED). Make the app's service principal the **owner** of the index so it may trigger syncs.
3. Grants – usually already covered by the schema-level grant in `sql/uc_tables.sql`. Give the service principal
   *Can use* on the endpoint (Compute → AI Search → endpoint → Permissions).
4. Set on Posit Connect: `PIM_SEARCH_BACKEND=ai_search` (the default), `PIM_AI_SEARCH_SYNC=1`, and the other `PIM_AI_SEARCH_*`
   values if you changed names.

## 4. Day-to-day behaviour

| Event | What happens |
|---|---|
| Upload / replace a document | the file is uploaded to the volume and, in parallel, a background job chunks the same bytes → MERGE rows (only changed ones) → delete surplus rows → ingest log → sync requested (debounced 20 s). A replaced file that turns out to be scanned removes the old chunks. |
| Delete a document / record | its chunk rows are deleted → sync. The uploaded file stays in the volume. |
| Edit a record (country, names, year, tiers, status …) | its chunks' copied fields are rewritten → sync (only rows that change are re-embedded). Archived records stay in the index and are filtered out by default. |
| Chunker change | edit `utils/chunking.py` and bump `CHUNKER_VERSION`; it applies to new uploads (re-upload a document to re-chunk it). |

If the service principal may not trigger syncs, set `PIM_AI_SEARCH_SYNC=0` and sync the index from Databricks
(*Sync now* on the index page, or a scheduled job); uploads then become searchable at the next sync.

## 5. Cost drivers (for the ITS discussion)

* **Endpoint** – a STANDARD endpoint is billed for every hour it exists, whether or not it is queried. This is the main recurring cost.
  One endpoint can serve several indexes.
* **Embeddings** – billed per token through Foundation Model APIs: only for new or changed chunks (~600 tokens each) and for each
  query text.
* **Sync** – TRIGGERED syncs on a STANDARD endpoint process only changed rows; no continuous pipeline is running.
* **Reranker** (`PIM_AI_SEARCH_RERANK=1`) – optional, adds per-query cost and latency.
* Ask ITS for a budget / usage policy to tag the endpoint (`budget_policy_id`) with the charge code.

## 6. Troubleshooting

| Symptom | Check |
|---|---|
| Coach answers "no indexed documents" | `SELECT COUNT(*) FROM policy_repo_document_chunks`; Statistics page → engine; app log for "AI Search failed – falling back". |
| New upload not found | ingest log row of the document (`status`, `detail`); index page in Catalog Explorer (last sync); `PIM_AI_SEARCH_SYNC`. |
| `PERMISSION_DENIED` on query | `SELECT` on the index + `USE CATALOG / USE SCHEMA` for the service principal; endpoint *Can use*. |
| `PERMISSION_DENIED` on sync | the index is owned by a user → make the service principal the owner, or sync from Databricks. |
| Scanned PDFs (`no_text_layer` / `empty`) | need OCR before they can be searched (e.g. a translated / OCR'd copy uploaded as the ENG document). |

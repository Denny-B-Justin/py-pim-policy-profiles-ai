# Changes – search index on Databricks AI Search

Copy these files over the repository (paths are relative to the repo root). Nothing else changes; no new Python package
is needed (the AI Search calls use the `databricks-sdk` already in `requirements.txt`).

## New files

| File | What it is |
|---|---|
| `notebooks/01_backfill_document_chunks.py` | Databricks notebook. Reads `…/documents/country-files/_manifest.json` as the primary file index (paths relative to the manifest's folder; `upload_to` ignored), validates it (missing files, files not in the manifest, entries without a document row, country / ENG–ORI folder vs record mismatches) before chunking. Databricks notebook (import via *Workspace → Import → File*). One-time, re-runnable backfill: creates the chunk tables, matches every document row to its file in the volume, chunks with the **same code as the app**, writes the tables, creates the AI Search endpoint + index, test query, grants. Not deployed to Connect. |
| `utils/ai_search.py` | AI Search client: hybrid query with filters (+ optional reranker), index status, debounced sync after writes. |
| `docs/AI_SEARCH.md` | Design, setup order, day-to-day behaviour, cost drivers for ITS, troubleshooting. |
| `tools/build_notebook.py`, `tools/notebook_template.py` | Regenerate the notebook after a chunker change so its shared-code cell stays identical to `utils/chunking.py` + the loaders in `utils/extract.py`. |

## Changed files

| File | Change |
|---|---|
| `utils/extract.py` | Reads **XLSX** workbooks (one "Sheet: name" block per sheet, one line per row, cached formula values, capped at 200k cells) – the repository has 6. `openpyxl` is already in `requirements.txt`. |
| `utils/storage.py` | Docstring only (folder name). |
| `utils/chunking.py` | **Chunker v2** (`CHUNKER_VERSION = "pim-chunker-v2.1"`), now pure Python and shared verbatim with the notebook. Fixes: v1 silently dropped the text before the first heading of a page whenever that heading started within the page's first 200 characters (typically the end of the previous article – lost text); headings matched case-insensitively (wrapped lines like "article 12 of the Law…" and list items started false sections); sections were cut at page boundaries and lost their heading; only the first page was recorded; wrong Chinese article pattern. New: whole-document section split with exact page_start/page_end, running header/footer + page-number removal, de-hyphenation, 200-char overlap between consecutive chunks of the same section, more languages (ES/FR/PT/IT/DE/RU). Also builds the table rows: `embedding_text` (Country/Document/Year/Section header + chunk), `row_hash`. |
| `utils/indexer.py` | Writes to the Delta table `policy_repo_document_chunks` (MERGE by `chunk_id`, only changed rows rewritten, surplus rows deleted) instead of JSON files; writes `policy_repo_document_ingest_log`; `sync_record_metadata()` now really updates the record fields on the chunks (needed for AI Search filters); `reconcile()` also re-chunks old chunker versions and repairs metadata drift; re-index rewrites in place (index never emptied); a document replaced by a scanned file no longer keeps its old chunks; requests an index sync after writes. Public function names/signatures unchanged. |
| `utils/rag.py` | `retrieve()` uses AI Search (hybrid) when `PIM_SEARCH_BACKEND=ai_search`, with automatic fallback to BM25; BM25 now loads from the chunks table (no JSON files); briefing top-up (`pad_to_k`) via SQL; statistics computed in SQL. Same return shapes – pages, chat and briefings are untouched. |
| `utils/schema.py` | Two new tables: `document_chunks` (Change Data Feed on, AI-Search-compatible types) and `document_ingest_log`; DDL supports TBLPROPERTIES. |
| `constants.py` | Table names for the two tables; `PIM_SEARCH_BACKEND`, `PIM_AI_SEARCH_*` settings; document folder default `documents/country-files` (where the files and `_manifest.json` are). |
| `sql/uc_tables.sql` | DDL of the two new tables. |
| `.env.sample` | New `PIM_SEARCH_BACKEND` / `PIM_AI_SEARCH_*` variables (all optional – defaults match the notebook). |
| `scripts/seed_demo.py` | `--reset` clears the chunk tables instead of chunk files. |
| `scripts/reindex.py` | Docstring (behaviour comes from `indexer.reindex`). |
| `README.md`, `ARCHITECTURE.md` | Point to `docs/AI_SEARCH.md`; manifest command excludes `notebooks/`. |
| `manifest.json` | Checksums of the changed files + `utils/ai_search.py` added (otherwise `rsconnect deploy manifest` would not upload it). |

## Unchanged on purpose

`utils/documents.py` (uploads still accept PDF / DOC / DOCX / TXT – add `.xlsx` to `ALLOWED_DOC_EXTENSIONS` if users should upload workbooks), `utils/chat.py`, `utils/briefings.py`, `utils/record_form.py`, `utils/batch.py`, all pages,
`requirements.txt`. The JSON files in `…/upload-files/chunks/` (if any were written) and `policy_repo_langchain_pg_collection` can be deleted.

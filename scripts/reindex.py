"""
Re-chunk the stored documents into policy_repo_document_chunks (the source of the AI Search index) and ask the
index to sync.  Documents are rewritten in place – the index keeps answering, unchanged passages are not re-embedded.

    python scripts/reindex.py                 # English documents (+ drops chunks of deleted ENG documents)
    python scripts/reindex.py --lang all      # English + original-language documents
    python scripts/reindex.py --lang ori      # original-language documents only

For the first, bulk load of the documents already in the volume use the Databricks notebook
notebooks/01_backfill_document_chunks.py instead (parallel, incremental, same chunker) – see docs/AI_SEARCH.md.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import indexer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", choices=["eng", "ori", "all"], default="eng")
    args = ap.parse_args()

    def progress(p: dict) -> None:
        if p.get("document"):
            print(f"[{p.get('current', '?')}/{p.get('total', '?')}] {p['document']} – {p.get('status', '')}", flush=True)

    result = indexer.reindex(lang_filter=args.lang, clear_existing=args.lang != "ori", progress=progress)
    print(result)
    return 1 if result.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())

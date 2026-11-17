"""
Rebuild the search index (the chunk files in <FILES_ROOT>/chunks/) from the stored documents.

    python scripts/reindex.py                 # English documents (clears the index first) – what the app's RAG admin page does
    python scripts/reindex.py --lang all      # English + original-language documents
    python scripts/reindex.py --lang ori      # append original-language documents (does not clear)

Run it once after copying the documents into the volume (scripts/copy_documents_to_volume.py): the pgvector index
of the old app is not copied (see docs/DATABRICKS_MIGRATION.md).
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

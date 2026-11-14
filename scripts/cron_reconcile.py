"""
Nightly repair of the search index (ported from the Vercel cron `/api/rag-sync/reconcile`).

Schedule it with a Posit Connect scheduled job (e.g. run this script from a Quarto/Python document), or call the HTTP
route instead:   GET /api/rag-sync/reconcile   with   Authorization: Bearer $CRON_SECRET

    python scripts/cron_reconcile.py [--max 12]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import indexer  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=12, help="maximum documents to (re)index in this run")
    args = ap.parse_args()
    print(json.dumps(indexer.reconcile(args.max), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

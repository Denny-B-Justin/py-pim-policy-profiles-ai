"""
Check that the app's service principal can use the Databricks AI Search index – and say exactly what is missing.

Runs with the same settings as the app (`.env` locally, or the Posit Connect variables when run there):
DATABRICKS_SERVER_HOSTNAME, DATABRICKS_CLIENT_ID, DATABRICKS_CLIENT_SECRET, PIM_AI_SEARCH_INDEX (optional).

    python scripts/check_ai_search.py                 read-only checks: credentials, identity, read index, query
    python scripts/check_ai_search.py --sync          also trigger a sync (harmless: picks up pending table changes)
    python scripts/check_ai_search.py --sync --json report.json     also write the report as JSON (to send on)

Exit code 0 = every check passed, 1 = something is missing (the report says what to ask for).
The client secret is never printed.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import ai_search  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sync", action="store_true", help="also test triggering a sync of the index")
    ap.add_argument("--json", metavar="FILE", help="write the report as JSON to FILE")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    report = ai_search.diagnose(include_sync=args.sync)
    print(ai_search.format_report(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nreport written to {args.json}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

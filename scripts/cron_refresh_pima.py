"""
Refresh the IMF PIMA / C-PIMA assessments from imf.org (ported from the Vercel cron `/api/imf-pima/refresh`).

    python scripts/cron_refresh_pima.py

or call   GET /api/imf-pima/refresh   with   Authorization: Bearer $CRON_SECRET
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import imf_pima  # noqa: E402


def main() -> int:
    stats = imf_pima.refresh(log_fn=print)
    print(json.dumps(stats, indent=2, default=str))
    return 1 if stats.get("fetch_errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())

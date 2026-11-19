"""
Issue (or revoke) a partner API key for `/api/v1/*`  – port of `web/scripts/issue-api-key.ts`.

    python scripts/issue_api_key.py --name "Acme Ministry" --email ops@example.org --rate-limit 120
    python scripts/issue_api_key.py --revoke <key id>

The plaintext key is printed ONCE; only its SHA-256 hash is stored in `policy_repo_partner_api_keys`.
Uses the same database settings as the app (DATABRICKS_* env vars, or PIM_DB_BACKEND=sqlite for local tests).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import constants as C  # noqa: E402
from utils import api_keys  # noqa: E402


def main() -> int:
    if not C.PARTNER_API_ENABLED:
        print("The partner API is switched off (PIM_PARTNER_API is not 1): it needs the "
              f"{C.TABLES['partner_api_keys']} table, which has not been created.")
        return 1
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", help="partner / organisation name")
    ap.add_argument("--email", help="contact e-mail")
    ap.add_argument("--rate-limit", type=int, default=60, help="requests per minute (default 60)")
    ap.add_argument("--notes")
    ap.add_argument("--revoke", metavar="KEY_ID", help="revoke an existing key by id")
    args = ap.parse_args()
    if args.revoke:
        api_keys.revoke_key(args.revoke)
        print(f"Revoked key {args.revoke}")
        return 0
    if not args.name:
        ap.error("--name is required")
    out = api_keys.issue_key(args.name, args.email, args.rate_limit, args.notes)
    print(f"\nKey id     : {out['id']}\nPrefix     : {out['prefix']}…\nRate limit : {args.rate_limit}/min\n")
    print("API key (shown once – store it securely):\n")
    print(f"    {out['plaintext']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

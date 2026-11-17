"""
Load the 196-country reference list (data/countries.json) into policy_repo_countries.

The app needs these rows for the country pick-lists, names on every page, the map and the IMF PIMA refresh.
Run it once on a NEW Unity Catalog setup (if you migrated from the old Postgres database, `migrate_from_postgres.py`
already copied the countries table and you can skip this).

    python scripts/seed_countries.py              # insert the countries that are missing (idempotent)
    python scripts/seed_countries.py --update     # also refresh name / GDP / public-investment values of existing rows

To change a value permanently, edit data/countries.json and run this script with --update.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import constants as C  # noqa: E402
from utils import db  # noqa: E402
from utils.db import table  # noqa: E402

BATCH = 50


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="also update rows that already exist")
    args = ap.parse_args()

    data = json.loads(C.COUNTRIES_JSON.read_text(encoding="utf-8"))
    have = {str(r["iso3"]).strip() for r in db.query(f"SELECT iso3 FROM {table('countries')}")}
    rows = [{"iso3": c["iso3"], "name": c["name"], "gdp_2024_bn": c["gdpBn"], "pub_inv_pct_gdp": c["pubInvPctGdp"],
             "pub_inv_2024_bn": c["pubInvBn"]} for c in data]
    new = [r for r in rows if r["iso3"] not in have]

    for i in range(0, len(new), BATCH):
        part = new[i:i + BATCH]
        params, groups = {}, []
        for j, r in enumerate(part):
            groups.append("(" + ",".join(f":{k}_{j}" for k in r) + ")")
            params.update({f"{k}_{j}": v for k, v in r.items()})
        db.execute(f"INSERT INTO {table('countries')} ({', '.join(part[0])}) VALUES {','.join(groups)}", params)

    updated = 0
    if args.update:
        for r in rows:
            if r["iso3"] in have:
                db.update("countries", r["iso3"], {k: v for k, v in r.items() if k != "iso3"}, id_col="iso3")
                updated += 1
    db.invalidate("")
    total = db.query(f"SELECT COUNT(*) AS n FROM {table('countries')}")[0]["n"]
    print(f"countries: {len(new)} inserted, {updated} updated, {total} in {table('countries')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
Create the Unity Catalog tables (Delta) for the app.

    python scripts/create_tables.py            # creates the prd_mega.sgpbpi163.policy_repo_* tables + the policy_repo_documents volume (needs DATABRICKS_* env vars)
    python scripts/create_tables.py --print    # just print the DDL
    PIM_DB_BACKEND=sqlite python scripts/create_tables.py   # local demo database

The same DDL is also available as plain SQL in sql/uc_tables.sql.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import constants as C  # noqa: E402
from utils import db  # noqa: E402
from utils.schema import all_ddl  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="print DDL instead of executing it")
    args = ap.parse_args()
    dialect = "sqlite" if C.DB_BACKEND == "sqlite" else "databricks"
    statements = all_ddl(dialect)
    if dialect == "databricks":
        statements.append(f"CREATE VOLUME IF NOT EXISTS {C.DATABRICKS_CATALOG}.{C.DATABRICKS_SCHEMA}.{C.UC_VOLUME} "
                          "COMMENT 'Policy documents (PDF / DOCX / TXT) – replaces Vercel Blob'")
    if args.print:
        print(";\n\n".join(statements) + ";")
        return
    for stmt in statements:
        name = stmt.split("(")[0].split("COMMENT")[0].replace("CREATE TABLE IF NOT EXISTS", "").replace("CREATE VOLUME IF NOT EXISTS", "").strip()
        print(f"creating {name} …")
        db.execute(stmt)
    print("done.")


if __name__ == "__main__":
    main()

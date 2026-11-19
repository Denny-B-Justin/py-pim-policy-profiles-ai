"""Generate notebooks/01_backfill_document_chunks.py from the template + the app's shared code.

    python tools/build_notebook.py            (run from the repository root)
"""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
TEMPLATE = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "tools" / "notebook_template.py"
OUT = ROOT / "notebooks" / "01_backfill_document_chunks.py"
sys.path.insert(0, str(ROOT))

import constants as C  # noqa: E402
from utils.schema import SCHEMA, ddl  # noqa: E402

# 1. loader part of utils/extract.py (verbatim source of the named top-level definitions)
extract_src = (ROOT / "utils" / "extract.py").read_text(encoding="utf-8")
tree = ast.parse(extract_src)
lines = extract_src.splitlines()
wanted = {"_CONTROL", "clean", "ext_of", "load_pages", "_docx_text", "_xlsx_text"}
parts = []
for node in tree.body:
    names = {node.name} if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else \
        {t.id for t in getattr(node, "targets", []) if isinstance(t, ast.Name)}
    if names & wanted:
        parts.append("\n".join(lines[node.lineno - 1: node.end_lineno]))
extract_code = "import io\nimport re\n\n" + "\n\n\n".join(parts)

# 2. utils/chunking.py, whole file
chunk_code = (ROOT / "utils" / "chunking.py").read_text(encoding="utf-8")
# `from __future__` must be the first statement of a cell – not needed on Python 3.10+ (DBR 13+), so drop it
chunk_code = chunk_code.replace("from __future__ import annotations\n", "")
shared = ("# ============ BEGIN SHARED CODE – generated from utils/extract.py + utils/chunking.py, do not edit ============\n"
          "# ---- utils/extract.py (loaders) ----\n" + extract_code + "\n\n\n# ---- utils/chunking.py ----\n" + chunk_code.rstrip()
          + "\n# ============ END SHARED CODE ============")

type_map = {"STRING": "string", "INT": "int", "BIGINT": "bigint", "TIMESTAMP": "timestamp"}
expected = {c: type_map[t.split()[0]] for c, t in SCHEMA["document_chunks"]["cols"]}

nb = TEMPLATE.read_text(encoding="utf-8")
nb = nb.replace("# @@SHARED_CODE@@", shared)
nb = nb.replace("@@DDL_CHUNKS@@", ddl("document_chunks").replace(f"{C.DATABRICKS_CATALOG}.{C.DATABRICKS_SCHEMA}.", "{CATALOG}.{SCHEMA}."))
nb = nb.replace("@@DDL_LOG@@", ddl("document_ingest_log").replace(f"{C.DATABRICKS_CATALOG}.{C.DATABRICKS_SCHEMA}.", "{CATALOG}.{SCHEMA}."))
nb = nb.replace("@@EXPECTED_COLUMNS@@", json.dumps(expected, indent=4))
nb = nb.replace('DDL_CHUNKS = """', 'DDL_CHUNKS = f"""').replace('DDL_LOG = """', 'DDL_LOG = f"""')
assert "@@" not in nb, "unfilled placeholder"
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(nb, encoding="utf-8")
print("wrote", OUT, f"({len(nb.splitlines())} lines)")

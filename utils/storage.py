"""
File storage – upload only.

Production : a folder in a Unity Catalog **Volume** (constants.FILES_ROOT), written through the Databricks
             Files API (databricks-sdk, OAuth M2M with the same service principal as the SQL connection).
Local demo : a plain folder (PIM_DB_BACKEND=sqlite → PIM_LOCAL_STORAGE).

The app only ever *uploads* to the volume: when a user attaches a document it is stored under
    <FILES_ROOT>/documents/upload-documents/<ISO3>_<Country>/<National|Subnational>/<id8>_<file name>
and nothing is read, listed, downloaded or deleted from there afterwards.  Search works on the chunks the app
writes to the chunks table at upload time (utils/indexer.py), never on the stored files.
"""

from __future__ import annotations

import io
import re
import threading
from pathlib import Path

import constants as C

_client = None
_client_lock = threading.Lock()


def _local() -> bool:
    return C.DB_BACKEND == "sqlite"


def _w():
    """Lazily-created Databricks WorkspaceClient (M2M OAuth)."""
    global _client
    with _client_lock:
        if _client is None:
            from databricks.sdk import WorkspaceClient

            if not (C.DATABRICKS_SERVER_HOSTNAME and C.DATABRICKS_CLIENT_ID and C.DATABRICKS_CLIENT_SECRET):
                raise RuntimeError("Databricks credentials are not configured (see .env.sample).")
            _client = WorkspaceClient(host=f"https://{C.DATABRICKS_SERVER_HOSTNAME}", client_id=C.DATABRICKS_CLIENT_ID,
                                      client_secret=C.DATABRICKS_CLIENT_SECRET)
        return _client


def _vol_path(key: str, area: str) -> str:
    return f"{C.FILES_ROOT}/{area}/{key.lstrip('/')}"


def _local_path(key: str, area: str) -> Path:
    return C.LOCAL_STORAGE_DIR / area / key.lstrip("/")


def safe_name(name: str | None) -> str:
    base = (name or "document").strip().replace("\\", "/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:120] or "document"


# ── upload layout ────────────────────────────────────────────────────────────
def _segment(text: str | None, limit: int, fallback: str) -> str:
    """One folder / file-name part: letters, digits, '.', '-' and '_' only (safe in volumes and on Windows)."""
    s = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9.-]+", "_", (text or "").strip())).strip("_.")
    return s[:limit].rstrip("_.") or fallback


def _file_part(file_name: str | None, limit: int = 80) -> str:
    base = (file_name or "document").replace("\\", "/").split("/")[-1]
    stem, dot, ext = base.rpartition(".")
    if not dot or len(ext) > 5:
        stem, ext = base, ""
    return _segment(stem, limit, "document") + (f".{_segment(ext, 5, '')}" if ext else "")


def upload_key(record: dict, country_name: str | None, doc_id: str, file_name: str | None) -> str:
    """Where an uploaded document goes (relative to C.DOCUMENTS_DIR):
    <ISO3>_<Country>/<National|Subnational>/<id8>_<name>"""
    iso3 = (record.get("country") or "XXX").upper()
    level = "Subnational" if (record.get("jurisdiction_level") or "").lower() == "subnational" else "National"
    return "/".join([f"{iso3}_{_segment(country_name, 40, iso3)}", level, f"{doc_id[:8]}_{_file_part(file_name)}"])


def location(key: str, area: str = C.DOCUMENTS_DIR) -> str:
    """Full path of a key, for messages and the informational `blob_url` column."""
    return (_local_path(key, area).as_posix() if _local() else _vol_path(key, area)).rstrip("/")


def upload_path(blob_url: str | None) -> str | None:
    """The key recorded in a documents row's `blob_url` (None for rows without an app upload).  String parsing only."""
    prefix = location("") + "/"
    stored = (blob_url or "").strip()
    return stored[len(prefix):] if stored.startswith(prefix) else None


def save(key: str, data: bytes | io.IOBase, area: str = C.DOCUMENTS_DIR) -> int:
    """Upload bytes / a file object under `key`; returns the size in bytes."""
    if isinstance(data, (bytes, bytearray)):
        size = len(data)
        stream: io.IOBase = io.BytesIO(bytes(data))
    else:
        stream = data
        pos = stream.tell()
        stream.seek(0, io.SEEK_END)
        size = stream.tell() - pos
        stream.seek(pos)
    if _local():
        dest = _local_path(key, area)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            while chunk := stream.read(1024 * 1024):
                f.write(chunk)
        return size
    _w().files.upload(_vol_path(key, area), stream, overwrite=True)
    return size

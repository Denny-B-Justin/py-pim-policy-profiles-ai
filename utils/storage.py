"""
File storage – replaces Vercel Blob (`web/lib/storage.ts`).

Production : a folder in a Unity Catalog **Volume** (constants.FILES_ROOT), accessed through the Databricks
             Files API (databricks-sdk, OAuth M2M with the same service principal as the SQL connection).
Local demo : a plain folder (PIM_DB_BACKEND=sqlite → PIM_LOCAL_STORAGE).

Two areas below the root:
    documents/country-files/    the policy documents, filed by country / jurisdiction / year / tier / language
                                (`repository_key`).  The documents table has no path column: a document's file is
                                found through `document_path(doc)` – the row's `blob_url` when it holds a path in
                                this area (uploads made by the app), else the repository manifest (_manifest.json,
                                {document id: path}) written by scripts/copy_documents_to_volume.py.
    chunks/                     the search index, one JSON file per document (utils/indexer.py).
Every function takes a key relative to an area (`area=C.DOCUMENTS_DIR` by default).
Never a public URL: files are served through the app's own `/api/download/<doc_id>` route.
"""

from __future__ import annotations

import io
import logging
import re
import json
import threading
import time
from pathlib import Path
from typing import Iterator

import constants as C

log = logging.getLogger(__name__)

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


# ── repository layout ────────────────────────────────────────────────────────
_TIER_FOLDERS = {1: "Tier1_Primary-Legislation", 2: "Tier2_Secondary-Regulations", 3: "Tier3_Guidelines",
                 4: "Tier4_Strategies"}


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


def repository_key(record: dict, country_name: str | None, doc_id: str, lang_type: str, lang_label: str | None,
                   file_name: str | None) -> str:
    """Where a document is filed in the repository (relative to C.DOCUMENTS_DIR):
    <ISO3>_<Country>/<National|Subnational_<entity>|Supranational>/<Year>/<Tier>/<ENG|ORI_<language>>/<id8>_<name>"""
    iso3 = (record.get("country") or "XXX").upper()
    level = (record.get("jurisdiction_level") or "national").lower()
    if level == "subnational":
        jur = "Subnational_" + _segment(record.get("jurisdiction_name"), 40, "Unspecified")
    else:
        jur = "Supranational" if level == "supranational" else "National"
    try:
        year = str(int(record.get("year")))
    except (TypeError, ValueError):
        year = "UnknownYear"
    try:
        tier = _TIER_FOLDERS.get(int(record.get("policy_guidance_tier")), "Unclassified")
    except (TypeError, ValueError):
        tier = "Unclassified"
    lang = "ENG" if lang_type == "ENG" else "ORI_" + _segment(lang_label, 30, "Original")
    return "/".join([f"{iso3}_{_segment(country_name, 40, iso3)}", jur, year, tier, lang,
                     f"{doc_id[:8]}_{_file_part(file_name)}"])


NOT_IN_REPOSITORY = "The document's file is not in the repository yet (it has not been transferred to the volume)."

_manifest: dict = {"data": None, "expires": 0.0}
_manifest_lock = threading.Lock()


def manifest() -> dict[str, str]:
    """{document id: key} of the repository (cached; {} while the manifest has not been uploaded)."""
    now = time.monotonic()
    with _manifest_lock:
        if _manifest["data"] is not None and now < _manifest["expires"]:
            return _manifest["data"]
        try:
            data = json.loads(read(C.DOCUMENT_MANIFEST).decode("utf-8"))
            data = data.get("documents", data) if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001 - not uploaded yet / unreadable → no migrated files resolvable
            log.info("document manifest not available (%s)", type(exc).__name__)
            data = {}
        _manifest.update(data=data, expires=now + C.DOCUMENT_MANIFEST_SECONDS)
        return data


def invalidate_manifest() -> None:
    with _manifest_lock:
        _manifest.update(data=None, expires=0.0)


def document_path(doc: dict) -> str | None:
    """Key of a document's file in the repository, or None when it is not there (not transferred yet)."""
    prefix = location("") + "/"
    stored = (doc.get("blob_url") or "").strip()
    if stored.startswith(prefix):               # uploaded through the app: the row records where it went
        return stored[len(prefix):]
    return manifest().get(str(doc.get("id")))


def location(key: str, area: str = C.DOCUMENTS_DIR) -> str:
    """Full path of a key, for messages and the informational `blob_url` column."""
    return (_local_path(key, area).as_posix() if _local() else _vol_path(key, area)).rstrip("/")


def save(key: str, data: bytes | io.IOBase, area: str = C.DOCUMENTS_DIR) -> int:
    """Store bytes / a file object under `key`; returns the size in bytes."""
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


def read(key: str, area: str = C.DOCUMENTS_DIR) -> bytes:
    if _local():
        return _local_path(key, area).read_bytes()
    resp = _w().files.download(_vol_path(key, area))
    return resp.contents.read()


def stream(key: str, chunk_size: int = 1024 * 256, area: str = C.DOCUMENTS_DIR) -> Iterator[bytes]:
    if _local():
        with open(_local_path(key, area), "rb") as f:
            while chunk := f.read(chunk_size):
                yield chunk
        return
    resp = _w().files.download(_vol_path(key, area))
    body = resp.contents
    try:
        while chunk := body.read(chunk_size):
            yield chunk
    finally:
        body.close()


def exists(key: str, area: str = C.DOCUMENTS_DIR) -> bool:
    if _local():
        return _local_path(key, area).exists()
    try:
        _w().files.get_metadata(_vol_path(key, area))
        return True
    except Exception:  # noqa: BLE001
        return False


def delete(key: str | None, area: str = C.DOCUMENTS_DIR) -> None:
    """Idempotent delete (missing file = success)."""
    if not key:
        return
    try:
        if _local():
            _local_path(key, area).unlink(missing_ok=True)
        else:
            _w().files.delete(_vol_path(key, area))
    except Exception as exc:  # noqa: BLE001
        if "not found" not in str(exc).lower() and "does not exist" not in str(exc).lower():
            log.warning("storage delete failed for %s: %s", key, exc)


def list_files(area: str) -> list[dict]:
    """Files directly inside an area: [{name, size, modified}] (`modified` = epoch ms / ns, only compared)."""
    if _local():
        d = C.LOCAL_STORAGE_DIR / area
        if not d.exists():
            return []
        return [{"name": f.name, "size": f.stat().st_size, "modified": f.stat().st_mtime_ns}
                for f in d.iterdir() if f.is_file()]
    try:
        entries = _w().files.list_directory_contents(f"{C.FILES_ROOT}/{area}")
        return [{"name": e.name, "size": e.file_size, "modified": e.last_modified}
                for e in entries if not e.is_directory]
    except Exception as exc:  # noqa: BLE001 - a folder that was never written to does not exist yet
        if "not found" in str(exc).lower() or "does not exist" in str(exc).lower():
            return []
        raise

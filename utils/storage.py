"""
Document file storage – replaces Vercel Blob (`web/lib/storage.ts`).

Production : a Unity Catalog **Volume**  /Volumes/<catalog>/<schema>/<volume>/…
             accessed through the Databricks Files API (databricks-sdk, OAuth M2M with the same
             service principal as the SQL connection).
Local demo : a plain folder (PIM_DB_BACKEND=sqlite → PIM_LOCAL_STORAGE).

`policy_repo_documents.file_path` stores the *key* (path relative to the volume root), e.g.
    LTU/6f1c…-law-on-budget.pdf
Never a public URL: files are served through the app's own `/api/download/<doc_id>` route.
"""

from __future__ import annotations

import io
import logging
import re
import threading
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


def _vol_path(key: str) -> str:
    return f"{C.UC_VOLUME_ROOT}/{key.lstrip('/')}"


def safe_name(name: str | None) -> str:
    base = (name or "document").strip().replace("\\", "/").split("/")[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:120] or "document"


def make_key(country: str, doc_id: str, file_name: str | None) -> str:
    return f"{(country or 'XXX').upper()}/{doc_id}-{safe_name(file_name)}"


def save(key: str, data: bytes | io.IOBase) -> int:
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
        dest = C.LOCAL_STORAGE_DIR / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            while chunk := stream.read(1024 * 1024):
                f.write(chunk)
        return size
    _w().files.upload(_vol_path(key), stream, overwrite=True)
    return size


def read(key: str) -> bytes:
    if _local():
        return (C.LOCAL_STORAGE_DIR / key).read_bytes()
    resp = _w().files.download(_vol_path(key))
    return resp.contents.read()


def stream(key: str, chunk_size: int = 1024 * 256) -> Iterator[bytes]:
    if _local():
        with open(C.LOCAL_STORAGE_DIR / key, "rb") as f:
            while chunk := f.read(chunk_size):
                yield chunk
        return
    resp = _w().files.download(_vol_path(key))
    body = resp.contents
    try:
        while chunk := body.read(chunk_size):
            yield chunk
    finally:
        body.close()


def exists(key: str) -> bool:
    if _local():
        return (C.LOCAL_STORAGE_DIR / key).exists()
    try:
        _w().files.get_metadata(_vol_path(key))
        return True
    except Exception:  # noqa: BLE001
        return False


def delete(key: str | None) -> None:
    """Idempotent delete (missing file = success)."""
    if not key:
        return
    try:
        if _local():
            Path(C.LOCAL_STORAGE_DIR / key).unlink(missing_ok=True)
        else:
            _w().files.delete(_vol_path(key))
    except Exception as exc:  # noqa: BLE001
        if "not found" not in str(exc).lower() and "does not exist" not in str(exc).lower():
            log.warning("storage delete failed for %s: %s", key, exc)

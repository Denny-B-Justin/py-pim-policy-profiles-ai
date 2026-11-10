"""
Background jobs with file-persisted state.

Posit Connect (and most reverse proxies) cut HTTP requests that run for minutes, and the mAI gateway has
no streaming – so long operations (briefing generation, batch imports, re-indexing, translation) run in a
worker thread while the page *polls* the job state (`dcc.Interval`).  State lives in small JSON files
(`PIM_JOBS_DIR`, default: system temp dir) so any worker process on the same host can answer the poll.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

JOBS_DIR = Path(os.environ.get("PIM_JOBS_DIR") or Path(tempfile.gettempdir()) / "pim_jobs")
JOBS_DIR.mkdir(parents=True, exist_ok=True)
_pool = ThreadPoolExecutor(max_workers=int(os.environ.get("PIM_JOB_WORKERS", "6")), thread_name_prefix="pim-job")
_lock = threading.Lock()
_TTL = 6 * 3600


def _path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _write(job_id: str, data: dict) -> None:
    tmp = _path(job_id).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, default=str), encoding="utf-8")
    os.replace(tmp, _path(job_id))


def get(job_id: str | None) -> dict | None:
    if not job_id or not all(c.isalnum() or c == "-" for c in job_id):
        return None
    try:
        return json.loads(_path(job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def update(job_id: str, **fields: Any) -> None:
    with _lock:
        cur = get(job_id) or {}
        cur.update(fields)
        cur["updated"] = time.time()
        _write(job_id, cur)


def submit(kind: str, fn: Callable[..., Any], *args, **kwargs) -> str:
    """Run `fn(job_id, *args, **kwargs)` in a worker thread; fn may call `update(job_id, progress=…)`.

    The return value of fn is stored as `result` (must be JSON-serialisable)."""
    job_id = uuid.uuid4().hex
    _write(job_id, {"id": job_id, "kind": kind, "status": "running", "created": time.time(), "updated": time.time(),
                    "progress": None, "result": None, "error": None})
    _cleanup()

    def run():
        try:
            res = fn(job_id, *args, **kwargs)
            update(job_id, status="done", result=res)
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s (%s) failed", job_id, kind)
            update(job_id, status="error", error=getattr(exc, "user_message", None) or str(exc) or type(exc).__name__)

    _pool.submit(run)
    return job_id


def run_background(fn: Callable[..., Any], *args, **kwargs) -> None:
    """Fire-and-forget (the `after()` hooks of the original): errors are logged, never raised."""
    def run():
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            log.exception("background task %s failed", getattr(fn, "__name__", fn))
    _pool.submit(run)


def _cleanup() -> None:
    now = time.time()
    try:
        for p in JOBS_DIR.glob("*.json"):
            if now - p.stat().st_mtime > _TTL:
                p.unlink(missing_ok=True)
    except OSError:
        pass

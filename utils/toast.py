"""Toast helper – callbacks return `toast("success", "Saved")` into `Output("toast", "data", allow_duplicate=True)`."""

from __future__ import annotations

import time


def toast(kind: str, message: str, description: str | None = None) -> dict:
    return {"type": kind, "message": message, "description": description, "t": time.time()}

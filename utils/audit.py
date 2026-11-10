"""Audit trail (port of `web/lib/audit.ts`) – never raises."""

from __future__ import annotations

import logging

from utils import db

log = logging.getLogger(__name__)


def log_audit(user: dict | None, action: str, entity: str, entity_id: str | None = None,
              detail: str | None = None, ip: str | None = None) -> None:
    try:
        db.insert("audit_logs", {
            "id": db.uid(), "user_id": (user or {}).get("id"), "user_email": (user or {}).get("email"),
            "action": action, "entity": entity, "entity_id": entity_id,
            "detail": (detail or "")[:2000] or None, "ip": ip, "created_at": db.now(),
        })
    except Exception:  # noqa: BLE001
        log.exception("audit log failed")

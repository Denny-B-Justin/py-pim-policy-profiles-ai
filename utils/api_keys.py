"""
Partner API keys for `/api/v1/*` – port of `web/lib/api-keys.ts`.

Keys are issued by the operator (`python scripts/issue_api_key.py --name "Acme"`) and only their SHA-256
hash is stored.  Format: `pim_v1_<32 hex chars>`.
"""

from __future__ import annotations

import hashlib
import secrets

from utils import db
from utils.db import table

KEY_PREFIX = "pim_v1_"
KEY_RANDOM_HEX_CHARS = 32       # 128 bits of entropy


def generate_plaintext_key() -> str:
    return KEY_PREFIX + secrets.token_hex(KEY_RANDOM_HEX_CHARS // 2)


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def validate_api_key(header_value: str | None) -> dict | None:
    """Active partner row {id, name, rate_limit_per_minute, key_prefix} for a valid X-API-Key, else None."""
    if not header_value:
        return None
    key = header_value.strip()
    if not key.startswith(KEY_PREFIX) or len(key) < len(KEY_PREFIX) + 16:
        return None
    rows = db.query(f"SELECT id, name, rate_limit_per_minute, key_prefix, active, revoked_at "
                    f"FROM {table('partner_api_keys')} WHERE key_hash = :h", {"h": hash_key(key)})
    if not rows:
        return None
    row = rows[0]
    if int(row["active"] or 0) != 1 or row.get("revoked_at") is not None:
        return None
    try:   # fire-and-forget bookkeeping – never blocks / fails the request
        db.execute(f"UPDATE {table('partner_api_keys')} SET last_used_at = :t WHERE id = :i",
                   {"t": db.now(), "i": row["id"]})
    except Exception:  # noqa: BLE001
        pass
    return {"id": row["id"], "name": row["name"], "rate_limit_per_minute": int(row["rate_limit_per_minute"]),
            "key_prefix": row["key_prefix"]}


def issue_key(name: str, contact_email: str | None = None, rate_limit_per_minute: int = 60,
              notes: str | None = None) -> dict:
    """Insert a new key; returns {plaintext, id, prefix} – show the plaintext ONCE and discard it."""
    plaintext = generate_plaintext_key()
    row = {"id": db.uid(), "key_hash": hash_key(plaintext), "key_prefix": plaintext[:12], "name": name,
           "contact_email": contact_email, "rate_limit_per_minute": int(rate_limit_per_minute), "active": 1,
           "created_at": db.now(), "last_used_at": None, "revoked_at": None, "notes": notes}
    db.insert("partner_api_keys", row)
    return {"plaintext": plaintext, "id": row["id"], "prefix": row["key_prefix"]}


def revoke_key(key_id: str) -> None:
    db.update("partner_api_keys", key_id, {"revoked_at": db.now(), "active": 0})

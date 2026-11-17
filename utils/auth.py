"""
Authentication.

Replaces NextAuth (Google + email/password).  Identity sources, in priority order:

1. **Posit Connect** – when the content is published with "Who can view: All users – login
   required" (or any restricted audience) Connect adds the `RStudio-Connect-Credentials`
   header (JSON: {"user": "<username>", "groups": [...]}) to every request.  Those users are
   treated as signed in – nothing else to configure.
2. **Local account** – email + bcrypt password hash in the `policy_repo_users` UC table, kept in the
   signed Flask session cookie (same rules as the original: the first account bootstraps the
   admin, later accounts need `ADMIN_INVITE_CODE`).

Mutating actions call `require_user()`; read-only pages stay public like the original app.
"""

from __future__ import annotations

import json
import re
from typing import Any

import bcrypt
from flask import request, session

import constants as C
from utils import db
from utils.db import table

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def client_ip() -> str | None:
    try:
        fwd = request.headers.get("X-Forwarded-For", "")
        return (fwd.split(",")[0].strip() or request.headers.get("X-Real-IP") or request.remote_addr)
    except RuntimeError:  # no request context
        return None


def connect_user() -> dict | None:
    """User injected by Posit Connect (None when anonymous or not on Connect)."""
    raw = request.headers.get("RStudio-Connect-Credentials")
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    username = (data.get("user") or "").strip()
    if not username:
        return None
    return {"id": f"connect:{username}", "name": username, "email": username, "image": None,
            "source": "connect", "groups": data.get("groups") or []}


def current_user() -> dict | None:
    try:
        u = connect_user()
        if u:
            return u
        return session.get("user")
    except RuntimeError:  # outside a request
        return None


def require_user() -> dict | None:
    """Alias that documents intent: returns the user or None (caller shows the 'sign in' message)."""
    return current_user()


# ── local accounts ───────────────────────────────────────────────────────────
def _find_user(email: str) -> dict | None:
    rows = db.query(f"SELECT * FROM {table('users')} WHERE LOWER(email) = :e", {"e": email.strip().lower()})
    return rows[0] if rows else None


def authenticate(email: str, password: str) -> dict | None:
    user = _find_user(email or "")
    if not user or not user.get("password_hash"):
        return None
    try:
        ok = bcrypt.checkpw(password.encode(), str(user["password_hash"]).encode())
    except ValueError:  # corrupted hash
        return None
    if not ok:
        return None
    return {"id": user["id"], "name": user.get("name"), "email": user.get("email"), "image": user.get("image"),
            "source": "local"}


def sign_in(user: dict) -> None:
    session["user"] = user
    session.permanent = True


def sign_out() -> None:
    session.pop("user", None)


def register(name: str | None, email: str, password: str, invite_code: str | None) -> tuple[bool, str]:
    """Port of POST /api/auth/register.  Returns (ok, error_message)."""
    email = (email or "").strip()
    invite_code = (invite_code or "").strip()
    if not _EMAIL_RE.match(email):
        return False, "A valid email is required."
    if not isinstance(password, str) or len(password) < 8:
        return False, "Password must be at least 8 characters."
    count = int(db.query(f"SELECT COUNT(*) AS n FROM {table('users')}")[0]["n"])
    if count > 0:
        expected = C.ADMIN_INVITE_CODE.strip()
        if not expected or invite_code != expected:
            return False, "Registration is closed. Ask an administrator for an invite code."
    if _find_user(email):
        return False, "An account with this email already exists."
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(10)).decode()
    db.insert("users", {"id": db.uid(), "name": (name or "").strip() or None, "email": email, "email_verified": None,
                        "image": None, "password_hash": pw_hash, "created_at": db.now()})
    return True, ""


def safe_callback(url: str | None) -> str:
    """Only allow in-app relative redirects after sign-in."""
    if url and url.startswith("?page="):
        return url
    return "?page=home"


def write_guard() -> tuple[dict | None, str | None]:
    """Gate for every mutating callback: signed-in user + the 30 writes/min/IP limit of the original.

    Returns (user, error_message); error_message is None when the call may proceed."""
    from utils.ratelimit import rate_limit  # local import: ratelimit is dependency-free, keeps import order simple

    user = current_user()
    if not user:
        return None, "Sign in to make changes."
    allowed, retry = rate_limit(f"write:{client_ip() or user['id']}", 30, 60)
    if not allowed:
        return None, f"Too many requests. Try again in {retry}s."
    return user, None

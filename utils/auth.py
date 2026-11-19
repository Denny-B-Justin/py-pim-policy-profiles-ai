"""
Authentication.

Replaces NextAuth (Google + email/password).  Identity sources, in priority order:

1. **Posit Connect** – when the content is published with "Who can view: All users – login
   required" (or any restricted audience) Connect adds the `RStudio-Connect-Credentials`
   header (JSON: {"user": "<username>", "groups": [...]}) to every request.  Those users are
   treated as signed in – nothing else to configure.
2. **Local account** – a row of `policy_repo_users` (id, name, email, email_verified, image, password_hash).
   The first account can be created freely, later accounts need `ADMIN_INVITE_CODE`.

How local accounts are protected:
  * Passwords are hashed with **bcrypt** (cost C.BCRYPT_ROUNDS, per-password salt) and never stored, logged or
    cached.  Hashes made with a lower cost are upgraded on the next successful sign-in.
  * In transit they are protected by **HTTPS** (TLS, terminated by Posit Connect); the session cookie is
    `Secure` + `HttpOnly` + `SameSite=Lax`, signed with PIM_SECRET_KEY.
  * The cookie holds only the user id and an HMAC of the stored hash – no email, name or hash.  Every request
    re-checks it against the table (cached C.SESSION_RECHECK_SECONDS), so deleting a user or changing their
    password ends all of their sessions.
  * Unknown e-mails cost the same bcrypt work as wrong passwords (no account enumeration by timing); sign-in is
    rate limited per IP and per account.
  * Credential lookups always read the table (never the query cache), like `persistent=False` lookups.

With AUTH_ENABLED=false (C.AUTH_ENABLED) local sign-in is off: the sign-in / register pages answer 404 and every
visitor who is not a Connect user acts as GUEST, so writes are allowed for anyone who can open the app.

Mutating actions call `write_guard()`; read-only pages stay public like the original app.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import logging
import re

import bcrypt
from flask import request, session

import constants as C
from utils import db
from utils.db import table

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD = 8
_MAX_PASSWORD_BYTES = 72        # bcrypt only uses 72 bytes; bcrypt >= 5 refuses longer input
_USER_COLS = "id, name, email, image, password_hash"
_SESSION_KEY = "auth"
GUEST = {"id": "guest", "name": "Guest", "email": None, "image": None, "source": "guest"}


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
        if not C.AUTH_ENABLED:
            return GUEST
        return _session_user()
    except RuntimeError:  # outside a request
        return None


# ── password hashing ─────────────────────────────────────────────────────────
@functools.cache
def _dummy_hash() -> bytes:
    """A real hash to check against when the e-mail is unknown, so both cases take the same time."""
    return bcrypt.hashpw(b"pim-timing-equaliser", bcrypt.gensalt(C.BCRYPT_ROUNDS))


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(C.BCRYPT_ROUNDS)).decode()


def _check_password(password: str, pw_hash: str | None) -> bool:
    if len(password.encode()) > _MAX_PASSWORD_BYTES:
        return False
    try:
        if not pw_hash:                                  # unknown e-mail: same bcrypt work, always fails
            bcrypt.checkpw(password.encode(), _dummy_hash())
            return False
        return bcrypt.checkpw(password.encode(), pw_hash.encode())
    except ValueError:  # corrupted hash
        return False


def _cost(pw_hash: str) -> int:
    try:
        return int(pw_hash.split("$")[2])
    except (IndexError, ValueError):
        return 0


def _fingerprint(pw_hash: str) -> str:
    """HMAC of the stored hash: changes when the password changes, reveals nothing about it."""
    key = (C.SECRET_KEY or "pim-session").encode()
    return hmac.new(key, pw_hash.encode(), hashlib.sha256).hexdigest()[:32]


# ── local accounts ───────────────────────────────────────────────────────────
def _find_user(email: str) -> dict | None:
    rows = db.query(f"SELECT {_USER_COLS} FROM {table('users')} WHERE LOWER(email) = :e",
                    {"e": email.strip().lower()})
    return rows[0] if rows else None


def _user_by_id(user_id: str) -> dict | None:
    """Session re-check (cached briefly; any write to the users table clears it)."""
    def load():
        rows = db.query(f"SELECT {_USER_COLS} FROM {table('users')} WHERE id = :i", {"i": user_id})
        return rows[0] if rows else None
    return db.cached(f"users:session:{user_id}", C.SESSION_RECHECK_SECONDS, load)


def _public(row: dict) -> dict:
    return {"id": row["id"], "name": row.get("name"), "email": row.get("email"), "image": row.get("image"),
            "source": "local"}


def authenticate(email: str, password: str) -> dict | None:
    """The user for these credentials, or None.  Pass the result to `sign_in()`."""
    row = _find_user(email or "") if email else None
    if not _check_password(password or "", row.get("password_hash") if row else None):
        return None
    pw_hash = str(row["password_hash"])
    if _cost(pw_hash) < C.BCRYPT_ROUNDS:            # upgrade an older, cheaper hash
        try:
            pw_hash = _hash_password(password)
            db.update("users", row["id"], {"password_hash": pw_hash})
        except Exception:  # noqa: BLE001 - the old hash keeps working
            log.warning("password hash upgrade failed for user %s", row["id"], exc_info=True)
            pw_hash = str(row["password_hash"])
    return {**_public(row), "_pwv": _fingerprint(pw_hash)}


def sign_in(user: dict) -> None:
    """Start a fresh session for a user returned by `authenticate()`."""
    session.clear()                                  # nothing from an earlier session carries over
    session[_SESSION_KEY] = {"uid": user["id"], "pwv": user["_pwv"]}
    session.permanent = True


def sign_out() -> None:
    session.clear()


def _session_user() -> dict | None:
    data = session.get(_SESSION_KEY)
    if not isinstance(data, dict) or not data.get("uid"):
        return None
    try:
        row = _user_by_id(str(data["uid"]))
    except Exception:  # noqa: BLE001 - fail closed when the table cannot be read
        log.warning("session check failed", exc_info=True)
        return None
    if not row or not row.get("password_hash") or \
            not hmac.compare_digest(str(data.get("pwv") or ""), _fingerprint(str(row["password_hash"]))):
        session.pop(_SESSION_KEY, None)              # user deleted or password changed
        return None
    return _public(row)


def register(name: str | None, email: str, password: str, invite_code: str | None) -> tuple[bool, str]:
    """Port of POST /api/auth/register.  Returns (ok, error_message)."""
    email = (email or "").strip().lower()
    invite_code = (invite_code or "").strip()
    if not _EMAIL_RE.match(email):
        return False, "A valid email is required."
    if not isinstance(password, str) or len(password) < _MIN_PASSWORD:
        return False, f"Password must be at least {_MIN_PASSWORD} characters."
    if len(password.encode()) > _MAX_PASSWORD_BYTES:
        return False, f"Password must be at most {_MAX_PASSWORD_BYTES} bytes."
    count = int(db.query(f"SELECT COUNT(*) AS n FROM {table('users')}")[0]["n"])
    if count > 0:
        expected = C.ADMIN_INVITE_CODE.strip()
        if not expected or not hmac.compare_digest(invite_code.encode(), expected.encode()):
            return False, "Registration is closed. Ask an administrator for an invite code."
    if _find_user(email):
        return False, "An account with this email already exists."
    db.insert("users", {"id": db.uid(), "name": (name or "").strip() or None, "email": email, "email_verified": None,
                        "image": None, "password_hash": _hash_password(password)})
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

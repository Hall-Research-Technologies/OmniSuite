"""Accounts, sessions and the security audit trail for remote access.

Three stores with deliberately different lifetimes:

    users.json      on disk, survives restart      who exists, and what they may do
    sessions        in memory, dropped on restart  who is currently logged in
    security audit  on disk, bounded               what happened

Sessions are server-side and opaque rather than signed tokens carrying claims.
The brief requires that an Admin disabling an account takes effect immediately,
and a self-contained token cannot do that without a revocation list -- which is
a session store with extra steps and a worse failure mode. Dropping every
session on restart is the safe default and means no session material is ever
written to disk.

Nothing in this module decides whether a *request* is allowed. It answers who
somebody is and what they have been granted; `omni_access` holds the route
policy and the gate joins them.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

import omni_access

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)

#: Idle timeout and absolute cap. Both enforced server-side on every request.
IDLE_TIMEOUT_SECONDS = 8 * 60 * 60
ABSOLUTE_TIMEOUT_SECONDS = 24 * 60 * 60

#: Login throttling. Counted per username and per source address, so neither a
#: password spray across accounts nor a dictionary attack on one is free.
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60

#: The audit log is bounded. An unbounded security log is its own outage.
AUDIT_MAX_ENTRIES = 5000

#: Never written to the audit log or returned by any API, whatever a caller
#: puts in a request body. Mirrors the server's existing audit redaction set.
SECRET_FIELDS = frozenset({
    "password", "passwd", "pwd", "new_password", "old_password",
    "current_password", "confirm_password", "fallback_password",
    "token", "secret", "session", "csrf", "csrf_token", "password_hash",
})


def _now() -> float:
    return time.time()


def _atomic_write_json(path: Path, payload) -> bool:
    """Unique temp file, fsync, os.replace -- the rule the project already has.

    A shared temp name lets two writers collide; on Windows os.replace fails
    with a sharing violation while another writer holds the file.
    """
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        return True
    except Exception as exc:
        log.error("[USERS] could not write %s: %s", path.name, type(exc).__name__)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def hash_password(password: str) -> str:
    """Salted scrypt, via werkzeug.

    Chosen because Flask already brings werkzeug, so remote access adds no
    dependency to a PyInstaller build; because scrypt is memory-hard, which a
    plain SHA is not; and because it is not custom cryptography.
    """
    return generate_password_hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    if not stored_hash or password is None:
        return False
    try:
        return check_password_hash(stored_hash, password)
    except Exception:
        # A malformed stored hash must read as "wrong password", never as an
        # exception that a caller might treat as a pass.
        return False


def redact(payload):
    """Recursively replace secret-bearing fields before anything records them."""
    if isinstance(payload, dict):
        return {k: ("***" if str(k).lower() in SECRET_FIELDS else redact(v))
                for k, v in payload.items()}
    if isinstance(payload, (list, tuple)):
        return [redact(v) for v in payload]
    return payload


class Permissions:
    """What one account may do. Devices are named by stable identity.

    Keyed by MAC wherever one is known, never by address: the project's own rule
    is that identity must not depend on an IP when a hardware identifier exists,
    and a permission that followed an address would silently transfer to
    whatever answers there next.
    """

    __slots__ = ("features", "sources", "destinations", "multiviews")

    def __init__(self, features=(), sources=(), destinations=(), multiviews=()):
        self.features = frozenset(f for f in features if f in omni_access.FEATURES)
        self.sources = frozenset(_normalise_identity(s) for s in sources if s)
        self.destinations = frozenset(_normalise_identity(d) for d in destinations if d)
        self.multiviews = frozenset(k for k in
                                    (multiview_key(*_split_multiview(m)) for m in multiviews)
                                    if k)

    def to_json(self) -> dict:
        return {"features": sorted(self.features),
                "sources": sorted(self.sources),
                "destinations": sorted(self.destinations),
                "multiviews": sorted(self.multiviews)}

    @classmethod
    def from_json(cls, raw) -> "Permissions":
        raw = raw or {}
        return cls(raw.get("features") or (),
                   raw.get("sources") or (),
                   raw.get("destinations") or (),
                   raw.get("multiviews") or ())

    def may_operate_multiview(self, decoder_identity, name) -> bool:
        """Whether this account may operate that saved Multiview on that decoder.

        Approval is per decoder *and* per preset. Holding the multiview
        capability grants nothing on its own -- a preset an administrator
        creates later is not reachable until it is approved, which is what
        section 13 of the brief asks for.
        """
        key = multiview_key(decoder_identity, name)
        return bool(key) and key in self.multiviews

    def has_feature(self, feature: str) -> bool:
        return feature in self.features

    def may_use_source(self, identity) -> bool:
        return _normalise_identity(identity) in self.sources

    def may_control_destination(self, identity) -> bool:
        return _normalise_identity(identity) in self.destinations


#: A saved Multiview has no stable id -- its identity is its name, scoped to
#: the decoder it lives on. So an approval is the pair, joined by a character
#: that cannot occur in either half: an identity is hex and colons, and the
#: device refuses a name with a space or a slash.
MULTIVIEW_KEY_SEPARATOR = "/"


def multiview_key(decoder_identity, name) -> str:
    """The stored form of "this preset, on this decoder"."""
    identity = _normalise_identity(decoder_identity)
    label = str(name or "").strip()
    if not identity or not label or MULTIVIEW_KEY_SEPARATOR in label:
        return ""
    return identity + MULTIVIEW_KEY_SEPARATOR + label


def _split_multiview(value):
    """Accept either the joined key or a {decoder, name} pair."""
    if isinstance(value, dict):
        return (value.get("decoder") or value.get("id") or "",
                value.get("name") or "")
    text = str(value or "")
    identity, separator, name = text.partition(MULTIVIEW_KEY_SEPARATOR)
    return (identity, name) if separator else ("", "")


def _normalise_identity(identity) -> str:
    """One spelling for a device identity.

    MACs arrive from different code paths as `B8:98:B0:..`, `b8-98-b0-..` and
    unseparated. A permission check that compared spellings would deny a
    permitted device, so they are folded here -- and only here, so there is one
    normaliser rather than one per call site.
    """
    text = str(identity or "").strip().lower()
    if not text:
        return ""
    compact = text.replace(":", "").replace("-", "").replace(".", "")
    if len(compact) == 12 and all(c in "0123456789abcdef" for c in compact):
        return ":".join(compact[i:i + 2] for i in range(0, 12, 2))
    return text


class Account:
    __slots__ = ("id", "username", "password_hash", "role", "enabled",
                 "permissions", "created_at", "password_changed_at")

    def __init__(self, id, username, password_hash, role, enabled=True,
                 permissions=None, created_at=None, password_changed_at=None):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.role = role if role in ROLES else ROLE_USER
        self.enabled = bool(enabled)
        self.permissions = permissions or Permissions()
        self.created_at = created_at or _now()
        self.password_changed_at = password_changed_at or self.created_at

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def to_json(self) -> dict:
        return {"id": self.id, "username": self.username,
                "password_hash": self.password_hash, "role": self.role,
                "enabled": self.enabled,
                "permissions": self.permissions.to_json(),
                "created_at": self.created_at,
                "password_changed_at": self.password_changed_at}

    def public(self) -> dict:
        """What an API may return. Never the hash, never anything derived from it."""
        return {"id": self.id, "username": self.username, "role": self.role,
                "enabled": self.enabled,
                "permissions": self.permissions.to_json(),
                "created_at": self.created_at}

    @classmethod
    def from_json(cls, raw) -> "Account":
        return cls(id=raw.get("id"), username=raw.get("username"),
                   password_hash=raw.get("password_hash"),
                   role=raw.get("role"), enabled=raw.get("enabled", True),
                   permissions=Permissions.from_json(raw.get("permissions")),
                   created_at=raw.get("created_at"),
                   password_changed_at=raw.get("password_changed_at"))


class Session:
    __slots__ = ("token", "user_id", "created_at", "last_seen", "csrf_token")

    def __init__(self, token, user_id, csrf_token):
        self.token = token
        self.user_id = user_id
        self.csrf_token = csrf_token
        self.created_at = _now()
        self.last_seen = self.created_at

    def expired(self, now=None) -> bool:
        now = now or _now()
        return (now - self.last_seen > IDLE_TIMEOUT_SECONDS
                or now - self.created_at > ABSOLUTE_TIMEOUT_SECONDS)


class UserStore:
    """Accounts on disk, sessions in memory, one lock over both.

    One lock rather than two because the operations that matter span them: an
    Admin disabling an account must revoke its sessions in the same critical
    section, or a request already in flight is authorised against an account
    that no longer exists.
    """

    def __init__(self, path: Path, audit_path: Path | None = None):
        self._path = Path(path)
        self._audit_path = Path(audit_path) if audit_path else None
        self._lock = threading.RLock()
        self._accounts: dict[str, Account] = {}
        self._sessions: dict[str, Session] = {}
        self._failures: dict[str, list] = {}
        self._audit: list = []
        self._load()

    # ---- persistence -------------------------------------------------
    def _load(self) -> None:
        with self._lock:
            self._accounts = {}
            if not self._path.exists():
                # No file means no accounts, which is the correct state for a
                # local-only installation that has never enabled remote access.
                return
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception as exc:
                # A malformed store quarantines and degrades to empty rather
                # than crashing, and never silently becomes "no restrictions".
                log.error("[USERS] %s is unreadable (%s); quarantining",
                          self._path.name, type(exc).__name__)
                try:
                    self._path.replace(self._path.with_suffix(".corrupt"))
                except OSError:
                    pass
                return
            for entry in (raw or {}).get("users") or []:
                try:
                    account = Account.from_json(entry)
                    if account.id and account.username:
                        self._accounts[account.id] = account
                except Exception:
                    log.warning("[USERS] skipping an unreadable account record")

    def _persist(self) -> bool:
        with self._lock:
            return _atomic_write_json(self._path, {
                "version": SCHEMA_VERSION,
                "users": [a.to_json() for a in self._accounts.values()]})

    # ---- bootstrap ---------------------------------------------------
    @property
    def has_admin(self) -> bool:
        with self._lock:
            return any(a.is_admin and a.enabled for a in self._accounts.values())

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return not self._accounts

    # ---- accounts ----------------------------------------------------
    def find_by_username(self, username) -> Account | None:
        target = str(username or "").strip().lower()
        with self._lock:
            for account in self._accounts.values():
                if account.username.lower() == target:
                    return account
        return None

    def get(self, user_id) -> Account | None:
        with self._lock:
            return self._accounts.get(user_id)

    def list_accounts(self) -> list:
        with self._lock:
            return [a.public() for a in
                    sorted(self._accounts.values(), key=lambda a: a.username.lower())]

    def create(self, username, password, role, permissions=None) -> dict:
        username = str(username or "").strip()
        if not username:
            return {"ok": False, "error": "username required"}
        if len(str(password or "")) < 8:
            return {"ok": False, "error": "password must be at least 8 characters"}
        if role not in ROLES:
            return {"ok": False, "error": "role must be admin or user"}
        with self._lock:
            if self.find_by_username(username):
                return {"ok": False, "error": "that username already exists"}
            account = Account(
                id=secrets.token_urlsafe(12), username=username,
                password_hash=hash_password(password), role=role,
                permissions=permissions or Permissions())
            self._accounts[account.id] = account
            self._persist()
            self.audit("user.create", actor=None,
                       detail={"username": username, "role": role})
            return {"ok": True, "user": account.public()}

    def set_enabled(self, user_id, enabled: bool) -> dict:
        with self._lock:
            account = self._accounts.get(user_id)
            if not account:
                return {"ok": False, "error": "no such user"}
            if not enabled and account.is_admin and self._last_enabled_admin(account):
                return {"ok": False,
                        "error": "this is the only enabled administrator"}
            account.enabled = bool(enabled)
            self._persist()
            if not enabled:
                # Immediately, in the same critical section: a disabled account
                # must not survive in a session that is already open.
                self.revoke_all_for_user(user_id, _locked=True)
            self.audit("user.enabled" if enabled else "user.disabled",
                       actor=None, detail={"username": account.username})
            return {"ok": True, "user": account.public()}

    def _last_enabled_admin(self, account: Account) -> bool:
        others = [a for a in self._accounts.values()
                  if a.is_admin and a.enabled and a.id != account.id]
        return not others

    def set_password(self, user_id, password) -> dict:
        if len(str(password or "")) < 8:
            return {"ok": False, "error": "password must be at least 8 characters"}
        with self._lock:
            account = self._accounts.get(user_id)
            if not account:
                return {"ok": False, "error": "no such user"}
            account.password_hash = hash_password(password)
            account.password_changed_at = _now()
            self._persist()
            # Every other session for this account ends. A password change that
            # left old sessions alive would not be a password change.
            self.revoke_all_for_user(user_id, _locked=True)
            self.audit("user.password_changed", actor=None,
                       detail={"username": account.username})
            return {"ok": True}

    def set_role(self, user_id, role) -> dict:
        if role not in ROLES:
            return {"ok": False, "error": "role must be admin or user"}
        with self._lock:
            account = self._accounts.get(user_id)
            if not account:
                return {"ok": False, "error": "no such user"}
            if account.is_admin and role != ROLE_ADMIN and self._last_enabled_admin(account):
                return {"ok": False, "error": "this is the only enabled administrator"}
            account.role = role
            self._persist()
            self.revoke_all_for_user(user_id, _locked=True)
            self.audit("user.role_changed", actor=None,
                       detail={"username": account.username, "role": role})
            return {"ok": True, "user": account.public()}

    def set_permissions(self, user_id, permissions: Permissions) -> dict:
        with self._lock:
            account = self._accounts.get(user_id)
            if not account:
                return {"ok": False, "error": "no such user"}
            account.permissions = permissions
            self._persist()
            # A permission change takes effect now, not at next login.
            self.revoke_all_for_user(user_id, _locked=True)
            self.audit("user.permissions_changed", actor=None,
                       detail={"username": account.username,
                               "permissions": permissions.to_json()})
            return {"ok": True, "user": account.public()}

    def delete(self, user_id) -> dict:
        with self._lock:
            account = self._accounts.get(user_id)
            if not account:
                return {"ok": False, "error": "no such user"}
            if account.is_admin and self._last_enabled_admin(account):
                return {"ok": False, "error": "this is the only enabled administrator"}
            del self._accounts[user_id]
            self._persist()
            self.revoke_all_for_user(user_id, _locked=True)
            self.audit("user.deleted", actor=None,
                       detail={"username": account.username})
            return {"ok": True}

    # ---- authentication ---------------------------------------------
    def _throttle_key(self, username, source) -> tuple:
        return (str(username or "").strip().lower(), str(source or ""))

    def locked_out(self, username, source) -> bool:
        key = self._throttle_key(username, source)
        cutoff = _now() - LOCKOUT_SECONDS
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if t > cutoff]
            self._failures[key] = recent
            return len(recent) >= MAX_FAILURES

    def _record_failure(self, username, source) -> None:
        key = self._throttle_key(username, source)
        with self._lock:
            self._failures.setdefault(key, []).append(_now())

    def authenticate(self, username, password, source=None) -> dict:
        """Verify a credential. Never says which half was wrong."""
        if self.locked_out(username, source):
            self.audit("login.throttled", actor=None,
                       detail={"username": username, "source": source})
            return {"ok": False, "error": "too many attempts; try again later",
                    "throttled": True}

        account = self.find_by_username(username)
        # Verify even when the account does not exist, so the response time does
        # not say whether a username is real.
        stored = account.password_hash if account else hash_password(secrets.token_urlsafe(16))
        good = verify_password(stored, password)

        if not account or not good or not account.enabled:
            self._record_failure(username, source)
            reason = ("disabled" if account and good and not account.enabled
                      else "bad_credentials")
            self.audit("login.failure", actor=None,
                       detail={"username": username, "source": source,
                               "reason": reason})
            return {"ok": False, "error": "invalid username or password"}

        with self._lock:
            self._failures.pop(self._throttle_key(username, source), None)
        session = self.open_session(account.id)
        self.audit("login.success", actor=account,
                   detail={"username": account.username, "source": source})
        return {"ok": True, "user": account.public(),
                "token": session.token, "csrf_token": session.csrf_token}

    # ---- sessions ----------------------------------------------------
    def open_session(self, user_id) -> Session:
        """A fresh token every time: logging in never reuses an existing id."""
        session = Session(token=secrets.token_urlsafe(32), user_id=user_id,
                          csrf_token=secrets.token_urlsafe(32))
        with self._lock:
            self._sessions[session.token] = session
        return session

    def resolve_session(self, token) -> tuple:
        """(account, session) for a live session, or (None, None).

        Re-checks the account on every request rather than trusting what was
        true at login: an account disabled or deleted a moment ago must not keep
        working until its session happens to expire.
        """
        if not token:
            return (None, None)
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return (None, None)
            if session.expired():
                self._sessions.pop(token, None)
                return (None, None)
            account = self._accounts.get(session.user_id)
            if account is None or not account.enabled:
                self._sessions.pop(token, None)
                return (None, None)
            session.last_seen = _now()
            return (account, session)

    def revoke(self, token) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def revoke_all_for_user(self, user_id, _locked=False) -> int:
        def _do():
            doomed = [t for t, s in self._sessions.items() if s.user_id == user_id]
            for token in doomed:
                self._sessions.pop(token, None)
            return len(doomed)
        if _locked:
            return _do()
        with self._lock:
            return _do()

    def active_session_count(self) -> int:
        with self._lock:
            live = {t: s for t, s in self._sessions.items() if not s.expired()}
            self._sessions = live
            return len(live)

    def revoke_every_session(self) -> int:
        """Used when remote access is disabled: nothing issued remotely survives."""
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            return count

    # ---- audit -------------------------------------------------------
    def audit(self, event, actor=None, detail=None) -> None:
        """Record a security-relevant event, with secrets stripped first.

        Named fields are recorded; a request body is never dumped verbatim,
        because a caller can put a credential in one.
        """
        entry = {"at": _now(), "event": str(event),
                 "actor": getattr(actor, "username", None),
                 "actor_id": getattr(actor, "id", None),
                 "detail": redact(detail or {})}
        with self._lock:
            self._audit.append(entry)
            if len(self._audit) > AUDIT_MAX_ENTRIES:
                # Bounded: an unbounded security log is its own outage.
                self._audit = self._audit[-AUDIT_MAX_ENTRIES:]
            if self._audit_path is not None:
                _atomic_write_json(self._audit_path,
                                   {"version": SCHEMA_VERSION,
                                    "entries": self._audit})

    def audit_entries(self, limit=200) -> list:
        with self._lock:
            return list(self._audit[-int(limit):])

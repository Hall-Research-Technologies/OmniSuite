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

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

import omni_access
import omni_secret

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)

#: The two ways a restricted account may be given to route. "Standard" is the
#: source-and-display page with the Route button (and drag and drop, which is
#: the same operation by another gesture); "matrix" is the permission-filtered
#: grid. Which an account may use is an administrator's decision, held on the
#: account, and never inferred from what a page happens to draw.
INTERFACE_STANDARD = "standard"
INTERFACE_MATRIX = "matrix"
INTERFACES = (INTERFACE_STANDARD, INTERFACE_MATRIX)

#: Idle timeout and absolute cap. Both enforced server-side on every request.
IDLE_TIMEOUT_SECONDS = 8 * 60 * 60
ABSOLUTE_TIMEOUT_SECONDS = 24 * 60 * 60

#: How a session was established. Not decoration: the gate refuses
#: administrative routes to a link-established session, and the audit says
#: which of the two happened.
BY_PASSWORD = "password"
BY_DIRECT_LINK = "direct_link"
SESSION_METHODS = (BY_PASSWORD, BY_DIRECT_LINK)

#: A link-established session does NOT expire with time or idleness.
#:
#: It used to expire sooner than a password session -- two hours idle, twelve
#: absolute -- on the reasoning that opening the link again makes a new one, so
#: nothing was lost. That reasoning is wrong for the thing these links are
#: actually for. A Direct Access link is a wall panel or a lectern tablet: it
#: sits untouched overnight, over a weekend, over a holiday, and the person who
#: walks up to it has no password and no reason to know it needs one. An
#: expiring session turned that panel into a sign-in page nobody could answer.
#:
#: Removing the timeout takes nothing away, because it never protected
#: anything. Whoever holds the URL can mint a fresh session at any moment by
#: opening it, so an old session grants exactly what the URL already grants.
#: What does the protecting is revocation, and that is enforced on every single
#: request rather than by waiting: `resolve_session` re-reads the account and
#: drops the session the instant it is disabled or deleted, `set_role` and
#: `set_permissions` revoke, and turning the link off or regenerating it
#: revokes every session it opened in the same critical section that replaces
#: the token.
#:
#: Password sessions are untouched and keep their 8-hour idle / 24-hour
#: absolute limits. The two are different because the credentials are
#: different: a password was typed by somebody who knew it and can type it
#: again, so ending their session costs them one sign-in. A link holder has no
#: second factor to fall back on -- the URL is the whole credential -- so
#: ending their session costs them the panel.
DIRECT_SESSIONS_EXPIRE = False

#: Most sessions one account's Direct Access link may have open at once. They do not
#: expire, and opening the link mints one, so without a limit anyone holding the URL
#: could grow the session table without end. Far more than any room has devices; past
#: it the session that has been quiet longest is dropped to make room.
MAX_DIRECT_SESSIONS = 256
#: Password sessions one account may hold at once. Generous -- a person has a few
#: browsers -- and a ceiling rather than a policy: sessions live in memory, expire
#: only when somebody looks, and a script that signs in with a valid password in a
#: loop would otherwise grow the table without limit. The oldest is the one to go.
MAX_PASSWORD_SESSIONS = 64

#: How many characters of a token may appear in a log line or an audit record.
#: Enough to correlate two entries, far too few to be a credential.
TOKEN_HINT_CHARS = 6

#: Login throttling. Counted per username and per source address, so neither a
#: password spray across accounts nor a dictionary attack on one is free.
MAX_FAILURES = 5

#: How much of a typed username is kept for the throttle and the audit. A sign-in
#: is the one request anybody on the network can make, and the name is theirs to
#: choose: unbounded, it is stored in the throttle for the lockout window and written
#: to an audit log that is rewritten on every flush.
MAX_RECORDED_USERNAME = 128
#: The longest username an administrator may create.
MAX_USERNAME = 64

#: Above this many throttle entries the expired ones are dropped on the next failure.
THROTTLE_SWEEP_AT = 1024

#: Most (name, source) records the throttle will hold. Expiry alone bounds growth only
#: by how fast a caller can fail -- each failure costs a key derivation, so that is
#: slow, but it is a rate and not a limit. At this size the record that has failed
#: least recently, and has not yet reached the lockout, is dropped to make room: the
#: records that actually lock someone out are never the ones evicted.
THROTTLE_MAX_KEYS = 8192
LOCKOUT_SECONDS = 15 * 60

#: The audit log is bounded. An unbounded security log is its own outage, and
#: a failed login is something an attacker can produce at will.
#:
#: This is the flood guard, not the retention policy. How long entries are kept
#: and how much disk they may use are operator settings (see ActivityRetention);
#: this is the hard ceiling underneath them, so a sign-in storm costs history
#: rather than memory whatever those settings say.
AUDIT_MAX_ENTRIES = 20000

#: Activity retention. Measured on a working installation: an entry is about 190
#: bytes of JSON and about 280 on disk (the file is written indented), and an
#: ordinary site produces tens of entries a day. A busy training room that
#: routes constantly might produce a thousand. 30 days at that rate is about
#: 8 MB, so the default disk budget is deliberately smaller than the worst case:
#: history is the thing that gives way, not the disk.
ACTIVITY_DEFAULT_DAYS = 30
ACTIVITY_MIN_DAYS, ACTIVITY_MAX_DAYS = 1, 365
ACTIVITY_DEFAULT_MAX_BYTES = 5 * 1024 * 1024
ACTIVITY_MIN_MAX_BYTES = 256 * 1024
ACTIVITY_MAX_MAX_BYTES = 50 * 1024 * 1024
ACTIVITY_SETTINGS_FILE = "activity_settings.json"


def _clamp_int(value, low, high, default):
    """An integer within bounds, or the default for anything that is not one.

    A hand-edited or damaged settings file must never crash startup or switch
    the limits off, so a bool, a float with a fraction, text or a missing value
    all fall back to the default rather than being coerced into something
    plausible. A number outside the range is clamped.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))
                                     or value != int(value)):
        return default
    return max(low, min(high, int(value)))


def _entry_bytes(entry) -> int:
    """What one entry costs on disk, as the audit file is actually written."""
    try:
        # An entry inside {"entries": [...]} is indented four spaces deeper on
        # every line than it is on its own, plus the separating comma.
        text = json.dumps(entry, indent=2)
        return len(text) + 4 * (text.count(chr(10)) + 1) + 2
    except (TypeError, ValueError):
        return 0

#: How often the audit file is rewritten at most. Every event used to rewrite
#: the whole file -- up to 5000 entries -- while holding the lock that
#: authentication also needs, so a brute-force attempt was simultaneously a
#: denial of service against sign-in. Entries are kept in memory and flushed on
#: this cadence, and always immediately for the events somebody would come
#: looking for.
AUDIT_FLUSH_SECONDS = 5.0

#: Events written through immediately rather than waiting for the cadence.
#: Chosen because these are what an administrator reconstructs an incident
#: from, and losing the last few seconds of them to a crash would matter.
AUDIT_IMMEDIATE_EVENTS = frozenset({
    "bootstrap.admin_created", "user.create", "user.deleted",
    "user.set_role", "user.set_permissions", "user.set_password",
    "user.disabled", "user.enabled",
    "remote_access.enabled", "remote_access.disabled",
    "certificate.generated", "certificate.configured",
})

#: Never written to the audit log or returned by any API, whatever a caller
#: puts in a request body. Mirrors the server's existing audit redaction set.
SECRET_FIELDS = frozenset({
    "password", "passwd", "pwd", "new_password", "old_password",
    "current_password", "confirm_password", "fallback_password",
    "token", "secret", "session", "csrf", "csrf_token", "password_hash",
    "passphrase", "authorization", "cookie", "credentials",
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


_dummy_hash_value = None
_dummy_hash_lock = threading.Lock()


def dummy_hash() -> str:
    """A stored hash that matches nothing, made once and then reused.

    A sign-in for a name that does not exist still has to be checked against
    something, or the answer comes back faster and says the name is not real. It used
    to be checked against a hash made on the spot, which is a second key derivation
    on top of the check: an unknown name cost about twice a known one, so the
    difference this was meant to remove was larger than the one it removed -- and a
    caller choosing names paid the server a double derivation each time.
    """
    global _dummy_hash_value
    with _dummy_hash_lock:
        if _dummy_hash_value is None:
            _dummy_hash_value = hash_password(secrets.token_urlsafe(16))
        return _dummy_hash_value


def hash_password(password: str) -> str:
    """Salted scrypt, via werkzeug.

    Chosen because Flask already brings werkzeug, so remote access adds no
    dependency to a PyInstaller build; because scrypt is memory-hard, which a
    plain SHA is not; and because it is not custom cryptography.
    """
    # Named, not defaulted: Werkzeug 2 defaults to PBKDF2 and only Werkzeug 3 to scrypt, so
    # the algorithm this docstring promises depended on which version happened to be
    # installed. Stored hashes of either kind still verify; the method travels in the hash.
    return generate_password_hash(password, method="scrypt")


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

    __slots__ = ("features", "sources", "destinations", "groups", "interfaces")

    def __init__(self, features=(), sources=(), destinations=(), groups=(),
                 interfaces=None):
        allowed = omni_access.user_features()
        self.features = frozenset(f for f in features if f in allowed)
        # Which routing interfaces the account may use. `None` means the field
        # was never recorded, which is every account that existed before the
        # Matrix did: they keep the interface they always had. An explicit
        # empty collection is kept as empty -- an account that was deliberately
        # given neither is shown no routing at all rather than quietly given one.
        if interfaces is None:
            interfaces = (INTERFACE_STANDARD,)
        self.interfaces = frozenset(i for i in (str(x).strip().lower()
                                                for x in interfaces)
                                    if i in INTERFACES)
        self.sources = frozenset(_normalise_identity(s) for s in sources if s)
        self.destinations = frozenset(_normalise_identity(d) for d in destinations if d)
        # A group is its own resource: holding one grants the group action and
        # nothing else. It never adds the group's members to `destinations`.
        self.groups = frozenset(_normalise_group_id(g) for g in groups if g)

    def to_json(self) -> dict:
        return {"features": sorted(self.features),
                "sources": sorted(self.sources),
                "destinations": sorted(self.destinations),
                "groups": sorted(self.groups),
                "interfaces": sorted(self.interfaces)}

    @classmethod
    def from_json(cls, raw) -> "Permissions":
        # A restricted account no longer carries Multiview. An older users.json
        # may still hold a "multiview" feature and a "multiviews" approval list:
        # neither is read, so the account loads as it was minus a capability that
        # no longer exists, and the next save writes the record without them.
        raw = raw or {}
        return cls(raw.get("features") or (),
                   raw.get("sources") or (),
                   raw.get("destinations") or (),
                   raw.get("groups") or (),
                   raw["interfaces"] if isinstance(raw.get("interfaces"),
                                                   (list, tuple)) else None)

    def has_interface(self, interface: str) -> bool:
        return str(interface or "").strip().lower() in self.interfaces

    def may_control_group(self, group_id) -> bool:
        return _normalise_group_id(group_id) in self.groups

    def has_feature(self, feature: str) -> bool:
        return feature in self.features

    def may_use_source(self, identity) -> bool:
        return _normalise_identity(identity) in self.sources

    def may_control_destination(self, identity) -> bool:
        return _normalise_identity(identity) in self.destinations


def interface_refusal(role, permissions) -> str:
    """Why these permissions cannot be saved, or "" when they can.

    A restricted account that may route has to have a way to do it. Both
    interfaces off with A/V on would save an account that signs in to a page
    with nothing on it, which reads as a fault rather than a decision. An
    administrator is exempt: their access is not built from these grants.
    """
    if role == ROLE_USER and permissions.has_feature("av") \
            and not permissions.interfaces:
        return "choose at least one routing interface"
    return ""


def _normalise_group_id(group_id) -> str:
    """A group id is opaque text; fold only case and surrounding space."""
    return str(group_id or "").strip().lower()


# aa:bb:cc:dd:ee:ff, aa-bb-cc-dd-ee-ff, aabbccddeeff and aabb.ccdd.eeff. Nothing else:
# in particular not a dotted-decimal address, whose digits can number twelve.
_MAC_SPELLINGS = re.compile(
    r"^(?:[0-9a-f]{2}([:-])(?:[0-9a-f]{2}\1){4}[0-9a-f]{2}"
    r"|[0-9a-f]{12}"
    r"|[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})$")


def is_mac_identity(identity) -> bool:
    """True when `identity` is a MAC in one of the accepted spellings."""
    return bool(_MAC_SPELLINGS.match(str(identity or "").strip().lower()))


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
    # Only a real MAC spelling is converted. Stripping every separator and
    # counting digits turned an address into a "MAC" made of its
    # own digits -- a plausible identity no device owns -- so an address
    # now stays what it is (and matches nothing), and the spellings accepted are
    # listed rather than inferred.
    if _MAC_SPELLINGS.match(text):
        compact = re.sub(r"[:.-]", "", text)
        return ":".join(compact[i:i + 2] for i in range(0, 12, 2))
    return text


class DirectAccess:
    """A per-account passwordless link, off unless an administrator turns it on.

    Possession of the link is the credential, so this is deliberately narrow:

      * it exists only on a restricted account. An administrator's account may
        never carry one, and a session created from a link is refused
        administrative routes even if the account is promoted afterwards.
      * it carries no permissions of its own. It names an account, and every
        request is authorised against that account as it is at that moment, so
        an administrator taking a capability or a device away takes it away
        from the link in the same instant.
      * exactly one token is live at a time. Regenerating replaces it; there is
        no history of previously valid links.

    Stored as a digest for lookup and comparison, plus a protected copy so the
    operator can be shown the same link again later. `omni_secret` says what
    that protection actually is on this platform -- it is DPAPI on Windows and
    the record says so, rather than the product implying more than it did.
    """

    __slots__ = ("enabled", "digest", "blob", "protection", "created_at")

    def __init__(self, enabled=False, digest="", blob="", protection="",
                 created_at=None):
        self.enabled = bool(enabled)
        self.digest = str(digest or "")
        self.blob = str(blob or "")
        self.protection = str(protection or "")
        self.created_at = created_at or 0

    @property
    def active(self) -> bool:
        return bool(self.enabled and self.digest)

    def to_json(self) -> dict:
        return {"enabled": self.enabled, "digest": self.digest,
                "blob": self.blob, "protection": self.protection,
                "created_at": self.created_at}

    @classmethod
    def from_json(cls, raw) -> "DirectAccess":
        raw = raw if isinstance(raw, dict) else {}
        return cls(enabled=raw.get("enabled"), digest=raw.get("digest"),
                   blob=raw.get("blob"), protection=raw.get("protection"),
                   created_at=raw.get("created_at"))

    def public(self) -> dict:
        """What an API may return about it. Never the token, never the blob."""
        return {"enabled": self.active,
                "protection": self.protection or "",
                "created_at": self.created_at or 0}


def direct_token_digest(token) -> str:
    """The lookup key for a link token.

    SHA-256 rather than a password hash: the token is 256 bits of output from
    the system random source, so there is nothing to brute force and a
    deliberately slow hash would only make every request slower.
    """
    text = str(token or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Account:
    __slots__ = ("id", "username", "password_hash", "role", "enabled",
                 "permissions", "created_at", "password_changed_at",
                 "direct_access")

    def __init__(self, id, username, password_hash, role, enabled=True,
                 permissions=None, created_at=None, password_changed_at=None,
                 direct_access=None):
        self.id = id
        self.username = username
        self.password_hash = password_hash
        self.role = role if role in ROLES else ROLE_USER
        self.enabled = bool(enabled)
        self.permissions = permissions or Permissions()
        self.created_at = created_at or _now()
        self.password_changed_at = password_changed_at or self.created_at
        self.direct_access = direct_access or DirectAccess()

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def to_json(self) -> dict:
        return {"id": self.id, "username": self.username,
                "password_hash": self.password_hash, "role": self.role,
                "enabled": self.enabled,
                "permissions": self.permissions.to_json(),
                "created_at": self.created_at,
                "password_changed_at": self.password_changed_at,
                "direct_access": self.direct_access.to_json()}

    def public(self) -> dict:
        """What an API may return. Never the hash, never anything derived from it."""
        return {"id": self.id, "username": self.username, "role": self.role,
                "enabled": self.enabled,
                "permissions": self.permissions.to_json(),
                "created_at": self.created_at,
                # Whether a link exists, and nothing that would let anybody
                # reconstruct one. Retrieving the link itself is a separate,
                # deliberate request.
                "direct_access": self.direct_access.public()}

    @classmethod
    def from_json(cls, raw) -> "Account":
        return cls(id=raw.get("id"), username=raw.get("username"),
                   password_hash=raw.get("password_hash"),
                   role=raw.get("role"), enabled=raw.get("enabled", True),
                   permissions=Permissions.from_json(raw.get("permissions")),
                   created_at=raw.get("created_at"),
                   password_changed_at=raw.get("password_changed_at"),
                   direct_access=DirectAccess.from_json(
                       raw.get("direct_access")))


class Session:
    __slots__ = ("token", "user_id", "created_at", "last_seen", "csrf_token",
                 "method")

    def __init__(self, token, user_id, csrf_token, method=BY_PASSWORD):
        self.token = token
        self.user_id = user_id
        self.csrf_token = csrf_token
        # How the holder proved who they were. Recorded because the two are
        # not equivalent: a password was typed by somebody who knew it, and a
        # link was opened by whoever has the link. The gate refuses
        # administrative routes to the second whatever the account's role has
        # since become.
        self.method = method if method in SESSION_METHODS else BY_PASSWORD
        self.created_at = _now()
        self.last_seen = self.created_at

    @property
    def from_direct_link(self) -> bool:
        return self.method == BY_DIRECT_LINK

    def expired(self, now=None) -> bool:
        now = now or _now()
        if self.from_direct_link:
            # Never, by time or idleness. A link-established session ends when
            # somebody ends it: the account disabled or deleted, the role
            # changed, the permissions changed, the link turned off or
            # regenerated. Every one of those revokes immediately and is
            # re-checked on every request, so waiting adds nothing.
            #
            # A wall panel is the ordinary case for these links, and it is
            # idle by definition. Expiring its session put a sign-in page in
            # front of somebody who has no password to give it.
            return DIRECT_SESSIONS_EXPIRE
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
        self._audit_dirty = False
        self._audit_flushed_at = 0.0
        # Retention: how long Activity keeps entries, and how much disk it may
        # use. Both bounded, both persisted beside the log.
        self._activity_days = ACTIVITY_DEFAULT_DAYS
        self._activity_max_bytes = ACTIVITY_DEFAULT_MAX_BYTES
        self._audit_bytes = 0
        self._audit_len_seen = 0
        self._load()
        self._load_activity_settings()
        self._load_audit()

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

    def create(self, username, password, role, permissions=None,
               *, actor=None, audit=True) -> dict:
        username = str(username or "").strip()
        if not username:
            return {"ok": False, "error": "username required"}
        if len(username) > MAX_USERNAME or any(ord(c) < 32 or ord(c) == 127 for c in username):
            return {"ok": False, "error": "username must be at most %d characters and "
                                          "contain no control characters" % MAX_USERNAME}
        if len(str(password or "")) < 8:
            return {"ok": False, "error": "password must be at least 8 characters"}
        if role not in ROLES:
            return {"ok": False, "error": "role must be admin or user"}
        refusal = interface_refusal(role, permissions or Permissions())
        if refusal:
            return {"ok": False, "error": refusal}
        with self._lock:
            if self.find_by_username(username):
                return {"ok": False, "error": "that username already exists"}
            account = Account(
                id=secrets.token_urlsafe(12), username=username,
                password_hash=hash_password(password), role=role,
                permissions=permissions or Permissions())
            self._accounts[account.id] = account
            self._persist()
            if audit:
                self.audit("user.create", actor=actor,
                           detail={"username": username, "role": role})
            return {"ok": True, "user": account.public()}

    def set_enabled(self, user_id, enabled: bool, *, actor=None) -> dict:
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
                       actor=actor, detail={"username": account.username})
            return {"ok": True, "user": account.public()}

    def _last_enabled_admin(self, account: Account) -> bool:
        others = [a for a in self._accounts.values()
                  if a.is_admin and a.enabled and a.id != account.id]
        return not others

    def set_password(self, user_id, password, *, actor=None) -> dict:
        if len(str(password or "")) < 8:
            return {"ok": False, "error": "password must be at least 8 characters"}
        with self._lock:
            account = self._accounts.get(user_id)
            if not account:
                return {"ok": False, "error": "no such user"}
            account.password_hash = hash_password(password)
            account.password_changed_at = _now()
            self._persist()
            # Every password-authenticated session for this account ends. A password
            # change that left old sessions alive would not be a password change. A
            # session a Direct Access link opened was never authenticated by the
            # password, so it follows the link's own lifecycle instead.
            self.revoke_all_for_user(user_id, _locked=True,
                                     except_method=BY_DIRECT_LINK)
            self.audit("user.password_changed", actor=actor,
                       detail={"username": account.username})
            return {"ok": True}

    def set_role(self, user_id, role, *, actor=None) -> dict:
        if role not in ROLES:
            return {"ok": False, "error": "role must be admin or user"}
        with self._lock:
            account = self._accounts.get(user_id)
            if not account:
                return {"ok": False, "error": "no such user"}
            if account.is_admin and role != ROLE_ADMIN and self._last_enabled_admin(account):
                return {"ok": False, "error": "this is the only enabled administrator"}
            account.role = role
            if role == ROLE_ADMIN:
                # An administrator never carries a passwordless link. Cleared
                # here as well as refused at validation, so the record agrees
                # with the rule rather than merely being overruled by it.
                account.direct_access = DirectAccess()
            self._persist()
            self.revoke_all_for_user(user_id, _locked=True)
            self.audit("user.role_changed", actor=actor,
                       detail={"username": account.username, "role": role})
            return {"ok": True, "user": account.public()}

    def set_permissions(self, user_id, permissions: Permissions,
                        *, actor=None) -> dict:
        with self._lock:
            account = self._accounts.get(user_id)
            if not account:
                return {"ok": False, "error": "no such user"}
            refusal = interface_refusal(account.role, permissions)
            if refusal:
                return {"ok": False, "error": refusal}
            before = account.permissions.interfaces
            account.permissions = permissions
            self._persist()
            if before != permissions.interfaces:
                # Its own entry, because who may use the Matrix is a decision
                # in its own right and should be findable without reading
                # every permission change for the one field.
                self.audit("user.routing_interfaces_changed", actor=actor,
                           detail={"username": account.username,
                                   "interfaces": sorted(permissions.interfaces),
                                   "was": sorted(before)})
            # Sessions are NOT ended. Every request is authorised against the
            # account as it is at that moment (resolve_session returns the live
            # account), so a removed grant is refused on the very next request
            # and an added one appears at the next read -- without signing
            # anybody out of a browser or a lectern tablet.
            self.audit("user.permissions_changed", actor=actor,
                       detail={"username": account.username,
                               "permissions": permissions.to_json()})
            return {"ok": True, "user": account.public()}

    def delete(self, user_id, *, actor=None) -> dict:
        with self._lock:
            account = self._accounts.get(user_id)
            if not account:
                return {"ok": False, "error": "no such user"}
            if account.is_admin and self._last_enabled_admin(account):
                return {"ok": False, "error": "this is the only enabled administrator"}
            del self._accounts[user_id]
            self._persist()
            self.revoke_all_for_user(user_id, _locked=True)
            self.audit("user.deleted", actor=actor,
                       detail={"username": account.username})
            return {"ok": True}

    # ---- authentication ---------------------------------------------
    def _throttle_key(self, username, source) -> tuple:
        return (str(username or "").strip().lower()[:MAX_RECORDED_USERNAME],
                str(source or "")[:MAX_RECORDED_USERNAME])

    def locked_out(self, username, source) -> bool:
        key = self._throttle_key(username, source)
        cutoff = _now() - LOCKOUT_SECONDS
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if t > cutoff]
            if recent:
                self._failures[key] = recent
            else:
                # Not an empty list: a name nobody has failed with is not a record.
                # Keeping one for every name ever asked about grows without limit.
                self._failures.pop(key, None)
            return len(recent) >= MAX_FAILURES

    def _record_failure(self, username, source) -> None:
        key = self._throttle_key(username, source)
        with self._lock:
            if len(self._failures) >= THROTTLE_SWEEP_AT:
                cutoff = _now() - LOCKOUT_SECONDS
                for stale in [k for k, times in self._failures.items()
                              if not times or times[-1] <= cutoff]:
                    del self._failures[stale]
            if key not in self._failures and len(self._failures) >= THROTTLE_MAX_KEYS:
                evictable = [(times[-1], k) for k, times in self._failures.items()
                             if len(times) < MAX_FAILURES]
                if evictable:
                    del self._failures[min(evictable)[1]]
                else:
                    # Every record is a lockout. Nothing is dropped to make room for a
                    # name that has failed once; it is simply not remembered.
                    return
            self._failures.setdefault(key, []).append(_now())

    def record_failure(self, name, source) -> None:
        """Count a refusal that is not a password attempt against the throttle.

        A Direct Access link has no username to attribute a refusal to -- by
        design, since every refusal answers identically -- so it is counted
        under a fixed name that no account can have. Same counter, same
        lockout, same per-source scoping.
        """
        self._record_failure(name, source)

    def authenticate(self, username, password, source=None) -> dict:
        """Verify a credential. Never says which half was wrong."""
        if self.locked_out(username, source):
            self.audit("login.throttled", actor=None,
                       detail={"username": str(username or "")[:MAX_RECORDED_USERNAME],
                               "source": source})
            return {"ok": False, "error": "too many attempts; try again later",
                    "throttled": True}

        account = self.find_by_username(username)
        # Verify even when the account does not exist, so the response time does
        # not say whether a username is real. Against a hash made once, not one made
        # now: see dummy_hash. Made before the check either way, so the first sign-in
        # after a start is not the slow one only when the name is unknown.
        dummy = dummy_hash()
        stored = account.password_hash if account else dummy
        good = verify_password(stored, password)

        session = None
        reason = None
        if not account or not good or not account.enabled:
            reason = ("disabled" if account and good and not account.enabled
                      else "bad_credentials")
        else:
            # The check above took real time and ran outside the lock, against
            # the hash that was current when it began. Whatever changed since --
            # the password replaced because it leaked, the account switched off or
            # swapped by a restore -- has to win, so the credential is confirmed
            # to still be the account's in the same critical section that opens
            # the session. Nothing can slip between the two.
            with self._lock:
                live = self._accounts.get(account.id)
                if (live is None or not live.enabled
                        or live.password_hash != stored):
                    reason = "credential_changed"
                else:
                    self._failures.pop(self._throttle_key(username, source), None)
                    session = self.open_session(live.id)
                    account = live
        if session is None:
            self._record_failure(username, source)
            self.audit("login.failure", actor=None,
                       detail={"username": str(username or "")[:MAX_RECORDED_USERNAME],
                               "source": source,
                               "reason": reason})
            return {"ok": False, "error": "invalid username or password"}

        self.audit("login.success", actor=account,
                   detail={"username": account.username, "source": source})
        return {"ok": True, "user": account.public(),
                "token": session.token, "csrf_token": session.csrf_token}

    # ---- sessions ----------------------------------------------------
    def open_session(self, user_id, method=BY_PASSWORD) -> Session:
        """A fresh token every time: logging in never reuses an existing id."""
        session = Session(token=secrets.token_urlsafe(32), user_id=user_id,
                          csrf_token=secrets.token_urlsafe(32), method=method)
        with self._lock:
            # Whatever has expired is dropped now, not whenever somebody happens to
            # ask how many sessions there are: opening one is the moment the table
            # grows, so it is the moment to tidy it.
            for stale in [t for t, s in self._sessions.items() if s.expired()]:
                self._sessions.pop(stale, None)
            direct = session.from_direct_link
            mine = [s for s in self._sessions.values()
                    if s.user_id == user_id and s.from_direct_link == direct]
            cap = MAX_DIRECT_SESSIONS if direct else MAX_PASSWORD_SESSIONS
            excess = len(mine) - (cap - 1)
            if excess > 0:
                for old in sorted(mine, key=lambda s: s.last_seen)[:excess]:
                    self._sessions.pop(old.token, None)
            self._sessions[session.token] = session
        return session

    # ---- direct access links -----------------------------------------
    #
    # An administrator may give one restricted account a URL that works without
    # a password, for a tablet on a lectern or a phone in a classroom. Four
    # things keep that from being a hole:
    #
    #   * it is off by default and per account, and an administrator turns it
    #     on deliberately,
    #   * it can never exist on an administrator's account,
    #   * the token names an account and carries no permissions, so everything
    #     is still authorised against that account as it is now,
    #   * and every way of changing the account -- disabling it, deleting it,
    #     changing its role, password or permissions -- already revokes its
    #     sessions, so a change takes effect on the next request rather than
    #     whenever a session happens to lapse.

    def enable_direct_access(self, user_id, *, actor=None) -> dict:
        """Turn the feature on and mint the first token."""
        return self._mint_direct_token(user_id, actor=actor,
                                       event="direct_access.enabled")

    def regenerate_direct_access(self, user_id, *, actor=None) -> dict:
        """Replace the token. Every URL handed out before this stops working."""
        with self._lock:
            account = self._accounts.get(user_id)
            if account is None or not account.direct_access.active:
                return {"ok": False, "error": "direct access is not enabled"}
        return self._mint_direct_token(user_id, actor=actor,
                                       event="direct_access.regenerated")

    def _mint_direct_token(self, user_id, *, actor, event) -> dict:
        token = secrets.token_urlsafe(32)
        blob, protection = omni_secret.protect(token)
        with self._lock:
            account = self._accounts.get(user_id)
            if account is None:
                return {"ok": False, "error": "no such user"}
            if account.is_admin:
                # Stated as a refusal rather than a filter in the UI, because a
                # passwordless administrator URL is the one outcome this whole
                # feature must not be able to produce.
                return {"ok": False,
                        "error": "direct access links are for restricted users "
                                 "only"}
            account.direct_access = DirectAccess(
                enabled=True, digest=direct_token_digest(token),
                blob=blob, protection=protection, created_at=_now())
            self._persist()
            # The previous token's sessions end here, in the same critical
            # section that replaced it. Regenerating a link that left the old
            # one's sessions running would not have replaced anything.
            self.revoke_direct_sessions(user_id, _locked=True)
            self.audit(event, actor=actor,
                       detail={"username": account.username,
                               "protection": protection})
            return {"ok": True, "token": token, "user": account.public()}

    def disable_direct_access(self, user_id, *, actor=None) -> dict:
        with self._lock:
            account = self._accounts.get(user_id)
            if account is None:
                return {"ok": False, "error": "no such user"}
            account.direct_access = DirectAccess()
            self._persist()
            self.revoke_direct_sessions(user_id, _locked=True)
            self.audit("direct_access.disabled", actor=actor,
                       detail={"username": account.username})
            return {"ok": True, "user": account.public()}

    def direct_access_token(self, user_id):
        """The live token for an account, or None.

        Returns None both when there is no link and when the stored copy cannot
        be read on this machine -- a blob protected for another Windows account
        or another computer. The caller says "regenerate the link", which is
        true and is the only thing that would help.
        """
        with self._lock:
            account = self._accounts.get(user_id)
            if account is None or not account.direct_access.active:
                return None
            record = account.direct_access
        recovered = omni_secret.unprotect(record.blob, record.protection)
        if not recovered:
            return None
        # The blob and the digest are written together; if they disagree the
        # record is damaged and showing the link would hand out one that does
        # not work.
        if not hmac.compare_digest(direct_token_digest(recovered),
                                   record.digest):
            log.warning("[DIRECT] stored link for %s does not match its "
                        "digest; treating as unreadable", account.username)
            return None
        return recovered

    def resolve_direct_token(self, token):
        """The account a link token names, or None. Never says why not.

        Deliberately one answer for every failure: an unknown token, a token
        whose account was deleted, disabled, promoted to administrator, or had
        its link turned off. Distinguishing them would let somebody holding an
        old URL learn things about an account they have no business knowing.
        """
        digest = direct_token_digest(token)
        if not digest:
            return None
        with self._lock:
            for account in self._accounts.values():
                record = account.direct_access
                if not record.active:
                    continue
                if not hmac.compare_digest(record.digest, digest):
                    continue
                if not account.enabled or account.is_admin:
                    return None
                return account
        return None

    # ---- what is assigned, and to whom --------------------------------
    def assigned_devices(self) -> dict:
        """identity -> sorted usernames that were given that device.

        Every way an account can be given a device counts: as a source or as a
        display.

        Disabled accounts are included. An account that is off is one an
        administrator can turn back on, and its assignments are still theirs.
        """
        assigned: dict = {}
        with self._lock:
            for account in self._accounts.values():
                if account.is_admin:
                    # An administrator has no per-device grants -- the role is
                    # the grant -- so there is nothing here to protect.
                    continue
                permissions = account.permissions
                identities = set(permissions.sources) | set(permissions.destinations)
                for identity in identities:
                    if identity:
                        assigned.setdefault(identity, set()).add(account.username)
        return {identity: sorted(names) for identity, names in assigned.items()}

    def forget_group(self, group_id) -> int:
        """Take a deleted group out of every account's grants; returns how many changed.

        A grant naming a group that no longer exists would silently apply to
        nothing today, and to a different group if an id were ever reused.
        """
        gid = _normalise_group_id(group_id)
        changed = 0
        with self._lock:
            for account in self._accounts.values():
                if gid in account.permissions.groups:
                    p = account.permissions
                    account.permissions = Permissions(
                        p.features, p.sources, p.destinations,
                        p.groups - {gid})
                    changed += 1
            if changed:
                self._persist()
        return changed

    def forget_devices(self, identities=None) -> int:
        """Take devices out of every account's source and display grants.

        `identities=None` means every device: the inventory was cleared, so no
        grant can name equipment that exists. Returns how many accounts changed.
        Group grants are about groups, not devices, and are left alone.
        """
        gone = None if identities is None else {
            _normalise_identity(i) for i in identities}
        changed = 0
        with self._lock:
            for account in self._accounts.values():
                p = account.permissions
                sources = [s for s in p.sources if gone is not None and s not in gone]
                dests = [d for d in p.destinations if gone is not None and d not in gone]
                if len(sources) != len(p.sources) or len(dests) != len(p.destinations):
                    account.permissions = Permissions(
                        p.features, sources, dests, p.groups)
                    changed += 1
            if changed and not self._persist():
                raise OSError("the account store could not be written")
        return changed

    def grants_snapshot(self) -> dict:
        with self._lock:
            return {a.id: a.permissions for a in self._accounts.values()}

    def restore_grants(self, snapshot) -> None:
        with self._lock:
            for uid, permissions in snapshot.items():
                account = self._accounts.get(uid)
                if account is not None:
                    account.permissions = permissions
            self._persist()

    def accounts_with_group(self, group_id) -> list:
        """Usernames granted this group, for the warning before deleting it."""
        gid = _normalise_group_id(group_id)
        with self._lock:
            return sorted(a.username for a in self._accounts.values()
                          if gid in a.permissions.groups)

    def revoke_direct_sessions(self, user_id, _locked=False) -> int:
        """End link-established sessions only, leaving a password one alone."""
        def _do():
            doomed = [t for t, s in self._sessions.items()
                      if s.user_id == user_id and s.from_direct_link]
            for token in doomed:
                self._sessions.pop(token, None)
            return len(doomed)
        if _locked:
            return _do()
        with self._lock:
            return _do()

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

    def revoke_all_for_user(self, user_id, _locked=False, except_method=None) -> int:
        """End the account's sessions. `except_method` spares one kind, for the one event
        that is about a single credential: a password change says nothing about a link."""
        def _do():
            doomed = [t for t, s in self._sessions.items()
                      if s.user_id == user_id and s.method != except_method]
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

    # ---- a restore replaces the table the sessions belong to ----------
    @staticmethod
    def _fingerprint(account) -> tuple:
        """What a session was opened against, and nothing that is not a credential.

        The password hash and the link digest are what a sign-in proved; the role,
        the enabled flag and the permissions are what that sign-in is worth. A
        session may outlive a restore only if the account it belongs to is
        security-equivalent afterwards, so every one of them is part of the
        comparison. Permissions are also read from the account on every request,
        so a reduction takes effect at once either way; ending the session as well
        means nobody stays signed in on an account that is no longer the one they
        proved they could use.
        """
        link = account.direct_access
        perms = account.permissions
        return (account.password_hash, account.role, bool(account.enabled),
                link.digest if link.active else "",
                (tuple(sorted(perms.features)), tuple(sorted(perms.sources)),
                 tuple(sorted(perms.destinations)), tuple(sorted(perms.groups)),
                 tuple(sorted(perms.interfaces))))

    def account_fingerprints(self) -> dict:
        """account id -> (username, fingerprint), for comparing two tables."""
        with self._lock:
            return {a.id: (a.username, self._fingerprint(a))
                    for a in self._accounts.values()}

    @classmethod
    def fingerprints_of(cls, raw_accounts) -> dict:
        """The same map for account records that are not (yet) the live table."""
        found = {}
        for entry in raw_accounts or []:
            try:
                account = Account.from_json(entry)
            except Exception:
                continue
            if account.id and account.username:
                found[account.id] = (account.username, cls._fingerprint(account))
        return found

    def reload_from_disk(self) -> dict:
        """Re-read the account file, then end every session it no longer justifies.

        One critical section, so no request can be authorised against the table
        in between, and the comparison is made against what the sessions were
        opened against rather than against what the file used to say.

            account gone, disabled or promoted/demoted   every session of it ends
            password hash different                       its password sessions end
            link turned off or replaced                   its link sessions end
            permissions or interfaces different           every session of it ends
            anything else                                 the session stays

        A session whose account is security-equivalent afterwards is left alone,
        an administrator's included: a restore is not a sign-out of the room.
        """
        with self._lock:
            before = {a.id: self._fingerprint(a) for a in self._accounts.values()}
            self._load()
            reasons = {"removed": 0, "disabled": 0, "role": 0, "password": 0,
                       "link": 0, "permissions": 0}
            for token, session in list(self._sessions.items()):
                account = self._accounts.get(session.user_id)
                held = before.get(session.user_id)
                reason = None
                if account is None:
                    reason = "removed"
                elif not account.enabled:
                    reason = "disabled"
                elif held is None:
                    # Not in the table the sessions were opened against: nothing
                    # to compare with, so nothing to trust.
                    reason = "removed"
                else:
                    now = self._fingerprint(account)
                    if now[1] != held[1]:
                        reason = "role"
                    elif now[4] != held[4]:
                        reason = "permissions"
                    elif session.from_direct_link:
                        if now[3] != held[3]:
                            reason = "link"
                    elif now[0] != held[0]:
                        reason = "password"
                if reason:
                    self._sessions.pop(token, None)
                    reasons[reason] += 1
            return {"sessions_ended": sum(reasons.values()), "reasons": reasons}

    # ---- audit -------------------------------------------------------
    def _load_audit(self) -> None:
        """Read the audit back so a restart does not blank the view.

        A damaged file costs the history and nothing else: the application
        starts, and the log begins again rather than failing.
        """
        if self._audit_path is None or not self._audit_path.exists():
            return
        try:
            raw = json.loads(self._audit_path.read_text(encoding="utf-8"))
            entries = (raw or {}).get("entries") or []
            with self._lock:
                self._audit = [e for e in entries if isinstance(e, dict)][
                    -AUDIT_MAX_ENTRIES:]
                self._prune_audit_locked(_now())
        except Exception as exc:
            log.warning("[AUDIT] could not read the existing log (%s); "
                        "starting a new one", type(exc).__name__)

    # ---- activity retention ------------------------------------------
    def _activity_settings_path(self):
        if self._audit_path is None:
            return None
        return self._audit_path.with_name(ACTIVITY_SETTINGS_FILE)

    def _load_activity_settings(self) -> None:
        """Read the retention settings. A bad file degrades to the defaults."""
        path = self._activity_settings_path()
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
        except Exception as exc:
            log.error("[AUDIT] %s is unreadable (%s); using the defaults",
                      path.name, type(exc).__name__)
            try:
                path.replace(path.with_suffix(".corrupt"))
            except OSError:
                pass
            return
        self._activity_days = _clamp_int(
            raw.get("days"), ACTIVITY_MIN_DAYS, ACTIVITY_MAX_DAYS,
            ACTIVITY_DEFAULT_DAYS)
        self._activity_max_bytes = _clamp_int(
            raw.get("max_bytes"), ACTIVITY_MIN_MAX_BYTES, ACTIVITY_MAX_MAX_BYTES,
            ACTIVITY_DEFAULT_MAX_BYTES)

    def activity_retention(self) -> dict:
        """The current limits and how much of the budget the log is using."""
        with self._lock:
            self._sync_audit_bytes_locked()
            return {"days": self._activity_days,
                    "max_bytes": self._activity_max_bytes,
                    "max_megabytes": round(self._activity_max_bytes / 1048576, 2),
                    "used_bytes": self._audit_bytes,
                    "entries": len(self._audit),
                    "defaults": {"days": ACTIVITY_DEFAULT_DAYS,
                                 "max_megabytes": ACTIVITY_DEFAULT_MAX_BYTES // 1048576},
                    "limits": {"min_days": ACTIVITY_MIN_DAYS,
                               "max_days": ACTIVITY_MAX_DAYS,
                               "min_megabytes": ACTIVITY_MIN_MAX_BYTES / 1048576,
                               "max_megabytes": ACTIVITY_MAX_MAX_BYTES // 1048576}}

    def set_activity_retention(self, days, max_megabytes, *, actor=None) -> dict:
        """Change how long and how much Activity keeps. Refuses what is out of range.

        Out-of-range input is refused rather than clamped: an operator who typed
        3650 days should be told the limit, not silently given 365.
        """
        if (isinstance(days, bool) or not isinstance(days, (int, float))
                or days != days or days != int(days)
                or not ACTIVITY_MIN_DAYS <= int(days) <= ACTIVITY_MAX_DAYS):
            return {"ok": False,
                    "error": "Keep activity for between %d and %d days."
                             % (ACTIVITY_MIN_DAYS, ACTIVITY_MAX_DAYS)}
        if (isinstance(max_megabytes, bool)
                or not isinstance(max_megabytes, (int, float))
                or max_megabytes != max_megabytes
                or max_megabytes in (float("inf"), float("-inf"))):
            return {"ok": False, "error": "Enter the size limit as a number of megabytes."}
        wanted = int(round(max_megabytes * 1048576))
        if not ACTIVITY_MIN_MAX_BYTES <= wanted <= ACTIVITY_MAX_MAX_BYTES:
            return {"ok": False,
                    "error": "The size limit must be between %s and %d megabytes."
                             % (("%g" % (ACTIVITY_MIN_MAX_BYTES / 1048576)),
                                ACTIVITY_MAX_MAX_BYTES // 1048576)}
        path = self._activity_settings_path()
        with self._lock:
            before = (self._activity_days, self._activity_max_bytes)
            self._activity_days, self._activity_max_bytes = int(days), wanted
            if path is not None and not _atomic_write_json(
                    path, {"version": SCHEMA_VERSION, "days": int(days),
                           "max_bytes": wanted}):
                self._activity_days, self._activity_max_bytes = before
                return {"ok": False, "error": "The setting could not be saved."}
            self._prune_audit_locked(_now())
            self._audit_dirty = True
        self.audit("activity.retention_changed", actor=actor, detail={
            "days": int(days), "max_megabytes": round(wanted / 1048576, 2)})
        return {"ok": True, "retention": self.activity_retention()}

    def _sync_audit_bytes_locked(self) -> None:
        """Recount only when something outside this class changed the list."""
        if len(self._audit) != self._audit_len_seen:
            self._audit_bytes = sum(_entry_bytes(e) for e in self._audit)
            self._audit_len_seen = len(self._audit)

    def _prune_audit_locked(self, now) -> bool:
        """Drop entries older than the retention window, then the oldest until
        the log fits its disk budget. Caller holds the lock."""
        self._sync_audit_bytes_locked()
        cutoff = now - self._activity_days * 86400
        dropped = 0
        while dropped < len(self._audit):
            at = self._audit[dropped].get("at")
            if isinstance(at, (int, float)) and at < cutoff:
                dropped += 1
            else:
                break
        if dropped:
            for entry in self._audit[:dropped]:
                self._audit_bytes -= _entry_bytes(entry)
            del self._audit[:dropped]
        while self._audit and self._audit_bytes > self._activity_max_bytes:
            self._audit_bytes -= _entry_bytes(self._audit[0])
            del self._audit[0]
            dropped += 1
        self._audit_len_seen = len(self._audit)
        if dropped:
            self._audit_bytes = max(0, self._audit_bytes)
        return bool(dropped)

    def audit(self, event, actor=None, detail=None) -> None:
        """Record a security-relevant event, with secrets stripped first.

        Named fields are recorded; a request body is never dumped verbatim,
        because a caller can put a credential in one.

        The file is written outside the lock and no more often than the flush
        cadence, so recording an event costs an append rather than a rewrite of
        everything that came before it.
        """
        name = str(event)
        entry = {"at": _now(), "event": name,
                 "actor": getattr(actor, "username", None),
                 "actor_id": getattr(actor, "id", None),
                 "detail": redact(detail or {})}

        snapshot = None
        with self._lock:
            self._sync_audit_bytes_locked()
            self._audit.append(entry)
            self._audit_bytes += _entry_bytes(entry)
            self._audit_len_seen = len(self._audit)
            if len(self._audit) > AUDIT_MAX_ENTRIES:
                # Bounded: an unbounded security log is its own outage.
                self._audit = self._audit[-AUDIT_MAX_ENTRIES:]
                self._audit_len_seen = -1      # recount
            self._prune_audit_locked(entry["at"])
            self._audit_dirty = True
            due = (name in AUDIT_IMMEDIATE_EVENTS
                   or _now() - self._audit_flushed_at >= AUDIT_FLUSH_SECONDS)
            if due and self._audit_path is not None:
                snapshot = list(self._audit)
                self._audit_flushed_at = _now()
                self._audit_dirty = False

        # Outside the lock: writing five thousand entries must not hold up a
        # sign-in, and this is the path an attacker can drive hardest.
        if snapshot is not None:
            _atomic_write_json(self._audit_path,
                               {"version": SCHEMA_VERSION, "entries": snapshot})

    def flush_audit(self) -> bool:
        """Write anything still held in memory. Called at shutdown."""
        with self._lock:
            if not self._audit_dirty or self._audit_path is None:
                return True
            snapshot = list(self._audit)
            self._audit_dirty = False
            self._audit_flushed_at = _now()
        return _atomic_write_json(self._audit_path,
                                  {"version": SCHEMA_VERSION,
                                   "entries": snapshot})

    def audit_entries(self, limit=200, event=None, actor=None,
                      search=None) -> list:
        """The most recent entries, newest first, optionally filtered.

        Filtering happens here rather than in the browser so a page never has
        to hold the whole log to show a slice of it.
        """
        needle = str(search or "").strip().lower()
        wanted_event = str(event or "").strip().lower()
        wanted_actor = str(actor or "").strip().lower()
        with self._lock:
            entries = list(self._audit)

        selected = []
        for item in reversed(entries):          # newest first
            if wanted_event and wanted_event not in str(item.get("event", "")).lower():
                continue
            if wanted_actor and wanted_actor != str(item.get("actor") or "").lower():
                continue
            if needle and needle not in json.dumps(item).lower():
                continue
            selected.append(item)
            if len(selected) >= int(limit):
                break
        return selected

    def audit_summary(self) -> dict:
        """Counts an administrator glances at before reading rows."""
        with self._lock:
            entries = list(self._audit)
        counts: dict = {}
        actors = set()
        for item in entries:
            counts[item.get("event", "")] = counts.get(item.get("event", ""), 0) + 1
            if item.get("actor"):
                actors.add(item["actor"])
        return {"total": len(entries), "capacity": AUDIT_MAX_ENTRIES,
                "retention": self.activity_retention(),
                "accounts": sorted(actors),
                "events": sorted(counts)}

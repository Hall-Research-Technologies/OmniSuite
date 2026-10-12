"""Portable, encrypted configuration backup and disaster restore.

The problem this solves: the OmniSuite host is an ordinary PC. When it dies,
everything that made the deployment *this* deployment -- who may route what,
what each display is called, which Multiview layouts exist, the credentials the
devices answer to -- dies with it, and rebuilding it by hand means walking the
site with a notebook.

So a backup has to be portable: it must restore onto a *different* computer,
which rules out anything protected by DPAPI or bound to a machine. It carries
device credentials, so it must be encrypted and authenticated. And it must
never be able to lock a technician out of the machine in front of them.

WHAT IS IN IT, AND WHY
======================

Every persistent file was classified rather than swept up. Four outcomes:

  configuration   the deployment's own decisions -- backed up
  machine-bound   valid only on the PC that wrote it -- carried, but applied
                  deliberately and reported, never written blindly
  regenerable     rediscovered by a scan or rebuilt on demand -- left out
  unsafe          would mislead or leak if moved -- left out, with a reason

    users.json              configuration   accounts, password hashes so
                                            passwords keep working,
                                            permissions, Direct Access records
    device_aliases.json     configuration   friendly names, keyed by MAC
    multiview_meta.json     configuration   saved layouts, keyed by MAC
    multiview_groups.json   configuration   decoder groups
    usb_associations.json   configuration   learned USB parent/child, by MAC
    usb_extenders.json      configuration   configured scan ranges
    scan_preferences.json   mixed           the scan expression is site
                                            configuration; interface_ip is
                                            machine-bound
    config.json             mixed           device credentials are the whole
                                            point; firmware_path is machine-
                                            bound
    remote_access.json      mixed           enabled/port/transport are
                                            configuration; the adapter GUID and
                                            address name hardware that does not
                                            exist on the replacement PC
    notice_ack.json         configuration   operator acknowledgements
    ui_preferences.json     configuration   appearance, if present
    topology_ack.json       configuration   LLDP acknowledgement, if present
    units_cache.json        REDUCED         479 KB of mostly volatile device
                                            state. Only the learned, expensive
                                            part is carried -- identity, name,
                                            model and the credential that unit
                                            answered to -- to be merged onto
                                            rebuilt records by MAC on the next
                                            scan.

    scan_results.json       regenerable     a scan rebuilds it
    units_view.csv          regenerable     a generated view
    security_audit.json     unsafe          it is a record of what happened on
                                            the OLD machine. Restoring it would
                                            make the new host's activity log
                                            claim events it never witnessed.
    remote_cert.pem         unsafe          a certificate is valid for the
    remote_key.pem                          names it carries, and those name the
                                            old machine's address. Carrying the
                                            private key off the host it was
                                            generated on buys nothing and costs
                                            a great deal; the replacement PC
                                            generates its own.
    *.tmp, *.pre-*          transient       atomic-write leftovers

Sessions and CSRF tokens live in memory only, so there is nothing to exclude.

DIRECT ACCESS SURVIVES A DISASTER
=================================

A Direct Access record holds a `digest` -- SHA-256 of the bearer token, used to
recognise it -- and a `blob`, the token itself protected by the platform secret
store so an administrator can display the link again later.

The digest is machine-independent. The blob is not: DPAPI cannot decrypt on
another computer. So the backup carries the digest and drops the blob, and the
consequence is exactly what a site wants: **every deployed panel keeps working
after the restore**, because the token it holds still matches. What is lost is
only the ability to *show* the link again, which regenerating restores at the
cost of the old URL.

The raw token is deliberately NOT carried. Putting a working passwordless
credential into a file that leaves the building would make the passphrase the
only thing between an attacker and the panels, and a digest achieves the
operational goal without it.

ENCRYPTION
==========

scrypt for the key, AES-256-GCM for the data, both from maintained
implementations -- `hashlib.scrypt` in the standard library and `cryptography`,
already a dependency of this project for its certificate work. Nothing here is
invented.

The header is plaintext on purpose: format version, application version and
creation time have to be readable *before* anyone is asked for a passphrase, so
an incompatible file can be refused without a pointless prompt. It is also the
GCM additional authenticated data, so altering a single byte of it -- including
the salt, the nonce or the version -- makes the tag fail and the file is
refused rather than half-read.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: The first line of every backup. A person looking at the file, and the
#: restore path, can both tell what it is without parsing anything.
MAGIC = "OMNISUITE-BACKUP"

#: Bumped when the payload's shape changes in a way an older reader could
#: misinterpret. A reader refuses a format from the future and migrates a
#: format from the past.
FORMAT_VERSION = 1

#: The oldest payload this build can still read and migrate forward.
MIN_SUPPORTED_FORMAT = 1

#: scrypt. The same work factor this project already uses for account
#: passwords, so there is one answer to "how hard is a guess here".
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
KEY_BYTES = 32
SALT_BYTES = 16
NONCE_BYTES = 12

#: Short enough not to be a nuisance, long enough that the scrypt work factor
#: is doing its job rather than covering for a four-character passphrase.
#:
#: Length is the only rule. No mandatory capital, digit or symbol: those push
#: people towards one predictable shape -- a word, a capital, a digit, an
#: exclamation mark -- and a passphrase nobody can remember gets written on
#: the box the backup is stored in, which is a worse outcome than a shorter
#: one. Letters, digits, punctuation and spaces are all accepted, and the
#: whole thing goes through scrypt either way.
MIN_PASSPHRASE = 8


class BackupError(Exception):
    """Something was wrong with the file, the passphrase, or the request.

    Carries a `reason` for a caller to branch on and a sentence for a person.
    One exception type for the whole module, so a stray ValueError escaping
    where callers expect this cannot surface as a 500 on a bad passphrase.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


# ======================================================================
# The container
# ======================================================================

def _derive(passphrase: str, salt: bytes) -> bytes:
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt,
                          n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                          dklen=KEY_BYTES, maxmem=64 * 1024 * 1024)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str, what: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise BackupError("corrupt",
                          "This file is damaged and cannot be read (%s)." % what)


def seal(payload: dict, passphrase: str, *, app_version: str = "",
         now: float = None) -> bytes:
    """Encrypt a payload into a backup file's bytes.

    The header is written first and is the additional authenticated data, so it
    cannot be edited without the tag failing.
    """
    if not isinstance(passphrase, str) or len(passphrase) < MIN_PASSPHRASE:
        raise BackupError(
            "weak_passphrase",
            "Choose a backup passphrase of at least %d characters. You will "
            "need it to restore, and nobody can recover it for you."
            % MIN_PASSPHRASE)
    # Whatever they chose is used as typed. Nothing is trimmed, folded or
    # rejected for its shape: a trailing space somebody meant is part of the
    # passphrase, and silently removing it would make the backup refuse the
    # very thing they typed to create it.

    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    header = {
        "format": FORMAT_VERSION,
        "omnisuite_version": app_version or "",
        "created_at": _timestamp(now),
        "cipher": "AES-256-GCM",
        "kdf": {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R,
                "p": SCRYPT_P, "salt": _b64(salt), "dklen": KEY_BYTES},
        "nonce": _b64(nonce),
    }
    # Sorted and compact so the bytes signed are the bytes written, whatever
    # dict ordering happens to be.
    header_line = json.dumps(header, sort_keys=True, separators=(",", ":"))
    aad = header_line.encode("utf-8")

    plaintext = json.dumps(payload, sort_keys=True).encode("utf-8")
    ciphertext = AESGCM(_derive(passphrase, salt)).encrypt(nonce, plaintext, aad)

    return b"\n".join([MAGIC.encode("ascii"), aad,
                       _b64(ciphertext).encode("ascii")]) + b"\n"


def read_header(blob: bytes) -> dict:
    """The non-secret header, without needing the passphrase.

    Lets the UI refuse an incompatible or damaged file before prompting for
    something the operator would have typed for nothing.
    """
    if not isinstance(blob, (bytes, bytearray)) or not blob.strip():
        raise BackupError("corrupt", "That file is empty.")
    parts = bytes(blob).split(b"\n", 2)
    if len(parts) < 3 or parts[0].strip() != MAGIC.encode("ascii"):
        raise BackupError(
            "not_a_backup",
            "That is not an OmniSuite backup file.")
    try:
        header = json.loads(parts[1].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BackupError("corrupt",
                          "This backup file's header is damaged.")
    if not isinstance(header, dict):
        raise BackupError("corrupt",
                          "This backup file's header is damaged.")

    fmt = header.get("format")
    if not isinstance(fmt, int):
        raise BackupError("corrupt",
                          "This backup file's header is damaged.")
    if fmt > FORMAT_VERSION:
        raise BackupError(
            "too_new",
            "This backup was written by a newer version of OmniSuite "
            "(backup format %d; this build reads up to %d). Restore it with "
            "that version, or upgrade this one." % (fmt, FORMAT_VERSION))
    if fmt < MIN_SUPPORTED_FORMAT:
        raise BackupError(
            "too_old",
            "This backup is in a format this build can no longer read "
            "(format %d)." % fmt)
    return header


def unseal(blob: bytes, passphrase: str) -> tuple:
    """(header, payload). Raises BackupError for every way it can go wrong."""
    header = read_header(blob)
    parts = bytes(blob).split(b"\n", 2)
    aad = parts[1]
    ciphertext = _unb64(parts[2].decode("ascii", "replace").strip(),
                        "the encrypted section")

    kdf = header.get("kdf") or {}
    if kdf.get("name") != "scrypt":
        raise BackupError("corrupt",
                          "This backup uses an encryption method this build "
                          "does not know.")
    salt = _unb64(str(kdf.get("salt") or ""), "the key material")
    nonce = _unb64(str(header.get("nonce") or ""), "the key material")
    if not salt or not nonce:
        raise BackupError("corrupt", "This backup file's header is damaged.")

    try:
        key = hashlib.scrypt(
            (passphrase or "").encode("utf-8"), salt=salt,
            n=int(kdf.get("n") or SCRYPT_N), r=int(kdf.get("r") or SCRYPT_R),
            p=int(kdf.get("p") or SCRYPT_P),
            dklen=int(kdf.get("dklen") or KEY_BYTES),
            maxmem=64 * 1024 * 1024)
    except (ValueError, OverflowError, MemoryError, TypeError):
        # A header claiming absurd scrypt parameters is a damaged or hostile
        # file, not a reason to try to allocate it. Which exception an
        # out-of-range value raises depends on the interpreter and platform:
        # Python 3.12 on Windows raises TypeError for a value that does not fit
        # a C unsigned int, where other builds raise ValueError or OverflowError.
        raise BackupError("corrupt", "This backup file's header is damaged.")

    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
    except InvalidTag:
        # One answer for a wrong passphrase and for a tampered file. They are
        # not distinguishable without weakening the authentication, and the
        # remedy an operator has is the same: check the passphrase, then check
        # the file came from where they think.
        raise BackupError(
            "cannot_decrypt",
            "That passphrase does not open this backup, or the file has been "
            "altered since it was written. Nothing has been changed.")

    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BackupError("corrupt",
                          "This backup decrypted but its contents are not "
                          "readable.")
    if not isinstance(payload, dict):
        raise BackupError("corrupt",
                          "This backup decrypted but its contents are not "
                          "readable.")
    return header, migrate(payload, header)


def migrate(payload: dict, header: dict) -> dict:
    """Bring an older supported payload up to the current shape.

    One place, so a reader never has to ask what an old file's section means.
    Format 1 is current and needs nothing; the function exists and is tested so
    that the next format has somewhere to go.
    """
    fmt = int(header.get("format") or FORMAT_VERSION)
    payload = dict(payload)
    if fmt < 1:                                   # pragma: no cover - refused
        raise BackupError("too_old", "This backup is too old to read.")
    payload.setdefault("sections", {})
    payload.setdefault("machine", {})
    payload["migrated_from"] = fmt
    return payload


def _timestamp(now: float = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now or time.time()))


def suggested_filename(app_version: str = "", now: float = None) -> str:
    """A name that says what it is and when, and is legal on all three OSes."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    version = "".join(c for c in (app_version or "") if c.isalnum() or c in "._-")
    middle = ("-" + version) if version else ""
    return "OmniSuite%s-backup-%s.omnibak" % (middle, stamp)



# ======================================================================
# What a deployment is made of
# ======================================================================
#
# Each entry says how a file is treated. `machine` fields are carried so the
# restore can REPORT them, and are stripped before anything is written, so a
# replacement PC never inherits an adapter GUID or a path that does not exist
# on it.

class Section:
    __slots__ = ("name", "filename", "machine_keys", "describe", "optional")

    def __init__(self, name, filename, machine_keys=(), describe=None,
                 optional=True):
        self.name = name
        self.filename = filename
        self.machine_keys = tuple(machine_keys)
        self.describe = describe or (lambda data: "")
        self.optional = optional


def _count(data, *keys):
    for key in keys:
        value = (data or {}).get(key)
        if isinstance(value, (list, dict)):
            return len(value)
    if isinstance(data, list):
        return len(data)
    return 0


SECTIONS = (
    Section("accounts", "users.json",
            describe=lambda d: "%d account(s)" % _count(d, "users")),
    Section("device_names", "device_aliases.json",
            describe=lambda d: "%d name(s)" % _count(d, "aliases")),
    Section("multiview", "multiview_meta.json",
            describe=lambda d: "%d display(s)" % _count(d, "decoders")),
    Section("multiview_groups", "multiview_groups.json",
            describe=lambda d: "%d group(s)" % _count(d, "groups")),
    Section("usb_associations", "usb_associations.json",
            describe=lambda d: "%d association(s)" % _count(d, "associations")),
    Section("usb_extenders", "usb_extenders.json",
            describe=lambda d: "%d scan range(s)" % _count(d, "ranges")),
    # interface_ip names a NIC on the old machine.
    Section("scan_preferences", "scan_preferences.json",
            machine_keys=("interface_ip",),
            describe=lambda d: (d or {}).get("scan_expression") or ""),
    # firmware_path is a folder on the old machine's disk.
    Section("device_credentials", "config.json",
            machine_keys=("firmware_path",),
            describe=lambda d: "device sign-in for %s"
                               % ((d or {}).get("username") or "admin")),
    # The adapter GUID and the address are hardware on the old machine.
    Section("remote_access", "remote_access.json",
            machine_keys=("adapter_id", "adapter_name", "address",
                          "cert_path", "key_path"),
            describe=lambda d: "remote access %s"
                               % ("on" if (d or {}).get("enabled") else "off")),
    Section("notices", "notice_ack.json"),
    Section("appearance", "ui_preferences.json"),
    Section("topology_notices", "topology_ack.json"),
)

#: Deliberately never exported. Each one has a reason, and the reasons are
#: different -- which is why this is a table and not a comment.
EXCLUDED = {
    "scan_results.json": "rebuilt by a scan",
    "units_view.csv": "a generated view",
    "security_audit.json": "the old machine's activity record",
    "remote_cert.pem": "valid only for the old machine's address",
    "remote_key.pem": "a private key does not travel",
}

#: The learned part of a device record: expensive to rediscover, and keyed by
#: hardware identity so it can be merged onto whatever a rescan finds.
LEARNED_DEVICE_FIELDS = ("hostname", "host", "model", "serial", "password",
                         "username")


def collect_devices(units) -> list:
    """Identity-keyed device facts worth carrying, and nothing else.

    `units_cache.json` is nearly half a megabyte of mostly volatile device
    state -- framerate options, HDCP lists, what was routed at the moment it
    was written. None of that is deployment configuration and all of it comes
    back on the next scan. What does not come back by itself is the credential
    a unit was learned to answer to.

    A record with no MAC is dropped rather than carried under its address: an
    address is not an identity, and a device later appearing at an old address
    must never inherit another unit's authorisation.
    """
    out = []
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        mac = _normal_mac(unit.get("mac"))
        if not mac:
            continue
        record = {"mac": mac}
        for field in LEARNED_DEVICE_FIELDS:
            value = unit.get(field)
            if value not in (None, ""):
                record[field] = value
        out.append(record)
    out.sort(key=lambda r: r["mac"])
    return out


def _normal_mac(raw) -> str:
    text = "".join(ch for ch in str(raw or "") if ch.isalnum()).lower()
    return ":".join(text[i:i + 2] for i in range(0, 12, 2)) if len(text) == 12 else ""


def strip_direct_access_blobs(accounts: dict) -> int:
    """Drop the platform-protected copy of every Direct Access token.

    The digest stays, so a panel already holding its link keeps working after
    the restore -- which is the whole operational point. The blob cannot be
    decrypted on another machine anyway, and carrying the raw token instead
    would put a working passwordless credential in a file that leaves the
    building.

    Returns how many links were carried, so the restore can tell the
    administrator which ones they will no longer be able to re-display.
    """
    carried = 0
    for account in (accounts or {}).get("users") or []:
        record = account.get("direct_access")
        if not isinstance(record, dict):
            continue
        if record.get("enabled") and record.get("digest"):
            carried += 1
        record["blob"] = ""
        record["protection"] = "not-portable"
    return carried


def build_payload(data_dir, units, app_version="") -> dict:
    """Read the deployment off disk and classify it as it goes."""
    data_dir = Path(data_dir)
    sections, machine, notes = {}, {}, []

    for section in SECTIONS:
        path = data_dir / section.filename
        if not path.exists():
            continue
        try:
            content = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError, UnicodeDecodeError) as e:
            # A file that cannot be read is named rather than silently
            # skipped: a backup missing a section without saying so is worse
            # than one that refuses.
            raise BackupError(
                "unreadable_source",
                "%s could not be read, so this backup would be incomplete "
                "(%s)." % (section.filename, type(e).__name__))
        if not isinstance(content, dict):
            continue

        held = {}
        for key in section.machine_keys:
            if key in content:
                held[key] = content.pop(key)
        if held:
            machine[section.name] = held
        sections[section.name] = content

    links = strip_direct_access_blobs(sections.get("accounts") or {})
    if links:
        notes.append("%d Direct Access link(s) carried; the links keep "
                     "working but cannot be displayed again after a restore "
                     "onto another computer." % links)

    devices = collect_devices(units)
    return {
        "sections": sections,
        "machine": machine,
        "devices": devices,
        "notes": notes,
        "excluded": dict(EXCLUDED),
        "app_version": app_version,
    }


def summarise(payload: dict, header: dict = None) -> dict:
    """What the administrator is shown before anything is touched."""
    sections = payload.get("sections") or {}
    rows = []
    for section in SECTIONS:
        if section.name not in sections:
            continue
        rows.append({"name": section.name,
                     "detail": section.describe(sections[section.name]) or ""})
    return {
        "created_at": (header or {}).get("created_at") or "",
        "omnisuite_version": (header or {}).get("omnisuite_version") or "",
        "format": (header or {}).get("format"),
        "sections": rows,
        "devices": len(payload.get("devices") or []),
        "notes": list(payload.get("notes") or []),
        "machine_specific": sorted((payload.get("machine") or {}).keys()),
        "excluded": payload.get("excluded") or dict(EXCLUDED),
    }


# ======================================================================
# Restore: snapshot, apply, and put it back if anything goes wrong
# ======================================================================

def snapshot(data_dir, into) -> list:
    """Copy every file the restore could touch, before it touches any.

    Not a nicety. A restore that fails halfway leaves a deployment that is
    half one site and half another, which is worse than either -- so the way
    back exists before there is anything to come back from.
    """
    data_dir, into = Path(data_dir), Path(into)
    into.mkdir(parents=True, exist_ok=True)
    saved = []
    for section in SECTIONS:
        source = data_dir / section.filename
        if source.exists():
            shutil.copy2(source, into / section.filename)
            saved.append(section.filename)
    return saved


def rollback(snapshot_dir, data_dir, touched) -> tuple:
    """Put back exactly what was there. Returns (restored, failures)."""
    snapshot_dir, data_dir = Path(snapshot_dir), Path(data_dir)
    restored, failures = [], []
    for name in touched:
        saved = snapshot_dir / name
        target = data_dir / name
        try:
            if saved.exists():
                shutil.copy2(saved, target)
                restored.append(name)
            elif target.exists():
                # It did not exist before the restore, so putting it back
                # means removing it.
                target.unlink()
                restored.append(name)
        except OSError as e:
            failures.append("%s (%s)" % (name, type(e).__name__))
    return restored, failures


def apply_payload(payload: dict, data_dir, *, write_json) -> dict:
    """Write the restored configuration, or leave the installation alone.

    `write_json` is the application's own atomic writer, passed in rather than
    reimplemented: every write to a shared JSON file in this project is a
    unique temp file, fsync and os.replace under one lock, and a restore is
    not an excuse to open that file a second way.
    """
    data_dir = Path(data_dir)
    sections = payload.get("sections") or {}
    machine = payload.get("machine") or {}
    written, skipped, attention = [], [], []

    for section in SECTIONS:
        content = sections.get(section.name)
        if content is None:
            skipped.append(section.name)
            continue
        content = json.loads(json.dumps(content))     # never share the payload

        # Machine-bound values are NOT written. The replacement PC keeps
        # whatever it already had -- its own adapter, its own paths -- and the
        # administrator is told which decisions did not travel.
        held = machine.get(section.name) or {}
        current = {}
        target = data_dir / section.filename
        if target.exists():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    current = existing
            except (ValueError, OSError, UnicodeDecodeError):
                current = {}
        for key in section.machine_keys:
            if key in current:
                content[key] = current[key]
            if key in held:
                attention.append({
                    "section": section.name, "setting": key,
                    "was": _readable(held[key]),
                    "now": _readable(current.get(key)),
                })

        write_json(target, content)
        written.append(section.filename)

    return {"written": written, "skipped": skipped, "attention": attention}


def _readable(value) -> str:
    """Machine-specific values are shown to an administrator, so no secrets.

    Nothing in `machine_keys` is a credential, and this is the only place a
    carried value is rendered -- so it stays a small, obvious function rather
    than a formatting call scattered about.
    """
    if value in (None, ""):
        return "not set"
    return str(value)[:120]


def merge_devices(payload: dict, units) -> tuple:
    """Put learned device facts back onto whatever a scan has found.

    Matched by hardware identity and never by address. A unit that has moved
    to a new address is still that unit; a different unit that has taken the
    old address is not, and must not inherit anything.

    Returns (units, matched, unmatched) -- unmatched being equipment the
    backup knew about that this installation has not seen yet, which is
    ordinary immediately after a restore and before the first scan.
    """
    remembered = {r["mac"]: r for r in (payload.get("devices") or [])
                  if isinstance(r, dict) and r.get("mac")}
    units = [dict(u) for u in (units or []) if isinstance(u, dict)]
    matched = 0
    for unit in units:
        mac = _normal_mac(unit.get("mac"))
        record = remembered.get(mac)
        if not record:
            continue
        matched += 1
        for field, value in record.items():
            if field == "mac":
                continue
            # Live data wins where the scan actually learned something; the
            # backup fills what it did not.
            if unit.get(field) in (None, ""):
                unit[field] = value
    return units, matched, len(remembered) - matched



def validate(payload: dict) -> list:
    """Everything wrong with this payload, before anything is written.

    Returns a list of sentences -- all of them, not the first -- because an
    operator fixing a backup wants to know what is wrong with it rather than
    discovering the next problem on the next attempt.

    This is deliberately about *structure and relationships*, not taste. A
    backup with no Multiview layouts is perfectly valid; one whose accounts
    are not a list is not, and would fail halfway through writing.
    """
    problems = []
    if not isinstance(payload.get("sections"), dict):
        return ["the backup has no configuration in it"]

    sections = payload["sections"]

    accounts = sections.get("accounts")
    if accounts is not None:
        users = (accounts or {}).get("users")
        if not isinstance(users, list):
            problems.append("the account list is damaged")
        else:
            seen_ids, seen_names = set(), set()
            for entry in users:
                if not isinstance(entry, dict):
                    problems.append("an account record is damaged")
                    break
                for field in ("id", "username", "password_hash", "role"):
                    if not entry.get(field):
                        problems.append(
                            "an account is missing its %s"
                            % ("sign-in name" if field == "username"
                               else field.replace("_", " ")))
                        break
                else:
                    # Duplicates would silently collapse on load, taking one
                    # person's permissions with them.
                    if entry["id"] in seen_ids:
                        problems.append("two accounts share an identifier")
                    if entry["username"].lower() in seen_names:
                        problems.append("two accounts share the sign-in name "
                                        "%r" % entry["username"])
                    seen_ids.add(entry["id"])
                    seen_names.add(entry["username"].lower())

    devices = payload.get("devices")
    if devices is not None and not isinstance(devices, list):
        problems.append("the device list is damaged")
    elif isinstance(devices, list):
        for record in devices:
            if not isinstance(record, dict) or not _normal_mac(record.get("mac")):
                # A device record without a usable hardware identity cannot be
                # matched to anything, and matching it by address is exactly
                # what must never happen.
                problems.append("a device record has no hardware identity")
                break

    for key in ("machine", "sections"):
        if key in payload and not isinstance(payload[key], dict):
            problems.append("the backup's %s section is damaged" % key)

    # Every section must be an object; a list or a string here would be
    # written to disk and then fail to load on the next start.
    for name, content in sections.items():
        if not isinstance(content, dict):
            problems.append("the %s section is damaged"
                            % name.replace("_", " "))

    # Deduplicate while keeping the order they were found in.
    out, seen = [], set()
    for problem in problems:
        if problem not in seen:
            seen.add(problem)
            out.append(problem)
    return out

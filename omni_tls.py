"""Transport security for the remote listener.

Remote access sends a password and then carries a session cookie, so on a
routable network it needs TLS. Two ways to get a certificate, in the order an
administrator should prefer them:

  1. **Supplied.** A certificate and private key the site already has, from its
     own CA or a real issuer. This needs nothing beyond the standard library:
     `ssl` loads the pair and `make_server` takes the context.

  2. **Generated.** A self-signed certificate OmniSuite creates for the address
     it is about to serve. This needs `cryptography`, and that is the one
     dependency this adds -- 11 MB, almost all of it a Rust extension, on a
     33 MB executable. It is carried because an AV technician setting up a
     classroom will not have a certificate, and without this they would stay on
     plain HTTP, which is the thing TLS was added to stop. If the library is
     absent, generation is unavailable and supplied certificates still work.

  3. A reverse proxy in front, which is a deployment choice rather than code.

Nothing here invents cryptography. Generation is `cryptography`'s own
certificate builder with conservative parameters; serving is the standard
library's TLS.

Two rules hold throughout:

  * **A private key is a secret.** It is never returned by an API, never
    logged, never copied anywhere a browser can reach, and never bundled into
    the executable. What this module publishes about a certificate is its
    subject, its validity and its fingerprint -- never key material.

  * **A failure is never a downgrade.** If configured TLS cannot start, the
     remote listener does not start. It never quietly becomes HTTP, because an
     administrator who configured HTTPS and got HTTP would have no way of
     knowing.
"""
from __future__ import annotations

import datetime
import hashlib
import ipaddress
import logging
import os
import re
import socket
import ssl
from pathlib import Path

log = logging.getLogger(__name__)

#: How long a generated certificate is good for, in CALENDAR years from the
#: moment it is generated. Three years is long enough not to be a recurring
#: chore and short enough to be replaced within the life of a room. It is
#: calendar arithmetic, not a day count: 3 * 365 would be a day short whenever a
#: leap day falls inside the period, and the expiry would drift off the
#: anniversary the operator can see on the certificate.
GENERATED_VALIDITY_YEARS = 3

#: Who a generated certificate says it is. The common name is the Windows
#: machine name; the organisation and unit say which product made it, so an
#: administrator looking at the browser's certificate viewer can tell this is
#: OmniSuite's own certificate and not something else on the machine.
CERT_ORGANIZATION = "Hall Research"
CERT_ORGANIZATIONAL_UNIT = "OmniSuite"

#: X.509 upper bounds (RFC 5280, appendix A): a common name is at most 64
#: characters, and a DNS name is at most 253 with each label at most 63.
MAX_COMMON_NAME = 64
MAX_DNS_LABEL = 63
MAX_DNS_NAME = 253

#: Used only when the machine reports no usable name at all.
FALLBACK_MACHINE_NAME = "OmniSuite"

#: File names inside the data directory. The key sits beside the certificate
#: and is created with an owner-only mode where the platform honours it.
CERT_FILE = "remote_cert.pem"
KEY_FILE = "remote_key.pem"


class TlsError(Exception):
    """A certificate or key could not be used. Carries an operator sentence."""


def generation_available() -> bool:
    """Whether a self-signed certificate can be made on this build."""
    try:
        import cryptography  # noqa: F401
        return True
    except Exception:
        return False


# ------------------------------------------------------- identity helpers
def add_calendar_years(moment: datetime.datetime, years: int) -> datetime.datetime:
    """`moment` plus `years` calendar years, keeping the date and time of day.

    The one awkward date is 29 February. When the target year has no 29
    February the result is **28 February** of that year: a certificate made on
    a leap day must not outlive the period it was meant to cover, and rolling
    forward to 1 March would give it an extra day. So 2028-02-29 plus three
    years is 2031-02-28, and 2028-02-29 plus four is 2032-02-29.
    """
    target_year = moment.year + int(years)
    try:
        return moment.replace(year=target_year)
    except ValueError:                       # 29 February into a common year
        return moment.replace(year=target_year, day=28)


def machine_name() -> str:
    """The Windows machine name, made safe to put in a certificate.

    Taken from the operating system, never from the request. Anything outside
    letters, digits, hyphen, underscore and dot becomes a hyphen, so a name that
    is not ASCII or carries a separator cannot produce an invalid DNS name or
    an unprintable subject. It is bounded to what X.509 allows, and an empty
    result falls back to a fixed product name rather than an empty field.
    """
    raw = ""
    try:
        raw = socket.gethostname()
    except OSError:
        pass
    raw = raw or os.environ.get("COMPUTERNAME", "")
    return sanitise_machine_name(raw)


def sanitise_machine_name(raw) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]", "-", str(raw or "").strip())
    labels = []
    for label in text.split("."):
        label = label.strip("-_")[:MAX_DNS_LABEL].strip("-_")
        if label:
            labels.append(label)
    name = ".".join(labels)[:MAX_DNS_NAME].strip(".-_")
    name = name[:MAX_COMMON_NAME].strip(".-_")
    return name or FALLBACK_MACHINE_NAME


# ---------------------------------------------------------------- loading
def load_context(cert_path, key_path) -> ssl.SSLContext:
    """Build a server TLS context, or raise TlsError saying what is wrong.

    The distinctions matter to whoever has to fix it: a missing file, a file
    that is not a certificate, and a key that does not match the certificate
    are three different mistakes with three different remedies.
    """
    cert = Path(cert_path or "")
    key = Path(key_path or "")
    if not cert_path or not key_path:
        raise TlsError("No certificate and key have been chosen.")
    if not cert.exists():
        raise TlsError(f"The certificate file was not found: {cert.name}")
    if not key.exists():
        raise TlsError(f"The private key file was not found: {key.name}")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # No SSLv3, no TLS 1.0/1.1. Everything in use since 2020 speaks 1.2.
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.load_cert_chain(str(cert), str(key))
    except ssl.SSLError as exc:
        message = str(exc).lower()
        if "key values mismatch" in message or "mismatch" in message:
            raise TlsError("That private key does not match that certificate.")
        if "bad decrypt" in message or "password" in message:
            raise TlsError("That private key appears to be encrypted. Supply a "
                           "key without a passphrase.")
        raise TlsError("That certificate and key could not be read. Check they "
                       "are PEM files and that the key is not encrypted.")
    except (OSError, ValueError) as exc:
        raise TlsError(f"That certificate or key could not be read "
                       f"({type(exc).__name__}).")
    return context


def describe(cert_path, now=None) -> dict:
    """What Settings shows about a certificate. Never any key material.

    Read with the standard library so this works whether or not the generation
    dependency is present.
    """
    cert = Path(cert_path or "")
    if not cert_path or not cert.exists():
        return {"present": False}

    try:
        pem = cert.read_text(encoding="utf-8")
        der = ssl.PEM_cert_to_DER_cert(pem)
    except Exception as exc:
        return {"present": True, "readable": False,
                "error": f"Not a readable PEM certificate ({type(exc).__name__})."}

    described = {
        "present": True,
        "readable": True,
        # A fingerprint is safe to show and is how somebody confirms the
        # certificate their browser warned about is the one OmniSuite is using.
        "fingerprint": hashlib.sha256(der).hexdigest()[:32].upper(),
    }
    try:
        # _ssl has a decoder that needs no third-party library.
        import ssl as _ssl
        details = _ssl._ssl._test_decode_cert(str(cert))
        subject = dict(x[0] for x in details.get("subject", ()) if x)
        issuer = dict(x[0] for x in details.get("issuer", ()) if x)
        # The names the certificate is actually valid for.
        #
        # Every browser in use decides this from the subjectAltName extension
        # and ignores the common name entirely, so anything the application
        # says about whether a certificate matches an address has to be decided
        # from here too. Reporting the CN and comparing the CN would agree with
        # itself and disagree with the browser, which is the worst of both.
        described["san"] = [str(value) for _kind, value
                            in details.get("subjectAltName", ()) if value]
        # The addresses among those names. With the machine name as the common
        # name, "who is this certificate for" is answered by the addresses, so
        # the wording that says "for <address>" reads these and not the CN.
        described["addresses"] = [str(value) for kind, value
                                  in details.get("subjectAltName", ())
                                  if value and kind == "IP Address"]
        described.update({
            "organization": subject.get("organizationName") or "",
            "organizational_unit": subject.get("organizationalUnitName") or "",
            "subject": subject.get("commonName") or "",
            "issuer": issuer.get("commonName") or "",
            "not_after": details.get("notAfter") or "",
            "not_before": details.get("notBefore") or "",
            "self_signed": bool(subject) and subject == issuer,
        })
        if described["not_after"]:
            # The certificate's own timestamp is UTC; compare against an aware
            # UTC now rather than the deprecated naive utcnow().
            expires = datetime.datetime.strptime(
                described["not_after"], "%b %d %H:%M:%S %Y %Z").replace(
                    tzinfo=datetime.timezone.utc)
            current = now or datetime.datetime.now(datetime.timezone.utc)
            remaining = (expires - current).days
            described["days_remaining"] = remaining
            described["expired"] = remaining < 0
    except Exception as exc:
        log.info("[TLS] certificate details unavailable: %s", type(exc).__name__)
    return described


def label_for(described) -> str:
    """The addresses a certificate names, for a sentence; else its subject.

    A generated certificate's common name is the machine name, so "this
    certificate is for X" must read the addresses it carries. A certificate
    that carries none (a supplied one, or one made before this) falls back to
    its subject, which is what those certificates have always been described by.
    """
    described = described or {}
    addresses = [a for a in described.get("addresses") or [] if a]
    return ", ".join(addresses) or described.get("subject") or ""


def covers(described, address) -> bool:
    """Whether a described certificate is valid for `address`.

    Decided from the subject alternative names and from nothing else. A
    certificate whose common name is the address but whose SAN is not will be
    rejected by every browser released this decade, so believing the common
    name here would produce an application that says "this is fine" about a
    connection the operator cannot open.

    A certificate whose SANs could not be read answers False. That is the fail-
    closed direction: it produces a visible "this may not match" rather than a
    silent claim that it does.
    """
    wanted = str(address or "").strip()
    if not wanted:
        return False
    names = [str(n).strip() for n in (described or {}).get("san") or []]
    if not names:
        return False
    try:
        target = ipaddress.ip_address(wanted)
    except ValueError:
        target = None
    for name in names:
        if name.lower() == wanted.lower():
            return True
        if target is not None:
            try:
                if ipaddress.ip_address(name) == target:
                    return True
            except ValueError:
                continue
    return False


def is_generated(described, cert_path, configured_cert_path="") -> bool:
    """Whether this is the certificate OmniSuite made, rather than a supplied one.

    A supplied certificate belongs to the site and is never touched. Only a
    certificate at OmniSuite's own path, with no supplied path configured, may
    be regenerated -- and even then only when an administrator asks.
    """
    if configured_cert_path:
        return False
    return bool((described or {}).get("present")) and bool(cert_path)


# ------------------------------------------------------------- generating
def generate_self_signed(address, cert_path, key_path, now=None) -> dict:
    """Create a self-signed certificate for `address`.

    Identity: common name is the Windows machine name, organisation "Hall
    Research", unit "OmniSuite". The names it is valid for are the machine name
    (DNS) and `address` (IP, or DNS when it is not an address). Validity is
    three calendar years from `now` (see add_calendar_years); `now` exists so a
    test can fix the clock and is never supplied by a caller in production.

    Honest about what this is worth: it stops passive interception, and it does
    not prove anything about who is answering. The first time a browser sees
    it, somebody has to accept a warning -- and that is the moment an attacker
    could substitute their own. On a trusted classroom network that is a
    reasonable trade; on anything else, supply a real certificate.
    """
    if not generation_available():
        raise TlsError(
            "This build cannot create a certificate. Supply a certificate and "
            "private key instead.")

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    text = str(address or "").strip()
    if not text:
        raise TlsError("An address is required to create a certificate.")

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = machine_name()
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, CERT_ORGANIZATION),
        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME,
                           CERT_ORGANIZATIONAL_UNIT),
    ])

    # The address people actually type has to be in the SAN, or every modern
    # browser rejects it regardless of the common name. The machine name is
    # there as well, so the computer can be reached by name; it is a DNS name
    # and never an address.
    alternatives = [x509.DNSName(name)]
    try:
        alternatives.append(x509.IPAddress(ipaddress.ip_address(text)))
    except ValueError:
        # Not an address: whatever was given is a name, and is kept as one.
        if text.lower() != name.lower():
            alternatives.append(x509.DNSName(text))

    # Whole seconds: that is all an X.509 time carries, so what is requested
    # and what a parser reads back are the same instant.
    generated = (now or datetime.datetime.now(datetime.timezone.utc)
                 ).replace(microsecond=0)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(generated)
        .not_valid_after(add_calendar_years(generated, GENERATED_VALIDITY_YEARS))
        .add_extension(x509.SubjectAlternativeName(alternatives), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                       critical=True)
        .sign(key, hashes.SHA256())
    )

    cert_path = Path(cert_path)
    key_path = Path(key_path)
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    # The key is written first and restrictively, then the certificate, so a
    # half-written pair never looks complete.
    key_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption())
    _write_private(key_path, key_bytes)
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))

    log.info("[TLS] generated a self-signed certificate for %s", text)
    return {"ok": True, "address": text, **describe(cert_path, now=now)}


def _write_private(path: Path, data: bytes) -> None:
    """Write key material as the owner only, where the platform allows it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with a restrictive mode rather than widening it afterwards, so
    # there is no window in which the key is world-readable.
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Windows honours this only loosely; the file still lands in the
        # per-user data directory, which is the real protection there.
        pass


def restrict_existing(path) -> None:
    """Tighten a key file OmniSuite did not create, best effort."""
    try:
        os.chmod(str(path), 0o600)
    except OSError:
        pass

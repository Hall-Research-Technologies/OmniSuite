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
import ssl
from pathlib import Path

log = logging.getLogger(__name__)

#: How long a generated certificate is good for. Long enough not to be a
#: recurring chore, short enough to be replaced within the life of a room.
GENERATED_VALIDITY_DAYS = 825

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


def describe(cert_path) -> dict:
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
        described.update({
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
            remaining = (expires - datetime.datetime.now(
                datetime.timezone.utc)).days
            described["days_remaining"] = remaining
            described["expired"] = remaining < 0
    except Exception as exc:
        log.info("[TLS] certificate details unavailable: %s", type(exc).__name__)
    return described


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
def generate_self_signed(address, cert_path, key_path,
                         days=GENERATED_VALIDITY_DAYS) -> dict:
    """Create a self-signed certificate for `address`.

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
    subject = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME,
                           "Hall Research Technologies LLC"),
        x509.NameAttribute(NameOID.COMMON_NAME, text),
    ])

    # The address people actually type has to be in the SAN, or every modern
    # browser rejects it regardless of the common name.
    alternatives = [x509.DNSName(text)]
    try:
        alternatives.append(x509.IPAddress(ipaddress.ip_address(text)))
        alternatives = alternatives[1:]          # an address is not a DNS name
    except ValueError:
        pass

    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=int(days)))
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
    return {"ok": True, "address": text, **describe(cert_path)}


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

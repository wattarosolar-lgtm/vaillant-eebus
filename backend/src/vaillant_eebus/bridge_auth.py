"""Credentials that protect the bridge's gRPC API.

All apps with host networking, and Home Assistant Core under HAOS, share the
host's loopback interface. Without credentials any of them could call the
bridge's write RPCs past this app's demo mode. The bridge therefore runs with
grpc.security.mode "tls_token": TLS with a self-signed certificate plus a
bearer token. Both are generated once into /data/eebus/grpc, which only this
container can read; the backend trusts exactly this certificate and sends the
token with every call.

The s6 oneshot init-grpc-credentials runs `python -m vaillant_eebus.bridge_auth
<directory>` before the bridge and the backend start.
"""

from __future__ import annotations

import argparse
import ipaddress
import logging
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

_LOGGER = logging.getLogger(__name__)

CERT_FILE = "cert.pem"
KEY_FILE = "key.pem"
TOKEN_FILE = "token"
# The name the backend expects in the certificate, independent of the address it dials.
SERVER_NAME = "eebus-bridge"
# Long enough never to expire while the app is installed; a new pair would only
# be needed if the files were deleted.
VALIDITY = timedelta(days=100 * 365)


@dataclass(frozen=True, slots=True)
class BridgeCredentials:
    # PEM; the backend trusts only this certificate.
    certificate: bytes
    token: str

    @classmethod
    def load(cls, directory: Path) -> BridgeCredentials:
        token = (directory / TOKEN_FILE).read_text(encoding="ascii").strip()
        if not token:
            raise ValueError(f"empty bridge token in {directory}")
        return cls(certificate=(directory / CERT_FILE).read_bytes(), token=token)


def ensure(directory: Path) -> bool:
    """Create certificate, key and token unless all three exist; True if they were created.

    They appear together or not at all: the files are written to a temporary
    directory next to the target, which is then renamed into place.
    """
    if all((directory / name).is_file() for name in (CERT_FILE, KEY_FILE, TOKEN_FILE)):
        return False
    directory.parent.mkdir(parents=True, exist_ok=True)
    for leftover in directory.parent.glob(f"{directory.name}.tmp-*"):
        shutil.rmtree(leftover, ignore_errors=True)
    # Incomplete, e.g. after a file was deleted by hand.
    shutil.rmtree(directory, ignore_errors=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{directory.name}.tmp-", dir=directory.parent))
    try:
        certificate, key = _self_signed()
        _write_private(staging / KEY_FILE, key)
        _write_private(staging / CERT_FILE, certificate)
        _write_private(staging / TOKEN_FILE, secrets.token_urlsafe(32).encode("ascii"))
        os.replace(staging, directory)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return True


def _self_signed() -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, SERVER_NAME)])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + VALIDITY)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(SERVER_NAME), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        # Its own trust anchor: the backend trusts this one certificate as its root.
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
    )


def _write_private(path: Path, content: bytes) -> None:
    """Readable by the owner only, like the 0700 directory tempfile creates."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(content)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create the credentials of the bridge's gRPC API once")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if ensure(args.directory):
        _LOGGER.info("Created the credentials of the bridge's gRPC API in %s", args.directory)


if __name__ == "__main__":
    main()

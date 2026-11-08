"""Generate local-development HTTPS files; does not install or trust certificates."""
import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def generate(hosts, output):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory is not empty. Reuse its files or select a new --out directory.")
    output.mkdir(parents=True, mode=0o700, exist_ok=True)
    now = datetime.now(timezone.utc)
    root_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Assistive IoE local development CA")])
    root = (x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name)
            .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .sign(root_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    names = []
    for host in dict.fromkeys(["localhost", "127.0.0.1"] + hosts):
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
            .issuer_name(root_name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
            .sign(root_key, hashes.SHA256()))
    for name, value in [("development-ca.crt", root.public_bytes(serialization.Encoding.PEM)),
                        ("gateway.crt", cert.public_bytes(serialization.Encoding.PEM)),
                        ("gateway.key", key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))]:
        path = output / name
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
    # The one-purpose CA signing key is intentionally not retained.
    print("HTTPS files:", output.resolve())
    print("CA SHA-256 fingerprint:", root.fingerprint(hashes.SHA256()).hex())
    print("Nothing was installed in any trust store. The phone must trust development-ca.crt for local HTTPS.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="append", required=True, help="Gateway IP or DNS name; may be repeated")
    parser.add_argument("--out", type=Path, default=Path(".local/tls"))
    args = parser.parse_args()
    try:
        generate(args.host, args.out)
    except ValueError as error:
        parser.error(str(error))

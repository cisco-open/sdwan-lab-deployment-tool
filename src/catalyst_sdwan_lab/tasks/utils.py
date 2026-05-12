import datetime
import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import typer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.types import PrivateKeyTypes
from rich.console import Console
from virl2_client import ClientLibrary

from catalyst_sdwan_lab.manager_client import ManagerClient

console = Console()
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CERTS_DIR = DATA_DIR / "certs"
CML_DEPLOY_TEMPLATES_DIR = DATA_DIR / "cml_lab_definition" / "deploy"
CML_BACKUP_TEMPLATES_DIR = DATA_DIR / "cml_lab_definition" / "backup"
CML_NODES_DEFINITION_DIR = DATA_DIR / "cml_nodes_definition"
MANAGER_CONFIGS_DIR = DATA_DIR / "manager_configs"
CONTROLLER_TEMPLATES_DIR = MANAGER_CONFIGS_DIR / "controller_templates"
DEFAULT_SERIAL_FILE = DATA_DIR / "serial_files" / "serialFile.viptela"


def basic_configuration_path(ip_type: str) -> Path:
    return MANAGER_CONFIGS_DIR / f"basic_configuration_{ip_type}.tar.gz"


_CRYPT64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_SHA512_TRANSPOSE = (
    42, 21,  0,  1, 43, 22, 23,  2, 44, 45, 24,  3,  4, 46, 25, 26,
     5, 47, 48, 27,  6,  7, 49, 28, 29,  8, 50, 51, 30,  9, 10, 52,
    31, 32, 11, 53, 54, 33, 12, 13, 55, 34, 35, 14, 56, 57, 36, 15,
    16, 58, 37, 38, 17, 59, 60, 39, 18, 19, 61, 40, 41, 20, 62, 63,
)


def sha512_crypt(password: str, rounds: int = 5000) -> str:
    """Hash a password in Linux shadow $6$ (sha512crypt) format."""
    pw = password.encode("utf-8")
    salt = "".join(_CRYPT64[b % 64] for b in os.urandom(16)).encode("ascii")
    pw_len, salt_len = len(pw), len(salt)

    def _rep(b: bytes, n: int) -> bytes:
        return (b * (n // len(b) + 1))[:n] if b else b""

    db = hashlib.sha512(pw + salt + pw).digest()

    a = hashlib.sha512(pw + salt)
    a.update(_rep(db, pw_len))
    i = pw_len
    while i:
        a.update(db if i & 1 else pw)
        i >>= 1
    da = a.digest()

    dp = _rep(hashlib.sha512(pw * pw_len).digest(), pw_len)
    ds = hashlib.sha512(salt * (16 + da[0])).digest()[:salt_len]

    dc = da
    for i in range(rounds):
        c = hashlib.sha512(dp if i & 1 else dc)
        if i % 3:
            c.update(ds)
        if i % 7:
            c.update(dp)
        c.update(dc if i & 1 else dp)
        dc = c.digest()

    t = bytes(dc[i] for i in _SHA512_TRANSPOSE)
    out = []
    for k in range(0, 63, 3):
        v = t[k] | (t[k + 1] << 8) | (t[k + 2] << 16)
        out += [_CRYPT64[(v >> s) & 0x3f] for s in (0, 6, 12, 18)]
    out += [_CRYPT64[(t[63] >> s) & 0x3f] for s in (0, 6)]

    prefix = f"$6$rounds={rounds}$" if rounds != 5000 else "$6$"
    return f"{prefix}{salt.decode('ascii')}${''.join(out)}"

@dataclass
class Certs:
    cert: str
    key: str
    chain: str


def load_certs() -> Certs:
    names = {"cert": "signCA.pem", "key": "signCA.key", "chain": "chainCA.pem"}
    loaded: dict[str, str] = {}
    for attr, filename in names.items():
        path = CERTS_DIR / filename
        if not path.exists():
            log.error("Certificate file not found: %s", path)
            raise typer.Exit(1)
        loaded[attr] = path.read_text()
    return Certs(**loaded)


def resolve_image(cml: ClientLibrary, node_type: str, version: str) -> str:
    available = [
        img["id"]
        for img in cml.definitions.image_definitions_for_node_definition(node_type)
    ]
    exact = f"{node_type}-{version}"
    if exact in available:
        return exact
    if node_type in ("cat-sdwan-controller", "cat-sdwan-validator"):
        parts = version.split(".")
        if len(parts) == 4 and parts[-1] == "1":
            fallback_version = ".".join(parts[:-1])
        else:
            parts[-1] = str(int(parts[-1]) - 1)
            fallback_version = ".".join(parts)
        fallback = f"{node_type}-{fallback_version}"
        if fallback in available:
            log.debug("%s: exact version not found, using %s", node_type, fallback)
            return fallback
    label = node_type.split("-")[2].title()
    versions = [img_id[len(node_type) + 1:] for img_id in available]
    log.error(
        "%s image %s not found in CML. Available: %s",
        label, version, ", ".join(versions) or "none",
    )
    raise typer.Exit(1)


def sign_device_cert(client: ManagerClient, certs: Certs, device_ip: str) -> None:
    csr = client.generate_csr(device_ip)
    cert = sign_csr(certs.cert, certs.key, csr)
    task_id = client.install_signed_cert(cert)
    client.wait_for_task(task_id)
    log.info("Certificate installed for %s", device_ip)


def sign_csr(ca_cert_pem: str, ca_key_pem: str, csr_pem: str) -> str:
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem.encode())
    ca_key: PrivateKeyTypes = serialization.load_pem_private_key(ca_key_pem.encode(), password=None)
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2 * 365))
        .sign(ca_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def verify_cml_version(cml: ClientLibrary) -> None:
    version = cml.check_controller_version()
    if version is None or (version.major, version.minor) < (2, 7):
        log.error("CML 2.7 or later is required.")
        raise typer.Exit(1)

import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from typer import Exit
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from catalyst_sdwan_lab.tasks.sign import run
from catalyst_sdwan_lab.tasks.utils import sign_csr


def _make_ca() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode(),
    )


def _make_csr() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Device")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


class TestSignCsr:
    def test_returns_pem_certificate(self) -> None:
        ca_cert, ca_key = _make_ca()
        result = sign_csr(ca_cert, ca_key, _make_csr())
        assert result.startswith("-----BEGIN CERTIFICATE-----")
        assert "-----END CERTIFICATE-----" in result

    def test_signed_by_ca(self) -> None:
        ca_cert_pem, ca_key = _make_ca()
        result = sign_csr(ca_cert_pem, ca_key, _make_csr())
        cert = x509.load_pem_x509_certificate(result.encode())
        ca = x509.load_pem_x509_certificate(ca_cert_pem.encode())
        assert cert.issuer == ca.subject


class TestRun:
    def test_exits_if_cert_missing(self, tmp_path: Path) -> None:
        (tmp_path / "signCA.key").write_text("key")
        with patch("catalyst_sdwan_lab.tasks.utils.CERTS_DIR", tmp_path):
            with pytest.raises(Exit):
                run(tmp_path / "csr.txt", None)

    def test_exits_if_key_missing(self, tmp_path: Path) -> None:
        (tmp_path / "signCA.pem").write_text("cert")
        with patch("catalyst_sdwan_lab.tasks.utils.CERTS_DIR", tmp_path):
            with pytest.raises(Exit):
                run(tmp_path / "csr.txt", None)

    def test_writes_to_stdout(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        ca_cert, ca_key = _make_ca()
        (tmp_path / "signCA.pem").write_text(ca_cert)
        (tmp_path / "signCA.key").write_text(ca_key)
        (tmp_path / "chainCA.pem").write_text(ca_cert)
        csr_file = tmp_path / "csr.txt"
        csr_file.write_text(_make_csr())
        with patch("catalyst_sdwan_lab.tasks.utils.CERTS_DIR", tmp_path):
            run(csr_file, None)
        assert "-----BEGIN CERTIFICATE-----" in capsys.readouterr().out

    def test_writes_to_file(self, tmp_path: Path) -> None:
        ca_cert, ca_key = _make_ca()
        (tmp_path / "signCA.pem").write_text(ca_cert)
        (tmp_path / "signCA.key").write_text(ca_key)
        (tmp_path / "chainCA.pem").write_text(ca_cert)
        csr_file = tmp_path / "csr.txt"
        csr_file.write_text(_make_csr())
        output = tmp_path / "cert.pem"
        with patch("catalyst_sdwan_lab.tasks.utils.CERTS_DIR", tmp_path):
            run(csr_file, output)
        assert "-----BEGIN CERTIFICATE-----" in output.read_text()

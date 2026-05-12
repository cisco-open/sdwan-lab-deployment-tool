from __future__ import annotations

import logging
import sys
from pathlib import Path

import typer

from .utils import CERTS_DIR, sign_csr

log = logging.getLogger(__name__)


def run(csr_file: Path, output: Path | None) -> None:
    cert_path = CERTS_DIR / "signCA.pem"
    key_path = CERTS_DIR / "signCA.key"
    for path in (cert_path, key_path):
        if not path.exists():
            log.error("Certificate file not found: %s", path)
            raise typer.Exit(1)

    csr_pem = csr_file.read_text()
    cert_pem = sign_csr(cert_path.read_text(), key_path.read_text(), csr_pem)

    if output:
        output.write_text(cert_pem)
        log.info("Certificate written to %s", output)
    else:
        sys.stdout.write(cert_pem)

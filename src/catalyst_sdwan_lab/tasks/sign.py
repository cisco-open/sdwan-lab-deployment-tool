import logging
import sys
from pathlib import Path

from .utils import load_certs, sign_csr

log = logging.getLogger(__name__)


def run(csr_file: Path, output: Path | None) -> None:
    certs = load_certs()
    csr_pem = csr_file.read_text()
    cert_pem = sign_csr(certs.cert, certs.key, csr_pem)

    if output:
        output.write_text(cert_pem)
        log.info("Certificate written to %s", output)
    else:
        sys.stdout.write(cert_pem)

import subprocess
import tempfile
from pathlib import Path

import pytest


def _run(cmd: list[str], *, timeout: int = 3600) -> None:
    result = subprocess.run(cmd, timeout=timeout)
    assert result.returncode == 0, f"Command failed: {' '.join(cmd)}"


@pytest.mark.integration
def test_full_workflow(sdwan_version: str, ip_type: str) -> None:
    _run(["csdwan", "--version"], timeout=10)
    _run(["csdwan", "setup"], timeout=120)
    _run(["csdwan", "deploy", sdwan_version, "--ip-type", ip_type])
    _run(["csdwan", "add", "2", "controller", sdwan_version], timeout=1200)
    _run(["csdwan", "add", "2", "validator", sdwan_version], timeout=1200)
    _run(["csdwan", "add", "2", "edges", sdwan_version], timeout=1200)
    _run(["csdwan", "add", "2", "sdrouting", sdwan_version], timeout=1200)

    with tempfile.TemporaryDirectory() as tmpdir:
        backup_path = Path(tmpdir) / "backup.zip"
        _run(["csdwan", "backup", "--output", str(backup_path)], timeout=600)
        assert backup_path.exists(), "Backup zip was not created"
        _run(["csdwan", "delete", "--force"], timeout=300)
        _run(["csdwan", "restore", str(backup_path)], timeout=3600)

    _run(["csdwan", "delete", "--force"], timeout=300)

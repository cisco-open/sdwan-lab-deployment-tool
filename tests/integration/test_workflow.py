from __future__ import annotations

import subprocess

import pytest


def _run(cmd: list[str], *, timeout: int = 3600) -> None:
    result = subprocess.run(cmd, timeout=timeout)
    assert result.returncode == 0, f"Command failed: {' '.join(cmd)}"


@pytest.mark.integration
def test_full_workflow(sdwan_version: str) -> None:
    _run(["csdwan", "--version"], timeout=10)
    _run(["csdwan", "setup"], timeout=120)
    _run(["csdwan", "deploy", sdwan_version])
    _run(["csdwan", "add", "2", "controller", sdwan_version], timeout=1200)
    _run(["csdwan", "add", "2", "validator", sdwan_version], timeout=1200)
    _run(["csdwan", "delete", "--force"], timeout=300)

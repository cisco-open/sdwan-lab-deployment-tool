from __future__ import annotations

import os

import pytest

_REQUIRED = ["CML_IP", "CML_USER", "CML_PASSWORD", "LAB_NAME", "MANAGER_PASSWORD", "SDWAN_VERSION"]


@pytest.fixture(scope="session")
def sdwan_version() -> str:
    missing = [k for k in _REQUIRED if not os.environ.get(k)]
    if missing:
        pytest.skip(f"Required env vars not set: {', '.join(missing)}")
    if not os.environ.get("MANAGER_PORT") and not os.environ.get("MANAGER_IP"):
        pytest.skip(
            "Set MANAGER_PORT (PATty mode) or MANAGER_IP + MANAGER_MASK + MANAGER_GATEWAY (direct mode)"
        )
    return os.environ["SDWAN_VERSION"]

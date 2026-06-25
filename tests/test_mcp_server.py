import asyncio
from unittest.mock import patch

import pytest

pytest.importorskip("mcp", reason="requires the optional 'mcp' extra")

from catalyst_sdwan_lab import mcp_server
from catalyst_sdwan_lab.mcp_server import _cml_creds, _started


def _run(coro):
    return asyncio.run(coro)


class TestCmlCreds:
    def test_explicit_args_take_precedence(self) -> None:
        host, user, pw = _cml_creds("h", "u", "p")
        assert (host, user, pw) == ("h", "u", "p")

    def test_falls_back_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CML_IP", "envhost")
        monkeypatch.setenv("CML_USER", "envuser")
        monkeypatch.setenv("CML_PASSWORD", "envpass")
        host, user, pw = _cml_creds()
        assert (host, user, pw) == ("envhost", "envuser", "envpass")

    def test_missing_host_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CML_IP", raising=False)
        with pytest.raises(ValueError, match="CML host"):
            _cml_creds(None, "u", "p")

    def test_missing_user_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CML_USER", raising=False)
        with pytest.raises(ValueError, match="CML user"):
            _cml_creds("h", None, "p")

    def test_missing_password_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CML_PASSWORD", raising=False)
        with pytest.raises(ValueError, match="CML password"):
            _cml_creds("h", "u", None)


class TestStartedMessage:
    def test_contains_job_id_and_poll_instruction(self) -> None:
        msg = _started("deploy", "abc123")
        assert "abc123" in msg
        assert "job_status" in msg
        assert "deploy" in msg


def _creds_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CML_IP", "cml")
    monkeypatch.setenv("CML_USER", "admin")
    monkeypatch.setenv("CML_PASSWORD", "secret")


class TestDeployValidation:
    def test_patty_mode_rejects_direct_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _creds_env(monkeypatch)
        with patch.object(mcp_server, "start_job") as start:
            result = _run(
                mcp_server.deploy(
                    ctx=None,
                    version="20.15.1",
                    manager_password="pw",
                    lab_name="lab",
                    manager_port=4443,
                    manager_ip="10.0.0.1",
                )
            )
        assert result.startswith("Error:")
        start.assert_not_called()

    def test_direct_mode_requires_all_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _creds_env(monkeypatch)
        with patch.object(mcp_server, "start_job") as start:
            result = _run(
                mcp_server.deploy(
                    ctx=None,
                    version="20.15.1",
                    manager_password="pw",
                    lab_name="lab",
                    manager_ip="10.0.0.1",  # missing mask + gateway
                )
            )
        assert result.startswith("Error:")
        start.assert_not_called()

    def test_patty_mode_starts_job(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _creds_env(monkeypatch)
        with patch.object(mcp_server, "start_job", return_value="job1") as start:
            result = _run(
                mcp_server.deploy(
                    ctx=None,
                    version="20.15.1",
                    manager_password="pw",
                    lab_name="lab",
                    manager_port=4443,
                )
            )
        assert "job1" in result
        start.assert_called_once()


class TestAddDevicesValidation:
    def test_unknown_device_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _creds_env(monkeypatch)
        with patch.object(mcp_server, "start_job") as start:
            result = _run(
                mcp_server.add_devices(
                    ctx=None,
                    count=1,
                    device_type="router",
                    version="20.15.1",
                    lab_name="lab",
                    manager_password="pw",
                )
            )
        assert result.startswith("Error:")
        start.assert_not_called()

    def test_zero_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _creds_env(monkeypatch)
        with patch.object(mcp_server, "start_job") as start:
            result = _run(
                mcp_server.add_devices(
                    ctx=None,
                    count=0,
                    device_type="edge",
                    version="20.15.1",
                    lab_name="lab",
                    manager_password="pw",
                )
            )
        assert result.startswith("Error:")
        start.assert_not_called()

    def test_edge_starts_job(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _creds_env(monkeypatch)
        with patch.object(mcp_server, "start_job", return_value="jobE") as start:
            result = _run(
                mcp_server.add_devices(
                    ctx=None,
                    count=2,
                    device_type="edges",  # plural accepted
                    version="20.15.1",
                    lab_name="lab",
                    manager_password="pw",
                )
            )
        assert "jobE" in result
        start.assert_called_once()

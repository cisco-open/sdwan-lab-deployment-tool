from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.exceptions import Exit

from catalyst_sdwan_lab.manager_client import ManagerAPIError, ManagerClient
from catalyst_sdwan_lab.tasks.add import (
    _CTRL_NUM_RE,
    _VLDTR_NUM_RE,
    _add_to_manager_retrying,
    _detect_ip_type,
    _find_lab,
    _next_device_num,
    _wait_for_controllers_ready,
    _wait_for_csrs,
)


def _make_node(label: str, configuration: str = "") -> MagicMock:
    node = MagicMock()
    node.label = label
    node.configuration = configuration
    return node


def _make_lab(nodes: list[MagicMock], notes: str = "") -> MagicMock:
    lab = MagicMock()
    lab.nodes.return_value = nodes
    lab.notes = notes
    return lab


class TestFindLab:
    def test_exits_if_no_lab(self) -> None:
        cml = MagicMock()
        cml.find_labs_by_title.return_value = []
        with pytest.raises(Exit):
            _find_lab(cml, "mylab")

    def test_exits_if_multiple_labs(self) -> None:
        cml = MagicMock()
        cml.find_labs_by_title.return_value = [MagicMock(), MagicMock()]
        with pytest.raises(Exit):
            _find_lab(cml, "mylab")

    def test_exits_if_no_notes(self) -> None:
        cml = MagicMock()
        lab = MagicMock()
        lab.notes = None
        cml.find_labs_by_title.return_value = [lab]
        with pytest.raises(Exit):
            _find_lab(cml, "mylab")

    def test_exits_if_notes_missing_manager_ip(self) -> None:
        cml = MagicMock()
        lab = _make_lab([], notes="no manager info here")
        cml.find_labs_by_title.return_value = [lab]
        with pytest.raises(Exit):
            _find_lab(cml, "mylab")

    def test_returns_lab_ip_port(self) -> None:
        cml = MagicMock()
        lab = _make_lab([], notes="manager_external_ip = 10.0.0.1:8443\n")
        cml.find_labs_by_title.return_value = [lab]
        result_lab, ip, port = _find_lab(cml, "mylab")
        assert result_lab is lab
        assert ip == "10.0.0.1"
        assert port == 8443


class TestDetectIpType:
    def test_no_reference_node_defaults_to_v4(self) -> None:
        lab = _make_lab([_make_node("Manager"), _make_node("Gateway")])
        assert _detect_ip_type(lab) == "v4"

    def test_v4_config(self) -> None:
        node = _make_node("Controller01", configuration="ip address 172.16.0.101/24")
        assert _detect_ip_type(_make_lab([node])) == "v4"

    def test_v6_config(self) -> None:
        node = _make_node("Controller01", configuration="ipv6 address fc00:172:16::101/64")
        assert _detect_ip_type(_make_lab([node])) == "v6"

    def test_dual_config(self) -> None:
        node = _make_node(
            "Validator01",
            configuration="172.16.0.201/24\nfc00:172:16::201/64",
        )
        assert _detect_ip_type(_make_lab([node])) == "dual"

    def test_validator_node_used_as_reference(self) -> None:
        node = _make_node("Validator01", configuration="fc00:172:16::201/64")
        assert _detect_ip_type(_make_lab([node])) == "v6"

    def test_none_configuration_treated_as_v4(self) -> None:
        node = _make_node("Controller01", configuration=None)  # type: ignore[arg-type]
        assert _detect_ip_type(_make_lab([node])) == "v4"


class TestNextDeviceNum:
    def test_no_existing_returns_01(self) -> None:
        lab = _make_lab([_make_node("Manager"), _make_node("Gateway")])
        assert _next_device_num(lab, _CTRL_NUM_RE) == "01"

    def test_increments_from_highest(self) -> None:
        lab = _make_lab([_make_node("Controller01"), _make_node("Controller03")])
        assert _next_device_num(lab, _CTRL_NUM_RE) == "04"

    def test_zero_pads_single_digit(self) -> None:
        lab = _make_lab([_make_node("Controller08")])
        assert _next_device_num(lab, _CTRL_NUM_RE) == "09"

    def test_validator_regex_ignores_controllers(self) -> None:
        lab = _make_lab([_make_node("Controller01"), _make_node("Validator02")])
        assert _next_device_num(lab, _VLDTR_NUM_RE) == "03"


class TestWaitForCsrs:
    def _make_client(self, controllers: list[dict]) -> MagicMock:
        client = MagicMock()
        client.get_controllers.return_value = controllers
        return client

    def test_resolves_when_all_have_csr(self) -> None:
        client = self._make_client([
            {"deviceIP": "172.16.0.101", "serialNumber": "No certificate installed"},
        ])
        _wait_for_csrs(client, ["172.16.0.101"], timeout=10)
        client.get_controllers.assert_called_once()

    def test_ignores_controllers_not_in_device_ips(self) -> None:
        client = self._make_client([
            {"deviceIP": "172.16.0.101", "serialNumber": "No certificate installed"},
            {"deviceIP": "172.16.0.201", "serialNumber": "some-serial"},
        ])
        _wait_for_csrs(client, ["172.16.0.101"], timeout=10)
        client.get_controllers.assert_called_once()

    def test_exits_on_timeout(self) -> None:
        client = self._make_client([
            {"deviceIP": "172.16.0.101", "serialNumber": "ABC123"},
        ])
        with pytest.raises(Exit):
            _wait_for_csrs(client, ["172.16.0.101"], timeout=-1)


class TestWaitForControllersReady:
    def _make_client(self, controllers: list[dict]) -> MagicMock:
        client = MagicMock()
        client.get_controllers.return_value = controllers
        return client

    def test_resolves_when_cert_installed(self) -> None:
        client = self._make_client([
            {"deviceIP": "100.0.0.101", "serialNumber": "ABCDEF123456"},
        ])
        _wait_for_controllers_ready(client, {"100.0.0.101"}, timeout=10)
        client.get_controllers.assert_called_once()

    def test_skips_no_certificate_installed(self) -> None:
        client = self._make_client([
            {"deviceIP": "100.0.0.101", "serialNumber": "No certificate installed"},
        ])
        with pytest.raises(Exit):
            _wait_for_controllers_ready(client, {"100.0.0.101"}, timeout=-1)

    def test_exits_on_timeout(self) -> None:
        client = self._make_client([])
        with pytest.raises(Exit):
            _wait_for_controllers_ready(client, {"100.0.0.101"}, timeout=-1)


class TestAddToManagerRetrying:
    def test_succeeds_on_first_try(self) -> None:
        client = MagicMock()
        _add_to_manager_retrying(client, "172.16.0.101", "vsmart", timeout=30)
        client.add_controller.assert_called_once_with("172.16.0.101", "vsmart", "admin", "admin")

    def test_retries_on_api_error(self) -> None:
        client = MagicMock()
        client.add_controller.side_effect = [ManagerAPIError("unreachable"), None]
        with patch("catalyst_sdwan_lab.tasks.add.time.sleep"):
            _add_to_manager_retrying(client, "172.16.0.101", "vsmart", timeout=30)
        assert client.add_controller.call_count == 2

    def test_exits_when_deadline_exceeded(self) -> None:
        client = MagicMock()
        client.add_controller.side_effect = ManagerAPIError("unreachable")
        with pytest.raises(Exit):
            _add_to_manager_retrying(client, "172.16.0.101", "vsmart", timeout=-1)


class TestWaitForTask:
    def _make_client(self, responses: list[dict]) -> tuple[ManagerClient, MagicMock]:
        client = ManagerClient.__new__(ManagerClient)
        mock_get = MagicMock(side_effect=responses)
        client._get = mock_get  # type: ignore[attr-defined]
        return client, mock_get

    def test_returns_on_success(self) -> None:
        client, _ = self._make_client([
            {"summary": {"status": "done", "count": {"Success": 1}}}
        ])
        client.wait_for_task("task-1")

    def test_raises_on_failure_count(self) -> None:
        client, _ = self._make_client([
            {"summary": {"status": "done", "count": {"Failure": 1}}}
        ])
        with pytest.raises(ManagerAPIError, match="completed with failures"):
            client.wait_for_task("task-1")

    def test_raises_on_timeout(self) -> None:
        client, _ = self._make_client([
            {"summary": {"status": "in_progress"}},
            {"summary": {"status": "in_progress"}},
        ])
        with patch("catalyst_sdwan_lab.manager_client.time.sleep"):
            with patch("catalyst_sdwan_lab.manager_client.time.time", side_effect=[0, 0, 999]):
                with pytest.raises(ManagerAPIError, match="timed out"):
                    client.wait_for_task("task-1", timeout=1)

    def test_polls_until_done(self) -> None:
        client, mock_get = self._make_client([
            {"summary": {"status": "in_progress"}},
            {"summary": {"status": "done", "count": {"Success": 1}}},
        ])
        with patch("catalyst_sdwan_lab.manager_client.time.sleep"):
            client.wait_for_task("task-1")
        assert mock_get.call_count == 2

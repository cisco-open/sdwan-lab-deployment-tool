from unittest.mock import MagicMock, patch

import pytest
from typer import Exit

from catalyst_sdwan_lab.manager_client import ManagerAPIError, ManagerClient
from catalyst_sdwan_lab.tasks.add import (
    _CTRL_NUM_RE,
    _EDGE_NUM_RE,
    _SDROUTING_NUM_RE,
    _VLDTR_NUM_RE,
    _add_sdwan_node,
    _add_to_manager_retrying,
    _add_wan_edge_node,
    _drop_unsupported_variables,
    _next_device_num,
    _next_system_ip_num,
    _wait_for_controllers_ready,
    _wait_for_csrs,
)
from catalyst_sdwan_lab.tasks.utils import detect_ip_type, find_lab, wait_for_edges_onboarded


def _make_node(label: str, configuration: str = "", node_definition: str = "") -> MagicMock:
    node = MagicMock()
    node.label = label
    node.configuration = configuration
    node.node_definition = node_definition
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
            find_lab(cml, "mylab")

    def test_exits_if_multiple_labs(self) -> None:
        cml = MagicMock()
        cml.find_labs_by_title.return_value = [MagicMock(), MagicMock()]
        with pytest.raises(Exit):
            find_lab(cml, "mylab")

    def test_exits_if_no_notes(self) -> None:
        cml = MagicMock()
        lab = MagicMock()
        lab.notes = None
        cml.find_labs_by_title.return_value = [lab]
        with pytest.raises(Exit):
            find_lab(cml, "mylab")

    def test_exits_if_notes_missing_manager_ip(self) -> None:
        cml = MagicMock()
        lab = _make_lab([], notes="no manager info here")
        cml.find_labs_by_title.return_value = [lab]
        with pytest.raises(Exit):
            find_lab(cml, "mylab")

    def test_returns_lab_ip_port(self) -> None:
        cml = MagicMock()
        lab = _make_lab([], notes="manager_external_ip = 10.0.0.1:8443\n")
        cml.find_labs_by_title.return_value = [lab]
        result_lab, ip, port = find_lab(cml, "mylab")
        assert result_lab is lab
        assert ip == "10.0.0.1"
        assert port == 8443


class TestDetectIpType:
    def test_no_controller_node_defaults_to_v4(self) -> None:
        lab = _make_lab([_make_node("Manager"), _make_node("Gateway")])
        assert detect_ip_type(lab) == "v4"

    def test_v4_config(self) -> None:
        node = _make_node("ctrl", configuration="ip address 172.16.0.101/24", node_definition="cat-sdwan-controller")
        assert detect_ip_type(_make_lab([node])) == "v4"

    def test_v6_config(self) -> None:
        node = _make_node("ctrl", configuration="ipv6 address fc00:172:16::101/64", node_definition="cat-sdwan-controller")
        assert detect_ip_type(_make_lab([node])) == "v6"

    def test_dual_config(self) -> None:
        node = _make_node(
            "ctrl",
            configuration="172.16.0.101/24\nfc00:172:16::101/64",
            node_definition="cat-sdwan-controller",
        )
        assert detect_ip_type(_make_lab([node])) == "dual"

    def test_validator_node_not_used_as_reference(self) -> None:
        node = _make_node("vldtr", configuration="fc00:172:16::201/64", node_definition="cat-sdwan-validator")
        assert detect_ip_type(_make_lab([node])) == "v4"

    def test_none_configuration_treated_as_v4(self) -> None:
        node = _make_node("ctrl", configuration=None, node_definition="cat-sdwan-controller")  # type: ignore[arg-type]
        assert detect_ip_type(_make_lab([node])) == "v4"


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

    def test_edge_regex_ignores_controllers_and_validators(self) -> None:
        lab = _make_lab([_make_node("Controller01"), _make_node("Validator01"), _make_node("Edge03")])
        assert _next_device_num(lab, _EDGE_NUM_RE) == "04"

    def test_sdrouting_regex_ignores_edges(self) -> None:
        lab = _make_lab([_make_node("Edge01"), _make_node("SD-Edge02")])
        assert _next_device_num(lab, _SDROUTING_NUM_RE) == "03"


class TestNextSystemIpNum:
    def test_no_devices_returns_1(self) -> None:
        lab = _make_lab([])
        assert _next_system_ip_num(lab, []) == 1

    def test_uses_manager_system_ip(self) -> None:
        lab = _make_lab([])
        vedges = [{"system-ip": "10.0.0.3"}]
        assert _next_system_ip_num(lab, vedges) == 4

    def test_uses_cml_edge_label(self) -> None:
        lab = _make_lab([_make_node("Edge05")])
        assert _next_system_ip_num(lab, []) == 6

    def test_uses_cml_sdrouting_label(self) -> None:
        lab = _make_lab([_make_node("SD-Edge03")])
        assert _next_system_ip_num(lab, []) == 4

    def test_takes_max_of_manager_and_cml(self) -> None:
        lab = _make_lab([_make_node("Edge02")])
        vedges = [{"system-ip": "10.0.0.5"}]
        assert _next_system_ip_num(lab, vedges) == 6

    def test_ignores_malformed_system_ip(self) -> None:
        lab = _make_lab([])
        vedges = [{"system-ip": ""}, {"system-ip": "not-an-ip"}, {"system-ip": "10.0.0.2"}]
        assert _next_system_ip_num(lab, vedges) == 3

    def test_both_edge_and_sdrouting_labels_considered(self) -> None:
        lab = _make_lab([_make_node("Edge03"), _make_node("SD-Edge05")])
        assert _next_system_ip_num(lab, []) == 6


class TestAddSdwanNode:
    def _make_sdwan_node(self, x: int, y: int) -> MagicMock:
        node = MagicMock()
        node.node_definition = "cat-sdwan-controller"
        node.x = x
        node.y = y
        return node

    def _make_vpn0(self) -> MagicMock:
        sw = MagicMock()
        sw.label = "VPN0"
        return sw

    def _make_lab(self, nodes: list[MagicMock]) -> MagicMock:
        lab = MagicMock()
        lab.nodes.return_value = nodes
        lab.create_node.return_value = MagicMock()
        return lab

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_first_node_placed_at_120_0(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        lab = self._make_lab([self._make_vpn0()])
        _add_sdwan_node(lab, "Controller01", "cat-sdwan-controller", "img", "cfg", "eth1")
        kwargs = lab.create_node.call_args[1]
        assert kwargs["x"] == 120  # 0 + 120
        assert kwargs["y"] == 0

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_extends_from_rightmost_sdwan_node(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        lab = self._make_lab([self._make_sdwan_node(300, 0), self._make_vpn0()])
        _add_sdwan_node(lab, "Controller02", "cat-sdwan-controller", "img", "cfg", "eth1")
        assert lab.create_node.call_args[1]["x"] == 420  # 300 + 120

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_exits_if_vpn0_not_found(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        lab = self._make_lab([])
        with pytest.raises(Exit):
            _add_sdwan_node(lab, "Controller01", "cat-sdwan-controller", "img", "cfg", "eth1")

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_exits_if_vpn0_has_no_free_ports(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        vpn0 = self._make_vpn0()
        vpn0.next_available_interface.return_value = None
        lab = self._make_lab([vpn0])
        with pytest.raises(Exit):
            _add_sdwan_node(lab, "Controller01", "cat-sdwan-controller", "img", "cfg", "eth1")


class TestAddEdgeNode:
    def _make_switch(self, label: str) -> MagicMock:
        sw = MagicMock()
        sw.label = label
        sw.y = 0
        return sw

    def _make_edge_node(self, x: int) -> MagicMock:
        node = MagicMock()
        node.label = "Edge01"
        node.y = 400
        node.x = x
        return node

    def _make_lab(self, nodes: list[MagicMock]) -> MagicMock:
        lab = MagicMock()
        lab.nodes.return_value = nodes
        lab.create_node.return_value = MagicMock()
        return lab

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_first_edge_placed_at_y400_x_minus280(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        lab = self._make_lab([self._make_switch("INET"), self._make_switch("MPLS")])
        _add_wan_edge_node(lab, "Edge01", "img", "cfg", connect_mpls=True)
        kwargs = lab.create_node.call_args[1]
        assert kwargs["y"] == 400
        assert kwargs["x"] == -280  # -400 + 120

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_extends_from_rightmost_y400_node(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        lab = self._make_lab([
            self._make_edge_node(200),
            self._make_switch("INET"),
            self._make_switch("MPLS"),
        ])
        _add_wan_edge_node(lab, "Edge02", "img", "cfg", connect_mpls=True)
        assert lab.create_node.call_args[1]["x"] == 320  # 200 + 120

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_exits_if_inet_not_found(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        lab = self._make_lab([self._make_switch("MPLS")])
        with pytest.raises(Exit):
            _add_wan_edge_node(lab, "Edge01", "img", "cfg", connect_mpls=True)

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_exits_if_mpls_not_found(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        lab = self._make_lab([self._make_switch("INET")])
        with pytest.raises(Exit):
            _add_wan_edge_node(lab, "Edge01", "img", "cfg", connect_mpls=True)

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_exits_if_inet_has_no_free_ports(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        inet = self._make_switch("INET")
        inet.next_available_interface.return_value = None
        lab = self._make_lab([inet, self._make_switch("MPLS")])
        with pytest.raises(Exit):
            _add_wan_edge_node(lab, "Edge01", "img", "cfg", connect_mpls=True)

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_exits_if_mpls_has_no_free_ports(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        mpls = self._make_switch("MPLS")
        mpls.next_available_interface.return_value = None
        lab = self._make_lab([self._make_switch("INET"), mpls])
        with pytest.raises(Exit):
            _add_wan_edge_node(lab, "Edge01", "img", "cfg", connect_mpls=True)


class TestAddSdroutingNode:
    def _make_switch(self, label: str) -> MagicMock:
        sw = MagicMock()
        sw.label = label
        sw.y = 0
        return sw

    def _make_lab(self, nodes: list[MagicMock]) -> MagicMock:
        lab = MagicMock()
        lab.nodes.return_value = nodes
        lab.create_node.return_value = MagicMock()
        return lab

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_connects_only_inet_not_mpls(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        lab = self._make_lab([self._make_switch("INET")])
        _add_wan_edge_node(lab, "SD-Edge1", "img", "cfg", connect_mpls=False)
        assert lab.create_link.call_count == 1

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_exits_if_inet_not_found(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        lab = self._make_lab([])
        with pytest.raises(Exit):
            _add_wan_edge_node(lab, "SD-Edge1", "img", "cfg", connect_mpls=False)

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_exits_if_inet_has_no_free_ports(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        inet = self._make_switch("INET")
        inet.next_available_interface.return_value = None
        lab = self._make_lab([inet])
        with pytest.raises(Exit):
            _add_wan_edge_node(lab, "SD-Edge1", "img", "cfg", connect_mpls=False)

    @patch("catalyst_sdwan_lab.tasks.add._sync_until_interface")
    def test_placed_at_y400(self, mock_sync: MagicMock) -> None:
        mock_sync.return_value = MagicMock()
        lab = self._make_lab([self._make_switch("INET")])
        _add_wan_edge_node(lab, "SD-Edge1", "img", "cfg", connect_mpls=False)
        assert lab.create_node.call_args[1]["y"] == 400


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


class TestWaitForEdgesOnboarded:
    def _make_client(self, vedges: list[dict]) -> MagicMock:
        client = MagicMock()
        client.get_vedges.return_value = vedges
        return client

    def test_resolves_when_all_onboarded(self) -> None:
        client = self._make_client([
            {"uuid": "uuid-1", "certInstallStatus": "Installed", "reachability": "reachable"},
        ])
        wait_for_edges_onboarded(client, ["uuid-1"], timeout=10)
        client.get_vedges.assert_called_once()

    def test_requires_both_installed_and_reachable(self) -> None:
        client = self._make_client([
            {"uuid": "uuid-1", "certInstallStatus": "Installed", "reachability": "unreachable"},
        ])
        with pytest.raises(Exit):
            wait_for_edges_onboarded(client, ["uuid-1"], timeout=-1)

    def test_ignores_other_uuids(self) -> None:
        client = self._make_client([
            {"uuid": "uuid-1", "certInstallStatus": "Installed", "reachability": "reachable"},
            {"uuid": "uuid-2", "certInstallStatus": None, "reachability": "unreachable"},
        ])
        wait_for_edges_onboarded(client, ["uuid-1"], timeout=10)
        client.get_vedges.assert_called_once()

    def test_exits_on_timeout(self) -> None:
        client = self._make_client([])
        with pytest.raises(Exit):
            wait_for_edges_onboarded(client, ["uuid-1"], timeout=-1)


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


class TestDropUnsupportedVariables:
    def test_keeps_all_when_allowed(self) -> None:
        variables = [
            {"name": "system_ip", "value": "10.0.0.1"},
            {"name": "host_name", "value": "Edge01"},
        ]
        allowed = {"system_ip", "host_name"}
        assert _drop_unsupported_variables(variables, allowed) == variables

    def test_drops_names_not_in_schema(self) -> None:
        variables = [
            {"name": "vpn0_gi1_inet_ip", "value": "172.16.1.1"},
            {"name": "vpn0_gi1_inet_mask", "value": "255.255.255.0"},
        ]
        allowed = {"vpn0_gi1_inet_ip"}
        assert _drop_unsupported_variables(variables, allowed) == [
            {"name": "vpn0_gi1_inet_ip", "value": "172.16.1.1"}
        ]

    def test_empty_allowed_drops_everything(self) -> None:
        variables = [{"name": "system_ip", "value": "10.0.0.1"}]
        assert _drop_unsupported_variables(variables, set()) == []

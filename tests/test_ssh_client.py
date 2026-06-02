import time
from unittest.mock import MagicMock, patch

import pytest

from catalyst_sdwan_lab.ssh_client import _strip_sdrouting_config, ssh_drain, ssh_recv


class TestSshRecv:
    def _make_channel(self, chunks: list[bytes], closed: bool = False) -> MagicMock:
        ch = MagicMock()
        ch.closed = closed
        responses = iter(chunks)

        def recv_ready_side_effect():
            return True

        def recv_side_effect(n):
            try:
                return next(responses)
            except StopIteration:
                return b""

        ch.recv_ready.side_effect = recv_ready_side_effect
        ch.recv.side_effect = recv_side_effect
        return ch

    def test_returns_when_prompt_found(self) -> None:
        ch = self._make_channel([b"Router", b"#"])
        result = ssh_recv(ch, "#", timeout=5.0)
        assert "#" in result

    def test_accumulates_chunks(self) -> None:
        ch = self._make_channel([b"line1\n", b"line2\n", b"Router#"])
        result = ssh_recv(ch, "#", timeout=5.0)
        assert "line1" in result
        assert "line2" in result

    def test_first_matching_prompt_wins(self) -> None:
        ch = self._make_channel([b"login:"])
        result = ssh_recv(ch, "login:", "Username:", "#", timeout=5.0)
        assert "login:" in result

    def test_raises_on_timeout(self) -> None:
        ch = MagicMock()
        ch.closed = False
        ch.recv_ready.return_value = False
        with pytest.raises(RuntimeError, match="Timed out"):
            ssh_recv(ch, "#", timeout=0.1)

    def test_raises_when_channel_closed(self) -> None:
        ch = MagicMock()
        ch.closed = True
        with pytest.raises(RuntimeError, match="closed"):
            ssh_recv(ch, "#", timeout=5.0)


class TestSshDrain:
    def test_drains_available_data(self) -> None:
        ch = MagicMock()
        ch.recv_ready.side_effect = [True, True, False, False, False]
        ch.recv.return_value = b"data"
        ssh_drain(ch, duration=0.1)
        assert ch.recv.call_count == 2

    def test_returns_when_nothing_to_drain(self) -> None:
        ch = MagicMock()
        ch.recv_ready.return_value = False
        ssh_drain(ch, duration=0.05)
        ch.recv.assert_not_called()


class TestStripSdroutingConfig:
    def _wrap(self, config: str, cmd: str = "show run") -> str:
        return f"{cmd}\r\n{config}\nRouter#"

    def test_strips_command_echo_and_prompt(self) -> None:
        raw = self._wrap("!\nhostname Router\n!")
        result = _strip_sdrouting_config(raw)
        assert "show run" not in result
        assert "Router#" not in result

    def test_skips_building_configuration_header(self) -> None:
        raw = "show run\r\n*May 1 log message\nBuilding configuration...\nCurrent configuration: 1000 bytes\n!\nhostname R\nRouter#"
        result = _strip_sdrouting_config(raw)
        assert "log message" not in result
        assert "Building configuration" not in result
        assert "Current configuration" not in result
        assert "hostname R" in result

    def test_starts_at_first_bang(self) -> None:
        raw = "show run\r\n! Last config change\nhostname R\n!\nRouter#"
        result = _strip_sdrouting_config(raw)
        assert result.startswith("!")

    def test_strips_crypto_pki_section(self) -> None:
        raw = self._wrap(
            "!\nhostname R\n!\ncrypto pki trustpoint SLA\n"
            " enrollment selfsigned\n!\nip route 0.0.0.0 0.0.0.0 10.0.0.1\n!"
        )
        result = _strip_sdrouting_config(raw)
        assert "crypto pki" not in result
        assert "ip route" in result

    def test_strips_license_udi_line(self) -> None:
        raw = self._wrap(
            "!\nhostname R\n!\n"
            "license udi pid C8000V sn ABC123\n!\nip route 0.0.0.0 0.0.0.0 10.0.0.1\n!"
        )
        result = _strip_sdrouting_config(raw)
        assert "license udi" not in result
        assert "ip route" in result

    def test_multiple_prompt_lines_at_end_stripped(self) -> None:
        raw = "show run\r\n!\nhostname R\n!\nRouter#\nRouter#"
        result = _strip_sdrouting_config(raw)
        assert "Router#" not in result

    def test_preserves_config_content(self) -> None:
        raw = self._wrap(
            "!\nhostname Edge01\n!\n"
            "interface GigabitEthernet1\n ip address dhcp\n!\n"
        )
        result = _strip_sdrouting_config(raw)
        assert "hostname Edge01" in result
        assert "GigabitEthernet1" in result

    def test_strips_spanning_tree(self) -> None:
        raw = self._wrap("!\nhostname R\nspanning-tree extend system-id\n!")
        assert "spanning-tree" not in _strip_sdrouting_config(raw)

    def test_strips_telemetry_ietf_subscription_block(self) -> None:
        raw = self._wrap(
            "!\nhostname R\n"
            "telemetry ietf subscription 294967214\n"
            " filter xpath /some-event\n"
            " stream rfc5277\n"
            " update-policy on-change\n"
            " receiver name confd-rfc5277\n"
            "telemetry ietf subscription 294967215\n"
            " filter xpath /other-event\n"
            " stream rfc5277\n"
            " update-policy on-change\n"
            " receiver name confd-rfc5277\n"
            "ip route 0.0.0.0 0.0.0.0 10.0.0.1\n!"
        )
        result = _strip_sdrouting_config(raw)
        assert "telemetry ietf subscription" not in result
        assert "ip route" in result

    def test_strips_telemetry_receiver_block(self) -> None:
        raw = self._wrap(
            "!\nhostname R\n"
            "telemetry receiver protocol confd-rfc5277\n"
            " host ip-address 0.0.0.0 0\n"
            " protocol rfc5277\n"
            "ip route 0.0.0.0 0.0.0.0 10.0.0.1\n!"
        )
        result = _strip_sdrouting_config(raw)
        assert "telemetry receiver" not in result
        assert "ip route" in result

    def test_strips_indented_login_local(self) -> None:
        raw = self._wrap("!\nhostname R\nline vty 0 4\n login local\n transport input ssh\n!")
        result = _strip_sdrouting_config(raw)
        assert "login local" not in result
        assert "transport input ssh" in result

    def test_strips_netconf_yang_candidate_datastore(self) -> None:
        raw = self._wrap("!\nhostname R\nnetconf-yang feature candidate-datastore\n!")
        assert "netconf-yang feature candidate-datastore" not in _strip_sdrouting_config(raw)

    def test_strips_mgcp_block(self) -> None:
        raw = self._wrap(
            "!\nhostname R\n"
            "mgcp behavior rsip-range tgcp-only\n"
            "mgcp behavior comedia-role none\n"
            "mgcp profile default\n"
            "ip route 0.0.0.0 0.0.0.0 10.0.0.1\n!"
        )
        result = _strip_sdrouting_config(raw)
        assert "mgcp" not in result
        assert "ip route" in result

    def test_strips_subscriber_templating(self) -> None:
        raw = self._wrap("!\nhostname R\nsubscriber templating\n!")
        assert "subscriber templating" not in _strip_sdrouting_config(raw)

    def test_strips_service_password_encryption(self) -> None:
        raw = self._wrap("!\nhostname R\nservice password-encryption\n!")
        assert "service password-encryption" not in _strip_sdrouting_config(raw)

    def test_strips_aaa_new_model(self) -> None:
        raw = self._wrap("!\nhostname R\naaa new-model\naaa authentication login default local\n!")
        result = _strip_sdrouting_config(raw)
        assert "aaa new-model" not in result
        assert "aaa authentication login default local" in result

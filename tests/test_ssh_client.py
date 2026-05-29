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
        raw = self._wrap("Building configuration...\n!\nhostname Router\n!")
        result = _strip_sdrouting_config(raw)
        assert "show run" not in result
        assert "Router#" not in result

    def test_starts_at_building_configuration(self) -> None:
        raw = "show run\r\n*May 1 log message\nBuilding configuration...\n!\nhostname R\nRouter#"
        result = _strip_sdrouting_config(raw)
        assert "log message" not in result
        assert "Building configuration" in result

    def test_starts_at_first_bang(self) -> None:
        raw = "show run\r\n! Last config change\nhostname R\n!\nRouter#"
        result = _strip_sdrouting_config(raw)
        assert result.startswith("!")

    def test_strips_crypto_pki_section(self) -> None:
        raw = self._wrap(
            "Building configuration...\n!\nhostname R\n!\ncrypto pki trustpoint SLA\n"
            " enrollment selfsigned\n!\nip route 0.0.0.0 0.0.0.0 10.0.0.1\n!"
        )
        result = _strip_sdrouting_config(raw)
        assert "crypto pki" not in result
        assert "ip route" in result

    def test_strips_license_udi_line(self) -> None:
        raw = self._wrap(
            "Building configuration...\n!\nhostname R\n!\n"
            "license udi pid C8000V sn ABC123\n!\nip route 0.0.0.0 0.0.0.0 10.0.0.1\n!"
        )
        result = _strip_sdrouting_config(raw)
        assert "license udi" not in result
        assert "ip route" in result

    def test_multiple_prompt_lines_at_end_stripped(self) -> None:
        raw = "show run\r\nBuilding configuration...\n!\nhostname R\n!\nRouter#\nRouter#"
        result = _strip_sdrouting_config(raw)
        assert "Router#" not in result

    def test_preserves_config_content(self) -> None:
        raw = self._wrap(
            "Building configuration...\n!\nhostname Edge01\n!\n"
            "interface GigabitEthernet1\n ip address dhcp\n!\n"
        )
        result = _strip_sdrouting_config(raw)
        assert "hostname Edge01" in result
        assert "GigabitEthernet1" in result

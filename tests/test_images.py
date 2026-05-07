from __future__ import annotations

import pytest

from catalyst_sdwan_lab.tasks.images import _normalize_id, _parse_filename


class TestNormalizeId:
    def test_manager_dashes_to_dots(self):
        assert _normalize_id("cat-sdwan-manager-20-13-1") == "cat-sdwan-manager-20.13.1"

    def test_controller_dashes_to_dots(self):
        assert _normalize_id("cat-sdwan-controller-20-15-4") == "cat-sdwan-controller-20.15.4"

    def test_validator_dashes_to_dots(self):
        assert _normalize_id("cat-sdwan-validator-20-18-2-1") == "cat-sdwan-validator-20.18.2.1"

    def test_edge_dashes_to_dots(self):
        assert _normalize_id("cat-sdwan-edge-17-13-01a") == "cat-sdwan-edge-17.13.01a"

    def test_unknown_prefix_passthrough(self):
        assert _normalize_id("some-other-id") == "some-other-id"


class TestParseFilename:
    @pytest.mark.parametrize("viptela_type,expected_node_type", [
        ("vmanage", "cat-sdwan-manager"),
        ("smart", "cat-sdwan-controller"),
        ("edge", "cat-sdwan-validator"),
        ("bond", "cat-sdwan-validator"),
    ])
    def test_viptela_types(self, viptela_type, expected_node_type):
        filename = f"viptela-{viptela_type}-20.15.4-genericx86-64.qcow2"
        result = _parse_filename(filename)
        assert result == (expected_node_type, "20.15.4")

    def test_viptela_multipart_version(self):
        result = _parse_filename("viptela-bond-20.18.2.1-genericx86-64.qcow2")
        assert result == ("cat-sdwan-validator", "20.18.2.1")

    def test_c8000v(self):
        result = _parse_filename("c8000v-universalk9_16G_serial.17.13.01a.qcow2")
        assert result == ("cat-sdwan-edge", "17.13.01a")

    def test_unrecognized_returns_none(self):
        assert _parse_filename("some-random-file.qcow2") is None

    def test_non_sdwan_qcow2_returns_none(self):
        assert _parse_filename("iosv-158-3.qcow2") is None

import json
import logging
import zipfile
from pathlib import Path

import pytest
import yaml

from catalyst_sdwan_lab.tasks.backup import (
    _inject_xml_personality,
    _save_directory,
    _save_zip,
    _update_node_configuration,
)
from catalyst_sdwan_lab.tasks.utils import dump_topology

_SYSTEM_TAG = '<system xmlns="http://viptela.com/system">'


class TestUpdateNodeConfiguration:
    def test_patches_top_level_nodes(self) -> None:
        topology = {"nodes": [{"label": "Manager01", "configuration": "old"}]}
        _update_node_configuration(topology, "Manager01", "new")
        assert topology["nodes"][0]["configuration"] == "new"

    def test_patches_nested_lab_nodes(self) -> None:
        topology = {"lab": {"nodes": [{"label": "Controller01", "configuration": "old"}]}}
        _update_node_configuration(topology, "Controller01", "new")
        assert topology["lab"]["nodes"][0]["configuration"] == "new"

    def test_top_level_nodes_takes_priority_over_lab(self) -> None:
        topology = {
            "nodes": [{"label": "Mgr", "configuration": "top"}],
            "lab": {"nodes": [{"label": "Mgr", "configuration": "nested"}]},
        }
        _update_node_configuration(topology, "Mgr", "updated")
        assert topology["nodes"][0]["configuration"] == "updated"
        assert topology["lab"]["nodes"][0]["configuration"] == "nested"

    def test_empty_top_level_nodes_does_not_fall_through(self) -> None:
        topology = {
            "nodes": [],
            "lab": {"nodes": [{"label": "Mgr", "configuration": "nested"}]},
        }
        _update_node_configuration(topology, "Mgr", "updated")
        assert topology["lab"]["nodes"][0]["configuration"] == "nested"

    def test_missing_node_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        topology = {"nodes": [{"label": "Other"}]}
        with caplog.at_level(logging.WARNING):
            _update_node_configuration(topology, "NotHere", "new")
        assert "NotHere" in caplog.text


class TestInjectXmlPersonality:
    def _xml(self, body: str = "<host-name>x</host-name>") -> str:
        return f"{_SYSTEM_TAG}{body}</system>"

    def test_injects_vmanage_for_manager(self) -> None:
        result = _inject_xml_personality(self._xml(), "cat-sdwan-manager")
        assert "<personality>vmanage</personality>" in result
        assert "<device-model>vmanage</device-model>" in result

    def test_injects_vsmart_for_controller(self) -> None:
        result = _inject_xml_personality(self._xml(), "cat-sdwan-controller")
        assert "<personality>vsmart</personality>" in result
        assert "<device-model>vsmart</device-model>" in result

    def test_injects_vedge_for_validator(self) -> None:
        result = _inject_xml_personality(self._xml(), "cat-sdwan-validator")
        assert "<personality>vedge</personality>" in result
        assert "<device-model>vedge-cloud</device-model>" in result

    def test_personality_inserted_immediately_after_system_tag(self) -> None:
        result = _inject_xml_personality(self._xml(), "cat-sdwan-manager")
        idx_tag = result.index(_SYSTEM_TAG) + len(_SYSTEM_TAG)
        idx_personality = result.index("<personality>")
        assert idx_personality > idx_tag
        assert result[idx_tag:idx_personality].strip() == ""

    def test_no_system_tag_leaves_string_unchanged(self) -> None:
        xml = "<config><other>data</other></config>"
        assert _inject_xml_personality(xml, "cat-sdwan-manager") == xml


class TestDumpTopology:
    def test_multiline_string_uses_literal_block(self) -> None:
        result = dump_topology({"cfg": "line1\nline2\nline3"})
        assert "cfg: |" in result

    def test_single_line_string_uses_plain_style(self) -> None:
        result = dump_topology({"key": "single line"})
        assert "key: single line" in result
        assert "key: |" not in result

    def test_trailing_spaces_stripped_per_line(self) -> None:
        result = dump_topology({"cfg": "line1   \nline2\nline3"})
        assert "cfg: |" in result
        loaded = yaml.safe_load(result)
        assert loaded["cfg"] == "line1\nline2\nline3"

    def test_mime_multipart_uses_literal_block(self) -> None:
        mime = 'Content-Type: multipart/mixed; boundary="==BOUNDARY=="\nMIME-Version: 1.0\n'
        result = dump_topology({"configuration": mime})
        assert "configuration: |" in result or "configuration: |-" in result

    def test_mime_with_trailing_space_uses_literal_block(self) -> None:
        mime = 'Content-Disposition: attachment; \n filename="cloud-config"\n'
        result = dump_topology({"configuration": mime})
        assert "configuration: |" in result or "configuration: |-" in result

    def test_roundtrip_preserves_multiline_content(self) -> None:
        config = "line1\nline2\nline3"
        topology = {"nodes": [{"label": "M", "configuration": config}]}
        loaded = yaml.safe_load(dump_topology(topology))
        assert loaded["nodes"][0]["configuration"] == config

    def test_non_string_values_unaffected(self) -> None:
        topology = {"count": 3, "flag": True, "items": [1, 2, 3]}
        loaded = yaml.safe_load(dump_topology(topology))
        assert loaded == topology


class TestSaveZip:
    def _make_manager_dir(self, tmp_path: Path, files: dict[str, str] | None = None) -> Path:
        d = tmp_path / "manager"
        d.mkdir()
        for name, content in (files or {}).items():
            p = d / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return d

    def test_topology_yaml_written(self, tmp_path: Path) -> None:
        output = tmp_path / "backup.zip"
        _save_zip(output, "nodes: []\n", self._make_manager_dir(tmp_path), [])
        with zipfile.ZipFile(output) as zf:
            assert zf.read("topology.yaml").decode() == "nodes: []\n"

    def test_mrf_json_written(self, tmp_path: Path) -> None:
        output = tmp_path / "backup.zip"
        mrf = [{"id": "r1", "name": "Region1"}]
        _save_zip(output, "nodes: []\n", self._make_manager_dir(tmp_path), mrf)
        with zipfile.ZipFile(output) as zf:
            assert json.loads(zf.read("manager_configs/mrf.json")) == mrf

    def test_manager_configs_included(self, tmp_path: Path) -> None:
        output = tmp_path / "backup.zip"
        manager_dir = self._make_manager_dir(
            tmp_path, {"feature_templates/tmpl.json": '{"x": 1}'}
        )
        _save_zip(output, "nodes: []\n", manager_dir, [])
        with zipfile.ZipFile(output) as zf:
            assert "manager_configs/feature_templates/tmpl.json" in zf.namelist()

    def test_zip_uses_deflate_compression(self, tmp_path: Path) -> None:
        output = tmp_path / "backup.zip"
        _save_zip(output, "nodes: []\n", self._make_manager_dir(tmp_path), [])
        with zipfile.ZipFile(output) as zf:
            info = zf.getinfo("topology.yaml")
            assert info.compress_type == zipfile.ZIP_DEFLATED


class TestSaveDirectory:
    def _make_manager_dir(self, tmp_path: Path, files: dict[str, str] | None = None) -> Path:
        d = tmp_path / "manager"
        d.mkdir()
        for name, content in (files or {}).items():
            p = d / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return d

    def test_topology_yaml_written(self, tmp_path: Path) -> None:
        output = tmp_path / "backup"
        _save_directory(output, "nodes: []\n", self._make_manager_dir(tmp_path), [])
        assert (output / "topology.yaml").read_text() == "nodes: []\n"

    def test_mrf_json_written(self, tmp_path: Path) -> None:
        output = tmp_path / "backup"
        mrf = [{"id": "r1"}]
        _save_directory(output, "nodes: []\n", self._make_manager_dir(tmp_path), mrf)
        assert json.loads((output / "manager_configs" / "mrf.json").read_text()) == mrf

    def test_manager_configs_copied(self, tmp_path: Path) -> None:
        output = tmp_path / "backup"
        manager_dir = self._make_manager_dir(
            tmp_path, {"sub/file.json": '{"x": 1}'}
        )
        _save_directory(output, "nodes: []\n", manager_dir, [])
        assert (output / "manager_configs" / "sub" / "file.json").exists()

    def test_output_dir_created_if_missing(self, tmp_path: Path) -> None:
        output = tmp_path / "deep" / "nested" / "backup"
        _save_directory(output, "nodes: []\n", self._make_manager_dir(tmp_path), [])
        assert output.is_dir()

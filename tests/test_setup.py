from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer import Exit

from catalyst_sdwan_lab.tasks.setup import (
    _check_iol_definitions,
    _check_license,
    _sync_node_definitions,
)


class TestCheckLicense:
    def test_init_status_exits(self):
        cml = MagicMock()
        cml.licensing.status.return_value = {"authorization": {"status": "INIT"}}
        with pytest.raises(Exit):
            _check_license(cml)

    def test_eval_status_exits(self):
        cml = MagicMock()
        cml.licensing.status.return_value = {"authorization": {"status": "EVAL"}}
        with pytest.raises(Exit):
            _check_license(cml)

    def test_valid_status_passes(self):
        cml = MagicMock()
        cml.licensing.status.return_value = {"authorization": {"status": "IN_COMPLIANCE"}}
        _check_license(cml)  # should not raise


class TestCheckIolDefinitions:
    def test_missing_iol_xe_exits(self):
        existing = {"ioll2-xe": {}}
        with pytest.raises(Exit):
            _check_iol_definitions(existing)

    def test_missing_ioll2_xe_exits(self):
        existing = {"iol-xe": {}}
        with pytest.raises(Exit):
            _check_iol_definitions(existing)

    def test_both_present_passes(self):
        existing = {"iol-xe": {}, "ioll2-xe": {}}
        _check_iol_definitions(existing)  # should not raise


class TestSyncNodeDefinitions:
    def _make_definition(self, node_id: str) -> dict:
        return {"id": node_id, "general": {"read_only": False}, "data": "value"}

    def test_creates_missing_node(self, tmp_path: Path):
        definition = self._make_definition("cat-sdwan-manager")
        (tmp_path / "cat-sdwan-manager.yaml").write_text(
            "id: cat-sdwan-manager\ngeneral:\n  read_only: false\ndata: value\n"
        )
        cml = MagicMock()

        with patch("catalyst_sdwan_lab.tasks.setup.CML_NODES_DEFINITION_DIR", tmp_path):
            _sync_node_definitions(cml, existing={}, update=MagicMock())

        cml.definitions.upload_node_definition.assert_called_once_with(definition)

    def test_updates_changed_node(self, tmp_path: Path):
        (tmp_path / "cat-sdwan-manager.yaml").write_text(
            "id: cat-sdwan-manager\ngeneral:\n  read_only: false\ndata: new\n"
        )
        existing = {
            "cat-sdwan-manager": {
                "id": "cat-sdwan-manager", "general": {"read_only": False}, "data": "old"
            }
        }
        cml = MagicMock()

        with patch("catalyst_sdwan_lab.tasks.setup.CML_NODES_DEFINITION_DIR", tmp_path):
            _sync_node_definitions(cml, existing=existing, update=MagicMock())

        cml.definitions.upload_node_definition.assert_called_once()
        _, kwargs = cml.definitions.upload_node_definition.call_args
        assert kwargs.get("update") is True

    def test_skips_unchanged_node(self, tmp_path: Path):
        (tmp_path / "cat-sdwan-manager.yaml").write_text(
            "id: cat-sdwan-manager\ngeneral:\n  read_only: false\ndata: value\n"
        )
        existing = {
            "cat-sdwan-manager": {
                "id": "cat-sdwan-manager", "general": {"read_only": False}, "data": "value"
            }
        }
        cml = MagicMock()

        with patch("catalyst_sdwan_lab.tasks.setup.CML_NODES_DEFINITION_DIR", tmp_path):
            _sync_node_definitions(cml, existing=existing, update=MagicMock())

        cml.definitions.upload_node_definition.assert_not_called()

    def test_clears_read_only_before_update(self, tmp_path: Path):
        (tmp_path / "cat-sdwan-manager.yaml").write_text(
            "id: cat-sdwan-manager\ngeneral:\n  read_only: false\ndata: new\n"
        )
        existing = {
            "cat-sdwan-manager": {
                "id": "cat-sdwan-manager", "general": {"read_only": True}, "data": "old"
            }
        }
        cml = MagicMock()

        with patch("catalyst_sdwan_lab.tasks.setup.CML_NODES_DEFINITION_DIR", tmp_path):
            _sync_node_definitions(cml, existing=existing, update=MagicMock())

        cml.definitions.set_node_definition_read_only.assert_called_once_with(
            "cat-sdwan-manager", False
        )

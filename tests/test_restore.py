from pathlib import Path

import pytest

from catalyst_sdwan_lab.tasks.restore import (
    _find_backup_root,
    _find_primary_manager_id,
    _load_backup,
)


class TestFindPrimaryManagerId:
    def _topology(self, nodes: list[dict], links: list[dict]) -> dict:
        return {"nodes": nodes, "links": links}

    def test_finds_manager_connected_to_external_via_eth0(self) -> None:
        topology = self._topology(
            nodes=[
                {"id": "n0", "node_definition": "cat-sdwan-manager"},
                {"id": "n5", "node_definition": "external_connector"},
            ],
            links=[{"n1": "n5", "n2": "n0", "i1": "i0", "i2": "i0"}],
        )
        assert _find_primary_manager_id(topology) == "n0"

    def test_link_reversed_still_found(self) -> None:
        topology = self._topology(
            nodes=[
                {"id": "n0", "node_definition": "cat-sdwan-manager"},
                {"id": "n5", "node_definition": "external_connector"},
            ],
            links=[{"n1": "n0", "n2": "n5", "i1": "i0", "i2": "i0"}],
        )
        assert _find_primary_manager_id(topology) == "n0"

    def test_secondary_manager_not_matched(self) -> None:
        topology = self._topology(
            nodes=[
                {"id": "n0", "node_definition": "cat-sdwan-manager"},
                {"id": "n1", "node_definition": "cat-sdwan-manager"},
                {"id": "n5", "node_definition": "external_connector"},
            ],
            links=[
                {"n1": "n5", "n2": "n0", "i1": "i0", "i2": "i0"},
                {"n1": "n1", "n2": "n3", "i1": "i2", "i2": "i0"},
            ],
        )
        assert _find_primary_manager_id(topology) == "n0"

    def test_non_eth0_link_not_matched(self) -> None:
        topology = self._topology(
            nodes=[
                {"id": "n0", "node_definition": "cat-sdwan-manager"},
                {"id": "n5", "node_definition": "external_connector"},
            ],
            links=[{"n1": "n5", "n2": "n0", "i1": "i0", "i2": "i1"}],
        )
        assert _find_primary_manager_id(topology) is None

    def test_no_external_connector_returns_none(self) -> None:
        topology = self._topology(
            nodes=[{"id": "n0", "node_definition": "cat-sdwan-manager"}],
            links=[],
        )
        assert _find_primary_manager_id(topology) is None

    def test_non_manager_connected_to_external_not_matched(self) -> None:
        topology = self._topology(
            nodes=[
                {"id": "n0", "node_definition": "cat-sdwan-controller"},
                {"id": "n5", "node_definition": "external_connector"},
            ],
            links=[{"n1": "n5", "n2": "n0", "i1": "i0", "i2": "i0"}],
        )
        assert _find_primary_manager_id(topology) is None


class TestFindBackupRoot:
    def test_topology_at_top_level(self, tmp_path: Path) -> None:
        (tmp_path / "topology.yaml").write_text("lab: {}")
        assert _find_backup_root(tmp_path) == tmp_path

    def test_topology_nested_in_single_wrapping_folder(self, tmp_path: Path) -> None:
        wrapped = tmp_path / "sdwan_lab_backup"
        wrapped.mkdir()
        (wrapped / "topology.yaml").write_text("lab: {}")
        assert _find_backup_root(tmp_path) == wrapped

    def test_falls_back_to_given_path_when_ambiguous(self, tmp_path: Path) -> None:
        (tmp_path / "one").mkdir()
        (tmp_path / "two").mkdir()
        (tmp_path / "two" / "topology.yaml").write_text("lab: {}")
        assert _find_backup_root(tmp_path) == tmp_path

    def test_falls_back_to_given_path_when_nothing_found(self, tmp_path: Path) -> None:
        assert _find_backup_root(tmp_path) == tmp_path


class TestLoadBackup:
    def test_directory_backup_resolves_to_absolute_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backup_dir = tmp_path / "sdwan_lab_backup"
        backup_dir.mkdir()
        (backup_dir / "topology.yaml").write_text("lab: {}")
        (backup_dir / "manager_configs").mkdir()

        monkeypatch.chdir(tmp_path)
        _, manager_configs_dir, tmpdir = _load_backup(Path("sdwan_lab_backup"))

        assert manager_configs_dir.is_absolute()
        assert tmpdir is None

from catalyst_sdwan_lab.tasks.restore import _find_primary_manager_id


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

import pytest

from catalyst_sdwan_lab.tasks.utils import _normalize_version, node_config_text


@pytest.mark.parametrize(
    "version, expected",
    [
        ("26.1.1", "26.01.01"),
        ("17.15.3a", "17.15.03a"),
        ("26.01.01", "26.01.01"),
        ("17.06.06a", "17.06.06a"),
        ("17.9.4a", "17.09.04a"),
        ("20.15.1", "20.15.01"),
    ],
)
def test_normalize_version(version: str, expected: str) -> None:
    assert _normalize_version(version) == expected


def test_node_config_text_string() -> None:
    assert node_config_text({"configuration": "uuid : abc-123"}) == "uuid : abc-123"


def test_node_config_text_list() -> None:
    node = {"configuration": [{"name": "Main", "content": "uuid : abc-123"}]}
    assert node_config_text(node) == "uuid : abc-123"


def test_node_config_text_empty_list() -> None:
    assert node_config_text({"configuration": []}) == ""


def test_node_config_text_missing() -> None:
    assert node_config_text({}) == ""

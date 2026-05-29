import pytest

from catalyst_sdwan_lab.tasks.utils import _normalize_version


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

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.exceptions import Exit

from catalyst_sdwan_lab.tasks.delete import run


def _make_cml(labs: list) -> MagicMock:
    cml = MagicMock()
    cml.find_labs_by_title.return_value = labs
    return cml


class TestDelete:
    def test_exits_if_no_lab_found(self) -> None:
        with pytest.raises(Exit):
            run(_make_cml([]), "my-lab", force=True)

    def test_exits_if_multiple_labs_found(self) -> None:
        with pytest.raises(Exit):
            run(_make_cml([MagicMock(), MagicMock()]), "my-lab", force=True)

    def test_force_skips_confirmation_and_deletes(self) -> None:
        lab = MagicMock()
        run(_make_cml([lab]), "my-lab", force=True)
        lab.stop.assert_called_once()
        lab.wipe.assert_called_once()
        lab.remove.assert_called_once()

    def test_confirmed_prompt_deletes(self) -> None:
        lab = MagicMock()
        with patch("typer.confirm", return_value=True):
            run(_make_cml([lab]), "my-lab", force=False)
        lab.stop.assert_called_once()
        lab.wipe.assert_called_once()
        lab.remove.assert_called_once()

    def test_denied_prompt_exits_without_deleting(self) -> None:
        lab = MagicMock()
        with patch("typer.confirm", return_value=False):
            with pytest.raises(Exit):
                run(_make_cml([lab]), "my-lab", force=False)
        lab.stop.assert_not_called()
        lab.remove.assert_not_called()

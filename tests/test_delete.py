from unittest.mock import MagicMock, patch

import pytest
from typer import Exit

from catalyst_sdwan_lab.tasks.delete import run

_CML_ARGS = ("cml.example.com", "admin", "password")


def _patch_cml(labs: list) -> MagicMock:
    cml = MagicMock()
    cml.find_labs_by_title.return_value = labs
    return cml


class TestDelete:
    def test_exits_if_no_lab_found(self) -> None:
        cml = _patch_cml([])
        with patch("catalyst_sdwan_lab.tasks.delete.connect_cml", return_value=cml):
            with pytest.raises(Exit):
                run(*_CML_ARGS, "my-lab", force=True)

    def test_exits_if_multiple_labs_found(self) -> None:
        cml = _patch_cml([MagicMock(), MagicMock()])
        with patch("catalyst_sdwan_lab.tasks.delete.connect_cml", return_value=cml):
            with pytest.raises(Exit):
                run(*_CML_ARGS, "my-lab", force=True)

    def test_force_skips_confirmation_and_deletes(self) -> None:
        lab = MagicMock()
        cml = _patch_cml([lab])
        with patch("catalyst_sdwan_lab.tasks.delete.connect_cml", return_value=cml):
            run(*_CML_ARGS, "my-lab", force=True)
        lab.stop.assert_called_once()
        lab.wipe.assert_called_once()
        lab.remove.assert_called_once()

    def test_confirmed_prompt_deletes(self) -> None:
        lab = MagicMock()
        cml = _patch_cml([lab])
        with patch("catalyst_sdwan_lab.tasks.delete.connect_cml", return_value=cml):
            with patch("typer.confirm", return_value=True):
                run(*_CML_ARGS, "my-lab", force=False)
        lab.stop.assert_called_once()
        lab.wipe.assert_called_once()
        lab.remove.assert_called_once()

    def test_denied_prompt_exits_without_deleting(self) -> None:
        lab = MagicMock()
        cml = _patch_cml([lab])
        with patch("catalyst_sdwan_lab.tasks.delete.connect_cml", return_value=cml):
            with patch("typer.confirm", return_value=False):
                with pytest.raises(Exit):
                    run(*_CML_ARGS, "my-lab", force=False)
        lab.stop.assert_not_called()
        lab.remove.assert_not_called()

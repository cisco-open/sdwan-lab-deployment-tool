import gzip
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.exceptions import Exit

from catalyst_sdwan_lab.manager_client import ManagerAPIError
from catalyst_sdwan_lab.tasks.deploy import (
    _attach_controller_template,
    _check_ip_free,
    _complete_initial_setup_workflow,
    _configure_manager,
    _extract_org_name,
    _find_lab,
    _import_controller_templates,
    _onboard_control_components,
    _restore_basic_configuration,
    _template_post_body,
)
from catalyst_sdwan_lab.tasks.utils import load_certs, resolve_image


def _make_serial_file(tmp_path: Path, org: str) -> Path:
    data = json.dumps({"organization": org}).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="viptela_serial_file")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    path = tmp_path / "serial.viptela"
    with gzip.open(path, "wb") as gz:
        gz.write(buf.getvalue())
    return path


class TestExtractOrgName:
    def test_valid_file_returns_org(self, tmp_path: Path) -> None:
        assert _extract_org_name(_make_serial_file(tmp_path, "MyOrg")) == "MyOrg"

    def test_missing_member_raises(self, tmp_path: Path) -> None:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w"):
            pass
        path = tmp_path / "serial.viptela"
        with gzip.open(path, "wb") as gz:
            gz.write(buf.getvalue())
        with pytest.raises(ValueError, match="viptela_serial_file not found"):
            _extract_org_name(path)

    def test_missing_org_field_raises(self, tmp_path: Path) -> None:
        data = json.dumps({"other": "field"}).encode()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name="viptela_serial_file")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        path = tmp_path / "serial.viptela"
        with gzip.open(path, "wb") as gz:
            gz.write(buf.getvalue())
        with pytest.raises(ValueError, match="organization field missing"):
            _extract_org_name(path)

    def test_invalid_gzip_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.viptela"
        path.write_bytes(b"not gzip data")
        with pytest.raises(ValueError):
            _extract_org_name(path)


class TestResolveImage:
    def _make_cml(self, available: list[str]) -> MagicMock:
        cml = MagicMock()
        cml.definitions.image_definitions_for_node_definition.return_value = [
            {"id": img_id} for img_id in available
        ]
        return cml

    def test_exact_match(self) -> None:
        cml = self._make_cml(["cat-sdwan-manager-20.15.1"])
        assert resolve_image(cml, "cat-sdwan-manager", "20.15.1") == "cat-sdwan-manager-20.15.1"

    def test_controller_strips_trailing_1(self) -> None:
        cml = self._make_cml(["cat-sdwan-controller-20.15.1"])
        result = resolve_image(cml, "cat-sdwan-controller", "20.15.1.1")
        assert result == "cat-sdwan-controller-20.15.1"

    def test_controller_decrements_patch(self) -> None:
        cml = self._make_cml(["cat-sdwan-controller-20.15.1"])
        result = resolve_image(cml, "cat-sdwan-controller", "20.15.2")
        assert result == "cat-sdwan-controller-20.15.1"

    def test_manager_no_fallback_exits(self) -> None:
        cml = self._make_cml(["cat-sdwan-manager-20.15.1"])
        with pytest.raises(Exit):
            resolve_image(cml, "cat-sdwan-manager", "20.15.2")

    def test_no_available_images_exits(self) -> None:
        cml = self._make_cml([])
        with pytest.raises(Exit):
            resolve_image(cml, "cat-sdwan-manager", "20.15.1")


class TestTemplatePostBody:
    def test_strips_metadata(self) -> None:
        raw = {
            "templateId": "abc", "@rid": 1, "createdBy": "admin",
            "createdOn": 1234, "lastUpdatedBy": "admin", "lastUpdatedOn": 5678,
            "templateName": "foo",
        }
        result = _template_post_body(raw)
        stripped = (
            "templateId", "@rid", "createdBy", "createdOn", "lastUpdatedBy", "lastUpdatedOn"
        )
        for key in stripped:
            assert key not in result
        assert result["templateName"] == "foo"

    def test_sets_factory_default_false(self) -> None:
        assert _template_post_body({})["factoryDefault"] is False

    def test_sets_readonly_false(self) -> None:
        assert _template_post_body({})["readonly"] is False


class TestCompleteInitialSetupWorkflow:
    def test_already_complete_does_nothing(self) -> None:
        client = MagicMock()
        client.get_workflows.return_value = [{"id": "wf1", "userContext": {"complete": True}}]
        _complete_initial_setup_workflow(client)
        client.create_workflow.assert_not_called()
        client.update_workflow.assert_not_called()

    def test_existing_incomplete_updates(self) -> None:
        client = MagicMock()
        client.get_workflows.return_value = [{"id": "wf1", "userContext": {"complete": False}}]
        _complete_initial_setup_workflow(client)
        client.create_workflow.assert_not_called()
        client.update_workflow.assert_called_once()
        assert client.update_workflow.call_args[0][2]["complete"] is True

    def test_no_existing_creates_then_updates(self) -> None:
        client = MagicMock()
        client.get_workflows.return_value = []
        client.create_workflow.return_value = "new-id"
        _complete_initial_setup_workflow(client)
        client.create_workflow.assert_called_once()
        assert client.update_workflow.call_args[0][0] == "new-id"


class TestConfigureManager:
    def test_skips_org_if_already_set(self) -> None:
        client = MagicMock()
        client.get_organization.return_value = "ExistingOrg"
        _configure_manager(client, "20.15.1", "NewOrg", "chain")
        client.settings_organization.assert_not_called()

    def test_sets_org_if_none(self) -> None:
        client = MagicMock()
        client.get_organization.return_value = None
        _configure_manager(client, "20.15.1", "NewOrg", "chain")
        client.settings_organization.assert_called_once_with("NewOrg")

    def test_initial_setup_called_for_v26(self) -> None:
        client = MagicMock()
        client.get_organization.return_value = "Org"
        client.get_workflows.return_value = [{"id": "wf", "userContext": {"complete": True}}]
        _configure_manager(client, "26.1.1", "Org", "chain")
        client.get_workflows.assert_called_once()

    def test_initial_setup_skipped_below_v26(self) -> None:
        client = MagicMock()
        client.get_organization.return_value = "Org"
        _configure_manager(client, "20.15.1", "Org", "chain")
        client.get_workflows.assert_not_called()


class TestOnboardControlComponents:
    def _make_client(self, pending_ips: list[str] = []) -> MagicMock:
        client = MagicMock()
        controllers = [
            {"personality": "vsmart", "deviceIP": ip, "serialNumber": "No certificate installed"}
            for ip in pending_ips
        ]
        client.get_controllers.return_value = controllers
        return client

    def test_adds_both_components(self) -> None:
        client = self._make_client()
        _onboard_control_components(client, MagicMock(), "v4", on_status=MagicMock())
        assert client.add_controller.call_count == 2

    def test_skips_on_already_exists_error(self) -> None:
        client = self._make_client()
        client.add_controller.side_effect = ManagerAPIError("Device UUID already exists")
        _onboard_control_components(client, MagicMock(), "v4", on_status=MagicMock())
        client.add_controller.assert_called()

    def test_raises_on_other_api_error(self) -> None:
        client = self._make_client()
        client.add_controller.side_effect = ManagerAPIError("some other error")
        with pytest.raises(ManagerAPIError):
            _onboard_control_components(client, MagicMock(), "v4", on_status=MagicMock())

    def test_always_fetches_controllers_for_signing(self) -> None:
        client = self._make_client()
        _onboard_control_components(client, MagicMock(), "v4", on_status=MagicMock())
        client.get_controllers.assert_called_once()


class TestRestoreBasicConfiguration:
    def test_skips_if_both_groups_present(self) -> None:
        client = MagicMock()
        client.get_config_groups.return_value = [
            {"name": "edge_basic"}, {"name": "sdrouting_basic"}
        ]
        _restore_basic_configuration(client, "v4")
        client.import_configuration.assert_not_called()

    def test_imports_if_edge_basic_missing(self) -> None:
        client = MagicMock()
        client.get_config_groups.return_value = [{"name": "sdrouting_basic"}]
        client.import_configuration.return_value = "task-1"
        _restore_basic_configuration(client, "v4")
        client.import_configuration.assert_called_once()

    def test_imports_if_sdrouting_basic_missing(self) -> None:
        client = MagicMock()
        client.get_config_groups.return_value = [{"name": "edge_basic"}]
        client.import_configuration.return_value = "task-1"
        _restore_basic_configuration(client, "v4")
        client.import_configuration.assert_called_once()

    def test_imports_if_missing(self) -> None:
        client = MagicMock()
        client.get_config_groups.return_value = [{"name": "other"}]
        client.import_configuration.return_value = "task-1"
        _restore_basic_configuration(client, "v4")
        client.import_configuration.assert_called_once()
        client.wait_for_task.assert_called_once_with("task-1")


class TestImportControllerTemplates:
    def test_skips_if_present_and_returns_id(self) -> None:
        client = MagicMock()
        client.get_device_templates.return_value = [
            {"templateName": "controller_basic", "templateId": "existing-id"}
        ]
        assert _import_controller_templates(client, "v4") == "existing-id"
        client.create_feature_template.assert_not_called()

    def test_creates_templates_and_returns_id(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.get_device_templates.return_value = []
        client.create_feature_template.return_value = "new-feat-id"
        client.create_device_template.return_value = "new-dev-id"

        common = tmp_path / "feature" / "common"
        common.mkdir(parents=True)
        (tmp_path / "feature" / "v4").mkdir(parents=True)
        feat = {"templateId": "old-feat-id", "templateName": "feat"}
        (common / "feat.json").write_text(json.dumps(feat))
        device = {"templateId": "old-dev-id", "templateName": "controller_basic",
                  "subTemplates": [{"templateId": "old-feat-id"}]}
        (tmp_path / "device_template.json").write_text(json.dumps(device))

        with patch("catalyst_sdwan_lab.tasks.deploy.CONTROLLER_TEMPLATES_DIR", tmp_path):
            result = _import_controller_templates(client, "v4")

        assert result == "new-dev-id"
        client.create_feature_template.assert_called_once()
        client.create_device_template.assert_called_once()


class TestAttachControllerTemplate:
    def _controller(self, *, has_template: bool, ip: str = "172.16.0.101") -> dict:
        return {"personality": "vsmart", "template": "t" if has_template else None,
                "deviceIP": ip, "uuid": "uuid-1"}

    def test_skips_if_all_have_template(self) -> None:
        client = MagicMock()
        client.get_controllers.return_value = [self._controller(has_template=True)]
        _attach_controller_template(client, "v4", "tmpl-id")
        client.attach_device_template.assert_not_called()

    def test_v4_payload_has_ipv4_only(self) -> None:
        client = MagicMock()
        client.get_controllers.return_value = [self._controller(has_template=False)]
        client.attach_device_template.return_value = "task-1"
        _attach_controller_template(client, "v4", "tmpl-id")
        device = client.attach_device_template.call_args[0][1][0]
        assert device["//system/system-ip"] == "100.0.0.101"
        assert device["/0/eth1/interface/ip/address"] == "172.16.0.101/24"
        assert "/0/eth1/interface/ipv6/address" not in device

    def test_v6_payload_has_ipv6_only(self) -> None:
        client = MagicMock()
        client.get_controllers.return_value = [
            self._controller(has_template=False, ip="fc00:172:16::101")
        ]
        client.attach_device_template.return_value = "task-1"
        _attach_controller_template(client, "v6", "tmpl-id")
        device = client.attach_device_template.call_args[0][1][0]
        assert device["//system/system-ip"] == "100.0.0.101"
        assert device["/0/eth1/interface/ipv6/address"] == "fc00:172:16::101/64"
        assert "/0/eth1/interface/ip/address" not in device

    def test_dual_payload_has_both(self) -> None:
        client = MagicMock()
        client.get_controllers.return_value = [self._controller(has_template=False)]
        client.attach_device_template.return_value = "task-1"
        _attach_controller_template(client, "dual", "tmpl-id")
        device = client.attach_device_template.call_args[0][1][0]
        assert "/0/eth1/interface/ip/address" in device
        assert "/0/eth1/interface/ipv6/address" in device


class TestCheckIpFree:
    def test_exits_if_ip_in_use(self) -> None:
        with patch("subprocess.call", return_value=0):
            with pytest.raises(Exit):
                _check_ip_free("10.0.0.1")

    def test_passes_if_ip_free(self) -> None:
        with patch("subprocess.call", return_value=1):
            _check_ip_free("10.0.0.1")


class TestLoadCerts:
    def test_exits_if_file_missing(self, tmp_path: Path) -> None:
        (tmp_path / "signCA.pem").write_text("cert")
        (tmp_path / "signCA.key").write_text("key")
        with patch("catalyst_sdwan_lab.tasks.utils.CERTS_DIR", tmp_path):
            with pytest.raises(Exit):
                load_certs()

    def test_loads_all_certs(self, tmp_path: Path) -> None:
        (tmp_path / "signCA.pem").write_text("cert-content")
        (tmp_path / "signCA.key").write_text("key-content")
        (tmp_path / "chainCA.pem").write_text("chain-content")
        with patch("catalyst_sdwan_lab.tasks.utils.CERTS_DIR", tmp_path):
            certs = load_certs()
        assert certs.cert == "cert-content"
        assert certs.key == "key-content"
        assert certs.chain == "chain-content"


class TestFindLab:
    def test_finds_matching_lab(self) -> None:
        cml = MagicMock()
        lab = MagicMock()
        lab.notes = "manager_external_ip = 10.0.0.1:8443\nother"
        cml.all_labs.return_value = [lab]
        _find_lab(cml, "10.0.0.1", 8443)

    def test_exits_if_ip_does_not_match(self) -> None:
        cml = MagicMock()
        lab = MagicMock()
        lab.notes = "manager_external_ip = 10.0.0.2:8443"
        cml.all_labs.return_value = [lab]
        with pytest.raises(Exit):
            _find_lab(cml, "10.0.0.1", 8443)

    def test_exits_if_no_labs(self) -> None:
        cml = MagicMock()
        cml.all_labs.return_value = []
        with pytest.raises(Exit):
            _find_lab(cml, "10.0.0.1", 8443)


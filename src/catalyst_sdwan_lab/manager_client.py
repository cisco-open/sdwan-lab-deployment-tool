import logging
import time
from pathlib import Path
from typing import Any

import requests
import requests.packages.urllib3
from requests import Response, Session

requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]

log = logging.getLogger(__name__)


class ManagerAPIError(Exception):
    pass


class ManagerClient:
    _TIMEOUT = 30

    def __init__(self, host: str, port: int, username: str, password: str) -> None:
        self._base = f"https://{host}:{port}"
        self._username = username
        self._password = password
        self._session: Session = requests.Session()
        self._session.verify = False

    def login(self) -> None:
        response = self._session.post(
            f"{self._base}/j_security_check",
            data={"j_username": self._username, "j_password": self._password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self._TIMEOUT,
        )
        if response.text or not self._session.cookies.get("JSESSIONID"):
            raise ManagerAPIError("Authentication failed: invalid credentials")

        token = self._session.get(f"{self._base}/dataservice/client/token", timeout=self._TIMEOUT)
        if token.status_code != 200 or "<html>" in token.text:
            raise ManagerAPIError("Failed to obtain XSRF token")
        self._session.headers["x-xsrf-token"] = token.text

    def get_organization(self) -> str | None:
        data = self._get("/dataservice/settings/configuration/organization").get("data", [])
        return data[0].get("org") if data else None

    def get_validator_fqdn(self) -> str | None:
        data = self._get("/dataservice/settings/configuration/device").get("data", [])
        return data[0].get("domainIp") if data else None

    def settings_organization(self, org_name: str) -> None:
        self._put("/dataservice/settings/configuration/organization", {"org": org_name})

    def settings_device(self, domain_ip: str, port: str = "12346") -> None:
        self._put(
            "/dataservice/settings/configuration/device",
            {"domainIp": domain_ip, "port": port},
        )

    def settings_vedge_cloud(self, certificate_authority: str) -> None:
        self._put(
            "/dataservice/settings/configuration/vedgecloud",
            {"certificateauthority": certificate_authority},
        )

    def settings_certificate(self, certificate_signing: str) -> None:
        self._post(
            "/dataservice/settings/configuration/certificate",
            {"certificateSigning": certificate_signing},
        )

    def settings_enterprise_rootca(self, ca_chain: str) -> None:
        self._put(
            "/dataservice/settings/configuration/certificate/enterpriserootca",
            {"enterpriseRootCA": ca_chain},
        )

    def settings_data_stream(
        self, *, enable: bool, ip_type: str, server_hostname: str, vpn: int
    ) -> None:
        self._put(
            "/dataservice/settings/configuration/vmanagedatastream",
            {"enable": enable, "ipType": ip_type, "serverHostName": server_hostname, "vpn": vpn},
        )

    def settings_cloudx(self, mode: str) -> None:
        self._put("/dataservice/settings/configuration/cloudx", {"mode": mode})

    def get_workflows(self, workflow_type: str) -> list[dict[str, Any]]:
        return self._get(f"/dataservice/workflow?type={workflow_type}").get("workflows", [])

    def create_workflow(self, workflow_type: str, user_context: dict[str, Any]) -> str:
        return self._post(
            "/dataservice/workflow",
            {"type": workflow_type, "userContext": user_context},
        )["id"]

    def update_workflow(
        self, workflow_id: str, workflow_type: str, user_context: dict[str, Any]
    ) -> None:
        self._put(
            "/dataservice/workflow",
            {
                "id": workflow_id,
                "type": workflow_type,
                "activities": {
                    "SAVE_ACTION": {"type": workflow_type, "userContext": user_context}
                },
                "userContext": user_context,
            },
        )

    def get_device_templates(self) -> list[dict[str, Any]]:
        return self._get("/dataservice/template/device").get("data", [])

    def create_feature_template(self, data: dict[str, Any]) -> str:
        result = self._post("/dataservice/template/feature", data)
        return result["templateId"]

    def create_device_template(self, data: dict[str, Any]) -> str:
        result = self._post("/dataservice/template/device/feature", data)
        return result["templateId"]

    def attach_device_template(self, template_id: str, devices: list[dict[str, Any]]) -> str:
        payload = {
            "deviceTemplateList": [
                {
                    "templateId": template_id,
                    "device": devices,
                    "isEdited": False,
                    "isMasterEdited": False,
                }
            ]
        }
        return self._post("/dataservice/template/device/config/attachfeature", payload)["id"]

    def get_config_groups(self) -> list[dict[str, Any]]:
        return self._get("/dataservice/v1/config-group")

    def get_vedges(self) -> list[dict[str, Any]]:
        return self._get("/dataservice/system/device/vedges").get("data", [])

    def get_bootstrap_config(self, uuid: str, *, wanif: str | None = None) -> str:
        url = (
            f"/dataservice/system/device/bootstrap/device/{uuid}"
            "?configtype=cloudinit&inclDefRootCert=false&version=v1"
        )
        if wanif:
            url += f"&wanif={wanif}"
        return self._get(url)["bootstrapConfig"]

    def associate_config_group(self, config_group_id: str, uuids: list[str]) -> None:
        self._put(
            f"/dataservice/v1/config-group/{config_group_id}/device/associate",
            {"devices": [{"id": u} for u in uuids]},
        )

    def set_config_group_variables(
        self, config_group_id: str, devices: list[dict[str, Any]], *, solution: str = "sdwan"
    ) -> None:
        self._put(
            f"/dataservice/v1/config-group/{config_group_id}/device/variables",
            {"solution": solution, "devices": devices},
        )

    def deploy_config_group(self, config_group_id: str, uuids: list[str]) -> str:
        data = self._post(
            f"/dataservice/v1/config-group/{config_group_id}/device/deploy",
            {"devices": [{"id": u} for u in uuids]},
        )
        if not data or "parentTaskId" not in data:
            raise ManagerAPIError("deploy_config_group: missing parentTaskId in response")
        return data["parentTaskId"]

    def import_configuration(self, path: Path) -> str:
        with open(path, "rb") as f:
            response = self._session.post(
                f"{self._base}/dataservice/v1/packages/import",
                files={"file": (path.name, f, "application/octet-stream")},
                timeout=60,
            )
        self._raise_for_status(response)
        return response.json()["taskId"]

    def get_controllers(self) -> list[dict[str, Any]]:
        return self._get("/dataservice/system/device/controllers").get("data", [])

    def get_network_hierarchy(self) -> list[dict[str, Any]]:
        return self._get("/dataservice/v1/network-hierarchy")

    def add_controller(self, ip: str, personality: str, username: str, password: str) -> None:
        self._post(
            "/dataservice/system/device",
            {"deviceIP": ip, "username": username, "password": password,
             "generateCSR": False, "personality": personality},
        )

    def generate_csr(self, ip: str) -> str:
        data = self._post("/dataservice/certificate/generate/csr", {"deviceIP": ip}, timeout=120)
        return data["data"][0]["deviceCSR"]

    def install_signed_cert(self, cert_pem: str) -> str:
        response = self._session.post(
            f"{self._base}/dataservice/certificate/install/signedCert",
            data=cert_pem,
            timeout=120,
        )
        self._raise_for_status(response)
        return response.json()["id"]

    def wait_for_task(self, task_id: str, timeout: int = 300) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self._get(f"/dataservice/device/action/status/{task_id}")
            status = data.get("summary", {}).get("status", "")
            if status == "done":
                count = data.get("summary", {}).get("count", {})
                log.debug("Task %s done: %s", task_id, count)
                if count.get("Failure", 0) > 0:
                    raise ManagerAPIError(f"Task {task_id} completed with failures")
                return
            time.sleep(5)
        raise ManagerAPIError(f"Task {task_id} timed out after {timeout}s")

    def upload_serial_file(self, path: Path) -> None:
        with open(path, "rb") as f:
            response = self._session.post(
                f"{self._base}/dataservice/system/device/fileupload",
                files={"file": (path.name, f)},
                data={"validity": "valid", "upload": True},
                timeout=60,
            )
        self._raise_for_status(response)

    def logout(self) -> None:
        try:
            self._session.get(f"{self._base}/logout", timeout=self._TIMEOUT)
        finally:
            self._session.close()

    def _get(self, path: str) -> Any:
        response = self._session.get(f"{self._base}{path}", timeout=self._TIMEOUT)
        self._raise_for_status(response)
        return response.json()

    def _post(self, path: str, body: Any = None, *, timeout: int | None = None) -> Any:
        response = self._session.post(
            f"{self._base}{path}", json=body, timeout=timeout or self._TIMEOUT
        )
        self._raise_for_status(response)
        return response.json() if response.text else None

    def _put(self, path: str, body: Any) -> Any:
        response = self._session.put(f"{self._base}{path}", json=body, timeout=self._TIMEOUT)
        self._raise_for_status(response)
        return response.json() if response.text else None

    def _raise_for_status(self, response: Response) -> None:
        if not response.ok:
            raise ManagerAPIError(f"HTTP {response.status_code}: {response.text[:200]}")

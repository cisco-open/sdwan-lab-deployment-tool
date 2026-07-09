import logging
import time
from pathlib import Path
from typing import Any, Literal

import requests
import urllib3
from requests import Response, Session

urllib3.disable_warnings()

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

    def get_certificate_signing(self) -> Literal["enterprise", "cisco"]:
        data = self._get("/dataservice/settings/configuration/certificate").get("data", [])
        value = data[0].get("certificateSigning", "enterprise") if data else "enterprise"
        return "cisco" if value == "cisco" else "enterprise"

    def settings_certificate(
        self,
        certificate_signing: str,
        validity_period: str | None = None,
        retrieve_interval: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"certificateSigning": certificate_signing}
        if validity_period is not None:
            payload["validityPeriod"] = validity_period
        if retrieve_interval is not None:
            payload["retrieveInterval"] = retrieve_interval
        self._post("/dataservice/settings/configuration/certificate", payload)

    def get_cisco_services(self) -> list[dict[str, Any]]:
        return self._get("/dataservice/settings/configuration/ciscoservices").get("data", [])

    _PROXY_NO_PROXY_LIST = (
        "localhost|::1|127.0.0.1"
        "|169.254.1.1|169.254.1.2|169.254.1.3|169.254.1.4|169.254.1.5"
        "|169.254.1.6|169.254.1.7|169.254.1.8|169.254.1.9|169.254.1.10"
        "|169.254.1.11|169.254.1.12|169.254.1.13|169.254.1.14|169.254.1.15"
        "|169.254.1.16|169.254.1.17"
    )
    _PROXY_NO_PROXY_RFC1918 = "10.*, 172.*, 192.168.*"

    def settings_proxy(self, proxy_ip: str, proxy_port: str, no_proxy_extra: str = "") -> None:
        no_proxy_custom = (
            f"{self._PROXY_NO_PROXY_RFC1918}, {no_proxy_extra}"
            if no_proxy_extra
            else self._PROXY_NO_PROXY_RFC1918
        )
        self._post(
            "/dataservice/settings/configuration/proxyHttpServer",
            {"proxy": True, "proxyIp": proxy_ip, "proxyPort": proxy_port,
             "NoProxyList": self._PROXY_NO_PROXY_LIST,
             "NoProxyListCustom": no_proxy_custom},
        )

    def initiate_cisco_account_registration(self) -> dict[str, Any]:
        return self._post("/dataservice/settings/services/account/register", {})

    def poll_cisco_account_token(self, user_code: str) -> bool:
        response = self._session.post(
            f"{self._base}/dataservice/settings/services/account/register/token?userCode={user_code}",
            json=["pnp"],
            timeout=self._TIMEOUT,
        )
        return response.ok

    def register_cisco_services(self, username: str) -> None:
        self._post(
            "/dataservice/settings/services/register",
            [{"pnp": {"enabled": True, "username": username}}],
            timeout=120,
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

    def get_vedge_otps(self) -> dict[str, str]:
        data = self._get("/dataservice/certificate/vedge/list").get("data", [])
        return {
            d["uuid"]: d["serialNumber"]
            for d in data
            if d.get("vedgeCertificateState") == "tokengenerated" and d.get("serialNumber")
        }

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

    def get_config_group_variable_names(self, config_group_id: str) -> set[str]:
        data = self._get(f"/dataservice/v1/config-group/{config_group_id}/device/variables/schema")
        return {
            name
            for entry in data
            for var in entry.get("variables", [])
            for name in var.get("schema", {}).get("properties", {})
        }

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

    def enable_mrf(self, global_id: str) -> None:
        self._post(
            f"/dataservice/v1/network-hierarchy/{global_id}/network-settings/mrf",
            {"data": {"enableMrfInterRegionRouting": {"optionType": "global", "value": True}}},
        )

    def create_network_hierarchy_entry(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/dataservice/v1/network-hierarchy", payload) or {}

    def get_sync_status(self) -> list[dict[str, Any]]:
        return self._get("/dataservice/device/sync_status?groupId=all").get("data", [])

    def get_cluster_management_list(self) -> list[dict[str, Any]]:
        return self._get("/dataservice/clusterManagement/list").get("data", [])

    def get_local_system_ip(self) -> str:
        return self._get("/dataservice/device/vmanage")["data"]["ipAddress"]

    def get_vpn0_interface_ip(self, system_ip: str, ifname: str) -> str:
        interfaces = self._get(
            f"/dataservice/device/interface?vpn-id=0&deviceId={system_ip}"
        ).get("data", [])
        iface = next(
            (i for i in interfaces if i.get("ifname") == ifname and i.get("af-type") == "ipv4"),
            None,
        )
        if iface is None:
            raise ManagerAPIError(f"Interface {ifname} not found in VPN 0 on {system_ip}")
        return iface["ip-address"].split("/")[0]

    def setup_cluster_ip(
        self, cluster_ip: str, persona: str, username: str, password: str
    ) -> None:
        try:
            self._put(
                "/dataservice/clusterManagement/setup/",
                {
                    "persona": persona,
                    "deviceIP": cluster_ip,
                    "username": username,
                    "password": password,
                    "genCSR": False,
                    "services": {"sd-avc": {"server": True}},
                    "vmanageID": "0",
                },
            )
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            # NMS API restarts after cluster IP setup — connection drop/timeout is expected
            pass

    def add_cluster_node(
        self, cluster_ip: str, persona: str, username: str, password: str,
        retries: int = 120, interval: int = 30,
    ) -> None:
        """Blocks until the POST succeeds (200 OK), retrying on VCC0001 (node API not yet ready)."""
        for _ in range(retries):
            response = self._session.post(
                f"{self._base}/dataservice/clusterManagement/setup/",
                json={
                    "persona": persona,
                    "deviceIP": cluster_ip,
                    "username": username,
                    "password": password,
                    "genCSR": False,
                    "services": {"sd-avc": {"server": False}},
                },
                timeout=self._TIMEOUT,
            )
            if response.ok:
                return
            try:
                code = response.json().get("error", {}).get("code", "")
            except Exception:
                code = ""
            if response.status_code == 400 and code == "VCC0001":
                time.sleep(interval)
                continue
            raise ManagerAPIError(f"HTTP {response.status_code}: {response.text[:200]}")
        raise ManagerAPIError(
            f"Cluster node {cluster_ip} did not become reachable after {retries * interval}s."
        )

    def rediscover_devices(self, devices: list[dict[str, Any]]) -> None:
        self._post(
            "/dataservice/device/action/rediscover",
            {"action": "rediscover", "devices": devices},
        )

    def add_controller(self, ip: str, personality: str, username: str, password: str) -> None:
        self._post(
            "/dataservice/system/device",
            {"deviceIP": ip, "username": username, "password": password,
             "generateCSR": False, "personality": personality},
        )

    def update_device_connection(
        self, uuid: str, device_ip: str, username: str, password: str
    ) -> None:
        self._put(
            f"/dataservice/system/device/{uuid}",
            {"deviceIP": device_ip, "username": username, "password": password},
        )

    def generate_csr(self, ip: str) -> tuple[str, str]:
        data = self._post("/dataservice/certificate/generate/csr", {"deviceIP": ip}, timeout=120)
        entry = data["data"][0]
        return entry["deviceCSR"], entry["uuid"]

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
        except requests.exceptions.RequestException:
            pass
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

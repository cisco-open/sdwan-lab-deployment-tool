import datetime
import gzip
import json
import logging
import re
import tarfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import requests
import typer
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.types import CertificateIssuerPrivateKeyTypes
from rich.console import Console
from virl2_client import ClientLibrary
from virl2_client.exceptions import APIError
from virl2_client.models.lab import Lab

from catalyst_sdwan_lab.manager_client import ManagerAPIError, ManagerClient

console = Console()
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CERTS_DIR = DATA_DIR / "certs"
CML_DEPLOY_TEMPLATES_DIR = DATA_DIR / "cml_lab_definition" / "deploy"
CML_BACKUP_TEMPLATES_DIR = DATA_DIR / "cml_lab_definition" / "backup"
CML_NODES_DEFINITION_DIR = DATA_DIR / "cml_nodes_definition"
MANAGER_CONFIGS_DIR = DATA_DIR / "manager_configs"
CONTROLLER_TEMPLATES_DIR = MANAGER_CONFIGS_DIR / "controller_templates"
DEFAULT_SERIAL_FILE = DATA_DIR / "serial_files" / "serialFile.viptela"

SDWAN_CTRL_NODE_DEFS: frozenset[str] = frozenset({
    "cat-sdwan-manager",
    "cat-sdwan-controller",
    "cat-sdwan-validator",
})
SDWAN_ALL_NODE_DEFS: tuple[str, ...] = (
    "cat-sdwan-manager",
    "cat-sdwan-controller",
    "cat-sdwan-validator",
    "cat-sdwan-edge",
)


def connect_cml(cml_host: str, cml_user: str, cml_password: str) -> ClientLibrary:
    try:
        cml = ClientLibrary(cml_host, username=cml_user, password=cml_password, ssl_verify=False)
        cml.system_info()
    except httpx.TransportError as e:
        log.error("Cannot reach CML at %s: %s", cml_host, e)
        raise typer.Exit(1)
    except APIError:
        log.error("CML authentication failed. Check --user / --password.")
        raise typer.Exit(1)
    verify_cml_version(cml)
    return cml


def connect_manager(host: str, port: int, username: str, password: str) -> ManagerClient:
    client = ManagerClient(host, port, username, password)
    try:
        client.login()
    except ManagerAPIError as e:
        log.error("Cannot connect to SD-WAN Manager: %s", e)
        raise typer.Exit(1)
    return client


def basic_configuration_path(ip_type: str) -> Path:
    return MANAGER_CONFIGS_DIR / f"basic_configuration_{ip_type}.tar.gz"


_MANAGER_NOTE_RE = re.compile(r"manager_external_ip\s*=\s*(.+):(\d+)")


def sha512_crypt(password: str) -> str:
    from passlib.hash import sha512_crypt as _sha512_crypt  # type: ignore[import-untyped]
    return _sha512_crypt.hash(password)

@dataclass
class Certs:
    cert: str
    key: str
    chain: str


def load_certs() -> Certs:
    names = {"cert": "signCA.pem", "key": "signCA.key", "chain": "chainCA.pem"}
    loaded: dict[str, str] = {}
    for attr, filename in names.items():
        path = CERTS_DIR / filename
        if not path.exists():
            log.error("Certificate file not found: %s", path)
            raise typer.Exit(1)
        loaded[attr] = path.read_text()
    return Certs(**loaded)


def _normalize_version(version: str) -> str:
    def _pad(seg: str) -> str:
        i = 0
        while i < len(seg) and seg[i].isdigit():
            i += 1
        num, suffix = seg[:i], seg[i:]
        return f"{int(num):02d}{suffix}" if num else seg

    return ".".join(_pad(s) for s in version.split("."))


def resolve_image(cml: ClientLibrary, node_type: str, version: str) -> str:
    available = [
        img["id"]
        for img in cml.definitions.image_definitions_for_node_definition(node_type)
    ]
    exact = f"{node_type}-{version}"
    if exact in available:
        return exact
    normalized = _normalize_version(version)
    # CML image IDs sometimes use zero-padded segments (26.01.01); accept un-padded input too
    if normalized != version and f"{node_type}-{normalized}" in available:
        return f"{node_type}-{normalized}"
    if node_type in ("cat-sdwan-controller", "cat-sdwan-validator"):
        parts = version.split(".")
        if len(parts) == 4 and parts[-1] == "1":
            fallback_version = ".".join(parts[:-1])
        else:
            parts[-1] = str(int(parts[-1]) - 1)
            fallback_version = ".".join(parts)
        fallback = f"{node_type}-{fallback_version}"
        if fallback in available:
            log.debug("%s: exact version not found, using %s", node_type, fallback)
            return fallback
    label = node_type.split("-")[2].title()
    versions = [img_id[len(node_type) + 1:] for img_id in available]
    log.error(
        "%s image %s not found in CML. Available: %s",
        label, version, ", ".join(versions) or "none",
    )
    raise typer.Exit(1)


def sign_device_cert(client: ManagerClient, certs: Certs, device_ip: str) -> None:
    try:
        csr = client.generate_csr(device_ip)
        cert = sign_csr(certs.cert, certs.key, csr)
        task_id = client.install_signed_cert(cert)
        client.wait_for_task(task_id)
    except requests.exceptions.RequestException as e:
        raise ManagerAPIError(f"Certificate signing failed for {device_ip}: {e}") from e
    log.info("Certificate installed for %s", device_ip)


def sign_csr(ca_cert_pem: str, ca_key_pem: str, csr_pem: str) -> str:
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem.encode())
    ca_key: CertificateIssuerPrivateKeyTypes = serialization.load_pem_private_key(  # type: ignore[assignment]
        ca_key_pem.encode(), password=None
    )
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2 * 365))
        .sign(ca_key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def verify_cml_version(cml: ClientLibrary) -> None:
    version = cml.check_controller_version()
    if version is None or (version.major, version.minor) < (2, 7):
        log.error("CML 2.7 or later is required.")
        raise typer.Exit(1)


def detect_ip_type(lab: Lab) -> str:
    ctrl = next(
        (n for n in lab.nodes() if n.node_definition == "cat-sdwan-controller"),
        None,
    )
    if ctrl is None:
        return "v4"
    cfg = ctrl.configuration or ""
    has_v4 = "172.16.0." in cfg
    has_v6 = "fc00:172:16::" in cfg
    if has_v4 and has_v6:
        return "dual"
    if has_v6:
        return "v6"
    return "v4"


def find_lab(cml: ClientLibrary, lab_name: str) -> tuple[Lab, str, int]:
    labs = cml.find_labs_by_title(lab_name)
    if not labs:
        log.error("No lab found with name '%s'.", lab_name)
        raise typer.Exit(1)
    if len(labs) > 1:
        log.error("Multiple labs found with name '%s'. Ensure lab names are unique.", lab_name)
        raise typer.Exit(1)
    lab = labs[0]
    if not lab.notes:
        log.error("Lab '%s' has no notes — was it created by this tool?", lab_name)
        raise typer.Exit(1)
    m = _MANAGER_NOTE_RE.search(lab.notes)
    if not m:
        log.error("Cannot find Manager IP in lab notes — was this lab created by this tool?")
        raise typer.Exit(1)
    return lab, m.group(1), int(m.group(2))


def topology_nodes(topology: dict[str, Any]) -> list[Any]:
    return topology.get("nodes", topology.get("lab", {}).get("nodes", []))


def check_serial_file_match(topology: dict[str, Any], serial_file: Path) -> None:
    nodes = topology_nodes(topology)
    backup_uuids = {
        m.group(1)
        for node in nodes
        if node.get("node_definition") == "cat-sdwan-edge"
        for cfg in [node.get("configuration", "")]
        if (m := re.search(r"uuid\s*:\s*([\w-]+)", cfg))
    }
    if not backup_uuids:
        return
    try:
        with gzip.open(serial_file, "rb") as gz:
            with tarfile.open(fileobj=gz) as tar:
                member = tar.extractfile("viptela_serial_file")
                if member is None:
                    return
                data = json.loads(member.read())
    except Exception:
        return
    serial_uuids = {d["chassis"] for d in data.get("chassis_list", []) if "chassis" in d}
    missing = backup_uuids - serial_uuids
    if missing:
        log.error(
            "Serial file mismatch: backup edge UUIDs not in serial file: %s. "
            "Restore requires the same serial file used at deploy time.",
            ", ".join(sorted(missing)),
        )
        raise typer.Exit(1)


def wait_for_edges_onboarded(
    client: ManagerClient,
    uuids: list[str],
    *,
    timeout: int = 600,
    on_progress: Callable[[int, int], None] | None = None,
) -> None:
    pending = set(uuids)
    total = len(uuids)
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        for v in client.get_vedges():
            uuid = v.get("uuid", "")
            if (
                uuid in pending
                and v.get("certInstallStatus") == "Installed"
                and v.get("reachability") == "reachable"
            ):
                pending.discard(uuid)
                log.info("Edge %s onboarded", uuid)
                if on_progress:
                    on_progress(total - len(pending), total)
        if pending:
            time.sleep(10)
    if pending:
        log.error("Timed out waiting for edges to onboard: %s", ", ".join(sorted(pending)))
        raise typer.Exit(1)

VALIDATOR_FQDN = "validator.sdwan.local"

_MANAGER_BOOT_RETRIES = 120
_MANAGER_BOOT_INTERVAL = 30


def extract_org_name(path: Path) -> str:
    try:
        with gzip.open(path, "rb") as gz:
            with tarfile.open(fileobj=gz) as tar:
                try:
                    member = tar.extractfile("viptela_serial_file")
                except KeyError:
                    raise ValueError("viptela_serial_file not found in archive")
                if member is None:
                    raise ValueError("viptela_serial_file not found in archive")
                data = json.loads(member.read())
                if "organization" not in data:
                    raise ValueError("organization field missing from serial file")
                return data["organization"]
    except (OSError, gzip.BadGzipFile, tarfile.TarError, json.JSONDecodeError) as e:
        raise ValueError(str(e)) from e


def wait_for_manager(
    manager_ip: str,
    manager_port: int,
    manager_user: str,
    manager_password: str,
    version: str,
    on_status: Callable[[str], None],
) -> ManagerClient:
    use_diagnostic = int(version.split(".")[0]) >= 26
    client = ManagerClient(manager_ip, manager_port, manager_user, manager_password)
    for _ in range(_MANAGER_BOOT_RETRIES):
        if use_diagnostic:
            boot = _query_boot_diagnostic(manager_ip, manager_port)
            if boot:
                active, total = boot
                on_status(f"SD-WAN Manager booting ({active}/{total} services)...")
        try:
            client.login()
            log.info("SD-WAN Manager login successful")
            return client
        except (ManagerAPIError, requests.exceptions.RequestException):
            pass
        time.sleep(_MANAGER_BOOT_INTERVAL)
    log.error("SD-WAN Manager did not become available within 60 minutes.")
    raise typer.Exit(1)


def _query_boot_diagnostic(ip: str, port: int) -> tuple[int, int] | None:
    try:
        response = requests.get(
            f"https://{ip}:{port}/diagnostic/api/v1/boot",
            verify=False,
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            return data["activeServices"], data["totalServices"]
    except Exception:
        pass
    return None


def configure_manager(
    client: ManagerClient, version: str, org_name: str, ca_chain: str
) -> None:
    if client.get_organization() is None:
        client.settings_organization(org_name)
    client.settings_device(VALIDATOR_FQDN)
    client.settings_vedge_cloud("vmanage")
    client.settings_certificate("enterprise")
    client.settings_enterprise_rootca(ca_chain)
    client.settings_data_stream(enable=True, ip_type="systemIp", server_hostname="systemIp", vpn=0)
    client.settings_cloudx("on")
    log.info("SD-WAN Manager settings configured")

    if int(version.split(".")[0]) >= 26:
        _complete_initial_setup_workflow(client)


def _complete_initial_setup_workflow(client: ManagerClient) -> None:
    existing = client.get_workflows("ux_initial_setup")
    if existing:
        workflow = existing[0]
        if workflow.get("userContext", {}).get("complete"):
            return
        workflow_id = workflow["id"]
    else:
        workflow_id = client.create_workflow(
            "ux_initial_setup",
            {
                "currentStep": 1,
                "complete": False,
                "type": "express",
                "stepsRemaining": 7,
                "version": 1,
                "id": None,
                "navigate": True,
            },
        )
    completed_context: dict[str, Any] = {
        "complete": True,
        "type": "none",
        "currentStep": 0,
        "stepsRemaining": 0,
        "version": 1,
        "id": workflow_id,
        "navigate": True,
    }
    client.update_workflow(workflow_id, "ux_initial_setup", completed_context)
    log.info("Initial setup workflow marked complete")


def collect_control_components(lab: Any) -> list[tuple[str, str]]:
    components: list[tuple[str, str]] = []
    for node in lab.nodes():
        cfg = node.configuration or ""
        if node.node_definition == "cat-sdwan-controller":
            m = re.search(r"<address>((?:172\.16\.0\.1\d+)|(?:fc00:172:16::1\d+))", cfg)
            if m:
                components.append((m.group(1), "vsmart"))
        elif node.node_definition == "cat-sdwan-validator":
            m = re.search(r"<address>((?:172\.16\.0\.2\d+)|(?:fc00:172:16::2\d+))", cfg)
            if m:
                components.append((m.group(1), "vbond"))
    return components


def onboard_control_components(
    client: ManagerClient,
    certs: Certs,
    components: list[tuple[str, str]],
    on_status: Callable[[str], None],
) -> None:
    total = len(components)
    for i, (ip, personality) in enumerate(components, 1):
        on_status(f"Adding control components ({i}/{total})...")
        try:
            client.add_controller(ip, personality, "admin", "admin")
        except ManagerAPIError as e:
            if "already exists" in str(e):
                log.debug("%s (%s) already exists — skipping", personality, ip)
            else:
                raise

    controllers = client.get_controllers()
    on_status("Signing certificates for control components...")
    pending = [
        d.get("deviceIP", "")
        for d in controllers
        if d.get("serialNumber") == "No certificate installed" and d.get("deviceIP")
    ]
    with ThreadPoolExecutor() as pool:
        futures = [pool.submit(sign_device_cert, client, certs, ip) for ip in pending]
        for f in futures:
            f.result()


def trigger_rediscovery(client: ManagerClient) -> None:
    reachable = [
        {"deviceId": d["uuid"], "deviceIP": d["local-system-ip"]}
        for d in client.get_sync_status()
        if d.get("reachability") == "reachable"
    ]
    if reachable:
        client.rediscover_devices(reachable)
        log.info("Network rediscovery triggered for %d devices", len(reachable))


class _TopologyDumper(yaml.SafeDumper):
    pass


def _literal_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        data = "\n".join(line.rstrip() for line in data.splitlines())
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=None)


_TopologyDumper.add_representer(str, _literal_str)


def dump_topology(topology: Any) -> str:
    return yaml.dump(topology, Dumper=_TopologyDumper, allow_unicode=True, default_flow_style=False)


def run_sastre_task(
    manager_ip: str,
    manager_port: int,
    manager_user: str,
    manager_password: str,
    task: Any,
    task_args: Any,
) -> None:
    from cisco_sdwan.base.rest_api import Rest  # type: ignore[import-untyped]

    with Rest(
        base_url=f"https://{manager_ip}:{manager_port}",
        username=manager_user,
        password=manager_password,
    ) as api:
        output = task.runner(task_args, api)
        if output:
            for entry in output:
                log.debug("Sastre: %s", entry)

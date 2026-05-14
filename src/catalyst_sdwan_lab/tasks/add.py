from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import paramiko
import typer
from jinja2 import Environment, FileSystemLoader
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn
from virl2_client import ClientLibrary
from virl2_client.exceptions import InterfaceNotFound
from virl2_client.models.interface import Interface
from virl2_client.models.lab import Lab
from virl2_client.models.node import Node

from catalyst_sdwan_lab.manager_client import ManagerAPIError, ManagerClient

from .deploy import (
    _VALIDATOR_FQDN,
    _attach_controller_template,
    _import_controller_templates,
)
from .utils import (
    CML_DEPLOY_TEMPLATES_DIR,
    connect_cml,
    console,
    load_certs,
    resolve_image,
    sign_device_cert,
)

log = logging.getLogger(__name__)

_MANAGER_NOTE_RE = re.compile(r"manager_external_ip\s*=\s*(.+):(\d+)")
_CTRL_NUM_RE = re.compile(r"^Controller(\d+)$")
_VLDTR_NUM_RE = re.compile(r"^Validator(\d+)$")
_IP_HOST_RE = re.compile(r"^ip host vrf (\S+) validator\.sdwan\.local (.+)$")

_CSR_POLL_INTERVAL = 15
_CSR_POLL_TIMEOUT = 600
_BOOT_TIMEOUT = 600
_BOOT_INTERVAL = 10
_GATEWAY_VRF_NAMES = ("inet", "mpls", "vpn0")


def run_controller(
    cml_host: str,
    cml_user: str,
    cml_password: str,
    lab_name: str,
    version: str,
    manager_user: str,
    manager_password: str,
    count: int,
) -> None:
    certs = load_certs()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Connecting to CML...")
        cml = connect_cml(cml_host, cml_user, cml_password)
        progress.update(task, description="Checking lab and images...")
        lab, manager_ip, manager_port = _find_lab(cml, lab_name)
        ip_type = _detect_ip_type(lab)
        image_id = resolve_image(cml, "cat-sdwan-controller", version)

        progress.update(task, description="Connecting to SD-WAN Manager...")
        client = ManagerClient(manager_ip, manager_port, manager_user, manager_password)
        client.login()
        try:
            org_name = client.get_organization() or ""
            template_id = _import_controller_templates(client, ip_type)

            nodes: list[Node] = []
            controller_ips: list[str] = []
            for i in range(count):
                controller_num = _next_controller_num(lab)
                progress.update(
                    task,
                    description=f"Adding Controller{controller_num} to CML ({i + 1}/{count})...",
                )
                cloud_init = _render_cloud_init(
                    org_name=org_name,
                    root_ca=certs.chain,
                    controller_num=controller_num,
                    ip_type=ip_type,
                )
                node = _add_node(
                    lab, f"Controller{controller_num}", image_id, cloud_init,
                )
                node.start()
                nodes.append(node)
                controller_ips.append(_controller_ip(controller_num, ip_type))

            for i, (node, ip) in enumerate(zip(nodes, controller_ips), 1):
                progress.update(
                    task,
                    description=f"Waiting for controller to boot ({i}/{count})...",
                )
                node.wait_until_converged()
                progress.update(
                    task,
                    description=f"Waiting for controller to be reachable ({i}/{count})...",
                )
                _add_controller_retrying(client, ip, timeout=_BOOT_TIMEOUT)

            progress.update(task, description="Waiting for controller CSRs...")
            _wait_for_csrs(client, controller_ips, timeout=_CSR_POLL_TIMEOUT)

            progress.update(task, description="Signing controller certificates...")
            with ThreadPoolExecutor() as pool:
                futures = [
                    pool.submit(sign_device_cert, client, certs, ip) for ip in controller_ips
                ]
                for f in futures:
                    f.result()

            system_ips = {
                f"100.0.0.{ip.split('.')[-1] if '.' in ip else ip.split(':')[-1]}"
                for ip in controller_ips
            }
            progress.update(task, description="Waiting for controllers to reconnect...")
            _wait_for_controllers_ready(client, system_ips, timeout=_CSR_POLL_TIMEOUT)

            progress.update(task, description="Attaching controller template...")
            _attach_controller_template(client, ip_type, template_id, system_ips)
        finally:
            client.logout()

    label = (
        f"Controller{controller_ips[0].split('.')[-1]}" if count == 1 else f"{count} controllers"
    )
    console.print(f"[green]Added.[/green] {label} added to lab '{escape(lab_name)}'.")


def _find_lab(cml: ClientLibrary, lab_name: str) -> tuple[Lab, str, int]:
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


def _next_controller_num(lab: Lab) -> str:
    nums = [
        int(m.group(1))
        for node in lab.nodes()
        if (m := _CTRL_NUM_RE.match(node.label))
    ]
    return f"{(max(nums) + 1) if nums else 1:02d}"


def _detect_ip_type(lab: Lab) -> str:
    ref = next(
        (n for n in lab.nodes() if _CTRL_NUM_RE.match(n.label) or _VLDTR_NUM_RE.match(n.label)),
        None,
    )
    if ref is None:
        return "v4"
    cfg = ref.configuration or ""
    has_v4 = "172.16.0." in cfg
    has_v6 = "fc00:172:16::" in cfg
    if has_v4 and has_v6:
        return "dual"
    if has_v6:
        return "v6"
    return "v4"


def _controller_ip(controller_num: str, ip_type: str) -> str:
    if ip_type == "v6":
        return f"fc00:172:16::1{controller_num}"
    return f"172.16.0.1{controller_num}"


def _render_cloud_init(org_name: str, root_ca: str, controller_num: str, ip_type: str) -> str:
    env = Environment(loader=FileSystemLoader(str(CML_DEPLOY_TEMPLATES_DIR)), trim_blocks=True)
    return env.get_template("controller-cloud-init.j2").render(
        root_ca=root_ca,
        org_name=org_name,
        validator_fqdn=_VALIDATOR_FQDN,
        controller_num=controller_num,
        ip_type=ip_type,
    )


_SDWAN_NODE_DEFS = {"cat-sdwan-manager", "cat-sdwan-controller", "cat-sdwan-validator"}


def _add_node(lab: Lab, label: str, image_id: str, configuration: str) -> Node:
    sdwan_nodes = [n for n in lab.nodes() if n.node_definition in _SDWAN_NODE_DEFS]
    x = max((n.x for n in sdwan_nodes), default=0) + 120
    y = max((n.y for n in sdwan_nodes), default=0)
    node = lab.create_node(
        label=label,
        node_definition="cat-sdwan-controller",
        image_definition=image_id,
        configuration=configuration,
        x=x,
        y=y,
        populate_interfaces=True,
        wait=False,
    )
    vpn0 = next((n for n in lab.nodes() if n.label == "VPN0"), None)
    if vpn0 is None:
        log.error("VPN0 switch not found in lab.")
        raise typer.Exit(1)
    eth1 = _sync_until_interface(lab, node, "eth1")
    free = vpn0.next_available_interface()
    if free is None:
        log.error("VPN0 switch has no free ports.")
        raise typer.Exit(1)
    lab.create_link(eth1, free, wait=False)
    return node


def _sync_until_interface(
    lab: Lab, node: Node, label: str, *, timeout: int = 30
) -> Interface:
    deadline = time.time() + timeout
    while True:
        lab.sync()
        try:
            return node.get_interface_by_label(label)
        except InterfaceNotFound:
            if time.time() >= deadline:
                log.error("Interface %s not available on %s after %ds.", label, node.label, timeout)
                raise typer.Exit(1)
            time.sleep(2)


def _add_controller_retrying(client: ManagerClient, ip: str, *, timeout: int) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            client.add_controller(ip, "vsmart", "admin", "admin")
            return
        except ManagerAPIError:
            remaining = deadline - time.time()
            if remaining <= 0:
                log.error("Timed out waiting for controller %s to become reachable.", ip)
                raise typer.Exit(1)
            time.sleep(min(_BOOT_INTERVAL, remaining))


def _wait_for_csrs(client: ManagerClient, controller_ips: list[str], *, timeout: int) -> None:
    pending = set(controller_ips)
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        for d in client.get_controllers():
            ip = d.get("deviceIP", "")
            if ip in pending and d.get("serialNumber") == "No certificate installed":
                pending.discard(ip)
        if pending:
            time.sleep(_CSR_POLL_INTERVAL)
    if pending:
        log.error("Timed out waiting for controllers: %s", ", ".join(sorted(pending)))
        raise typer.Exit(1)


def _wait_for_controllers_ready(
    client: ManagerClient, system_ips: set[str], *, timeout: int
) -> None:
    """Poll until each newly added controller appears with its system IP (post-cert reconnect)."""
    pending = set(system_ips)
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        for d in client.get_controllers():
            ip = d.get("deviceIP", "")
            sn = d.get("serialNumber", "")
            if ip in pending and sn and sn != "No certificate installed":
                pending.discard(ip)
        if pending:
            time.sleep(_CSR_POLL_INTERVAL)
    if pending:
        log.error(
            "Timed out waiting for controllers to reconnect: %s", ", ".join(sorted(pending))
        )
        raise typer.Exit(1)


def run_validator(
    cml_host: str,
    cml_user: str,
    cml_password: str,
    lab_name: str,
    version: str,
    manager_user: str,
    manager_password: str,
    count: int,
) -> None:
    certs = load_certs()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Connecting to CML...")
        cml = connect_cml(cml_host, cml_user, cml_password)
        progress.update(task, description="Checking lab and images...")
        lab, manager_ip, manager_port = _find_lab(cml, lab_name)
        ip_type = _detect_ip_type(lab)
        image_id = resolve_image(cml, "cat-sdwan-validator", version)

        progress.update(task, description="Connecting to SD-WAN Manager...")
        client = ManagerClient(manager_ip, manager_port, manager_user, manager_password)
        client.login()
        try:
            org_name = client.get_organization() or ""

            nodes: list[Node] = []
            validator_ips: list[str] = []
            for i in range(count):
                validator_num = _next_validator_num(lab)
                progress.update(
                    task,
                    description=f"Adding Validator{validator_num} to CML ({i + 1}/{count})...",
                )
                cloud_init = _render_validator_cloud_init(
                    org_name=org_name,
                    root_ca=certs.chain,
                    validator_num=validator_num,
                    ip_type=ip_type,
                )
                node = _add_validator_node(
                    lab, f"Validator{validator_num}", image_id, cloud_init,
                )
                node.start()
                nodes.append(node)
                validator_ips.append(_validator_ip(validator_num, ip_type))

            for i, (node, ip) in enumerate(zip(nodes, validator_ips), 1):
                progress.update(
                    task,
                    description=f"Waiting for validator to boot ({i}/{count})...",
                )
                node.wait_until_converged()
                progress.update(
                    task,
                    description=f"Waiting for validator to be reachable ({i}/{count})...",
                )
                _add_validator_retrying(client, ip, timeout=_BOOT_TIMEOUT)

            progress.update(task, description="Waiting for validator CSRs...")
            _wait_for_csrs(client, validator_ips, timeout=_CSR_POLL_TIMEOUT)

            progress.update(task, description="Signing validator certificates...")
            with ThreadPoolExecutor() as pool:
                futures = [
                    pool.submit(sign_device_cert, client, certs, ip) for ip in validator_ips
                ]
                for f in futures:
                    f.result()

            progress.update(task, description="Updating Gateway DNS entries...")
            _update_gateway_dns(cml, lab, lab_name, validator_ips)
        finally:
            client.logout()

    label = (
        f"Validator{validator_ips[0].split('.')[-1]}" if count == 1 else f"{count} validators"
    )
    console.print(f"[green]Added.[/green] {label} added to lab '{escape(lab_name)}'.")


def _next_validator_num(lab: Lab) -> str:
    nums = [
        int(m.group(1))
        for node in lab.nodes()
        if (m := _VLDTR_NUM_RE.match(node.label))
    ]
    return f"{(max(nums) + 1) if nums else 1:02d}"


def _validator_ip(validator_num: str, ip_type: str) -> str:
    if ip_type == "v6":
        return f"fc00:172:16::2{validator_num}"
    return f"172.16.0.2{validator_num}"


def _render_validator_cloud_init(
    org_name: str, root_ca: str, validator_num: str, ip_type: str
) -> str:
    env = Environment(loader=FileSystemLoader(str(CML_DEPLOY_TEMPLATES_DIR)), trim_blocks=True)
    return env.get_template("validator-cloud-init.j2").render(
        root_ca=root_ca,
        org_name=org_name,
        validator_num=validator_num,
        ip_type=ip_type,
    )


def _add_validator_node(lab: Lab, label: str, image_id: str, configuration: str) -> Node:
    sdwan_nodes = [n for n in lab.nodes() if n.node_definition in _SDWAN_NODE_DEFS]
    x = max((n.x for n in sdwan_nodes), default=0) + 120
    y = max((n.y for n in sdwan_nodes), default=0)
    node = lab.create_node(
        label=label,
        node_definition="cat-sdwan-validator",
        image_definition=image_id,
        configuration=configuration,
        x=x,
        y=y,
        populate_interfaces=True,
        wait=False,
    )
    vpn0 = next((n for n in lab.nodes() if n.label == "VPN0"), None)
    if vpn0 is None:
        log.error("VPN0 switch not found in lab.")
        raise typer.Exit(1)
    ge0_0 = _sync_until_interface(lab, node, "ge0/0")
    free = vpn0.next_available_interface()
    if free is None:
        log.error("VPN0 switch has no free ports.")
        raise typer.Exit(1)
    lab.create_link(ge0_0, free, wait=False)
    return node


def _add_validator_retrying(client: ManagerClient, ip: str, *, timeout: int) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            client.add_controller(ip, "vbond", "admin", "admin")
            return
        except ManagerAPIError:
            remaining = deadline - time.time()
            if remaining <= 0:
                log.error("Timed out waiting for validator %s to become reachable.", ip)
                raise typer.Exit(1)
            time.sleep(min(_BOOT_INTERVAL, remaining))


def _update_gateway_dns(
    cml: ClientLibrary, lab: Lab, lab_name: str, new_ips: list[str]
) -> None:
    gateway = next((n for n in lab.nodes() if n.label == "Gateway"), None)
    if gateway is None:
        log.warning("Gateway node not found in lab; skipping DNS update.")
        return

    cml_host = str(cml._session.base_url.host)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(cml_host, username=cml.username, password=cml.password, timeout=15)
    ch = ssh.invoke_shell()
    ch.settimeout(5.0)
    try:
        time.sleep(1)
        _gw_drain(ch)
        ch.send(f"open /{lab_name}/Gateway/0\n".encode())
        _gw_recv(ch, ">", timeout=15)
        ch.send(b"enable\r\n")
        out = _gw_recv(ch, "#", timeout=10)
        if "Password" in out:
            ch.send(b"cisco\r\n")
            _gw_recv(ch, "#", timeout=5)
        ch.send(b"terminal length 0\r\n")
        _gw_recv(ch, "#")

        ch.send(b"show run | include ip host\r\n")
        out = _gw_recv(ch, "#", timeout=10)

        vrf_ips: dict[str, list[str]] = {vrf: [] for vrf in _GATEWAY_VRF_NAMES}
        for line in out.splitlines():
            m = _IP_HOST_RE.match(line.strip())
            if m and m.group(1) in vrf_ips:
                vrf_ips[m.group(1)] = m.group(2).split()

        for ip in new_ips:
            for vrf in _GATEWAY_VRF_NAMES:
                if ip not in vrf_ips[vrf]:
                    vrf_ips[vrf].append(ip)

        ch.send(b"configure terminal\r\n")
        _gw_recv(ch, "(config)#")
        for vrf, ips in vrf_ips.items():
            ch.send(f"no ip host vrf {vrf} validator.sdwan.local\r\n".encode())
            _gw_recv(ch, "(config)#")
            ch.send(f"ip host vrf {vrf} validator.sdwan.local {' '.join(ips)}\r\n".encode())
            _gw_recv(ch, "(config)#")
        ch.send(b"end\r\n")
        _gw_recv(ch, "#")
        ch.send(b"write memory\r\n")
        _gw_recv(ch, "#", timeout=15)
        log.info("Gateway DNS updated for validators: %s", ", ".join(new_ips))
    finally:
        ch.close()
        ssh.close()


def _gw_recv(ch: paramiko.Channel, prompt: str, timeout: float = 10.0) -> str:
    buf = ""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ch.recv_ready():
            buf += ch.recv(4096).decode("utf-8", errors="replace")
            if prompt in buf:
                break
        else:
            time.sleep(0.1)
    return buf


def _gw_drain(ch: paramiko.Channel) -> None:
    time.sleep(0.2)
    while ch.recv_ready():
        ch.recv(4096)

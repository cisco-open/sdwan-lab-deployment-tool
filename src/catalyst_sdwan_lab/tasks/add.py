import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

import typer
from jinja2 import Environment, FileSystemLoader
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn
from virl2_client.exceptions import InterfaceNotFound
from virl2_client.models.interface import Interface
from virl2_client.models.lab import Lab
from virl2_client.models.node import Node

from catalyst_sdwan_lab.manager_client import ManagerAPIError, ManagerClient
from catalyst_sdwan_lab.ssh_client import cml_shell, ssh_drain, ssh_recv

from .deploy import (
    _attach_controller_template,
    _import_controller_templates,
)
from .utils import (
    CML_DEPLOY_TEMPLATES_DIR,
    SDWAN_CTRL_NODE_DEFS,
    VALIDATOR_FQDN,
    connect_cml,
    connect_manager,
    console,
    detect_ip_type,
    find_lab,
    load_certs,
    resolve_image,
    sign_device_cert,
    trigger_rediscovery,
    wait_for_edges_onboarded,
)

log = logging.getLogger(__name__)

_CLOUD_INIT_ENV = Environment(
    loader=FileSystemLoader(str(CML_DEPLOY_TEMPLATES_DIR)), trim_blocks=True
)

_CTRL_NUM_RE = re.compile(r"^Controller(\d+)$")
_VLDTR_NUM_RE = re.compile(r"^Validator(\d+)$")
_EDGE_NUM_RE = re.compile(r"^Edge(\d+)$")
_SDROUTING_NUM_RE = re.compile(r"^SD-Edge(\d+)$")
_IP_HOST_RE = re.compile(r"^ip host vrf (\S+) validator\.sdwan\.local (.+)$")

_CSR_POLL_INTERVAL = 15
_CSR_POLL_TIMEOUT = 600
_BOOT_TIMEOUT = 600
_BOOT_INTERVAL = 10
_GATEWAY_VRF_NAMES = ("inet", "mpls", "vpn0")


def run_control_component(
    cml_host: str,
    cml_user: str,
    cml_password: str,
    lab_name: str,
    version: str,
    manager_user: str,
    manager_password: str,
    count: int,
    device_type: Literal["controller", "validator"],
    cpus: int | None = None,
    ram: int | None = None,
) -> None:
    is_ctrl = device_type == "controller"
    label_prefix = "Controller" if is_ctrl else "Validator"
    node_def = "cat-sdwan-controller" if is_ctrl else "cat-sdwan-validator"
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
        lab, manager_ip, manager_port = find_lab(cml, lab_name)
        ip_type = detect_ip_type(lab)
        image_id = resolve_image(cml, node_def, version)

        progress.update(task, description="Connecting to SD-WAN Manager...")
        client = connect_manager(manager_ip, manager_port, manager_user, manager_password)
        try:
            org_name = client.get_organization() or ""
            template_id = _import_controller_templates(client, ip_type) if is_ctrl else None

            ip_offset = "1" if is_ctrl else "2"
            num_re = _CTRL_NUM_RE if is_ctrl else _VLDTR_NUM_RE
            iface = "eth1" if is_ctrl else "ge0/0"
            personality = "vsmart" if is_ctrl else "vbond"

            nodes: list[Node] = []
            device_ips: list[str] = []
            for i in range(count):
                num = _next_device_num(lab, num_re)
                progress.update(
                    task,
                    description=f"Adding {label_prefix}{num} to CML ({i + 1}/{count})...",
                )
                extra = (
                    {"controller_num": num, "validator_fqdn": VALIDATOR_FQDN}
                    if is_ctrl
                    else {"validator_num": num}
                )
                cloud_init = _render_device_cloud_init(
                    f"{device_type}-cloud-init.j2",
                    org_name=org_name,
                    root_ca=certs.chain,
                    ip_type=ip_type,
                    **extra,
                )
                node = _add_sdwan_node(
                    lab, f"{label_prefix}{num}", node_def, image_id, cloud_init, iface, cpus, ram,
                )
                node.start()
                nodes.append(node)
                device_ips.append(
                    f"fc00:172:16::{ip_offset}{num}"
                    if ip_type == "v6"
                    else f"172.16.0.{ip_offset}{num}"
                )

            for i, (node, ip) in enumerate(zip(nodes, device_ips), 1):
                progress.update(task, description=f"Waiting for {device_type}s to boot...")
                node.wait_until_converged()
                progress.update(
                    task,
                    description=f"Waiting for {device_type} to be reachable ({i}/{count})...",
                )
                _add_to_manager_retrying(client, ip, personality, timeout=_BOOT_TIMEOUT)

            progress.update(task, description=f"Waiting for {device_type} CSRs...")
            _wait_for_csrs(client, device_ips, timeout=_CSR_POLL_TIMEOUT)

            progress.update(task, description=f"Signing {device_type} certificates...")
            with ThreadPoolExecutor() as pool:
                futures = [
                    pool.submit(sign_device_cert, client, certs, ip) for ip in device_ips
                ]
                for f in futures:
                    f.result()

            if is_ctrl:
                system_ips = {
                    f"100.0.0.{ip.split('.')[-1] if '.' in ip else ip.split(':')[-1]}"
                    for ip in device_ips
                }
                progress.update(task, description="Waiting for controllers to reconnect...")
                _wait_for_controllers_ready(client, system_ips, timeout=_CSR_POLL_TIMEOUT)
                progress.update(task, description="Attaching controller template...")
                assert template_id is not None
                _attach_controller_template(client, ip_type, template_id, system_ips)
            else:
                progress.update(task, description="Updating Gateway DNS entries...")
                _update_gateway_dns(
                    cml_host, cml_user, cml_password, lab, lab_name, device_ips,
                )
            progress.update(task, description="Triggering network rediscovery...")
            trigger_rediscovery(client)
        except ManagerAPIError as e:
            log.error("%s", e)
            raise typer.Exit(1)
        finally:
            client.logout()

    label = (
        f"{label_prefix}{device_ips[0].split('.')[-1]}"
        if count == 1
        else f"{count} {device_type}s"
    )
    console.print(f"[green]Added.[/green] {label} added to lab '{escape(lab_name)}'.")


def run_edge(
    cml_host: str,
    cml_user: str,
    cml_password: str,
    lab_name: str,
    version: str,
    manager_user: str,
    manager_password: str,
    count: int,
    cpus: int | None = None,
    ram: int | None = None,
) -> None:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Connecting to CML...")
        cml = connect_cml(cml_host, cml_user, cml_password)
        progress.update(task, description="Checking lab and images...")
        lab, manager_ip, manager_port = find_lab(cml, lab_name)
        ip_type = detect_ip_type(lab)
        image_id = resolve_image(cml, "cat-sdwan-edge", version)

        progress.update(task, description="Connecting to SD-WAN Manager...")
        client = connect_manager(manager_ip, manager_port, manager_user, manager_password)
        try:
            progress.update(task, description="Checking available edge devices...")
            vedges = client.get_vedges()
            free_uuids = [
                v["uuid"]
                for v in vedges
                if v.get("deviceModel") == "vedge-C8000V" and v.get("certInstallStatus") is None
            ]
            if count > len(free_uuids):
                log.error(
                    "Not enough free C8000V UUIDs: need %d, have %d.", count, len(free_uuids)
                )
                raise typer.Exit(1)

            progress.update(task, description="Looking up edge config group...")
            config_group_id = _get_config_group_id(client, "edge_basic")

            start = _next_system_ip_num(lab, vedges)
            nums = [f"{start + i:02d}" for i in range(count)]
            uuids = free_uuids[:count]

            devices_vars: list[dict[str, Any]] = []
            for num, uuid in zip(nums, uuids):
                n = int(num)
                variables: list[dict[str, Any]] = [
                    {"name": "system_ip", "value": f"10.0.0.{n}"},
                    {"name": "host_name", "value": f"Edge{num}"},
                    {"name": "site_id", "value": n},
                    {"name": "pseudo_commit_timer", "value": 300},
                    {"name": "ipv6_strict_control", "value": False},
                    {"name": "aaa_password", "value": "admin"},
                ]
                if ip_type in ("v4", "dual"):
                    variables += [
                        {"name": "vpn0_gi1_inet_ip", "value": f"172.16.1.{n}"},
                        {"name": "vpn0_gi1_inet_mask", "value": "255.255.255.0"},
                        {"name": "vpn0_gi2_mpls_ip", "value": f"172.16.2.{n}"},
                        {"name": "vpn0_gi2_mpls_mask", "value": "255.255.255.0"},
                        {"name": "vpn1_gi3_lan_ip", "value": f"192.168.{n}.1"},
                        {"name": "vpn1_gi3_lan_mask", "value": "255.255.255.0"},
                        {"name": "vpn1_gi3_dhcp_network", "value": f"192.168.{n}.0"},
                        {"name": "vpn1_gi3_dhcp_address_exclude", "value": [f"192.168.{n}.1"]},
                        {"name": "vpn1_gi3_dhcp_default_gateway", "value": f"192.168.{n}.1"},
                    ]
                if ip_type in ("v6", "dual"):
                    variables += [
                        {"name": "vpn0_gi1_inet_ipv6", "value": f"fc00:172:16:1::{n}/64"},
                        {"name": "vpn0_gi2_mpls_ipv6", "value": f"fc00:172:16:2::{n}/64"},
                        {"name": "vpn1_gi3_lan_ipv6", "value": f"fc00:192:168:{n}::1/64"},
                    ]
                devices_vars.append({"device-id": uuid, "variables": variables})

            progress.update(task, description="Associating config group...")
            client.associate_config_group(config_group_id, uuids)
            progress.update(task, description="Setting device variables...")
            client.set_config_group_variables(config_group_id, devices_vars)
            progress.update(task, description="Deploying config group...")
            task_id = client.deploy_config_group(config_group_id, uuids)
            client.wait_for_task(task_id)

            progress.update(task, description="Fetching bootstrap configs...")
            bootstrap_configs = {uuid: client.get_bootstrap_config(uuid) for uuid in uuids}

            nodes: list[Node] = []
            for i, (num, uuid) in enumerate(zip(nums, uuids), 1):
                progress.update(
                    task, description=f"Adding Edge{num} to CML ({i}/{count})..."
                )
                node = _add_wan_edge_node(
                    lab, f"Edge{num}", image_id, bootstrap_configs[uuid], True, cpus, ram,
                )
                node.start()
                nodes.append(node)

            for node in nodes:
                progress.update(
                    task, description=f"Waiting for {'edges' if count > 1 else 'edge'} to boot..."
                )
                node.wait_until_converged()

            progress.update(task, description=f"Waiting for edges to onboard (0/{count})...")
            wait_for_edges_onboarded(
                client,
                uuids,
                timeout=_BOOT_TIMEOUT,
                on_progress=lambda done, total: progress.update(
                    task, description=f"Waiting for edges to onboard ({done}/{total})..."
                ),
            )
            progress.update(task, description="Triggering network rediscovery...")
            trigger_rediscovery(client)
        except ManagerAPIError as e:
            log.error("%s", e)
            raise typer.Exit(1)
        finally:
            client.logout()
    label = f"Edge{nums[0]}" if count == 1 else f"{count} edges"
    console.print(f"[green]Added.[/green] {label} added to lab '{escape(lab_name)}'.")


def run_sdrouting(
    cml_host: str,
    cml_user: str,
    cml_password: str,
    lab_name: str,
    version: str,
    manager_user: str,
    manager_password: str,
    count: int,
    cpus: int | None = None,
    ram: int | None = None,
) -> None:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Connecting to CML...")
        cml = connect_cml(cml_host, cml_user, cml_password)
        progress.update(task, description="Checking lab and images...")
        lab, manager_ip, manager_port = find_lab(cml, lab_name)
        ip_type = detect_ip_type(lab)
        image_id = resolve_image(cml, "cat-sdwan-edge", version)

        progress.update(task, description="Connecting to SD-WAN Manager...")
        client = connect_manager(manager_ip, manager_port, manager_user, manager_password)
        try:
            progress.update(task, description="Checking available SD-Routing devices...")
            vedges = client.get_vedges()
            free_uuids = [
                v["uuid"]
                for v in vedges
                if v.get("deviceModel") == "vedge-C8000V-SD-ROUTING"
                and v.get("certInstallStatus") is None
            ]
            if count > len(free_uuids):
                log.error(
                    "Not enough free SD-Routing UUIDs: need %d, have %d.", count, len(free_uuids)
                )
                raise typer.Exit(1)

            start = _next_system_ip_num(lab, vedges)
            nums = [str(start + i) for i in range(count)]
            uuids = free_uuids[:count]

            progress.update(task, description="Looking up SD-Routing config group...")
            config_group_id = _get_config_group_id(client, "sdrouting_basic")

            devices_vars: list[dict[str, Any]] = []
            for num, uuid in zip(nums, uuids):
                n = int(num)
                variables: list[dict[str, Any]] = [
                    {"name": "system_ip", "value": f"10.0.0.{n}"},
                    {"name": "host_name", "value": f"SD-Edge{num}"},
                    {"name": "site_id", "value": n},
                    {"name": "aaap", "value": "admin"},
                ]
                if ip_type in ("v4", "dual"):
                    variables += [
                        {"name": "global_vrf_gi1_inet_ip", "value": f"172.16.1.{n}"},
                        {"name": "global_vrf_gi1_inet_mask", "value": "255.255.255.0"},
                        {"name": "vrf1_gi3_lan_ip", "value": f"192.168.{n}.1"},
                        {"name": "vrf1_gi3_lan_mask", "value": "255.255.255.0"},
                        {"name": "vrf1_gi3_dhcp_mask", "value": "255.255.255.0"},
                        {"name": "vrf1_gi3_dhcp_network", "value": f"192.168.{n}.0"},
                        {"name": "vrf1_gi3_dhcp_address_exclude", "value": [f"192.168.{n}.1"]},
                        {"name": "vrf1_gi3_dhcp_default_gateway", "value": f"192.168.{n}.1"},
                    ]
                if ip_type in ("v6", "dual"):
                    variables += [
                        {"name": "global_vrf_gi1_inet_ipv6", "value": f"fc00:172:16:1::{n}/64"},
                        {"name": "vrf1_gi3_lan_ipv6", "value": f"fc00:192:168:{n}::1/64"},
                    ]
                devices_vars.append({"device-id": uuid, "variables": variables})

            progress.update(task, description="Associating config group...")
            client.associate_config_group(config_group_id, uuids)
            progress.update(task, description="Setting device variables...")
            client.set_config_group_variables(
                config_group_id, devices_vars, solution="sd-routing"
            )
            progress.update(task, description="Deploying config group...")
            task_id = client.deploy_config_group(config_group_id, uuids)
            client.wait_for_task(task_id)

            progress.update(task, description="Fetching bootstrap configs...")
            bootstrap_configs = {
                uuid: client.get_bootstrap_config(uuid, wanif="GigabitEthernet1")
                for uuid in uuids
            }

            nodes: list[Node] = []
            for i, (num, uuid) in enumerate(zip(nums, uuids), 1):
                progress.update(
                    task, description=f"Adding SD-Edge{num} to CML ({i}/{count})..."
                )
                node = _add_wan_edge_node(
                    lab, f"SD-Edge{num}", image_id, bootstrap_configs[uuid], False, cpus, ram,
                )
                node.start()
                nodes.append(node)

            for node in nodes:
                progress.update(task, description="Waiting for SD-Routing edges to boot...")
                node.wait_until_converged()

            progress.update(
                task, description=f"Waiting for SD-Routing edges to onboard (0/{count})..."
            )
            wait_for_edges_onboarded(
                client,
                uuids,
                timeout=_BOOT_TIMEOUT,
                on_progress=lambda done, total: progress.update(
                    task,
                    description=f"Waiting for SD-Routing edges to onboard ({done}/{total})...",
                ),
            )
            progress.update(task, description="Triggering network rediscovery...")
            trigger_rediscovery(client)
        except ManagerAPIError as e:
            log.error("%s", e)
            raise typer.Exit(1)
        finally:
            client.logout()

    label = f"SD-Edge{nums[0]}" if count == 1 else f"{count} SD-Routing edges"
    console.print(f"[green]Added.[/green] {label} added to lab '{escape(lab_name)}'.")


def _next_device_num(lab: Lab, num_re: re.Pattern[str]) -> str:
    nums = [int(m.group(1)) for node in lab.nodes() if (m := num_re.match(node.label))]
    return f"{(max(nums) + 1) if nums else 1:02d}"


def _next_system_ip_num(lab: Lab, vedges: list[dict[str, Any]]) -> int:
    from_manager = max(
        (
            int(sip.split(".")[-1])
            for v in vedges
            if (sip := v.get("system-ip", "")).count(".") == 3
        ),
        default=0,
    )
    from_cml = max(
        (
            int(m.group(1))
            for n in lab.nodes()
            if (m := _EDGE_NUM_RE.match(n.label) or _SDROUTING_NUM_RE.match(n.label))
        ),
        default=0,
    )
    return max(from_manager, from_cml) + 1


def _render_device_cloud_init(
    template_name: str, *, org_name: str, root_ca: str, ip_type: str, **kwargs: str
) -> str:
    return _CLOUD_INIT_ENV.get_template(template_name).render(
        root_ca=root_ca, org_name=org_name, ip_type=ip_type, **kwargs,
    )


def _add_sdwan_node(
    lab: Lab,
    label: str,
    node_def: str,
    image_id: str,
    configuration: str,
    iface: str,
    cpus: int | None = None,
    ram: int | None = None,
) -> Node:
    sdwan_nodes = [n for n in lab.nodes() if n.node_definition in SDWAN_CTRL_NODE_DEFS]
    x = max((n.x for n in sdwan_nodes), default=0) + 120
    y = max((n.y for n in sdwan_nodes), default=0)
    node = lab.create_node(
        label=label,
        node_definition=node_def,
        image_definition=image_id,
        configuration=configuration,
        x=x,
        y=y,
        populate_interfaces=True,
        wait=False,
        cpus=cpus,
        ram=ram,
    )
    vpn0 = next((n for n in lab.nodes() if n.label == "VPN0"), None)
    if vpn0 is None:
        log.error("VPN0 switch not found in lab.")
        raise typer.Exit(1)
    port = _sync_until_interface(lab, node, iface)
    free = vpn0.next_available_interface()
    if free is None:
        log.error("VPN0 switch has no free ports.")
        raise typer.Exit(1)
    lab.create_link(port, free, wait=False)
    return node


def _add_wan_edge_node(
    lab: Lab,
    label: str,
    image_id: str,
    configuration: str,
    connect_mpls: bool,
    cpus: int | None = None,
    ram: int | None = None,
) -> Node:
    x = max((n.x for n in lab.nodes() if n.y == 400), default=-400) + 120
    node = lab.create_node(
        label=label,
        node_definition="cat-sdwan-edge",
        image_definition=image_id,
        configuration=configuration,
        x=x,
        y=400,
        populate_interfaces=True,
        wait=False,
        cpus=cpus,
        ram=ram,
    )
    inet = next((n for n in lab.nodes() if n.label == "INET"), None)
    if inet is None:
        log.error("INET switch not found in lab.")
        raise typer.Exit(1)
    gi1 = _sync_until_interface(lab, node, "GigabitEthernet1")
    inet_free = inet.next_available_interface()
    if inet_free is None:
        log.error("INET switch has no free ports.")
        raise typer.Exit(1)
    lab.create_link(gi1, inet_free, wait=False)
    if connect_mpls:
        mpls = next((n for n in lab.nodes() if n.label == "MPLS"), None)
        if mpls is None:
            log.error("MPLS switch not found in lab.")
            raise typer.Exit(1)
        gi2 = _sync_until_interface(lab, node, "GigabitEthernet2")
        mpls_free = mpls.next_available_interface()
        if mpls_free is None:
            log.error("MPLS switch has no free ports.")
            raise typer.Exit(1)
        lab.create_link(gi2, mpls_free, wait=False)
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
                log.error(
                    "Interface %s not available on %s after %ds.", label, node.label, timeout
                )
                raise typer.Exit(1)
            time.sleep(2)


def _add_to_manager_retrying(
    client: ManagerClient, ip: str, personality: str, *, timeout: int
) -> None:
    deadline = time.time() + timeout
    while True:
        try:
            client.add_controller(ip, personality, "admin", "admin")
            return
        except ManagerAPIError:
            remaining = deadline - time.time()
            if remaining <= 0:
                log.error("Timed out waiting for %s %s to become reachable.", personality, ip)
                raise typer.Exit(1)
            time.sleep(min(_BOOT_INTERVAL, remaining))


def _wait_for_csrs(client: ManagerClient, device_ips: list[str], *, timeout: int) -> None:
    pending = set(device_ips)
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        for d in client.get_controllers():
            ip = d.get("deviceIP", "")
            if ip in pending and d.get("serialNumber") == "No certificate installed":
                pending.discard(ip)
        if pending:
            time.sleep(_CSR_POLL_INTERVAL)
    if pending:
        log.error("Timed out waiting for CSRs: %s", ", ".join(sorted(pending)))
        raise typer.Exit(1)


def _wait_for_controllers_ready(
    client: ManagerClient, system_ips: set[str], *, timeout: int
) -> None:
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


def _update_gateway_dns(
    cml_host: str, cml_user: str, cml_password: str,
    lab: Lab, lab_name: str, new_ips: list[str],
) -> None:
    gateway = next((n for n in lab.nodes() if n.label == "Gateway"), None)
    if gateway is None:
        log.warning("Gateway node not found in lab; skipping DNS update.")
        return

    with cml_shell(cml_host, cml_user, cml_password) as ch:
        time.sleep(1)
        ssh_drain(ch)
        ch.send(f"open /{lab_name}/Gateway/0\n".encode())
        ssh_drain(ch, duration=3)   # consume echo + wait for IOS console to attach
        ch.send(b"\r\n")
        out = ssh_recv(ch, ">", "#", timeout=15)
        if ">" in out and "#" not in out:
            ch.send(b"enable\r\n")
            out = ssh_recv(ch, "#", "Password", timeout=30)
            if "Password" in out and "#" not in out:
                ch.send(b"cisco\r\n")
                ssh_recv(ch, "#", timeout=10)
        ch.send(b"terminal length 0\r\n")
        ssh_recv(ch, "#")

        ch.send(b"show run | include ip host\r\n")
        out = ssh_recv(ch, "#", timeout=10)

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
        ssh_recv(ch, "(config)#")
        for vrf, ips in vrf_ips.items():
            ch.send(f"no ip host vrf {vrf} validator.sdwan.local\r\n".encode())
            ssh_recv(ch, "(config)#")
            ch.send(f"ip host vrf {vrf} validator.sdwan.local {' '.join(ips)}\r\n".encode())
            ssh_recv(ch, "(config)#")
        ch.send(b"end\r\n")
        ssh_recv(ch, "#")
        ch.send(b"write memory\r\n")
        ssh_recv(ch, "#", timeout=15)
        log.info("Gateway DNS updated for validators: %s", ", ".join(new_ips))


def _get_config_group_id(client: ManagerClient, name: str) -> str:
    cg = next((g for g in client.get_config_groups() if g.get("name") == name), None)
    if cg is None:
        log.error("Config group '%s' not found in Manager.", name)
        raise typer.Exit(1)
    return cg["id"]


import datetime
import json
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Literal

import typer
import yaml
from rich.markup import escape

from catalyst_sdwan_lab.manager_client import ManagerAPIError, ManagerClient
from catalyst_sdwan_lab.ssh_client import fix_sdrouting_default_route

from .delete import run as _delete_lab
from .utils import (
    SDWAN_CTRL_NODE_DEFS,
    Certs,
    check_serial_file_match,
    collect_control_components,
    configure_manager,
    connect_cml,
    console,
    dump_topology,
    enroll_cluster_manager,
    ensure_cluster_ip_configured,
    extract_org_name,
    load_certs,
    onboard_control_components,
    resolve_image,
    run_sastre_task,
    sha512_crypt,
    task_progress,
    topology_nodes,
    trigger_rediscovery,
    wait_for_edges_onboarded,
    wait_for_manager,
)

log = logging.getLogger(__name__)

def run(
    cml_host: str,
    cml_user: str,
    cml_password: str,
    lab_name: str,
    manager_user: str,
    manager_password: str,
    manager_ip: str,
    manager_port: int,
    manager_mask: str,
    manager_gateway: str,
    backup: Path,
    serial_file: Path,
    control_version: str | None,
    edge_version: str | None,
    delete_existing: bool,
    retry: bool,
    patty: bool,
    pki: Literal["enterprise", "cisco"] = "enterprise",
    proxy_ip: str = "",
    proxy_port: str = "80",
    no_proxy: str = "",
) -> None:
    if manager_password == "admin":
        log.error("Cannot use default credentials. Update Manager password and try again.")
        raise typer.Exit(1)

    try:
        org_name = extract_org_name(serial_file)
    except ValueError as e:
        log.error("Invalid serial file: %s", e)
        raise typer.Exit(1)

    certs = load_certs()

    with task_progress(console) as update:
        cml = connect_cml(cml_host, cml_user, cml_password)

        update("Loading backup...")
        topology, manager_configs_dir, backup_tmpdir = _load_backup(backup)
        check_serial_file_match(topology, serial_file)

        nodes = topology_nodes(topology)
        mgr = next((n for n in nodes if n.get("node_definition") == "cat-sdwan-manager"), None)
        raw_version = (
            mgr["image_definition"].split("-")[-1] if mgr and mgr.get("image_definition") else ""
        )
        backup_version = raw_version if raw_version and raw_version[0].isdigit() else "20.15"
        version = control_version or backup_version

        if pki == "cisco":
            try:
                parts = (version.split(".")[:3] + ["0", "0"])[:3]
                major, minor, patch = (int(x) for x in parts)
            except ValueError:
                major, minor, patch = 0, 0, 0
            if (major, minor, patch) < (20, 18, 2):
                log.error("Cisco PKI requires Manager version 20.18.2 or later. Got: %s", version)
                raise typer.Exit(1)

        update("Checking images...")
        _check_images(cml, topology, control_version, edge_version)

        if delete_existing:
            update("Removing existing lab...")
            _delete_lab(cml_host, cml_user, cml_password, lab_name, force=True)

        if not retry and not delete_existing:
            if any(lab.title == lab_name for lab in cml.all_labs(show_all=True)):
                log.error(
                    "Lab '%s' already exists. Use --retry to resume, "
                    "--delete-existing to replace, or choose a different name.",
                    lab_name,
                )
                raise typer.Exit(1)

        try:
            if retry:
                update("Locating existing lab...")
                lab = _find_lab_by_manager(cml, manager_ip, manager_port, patty)
            else:
                update("Patching topology...")
                _patch_topology(
                    topology, lab_name, manager_ip, manager_mask, manager_gateway,
                    manager_port, manager_user, manager_password, patty,
                    control_version, edge_version, cml,
                )
                topology_yaml = dump_topology(topology)
                update("Importing lab into CML...")
                lab = cml.import_lab(topology_yaml)

            update("Starting control plane...")
            _start_control_plane(lab)

            update("Waiting for SD-WAN Manager...")
            client = wait_for_manager(
                manager_ip, manager_port, manager_user, manager_password, version,
                on_status=update,
            )
            try:
                update("Configuring SD-WAN Manager...")
                configure_manager(
                    client, version, org_name, certs.chain, pki=pki,
                    on_status=update,
                    proxy_ip=proxy_ip, proxy_port=proxy_port, no_proxy=no_proxy,
                )

                update("Onboarding control components...")
                components = collect_control_components(lab)
                if not components:
                    log.error(
                        "No controller or validator nodes found in topology"
                        " — cannot onboard control plane."
                    )
                    raise typer.Exit(1)
                onboard_control_components(
                    client, certs, components,
                    on_status=update,
                    pki=pki,
                )

                update("Uploading serial file...")
                client.upload_serial_file(serial_file)

                update("Patching Sastre controller UUIDs...")
                _patch_sastre_controller_uuids(manager_configs_dir, client)

                update("Patching config group passwords...")
                _patch_config_group_passwords(manager_configs_dir)

                update("Restoring Manager configuration...")
                _run_sastre_restore(
                    manager_ip, manager_port, manager_user, manager_password, manager_configs_dir
                )

                mrf_path = manager_configs_dir / "mrf.json"
                if mrf_path.exists():
                    update("Restoring network hierarchy...")
                    _restore_mrf(client, json.loads(mrf_path.read_text()))

                client = _restore_cluster(
                    client, lab, certs, pki,
                    manager_ip, manager_port, manager_user, manager_password, version,
                    on_status=update,
                )

                update("Starting edge nodes...")
                edge_uuids = _inject_otps_and_start_edges(
                    lab, client, ca_chain=certs.chain if pki == "enterprise" else ""
                )

                for node in lab.nodes():
                    if node.node_definition != "cat-sdwan-edge":
                        continue
                    if "SD-Routing : true" not in (node.configuration or ""):
                        continue
                    update(f"Checking default route on {node.label}...")
                    node.wait_until_converged()
                    if fix_sdrouting_default_route(
                        cml_host, cml_user, cml_password, lab.title or lab_name, node.label,
                        console=console,
                    ):
                        node.wait_until_converged()

                if edge_uuids:
                    total_edges = len(edge_uuids)
                    update(f"Waiting for edges to onboard... (0/{total_edges})")
                    wait_for_edges_onboarded(
                        client,
                        edge_uuids,
                        on_progress=lambda done, total: update(
                            f"Waiting for edges to onboard... ({done}/{total})"
                        ),
                    )

                update("Triggering network rediscovery...")
                trigger_rediscovery(client)

            except ManagerAPIError as e:
                log.error("%s", e)
                raise typer.Exit(1)
            finally:
                client.logout()

        finally:
            cml.logout()

    console.print(
        f"[green]Restore complete.[/green] Lab '{escape(lab_name)}' "
        f"available at https://{manager_ip}:{manager_port}"
    )


def _find_backup_root(path: Path) -> Path:
    if (path / "topology.yaml").exists():
        return path
    subdirs = [p for p in path.iterdir() if p.is_dir()]
    if len(subdirs) == 1 and (subdirs[0] / "topology.yaml").exists():
        return subdirs[0]
    return path


def _load_backup(backup: Path) -> tuple[dict[str, Any], Path, Any]:
    if backup.suffix == ".zip":
        tmpdir = tempfile.TemporaryDirectory()
        out = Path(tmpdir.name)
        with zipfile.ZipFile(backup) as zf:
            for member in zf.infolist():
                if ".." not in member.filename and not member.filename.startswith("/"):
                    zf.extract(member, out)
        root = _find_backup_root(out)
        topology = yaml.safe_load((root / "topology.yaml").read_text())
        return topology, root / "manager_configs", tmpdir
    root = _find_backup_root(backup.resolve())
    return yaml.safe_load((root / "topology.yaml").read_text()), root / "manager_configs", None


def _check_images(
    cml: Any,
    topology: dict[str, Any],
    control_version: str | None,
    edge_version: str | None,
) -> None:
    nodes = topology_nodes(topology)
    checked: set[str] = set()
    for node in nodes:
        node_def = node.get("node_definition", "")
        if node_def not in SDWAN_CTRL_NODE_DEFS and node_def != "cat-sdwan-edge":
            continue
        is_edge = node_def == "cat-sdwan-edge"
        override = edge_version if is_edge else control_version
        if override:
            version = override
        else:
            img = node.get("image_definition") or ""
            parts = img.split("-")
            version = parts[-1] if parts and parts[-1][:1].isdigit() else None
        if not version:
            continue
        key = f"{node_def}:{version}"
        if key in checked:
            continue
        checked.add(key)
        resolve_image(cml, node_def, version)


def _find_lab_by_manager(cml: Any, manager_ip: str, manager_port: int, patty: bool) -> Any:
    marker = f"{manager_ip}:{manager_port}" if patty else manager_ip
    lab = next(
        (lab for lab in cml.all_labs(show_all=True) if lab.notes and marker in lab.notes), None
    )
    if not lab:
        log.error("Cannot find lab with Manager IP %s:%s in notes.", manager_ip, manager_port)
        raise typer.Exit(1)
    return lab


def _patch_topology(
    topology: dict[str, Any],
    lab_name: str,
    manager_ip: str,
    manager_mask: str,
    manager_gateway: str,
    manager_port: int,
    manager_user: str,
    manager_password: str,
    patty: bool,
    control_version: str | None,
    edge_version: str | None,
    cml: Any,
) -> None:
    lab_section = topology.setdefault("lab", {})
    lab_section["title"] = lab_name
    lab_section["notes"] = (
        f"-- Do not delete this text --\n"
        f"manager_external_ip = {manager_ip}:{manager_port}\n"
        f"-- Do not delete this text --"
    )

    nodes = topology_nodes(topology)
    primary_manager_id = _find_primary_manager_id(topology)
    for node in nodes:
        node_def = node.get("node_definition", "")
        if node_def == "cat-sdwan-manager":
            is_primary = node.get("id") == primary_manager_id
            cfg: str = node.get("configuration", "")
            encrypted = sha512_crypt(manager_password)
            cfg = re.sub(r"<password>(\S+)</password>", f"<password>{encrypted}</password>", cfg)
            now = (
                datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                + "+00:00"
            )
            ts_tag = f"<last-password-change-time>{now}</last-password-change-time>"
            if "<last-password-change-time>" in cfg:
                cfg = re.sub(
                    r"<last-password-change-time>[^<]+</last-password-change-time>", ts_tag, cfg
                )
            else:
                cfg = cfg.replace("</password>", f"</password>\n            {ts_tag}", 1)
            if manager_user != "admin" and f"<name>{manager_user}</name>" not in cfg:
                admin_block = next(
                    (
                        b.group(0)
                        for b in re.finditer(r"[ \t]*<user>[\s\S]*?</user>\n", cfg)
                        if "<name>admin</name>" in b.group(0)
                    ),
                    None,
                )
                if admin_block:
                    new_user_block = admin_block.replace(
                        "<name>admin</name>", f"<name>{manager_user}</name>", 1
                    )
                    if "<group>" not in new_user_block:
                        groups = "".join(
                            f"            <group>{g}</group>\n"
                            for g in ("all", "global", "netadmin")
                        )
                        new_user_block = re.sub(
                            r"\n(\s*)</user>\n$",
                            f"\n{groups}" r"\1</user>\n",
                            new_user_block,
                        )
                    cfg = cfg.replace(admin_block, admin_block + new_user_block, 1)
            if is_primary:
                if m := re.search(
                    r"(<vpn-instance>[\s\S]+?<vpn-id>512</vpn-id>[\s\S]+?<address>)([\d./]+)(</address>)",
                    cfg,
                ):
                    cfg = cfg.replace(
                        m.group(0),
                        f"{m.group(1)}{manager_ip}{manager_mask}{m.group(3)}",
                    )
                if m := re.search(
                    r"(<vpn-instance>[\s\S]+?<vpn-id>512</vpn-id>[\s\S]+?<next-hop>[\s\S]+?<address>)([\d.]+)(</address>)",
                    cfg,
                ):
                    cfg = cfg.replace(m.group(0), f"{m.group(1)}{manager_gateway}{m.group(3)}")
            node_defs = cml.definitions.node_definitions()
            mgr_def = next((nd for nd in node_defs if nd["id"] == node_def), None)
            sim = mgr_def.get("sim", {}) if mgr_def else {}
            disk_driver = sim.get("linux_native", {}).get("disk_driver")
            if disk_driver == "virtio":
                cfg = cfg.replace('"/dev/sdb"', '"/dev/vdb"').replace("[ sdb,", "[ vdb,")
            node["configuration"] = cfg
            if is_primary and patty:
                node.setdefault("tags", [])
                pat_tag = f"pat:{manager_port}:443"
                if pat_tag not in node["tags"]:
                    node["tags"].append(pat_tag)
        if control_version and node_def in SDWAN_CTRL_NODE_DEFS:
            node["image_definition"] = f"{node_def}-{control_version}"
        if edge_version and node_def == "cat-sdwan-edge":
            node["image_definition"] = f"{node_def}-{edge_version}"


def _find_primary_manager_id(topology: Any) -> str | None:
    nodes = topology_nodes(topology)
    ext_ids = {n["id"] for n in nodes if n.get("node_definition") == "external_connector"}
    manager_ids = {n["id"] for n in nodes if n.get("node_definition") == "cat-sdwan-manager"}
    for link in topology.get("links", []):
        n1, n2 = link.get("n1"), link.get("n2")
        i1, i2 = link.get("i1"), link.get("i2")
        if n1 in ext_ids and n2 in manager_ids and i2 == "i0":
            return n2
        if n2 in ext_ids and n1 in manager_ids and i1 == "i0":
            return n1
    return None


def _restore_cluster(
    client: ManagerClient,
    lab: Any,
    certs: Certs,
    pki: Literal["enterprise", "cisco"],
    manager_host: str,
    manager_port: int,
    manager_user: str,
    manager_password: str,
    version: str,
    on_status: Callable[[str], None],
) -> ManagerClient:
    manager_nodes = [n for n in lab.nodes() if n.node_definition == "cat-sdwan-manager"]
    if len(manager_nodes) <= 1:
        return client

    secondary_managers = [n for n in manager_nodes if not _manager_connected_to_external(n)]
    if not secondary_managers:
        return client

    on_status("Checking cluster IP configuration...")
    ensure_cluster_ip_configured(
        client, manager_user, manager_password, on_status=on_status,
    )
    client = wait_for_manager(manager_host, manager_port, manager_user, manager_password, version)

    on_status("Waiting for secondary managers to boot...")
    for node in secondary_managers:
        node.wait_until_converged()

    for node in secondary_managers:
        m = re.search(r"<system-ip>100\.0\.0\.(\d+)</system-ip>", node.configuration or "")
        if not m:
            log.error("Cannot determine cluster IP for %s — skipping.", node.label)
            continue
        num = m.group(1)
        cluster_ip = f"172.16.254.{num}"

        entries = client.get_cluster_management_list()
        enrolled = any(
            n.get("configJson", {}).get("deviceIP") == cluster_ip
            for entry in entries
            for n in entry.get("data", [])
        )
        if enrolled:
            log.info("%s already enrolled in cluster, skipping.", node.label)
            continue

        persona_m = re.search(r'"persona":"([^"]+)"', node.configuration or "")
        persona = persona_m.group(1) if persona_m else "COMPUTE_AND_DATA"

        on_status(f"Adding {node.label} to cluster...")
        client = enroll_cluster_manager(
            client, cluster_ip, persona,
            manager_host, manager_port, manager_user, manager_password, version,
            certs, pki, node.label, on_status=on_status,
        )

    return client


def _manager_connected_to_external(node: Any) -> bool:
    try:
        eth0 = node.get_interface_by_label("eth0")
    except Exception:
        return False
    if not eth0.connected or eth0.link is None:
        return False
    link = eth0.link
    other = link.interface_b if link.interface_a.node == node else link.interface_a
    return other.node.node_definition == "external_connector"


def _start_control_plane(lab: Any) -> None:
    for node in lab.nodes():
        if node.node_definition == "cat-sdwan-edge":
            continue
        node.start()
        log.info("Started %s", node.label)


def _patch_config_group_passwords(manager_configs_dir: Path) -> None:
    values_dir = manager_configs_dir / "config_groups" / "values"
    if not values_dir.exists():
        return
    for path in values_dir.glob("*.json"):
        data = json.loads(path.read_text())
        changed = False
        for device in data.get("devices", []):
            for var in device.get("variables", []):
                if var.get("value") == "$cRYPT_CLUSTER1":
                    var["value"] = "admin"
                    changed = True
        if changed:
            path.write_text(json.dumps(data, indent=2))
            log.info("Patched encrypted passwords in %s", path.name)


def _patch_sastre_controller_uuids(manager_configs_dir: Path, client: ManagerClient) -> None:
    attached_dir = manager_configs_dir / "device_templates" / "attached"
    values_dir = manager_configs_dir / "device_templates" / "values"
    if not attached_dir.exists():
        return

    controllers = client.get_controllers()
    sys_ip_to_uuid = {
        c["deviceIP"]: c["uuid"]
        for c in controllers
        if c.get("personality") == "vsmart" and c.get("deviceIP") and c.get("uuid")
    }
    if not sys_ip_to_uuid:
        return

    for path in attached_dir.glob("*.json"):
        data = json.loads(path.read_text())
        if not data or data[0].get("personality") != "vsmart":
            continue
        changed = False
        for device in data:
            sys_ip = device.get("deviceIP")
            new_uuid = sys_ip_to_uuid.get(sys_ip)
            if new_uuid and device.get("uuid") != new_uuid:
                device["uuid"] = new_uuid
                changed = True
        if changed:
            path.write_text(json.dumps(data, indent=2))
            values_path = values_dir / path.name
            if values_path.exists():
                vdata = json.loads(values_path.read_text())
                for device in vdata.get("data", []):
                    sys_ip = device.get("csv-deviceIP")
                    new_uuid = sys_ip_to_uuid.get(sys_ip)
                    if new_uuid:
                        device["csv-deviceId"] = new_uuid
                values_path.write_text(json.dumps(vdata, indent=2))


def _run_sastre_restore(
    manager_ip: str, manager_port: int, manager_user: str, manager_password: str, workdir: Path
) -> None:
    from cisco_sdwan.tasks.implementation import (  # type: ignore[import-untyped]
        RestoreArgs,
        TaskRestore,
    )

    run_sastre_task(
        manager_ip, manager_port, manager_user, manager_password,
        TaskRestore(),
        RestoreArgs(workdir=str(workdir), attach=True, tag="all"),
    )
    log.info("Sastre restore completed from %s", workdir)


_MRF_SERVER_FIELDS = frozenset({"uuid", "id", "directChildCount", "hierarchyPath"})


def _restore_mrf(client: ManagerClient, mrf_data: list[dict[str, Any]]) -> None:
    regions = [e for e in mrf_data if e.get("data", {}).get("label") == "REGION"]
    subregions = [e for e in mrf_data if e.get("data", {}).get("label") == "SUB_REGION"]
    if not regions:
        return

    hierarchy = client.get_network_hierarchy()
    existing_names = {e["name"] for e in hierarchy}
    global_id = next(
        (e["id"] for e in hierarchy if e.get("data", {}).get("label") == "GLOBAL"), None
    )
    if not global_id:
        log.warning("Cannot find GLOBAL node in network hierarchy — skipping MRF restore")
        return

    # Enable MRF if no regions exist yet
    if not any(e.get("data", {}).get("label") == "REGION" for e in hierarchy):
        try:
            client.enable_mrf(global_id)
            hierarchy = client.get_network_hierarchy()
            global_id = next(
                e["id"] for e in hierarchy if e.get("data", {}).get("label") == "GLOBAL"
            )
        except ManagerAPIError:
            log.warning("Could not enable MRF inter-region routing — continuing")

    old_to_new: dict[str, str] = {}
    for entry in regions:
        if entry["name"] in existing_names:
            log.debug("MRF region '%s' already exists — skipping", entry["name"])
            continue
        payload = {k: v for k, v in entry.items() if k not in _MRF_SERVER_FIELDS}
        payload["data"]["parentUuid"] = global_id
        result = client.create_network_hierarchy_entry(payload)
        new_uuid = result.get("Network Hierarchy UUID") or result.get("id", "")
        old_to_new[entry["uuid"]] = new_uuid

    for entry in subregions:
        if entry["name"] in existing_names:
            continue
        payload = {k: v for k, v in entry.items() if k not in _MRF_SERVER_FIELDS}
        old_parent = entry["data"].get("parentUuid", "")
        payload["data"]["parentUuid"] = old_to_new.get(old_parent, old_parent)
        result = client.create_network_hierarchy_entry(payload)
        new_uuid = result.get("Network Hierarchy UUID") or result.get("id", "")
        old_to_new[entry["uuid"]] = new_uuid

    log.info("MRF regions restored: %d regions, %d subregions", len(regions), len(subregions))


def _add_ca_certs(cfg: str, ca_chain: str) -> str:
    cloud_start = cfg.find("#cloud-config")
    if cloud_start == -1:
        return cfg
    boundary = cfg.find("--==BOUNDARY==", cloud_start)
    if boundary == -1:
        return cfg
    indented = "\n".join("   " + line for line in ca_chain.strip().splitlines())
    ca_block = " - rcc : true\nca-certs:\n  remove-defaults: false\n  trusted:\n  - |\n"
    ca_block += indented + "\n"
    return cfg[:boundary] + ca_block + "\n" + cfg[boundary:]


def _inject_otps_and_start_edges(
    lab: Any,
    client: ManagerClient,
    ca_chain: str = "",
) -> list[str]:
    otps = client.get_vedge_otps()
    uuids: list[str] = []
    for node in lab.nodes():
        if node.node_definition != "cat-sdwan-edge":
            continue
        cfg: str = node.configuration or ""
        m = re.search(r"uuid\s*:\s*([\w-]+)", cfg)
        if not m:
            log.warning("No UUID found in %s cloud-init — skipping OTP injection", node.label)
            node.start()
            continue
        uuid = m.group(1)
        otp = otps.get(uuid)
        if otp:
            if ca_chain and "ca-certs:" not in cfg:
                cfg = _add_ca_certs(cfg, ca_chain)
            node.configuration = re.sub(r"(otp\s*:)\s*\w+", rf"\g<1> {otp}", cfg)
            log.debug("OTP injected for %s", node.label)
        else:
            log.warning("No OTP found for %s (uuid=%s)", node.label, uuid)
        node.start()
        uuids.append(uuid)
    return uuids



import datetime
import json
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn

from catalyst_sdwan_lab.manager_client import ManagerAPIError, ManagerClient
from catalyst_sdwan_lab.ssh_client import fix_sdrouting_default_route

from .delete import run as _delete_lab
from .utils import (
    SDWAN_CTRL_NODE_DEFS,
    check_serial_file_match,
    collect_control_components,
    configure_manager,
    connect_cml,
    console,
    dump_topology,
    extract_org_name,
    load_certs,
    onboard_control_components,
    resolve_image,
    run_sastre_task,
    sha512_crypt,
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

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Connecting to CML...")
        cml = connect_cml(cml_host, cml_user, cml_password)

        progress.update(task, description="Loading backup...")
        topology, manager_configs_dir, backup_tmpdir = _load_backup(backup)
        check_serial_file_match(topology, serial_file)

        nodes = topology_nodes(topology)
        mgr = next((n for n in nodes if n.get("node_definition") == "cat-sdwan-manager"), None)
        raw_version = (
            mgr["image_definition"].split("-")[-1] if mgr and mgr.get("image_definition") else ""
        )
        backup_version = raw_version if raw_version and raw_version[0].isdigit() else "20.15"
        version = control_version or backup_version

        progress.update(task, description="Checking images...")
        _check_images(cml, topology, control_version, edge_version)

        if delete_existing:
            progress.update(task, description="Removing existing lab...")
            _delete_lab(cml_host, cml_user, cml_password, lab_name, force=True)

        try:
            if retry:
                progress.update(task, description="Locating existing lab...")
                lab = _find_lab_by_manager(cml, manager_ip, manager_port, patty)
            else:
                progress.update(task, description="Patching topology...")
                _patch_topology(
                    topology, lab_name, manager_ip, manager_mask, manager_gateway,
                    manager_port, manager_user, manager_password, patty,
                    control_version, edge_version, cml,
                )
                topology_yaml = dump_topology(topology)
                progress.update(task, description="Importing lab into CML...")
                lab = cml.import_lab(topology_yaml)

            progress.update(task, description="Starting control plane...")
            _start_control_plane(lab)

            progress.update(task, description="Waiting for SD-WAN Manager...")
            client = wait_for_manager(
                manager_ip, manager_port, manager_user, manager_password, version,
                lambda s: progress.update(task, description=s),
            )
            try:
                progress.update(task, description="Configuring SD-WAN Manager...")
                configure_manager(client, version, org_name, certs.chain)

                progress.update(task, description="Onboarding control components...")
                components = collect_control_components(lab)
                if not components:
                    log.error(
                        "No controller or validator nodes found in topology"
                        " — cannot onboard control plane."
                    )
                    raise typer.Exit(1)
                onboard_control_components(
                    client, certs, components,
                    lambda s: progress.update(task, description=s),
                )

                progress.update(task, description="Uploading serial file...")
                client.upload_serial_file(serial_file)

                progress.update(task, description="Patching Sastre controller UUIDs...")
                _patch_sastre_controller_uuids(manager_configs_dir, client)

                progress.update(task, description="Patching config group passwords...")
                _patch_config_group_passwords(manager_configs_dir)

                progress.update(task, description="Restoring Manager configuration...")
                _run_sastre_restore(
                    manager_ip, manager_port, manager_user, manager_password, manager_configs_dir
                )

                mrf_path = manager_configs_dir / "mrf.json"
                if mrf_path.exists():
                    progress.update(task, description="Restoring network hierarchy...")
                    _restore_mrf(client, json.loads(mrf_path.read_text()))

                progress.update(task, description="Starting edge nodes...")
                edge_uuids = _inject_otps_and_start_edges(lab, client)

                for node in lab.nodes():
                    if node.node_definition != "cat-sdwan-edge":
                        continue
                    if "SD-Routing : true" not in (node.configuration or ""):
                        continue
                    img_version = (node.image_definition or "").removeprefix("cat-sdwan-edge-")
                    if not img_version or int(img_version.split(".")[0]) >= 26:
                        continue
                    progress.update(task, description=f"Checking default route on {node.label}...")
                    node.wait_until_converged()
                    if fix_sdrouting_default_route(
                        cml_host, cml_user, cml_password, lab.title or lab_name, node.label,
                        console=console,
                    ):
                        node.wait_until_converged()

                if edge_uuids:
                    total_edges = len(edge_uuids)
                    progress.update(
                        task, description=f"Waiting for edges to onboard... (0/{total_edges})"
                    )
                    wait_for_edges_onboarded(
                        client,
                        edge_uuids,
                        on_progress=lambda done, total: progress.update(
                            task, description=f"Waiting for edges to onboard... ({done}/{total})"
                        ),
                    )

                progress.update(task, description="Triggering network rediscovery...")
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


def _load_backup(backup: Path) -> tuple[dict[str, Any], Path, Any]:
    if backup.suffix == ".zip":
        tmpdir = tempfile.TemporaryDirectory()
        out = Path(tmpdir.name)
        with zipfile.ZipFile(backup) as zf:
            for member in zf.infolist():
                if ".." not in member.filename and not member.filename.startswith("/"):
                    zf.extract(member, out)
        return yaml.safe_load((out / "topology.yaml").read_text()), out / "manager_configs", tmpdir
    return yaml.safe_load((backup / "topology.yaml").read_text()), backup / "manager_configs", None


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
    for node in nodes:
        node_def = node.get("node_definition", "")
        if node_def == "cat-sdwan-manager":
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
                cfg = re.sub(
                    r"(<user>[\s\S]+?<name>)admin(</name>)",
                    lambda m: f"{m.group(1)}{manager_user}{m.group(2)}",
                    cfg,
                    count=1,
                )
            if m := re.search(
                r"(<vpn-instance>[\s\S]+?<vpn-id>512</vpn-id>[\s\S]+?<address>)([\d./]+)(</address>)",
                cfg,
            ):
                cfg = cfg.replace(m.group(0), f"{m.group(1)}{manager_ip}{manager_mask}{m.group(3)}")
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
            if patty:
                node.setdefault("tags", [])
                pat_tag = f"pat:{manager_port}:443"
                if pat_tag not in node["tags"]:
                    node["tags"].append(pat_tag)
        if control_version and node_def in SDWAN_CTRL_NODE_DEFS:
            node["image_definition"] = f"{node_def}-{control_version}"
        if edge_version and node_def == "cat-sdwan-edge":
            node["image_definition"] = f"{node_def}-{edge_version}"


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


def _inject_otps_and_start_edges(lab: Any, client: ManagerClient) -> list[str]:
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
            node.configuration = re.sub(r"(otp\s*:)\s*\w+", rf"\g<1> {otp}", cfg)
            log.debug("OTP injected for %s", node.label)
        else:
            log.warning("No OTP found for %s (uuid=%s)", node.label, uuid)
        node.start()
        uuids.append(uuid)
    return uuids



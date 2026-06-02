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
from jinja2 import Environment, FileSystemLoader
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn

from catalyst_sdwan_lab.manager_client import ManagerAPIError
from catalyst_sdwan_lab.ssh_client import extract_control_config, extract_edge_config

from .utils import (
    CML_BACKUP_TEMPLATES_DIR,
    SDWAN_CTRL_NODE_DEFS,
    connect_cml,
    connect_manager,
    console,
    find_lab,
    load_certs,
    topology_nodes,
)

log = logging.getLogger(__name__)

_CTRL_XML_PERSONALITIES = {
    "cat-sdwan-manager": "    <personality>vmanage</personality>\n    <device-model>vmanage</device-model>",
    "cat-sdwan-controller": "    <personality>vsmart</personality>\n    <device-model>vsmart</device-model>",
    "cat-sdwan-validator": "    <personality>vedge</personality>\n    <device-model>vedge-cloud</device-model>",
}

_TEMPLATE_NAMES = {
    "cat-sdwan-manager": "cat-sdwan-manager.j2",
    "cat-sdwan-controller": "cat-sdwan-controller.j2",
    "cat-sdwan-validator": "cat-sdwan-validator.j2",
    "cat-sdwan-edge": "cat-sdwan-edge.j2",
    "cat-sdwan-edge-sdrouting": "cat-sdwan-edge-sdrouting.j2",
}

_env = Environment(loader=FileSystemLoader(str(CML_BACKUP_TEMPLATES_DIR)), trim_blocks=True)


def run(
    cml_host: str,
    cml_user: str,
    cml_password: str,
    lab_name: str,
    manager_user: str,
    manager_password: str,
    output: Path | None,
    directory: bool,
) -> None:
    certs = load_certs()
    cml = connect_cml(cml_host, cml_user, cml_password)
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Locating lab...")
            lab, manager_ip, manager_port = find_lab(cml, lab_name)
            if not lab.is_active():
                log.error("Lab '%s' is not active — start the lab before running backup.", lab_name)
                raise typer.Exit(1)

            progress.update(task, description="Connecting to SD-WAN Manager...")
            client = connect_manager(manager_ip, manager_port, manager_user, manager_password)

            try:
                org_name = client.get_organization()
                validator_fqdn = client.get_validator_fqdn()
                if not org_name or not validator_fqdn:
                    log.error("Could not fetch org name or validator FQDN from Manager.")
                    raise typer.Exit(1)

                nodes = list(lab.nodes())
                cml_extract_nodes = [
                    n for n in nodes
                    if n.is_active()
                    and n.node_definition not in SDWAN_CTRL_NODE_DEFS
                    and n.node_definition != "cat-sdwan-edge"
                ]
                for i, node in enumerate(cml_extract_nodes, 1):
                    progress.update(task, description=f"Extracting CML node configurations ({i}/{len(cml_extract_nodes)})...")
                    try:
                        node.extract_configuration()
                        log.info("Extracted config from %s via CML.", node.label)
                    except Exception:
                        log.debug("Node '%s' does not support config extract — skipping.", node.label)

                progress.update(task, description="Downloading CML topology...")
                topology_str = lab.download()
                topology: Any = yaml.safe_load(topology_str)

                extract_nodes = [
                    n for n in nodes
                    if n.is_active() and (
                        n.node_definition in SDWAN_CTRL_NODE_DEFS
                        or n.node_definition == "cat-sdwan-edge"
                    )
                ]
                total = len(extract_nodes)
                for i, node in enumerate(extract_nodes, 1):
                    node_def = node.node_definition
                    if node_def in SDWAN_CTRL_NODE_DEFS:
                        progress.update(task, description=f"Extracting config from {node.label} ({i}/{total})...")
                        if node_def == "cat-sdwan-manager":
                            node_user, node_pass = manager_user, manager_password
                        else:
                            node_user, node_pass = "admin", "admin"
                        try:
                            config_xml = extract_control_config(
                                cml_host, cml_user, cml_password, lab_name,
                                node.label, node_user, node_pass,
                            )
                            config_xml = _inject_xml_personality(config_xml, node_def)
                        except Exception as e:
                            log.error("Failed to extract config from %s: %s", node.label, e)
                            raise typer.Exit(1)
                        cloud_init = _render_cloud_init(node_def, certs.chain, config_xml)
                        _update_node_configuration(topology, node.label, cloud_init)
                    elif node_def == "cat-sdwan-edge":
                        progress.update(task, description=f"Extracting config from {node.label} ({i}/{total})...")
                        try:
                            edge_type, config_text, uuid = extract_edge_config(
                                cml_host, cml_user, cml_password, lab_name, node.label,
                                manager_password,
                            )
                        except Exception as e:
                            log.error("Failed to extract config from %s: %s", node.label, e)
                            raise typer.Exit(1)
                        template_key = "cat-sdwan-edge" if edge_type == "sdwan" else "cat-sdwan-edge-sdrouting"
                        cloud_init = _render_edge_cloud_init(
                            template_key, certs.chain, org_name, validator_fqdn, config_text, uuid,
                        )
                        _update_node_configuration(topology, node.label, cloud_init)

                progress.update(task, description="Backing up SD-WAN Manager configuration...")
                with tempfile.TemporaryDirectory() as tmpdir:
                    _run_sastre_backup(
                        manager_ip, manager_port, manager_user, manager_password, Path(tmpdir)
                    )
                    progress.update(task, description="Backing up network hierarchy...")
                    mrf_data = client.get_network_hierarchy()

                    if output is None:
                        ts = datetime.date.today().strftime("%Y%m%d")
                        suffix = "" if directory else ".zip"
                        output = Path(f"{lab_name}-{ts}{suffix}")

                    progress.update(task, description="Saving backup...")
                    updated_topology = _dump_topology(topology)
                    if directory:
                        _save_directory(output, updated_topology, Path(tmpdir), mrf_data)
                    else:
                        _save_zip(output, updated_topology, Path(tmpdir), mrf_data)

            except ManagerAPIError as e:
                log.error("%s", e)
                raise typer.Exit(1)
            finally:
                client.logout()

    finally:
        cml.logout()
    console.print(f"[green]Backup complete.[/green] Saved to: {escape(str(output))}")


class _TopologyDumper(yaml.SafeDumper):
    pass


def _literal_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        data = "\n".join(line.rstrip() for line in data.splitlines())
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=None)


_TopologyDumper.add_representer(str, _literal_str)


def _dump_topology(topology: Any) -> str:
    return yaml.dump(topology, Dumper=_TopologyDumper, allow_unicode=True, default_flow_style=False)


def _inject_xml_personality(config_xml: str, node_def: str) -> str:
    personality = _CTRL_XML_PERSONALITIES[node_def]
    return re.sub(
        r'(<system xmlns="http://viptela.com/system">)',
        rf"\1\n{personality}",
        config_xml,
    )


def _render_cloud_init(node_def: str, root_ca: str, config: str) -> str:
    template = _env.get_template(_TEMPLATE_NAMES[node_def])
    return template.render(root_ca=root_ca, config=config)


def _render_edge_cloud_init(
    template_key: str, root_ca: str, org_name: str, validator_fqdn: str, config: str, uuid: str,
) -> str:
    template = _env.get_template(_TEMPLATE_NAMES[template_key])
    return template.render(
        root_ca=root_ca, org_name=org_name, validator_fqdn=validator_fqdn,
        config=config, uuid=uuid,
    )


def _update_node_configuration(topology: Any, node_label: str, cloud_init: str) -> None:
    nodes = topology_nodes(topology)
    for node in nodes:
        if node.get("label") == node_label:
            node["configuration"] = cloud_init
            return
    log.warning("Node '%s' not found in topology YAML — skipping config patch", node_label)


def _run_sastre_backup(
    manager_ip: str, manager_port: int, manager_user: str, manager_password: str, workdir: Path
) -> None:
    from cisco_sdwan.base.rest_api import Rest  # type: ignore[import-untyped]
    from cisco_sdwan.tasks.implementation import BackupArgs, TaskBackup  # type: ignore[import-untyped]

    task_args = BackupArgs(
        save_running=False, no_rollover=True, workdir=str(workdir), tags=["all"]
    )
    with Rest(
        base_url=f"https://{manager_ip}:{manager_port}",
        username=manager_user,
        password=manager_password,
    ) as api:
        task_output = TaskBackup().runner(task_args, api)
        if task_output:
            for entry in task_output:
                log.debug("Sastre: %s", entry)
    log.info("Sastre backup completed to %s", workdir)


def _save_zip(
    output: Path,
    topology_yaml: str,
    manager_configs_dir: Path,
    mrf_data: list[dict[str, Any]],
) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("topology.yaml", topology_yaml)
        zf.writestr("manager_configs/mrf.json", json.dumps(mrf_data, indent=2))
        for src in manager_configs_dir.rglob("*"):
            if src.is_file():
                zf.write(src, Path("manager_configs") / src.relative_to(manager_configs_dir))
    log.info("Backup saved to %s", output)


def _save_directory(
    output: Path,
    topology_yaml: str,
    manager_configs_dir: Path,
    mrf_data: list[dict[str, Any]],
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "topology.yaml").write_text(topology_yaml)
    configs_out = output / "manager_configs"
    configs_out.mkdir(exist_ok=True)
    (configs_out / "mrf.json").write_text(json.dumps(mrf_data, indent=2))
    for src in manager_configs_dir.rglob("*"):
        if src.is_file():
            dest = configs_out / src.relative_to(manager_configs_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
    log.info("Backup saved to %s", output)

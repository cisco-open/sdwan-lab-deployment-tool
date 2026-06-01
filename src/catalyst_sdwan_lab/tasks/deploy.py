import json
import logging
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import typer
from jinja2 import Environment, FileSystemLoader
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn
from virl2_client import ClientLibrary

from catalyst_sdwan_lab.manager_client import ManagerAPIError, ManagerClient

from .utils import (
    CML_DEPLOY_TEMPLATES_DIR,
    CONTROLLER_TEMPLATES_DIR,
    VALIDATOR_FQDN,
    Certs,
    basic_configuration_path,
    configure_manager,
    connect_cml,
    console,
    extract_org_name,
    load_certs,
    onboard_control_components,
    resolve_image,
    sha512_crypt,
    sign_device_cert,
    wait_for_manager,
)

log = logging.getLogger(__name__)

_NODE_TYPES = ("cat-sdwan-manager", "cat-sdwan-controller", "cat-sdwan-validator")


@dataclass
class _Images:
    manager: str
    controller: str
    validator: str


def run(
    cml_host: str,
    cml_user: str,
    cml_password: str,
    manager_ip: str,
    manager_port: int,
    manager_user: str,
    manager_password: str,
    manager_mask: str,
    manager_gateway: str,
    version: str,
    lab_name: str,
    bridge: str,
    dns_server: str,
    ip_type: str,
    retry: bool,
    patty: bool,
    serial_file: Path,
) -> None:
    if manager_password == "admin":
        log.error("Cannot use default credentials. Update Manager password and try again.")
        raise typer.Exit(1)

    try:
        org_name = extract_org_name(serial_file)
    except ValueError as e:
        log.error("Invalid serial file: %s", e)
        raise typer.Exit(1)
    log.info("Organization name: %s", org_name)

    certs = load_certs()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Connecting to CML...")
        cml = connect_cml(cml_host, cml_user, cml_password)

        progress.update(task, description="Checking SD-WAN images...")
        images = _check_images(cml, version)

        try:
            if retry:
                progress.update(task, description="Locating existing lab...")
                _find_lab(cml, manager_ip, manager_port)
            else:
                progress.update(task, description=f"Creating lab {lab_name}...")
                _create_lab(
                    cml,
                    lab_name=lab_name,
                    images=images,
                    certs=certs,
                    org_name=org_name,
                    manager_ip=manager_ip,
                    manager_port=manager_port,
                    manager_user=manager_user,
                    manager_password=manager_password,
                    manager_mask=manager_mask,
                    manager_gateway=manager_gateway,
                    bridge="NAT" if patty else bridge,
                    dns_server=dns_server,
                    ip_type=ip_type,
                    patty=patty,
                )

            progress.update(task, description="Waiting for SD-WAN Manager...")
            client = wait_for_manager(
                manager_ip,
                manager_port,
                manager_user,
                manager_password,
                version,
                on_status=lambda msg: progress.update(task, description=msg),
            )
            try:
                progress.update(task, description="Configuring SD-WAN Manager...")
                configure_manager(client, version, org_name, certs.chain)

                onboard_control_components(
                    client,
                    certs,
                    [("fc00:172:16::201", "vbond"), ("fc00:172:16::101", "vsmart")]
                    if ip_type == "v6"
                    else [("172.16.0.201", "vbond"), ("172.16.0.101", "vsmart")],
                    on_status=lambda msg: progress.update(task, description=msg),
                )

                progress.update(task, description="Uploading serial file...")
                client.upload_serial_file(serial_file)

                progress.update(task, description="Importing basic configuration...")
                _restore_basic_configuration(client, ip_type)

                progress.update(task, description="Importing controller templates...")
                template_id = _import_controller_templates(client, ip_type)

                progress.update(task, description="Attaching controller template...")
                _attach_controller_template(client, ip_type, template_id)
            except ManagerAPIError as e:
                log.error("%s", e)
                raise typer.Exit(1)
            finally:
                client.logout()
        finally:
            cml.logout()

    log.info(
        "Deploy complete. Manager: https://%s:%s  org: %s  version: %s",
        manager_ip, manager_port, org_name, version,
    )
    console.print(f"[green]Deploy complete.[/green] Manager: https://{escape(manager_ip)}:{manager_port}")


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TEMPLATE_STRIP = {"templateId", "@rid", "createdBy", "createdOn", "lastUpdatedBy", "lastUpdatedOn"}


def _template_post_body(raw: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in raw.items() if k not in _TEMPLATE_STRIP}
    body["factoryDefault"] = False
    body["readonly"] = False
    return body


def _import_controller_templates(client: ManagerClient, ip_type: str) -> str:
    templates = {t["templateName"]: t["templateId"] for t in client.get_device_templates()}
    if "controller_basic" in templates:
        log.debug("controller_basic template already present — skipping")
        return templates["controller_basic"]

    feature_dir = CONTROLLER_TEMPLATES_DIR / "feature"
    template_files = list((feature_dir / "common").glob("*.json")) + list(
        (feature_dir / ip_type).glob("*.json")
    )

    id_map: dict[str, str] = {}
    for path in template_files:
        raw = json.loads(path.read_text())
        old_id: str = raw["templateId"]
        new_id = client.create_feature_template(_template_post_body(raw))
        id_map[old_id] = new_id
        log.debug("Feature template created: %s -> %s", path.stem, new_id)

    device_raw = json.loads((CONTROLLER_TEMPLATES_DIR / "device_template.json").read_text())
    remapped_text = _UUID_RE.sub(lambda m: id_map.get(m.group(), m.group()), json.dumps(device_raw))
    template_id = client.create_device_template(_template_post_body(json.loads(remapped_text)))
    log.info("Controller templates imported")
    return template_id


def _attach_controller_template(
    client: ManagerClient,
    ip_type: str,
    template_id: str,
    controller_ips: set[str] | None = None,
) -> None:
    all_controllers = client.get_controllers()
    controllers = [
        d for d in all_controllers
        if d.get("personality") == "vsmart"
        and not d.get("template")
        and (controller_ips is None or d.get("deviceIP") in controller_ips)
    ]
    if not controllers:
        log.debug("All controllers already have a template — skipping attach")
        return

    devices = []
    for d in controllers:
        dev_ip = d["deviceIP"]
        last_octet = dev_ip.split(".")[-1] if "." in dev_ip else dev_ip.split(":")[-1]
        variables: dict[str, Any] = {
            "csv-status": "complete",
            "csv-deviceId": d["uuid"],
            "csv-deviceIP": f"100.0.0.{last_octet}",
            "csv-host-name": f"Controller{last_octet[-2:]}",
            "//system/host-name": f"Controller{last_octet[-2:]}",
            "//system/system-ip": f"100.0.0.{last_octet}",
            "//system/site-id": "100",
            "csv-templateId": template_id,
        }
        if ip_type in ("v4", "dual"):
            variables["/0/eth1/interface/ip/address"] = f"172.16.0.{last_octet}/24"
        if ip_type in ("v6", "dual"):
            variables["/0/eth1/interface/ipv6/address"] = f"fc00:172:16::{last_octet}/64"
        devices.append(variables)

    task_id = client.attach_device_template(template_id, devices)
    client.wait_for_task(task_id)
    log.info("Controller template attached to %d controller(s)", len(devices))


def _restore_basic_configuration(client: ManagerClient, ip_type: str) -> None:
    existing = {g.get("name") for g in client.get_config_groups()}
    if "edge_basic" in existing and "sdrouting_basic" in existing:
        log.debug("Basic configuration already imported — skipping")
        return
    task_id = client.import_configuration(basic_configuration_path(ip_type))
    client.wait_for_task(task_id)
    log.info("Basic configuration imported")


def _check_images(cml: ClientLibrary, version: str) -> _Images:
    results: dict[str, str] = {}
    for node_type in _NODE_TYPES:
        image_id = resolve_image(cml, node_type, version)
        results[node_type] = image_id
        log.info("Image OK: %s", image_id)
    return _Images(
        manager=results["cat-sdwan-manager"],
        controller=results["cat-sdwan-controller"],
        validator=results["cat-sdwan-validator"],
    )


def _find_lab(cml: ClientLibrary, manager_ip: str, manager_port: int) -> None:
    needle = f"manager_external_ip = {manager_ip}:{manager_port}"
    for lab in cml.all_labs(show_all=True):
        if lab.notes and needle in lab.notes:
            log.info("Found existing lab: %s", lab.title)
            return
    log.error(
        "Retry flag set but no lab found with Manager IP %s:%s.", manager_ip, manager_port
    )
    raise typer.Exit(1)


def _create_lab(
    cml: ClientLibrary,
    lab_name: str,
    images: _Images,
    certs: Certs,
    org_name: str,
    manager_ip: str,
    manager_port: int,
    manager_user: str,
    manager_password: str,
    manager_mask: str,
    manager_gateway: str,
    bridge: str,
    dns_server: str,
    ip_type: str,
    patty: bool,
) -> Any:
    existing = [lab.title for lab in cml.all_labs(show_all=True)]
    if lab_name in existing:
        log.error(
            "Lab '%s' already exists. Use a different name or --retry to resume.", lab_name
        )
        raise typer.Exit(1)

    if not patty:
        _check_ip_free(manager_ip)

    encrypted_password = sha512_crypt(manager_password, rounds=5000)
    env = Environment(loader=FileSystemLoader(str(CML_DEPLOY_TEMPLATES_DIR)), trim_blocks=True)
    topology = env.get_template("cml-base-topology.j2").render(
        title=lab_name,
        manager_image=images.manager,
        controller_image=images.controller,
        validator_image=images.validator,
        root_ca=certs.chain,
        org_name=org_name,
        validator_fqdn=VALIDATOR_FQDN,
        manager_num="1",
        controller_num="01",
        validator_num="01",
        manager_user=manager_user,
        manager_pass=encrypted_password,
        manager_external_ip=manager_ip,
        external_subnet_mask=manager_mask,
        external_gateway=manager_gateway,
        bridge=bridge,
        dns_server=dns_server,
        ip_type=ip_type,
        manager_port=manager_port,
        patty_used=patty,
    )

    log.info("Importing lab '%s' to CML...", lab_name)
    lab = cml.import_lab(topology, lab_name)
    log.info("Starting lab nodes...")
    lab.start()
    return lab


def _check_ip_free(ip: str) -> None:
    flag = "-n" if platform.system().lower() == "windows" else "-c"
    result = subprocess.call(
        ["ping", flag, "1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if result == 0:
        log.error(
            "IP %s is already in use. Resolve the conflict or use a different Manager IP.", ip
        )
        raise typer.Exit(1)

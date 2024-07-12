# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd

import datetime
import io
import json
import os
import re
import sys
from os.path import join
from typing import Any, Dict, List, Union

from catalystwan.endpoints.configuration_device_inventory import SerialFilePayload
from cisco_sdwan.base.rest_api import Rest
from passlib.hash import sha512_crypt
from ruamel.yaml import YAML
from virl2_client import ClientLibrary

from . import delete
from .utils import (
    DATA_DIR,
    check_manager_ip_is_free,
    configure_manager_basic_settings,
    get_sdwan_lab_parameters,
    load_certificate_details,
    onboard_control_components,
    restore_manager_configuration,
    setup_logging,
    track_progress,
    wait_for_manager_session,
    wait_for_wan_edge_onboaring,
)


def main(
    cml: ClientLibrary,
    cml_ip: str,
    manager_ip: str,
    manager_port: int,
    manager_mask: str,
    manager_gateway: str,
    manager_user: str,
    manager_password: str,
    workdir: str,
    lab_name: str,
    patty_used: bool,
    deleteexisitng: bool,
    retry: bool,
    loglevel: Union[int, str],
) -> None:
    # Time the script execution
    begin_time = datetime.datetime.now()

    # Setup logging
    log = setup_logging(loglevel)

    # Setup YAML
    yaml = YAML(typ="rt")

    # Import topology from backup
    workdir = join(os.path.abspath(os.getcwd()), workdir)
    with open(join(workdir, "cml_topology.yaml"), "r") as f:
        cml_topology = f.read()

    cml_topology_dict = yaml.load(cml_topology)

    # Verify if requested software version is defined in CML
    track_progress(log, "Preparing for restore...")
    log.info("Checking software version...")
    cml_image_definitions = cml.definitions.image_definitions()
    defined_images = set(image["id"] for image in cml_image_definitions)
    missing_images = []
    for node in cml_topology_dict["nodes"]:
        if node["image_definition"] and node["image_definition"] not in defined_images:
            missing_images.append(node["image_definition"])
    if missing_images:
        sys.exit(
            f"The following image definitions are required, but not present: {missing_images}\n"
            f"Use setup task to upload the correct images."
        )

    if deleteexisitng:
        track_progress(log, "Checking for exiting lab...")
        # If deleteexisting is set, check if there's existing lab with same name and SD-WAN Manager IP.
        lab = next(
            (
                lab
                for lab in cml.find_labs_by_title(lab_name)
                if manager_ip in lab.notes
            ),
            None,
        )
        if lab:
            # If found, remove this lab.
            track_progress(log, "Removing existing lab...")
            delete.main(cml, lab.title, True, loglevel)
        else:
            log.info("No existing lab found, continuing...")

    track_progress(log, "Preparing for restore...")

    # Prepare the CA for controllers certificate signing
    ca_cert, ca_key, ca_chain = load_certificate_details()

    if retry:
        # If retry flag is set, skip the lab bringup and move directly to SD-WAN Manager steps
        track_progress(log, "Retry flag set, checking if lab already exists in CML...")
        labs = [lab for lab in cml.all_labs(show_all=True)]
        lab = next((lab for lab in labs if manager_ip in lab.notes), None)
        if not lab:
            exit(
                "\nRetry option is set, but script cloud not find the "
                "lab with specified SD-WAN Manager IP."
            )
    else:
        track_progress(log, "Updating lab parameters...")
        if not patty_used:
            # Check if the IP allocated for SD-WAN Manager is not already it use.
            check_manager_ip_is_free(manager_ip)
        # Update SD-WAN Manager IP in lab title and description
        cml_topology_dict["lab"]["notes"] = (
            f"-- Do not delete this text --\nmanager_external_ip = {manager_ip}:{manager_port}\n-- "
            f"Do not delete this text --"
        )
        cml_topology_dict["lab"]["title"] = lab_name
        # Update SD-WAN Manager cloud-init config with password
        encrypted_manager_password = sha512_crypt.encrypt(manager_password, rounds=5000)
        manager_node = next(
            node
            for node in cml_topology_dict["nodes"]
            if node["node_definition"] == "cat-sdwan-manager"
        )
        existing_manager_passwords = re.findall(
            r"<user>[\s\S]+?<password>(\S+)</password>", manager_node["configuration"]
        )
        for existing_manager_password in existing_manager_passwords:
            manager_node["configuration"] = manager_node["configuration"].replace(
                existing_manager_password, encrypted_manager_password
            )
        # Update SD-WAN Manager cloud-init config with username
        existing_manager_users = re.findall(
            r"<user>[\s\S]+?<name>(\w+)</name>", manager_node["configuration"]
        )
        existing_manager_users.remove("admin")
        if manager_user not in existing_manager_users:
            manager_node["configuration"] = manager_node["configuration"].replace(
                existing_manager_users[0], manager_user
            )
        # Update SD-WAN Manager IP
        existing_manager_ip_mask_search = re.search(
            r"<vpn-instance>[\s\S]+?<vpn-id>512</vpn-id>[\s\S]+?"
            r"<address>([\d./]+)</address>",
            manager_node["configuration"],
        )
        if existing_manager_ip_mask_search:
            existing_manager_ip_mask = existing_manager_ip_mask_search.group(1)
            manager_node["configuration"] = manager_node["configuration"].replace(
                existing_manager_ip_mask, manager_ip + manager_mask
            )
        # Update SD-WAN Manager Gateway
        existing_manager_gateway_search = re.search(
            r"<vpn-instance>[\s\S]+?<vpn-id>512</vpn-id>[\s\S]+?"
            r"<next-hop>[\s\S]+?<address>([\d.]+)</address>",
            manager_node["configuration"],
        )
        if existing_manager_gateway_search:
            existing_manager_gateway = existing_manager_gateway_search.group(1)
            manager_node["configuration"] = manager_node["configuration"].replace(
                existing_manager_gateway, manager_gateway
            )
        if patty_used:
            manager_node["tags"] = [f"pat:{manager_port}:443"]

        stream = io.StringIO()
        yaml.dump(cml_topology_dict, stream)
        track_progress(log, "Importing the lab...")
        lab = cml.import_lab(stream.getvalue())

    # Define lists that will hold Validators and Controllers VPN0 IPs
    control_components = {}
    device_ip_to_system_ip = {}
    serial_file_version = None
    manager_node = None
    config_version = 2
    for node in lab.nodes():
        if node.node_definition == "cat-sdwan-manager":
            # Choose the parameters
            # This will determine what devices are included in serial file (SD-WAN/SD-Routing)
            software_version = node.image_definition.split("-")[3]
            serial_file_version, config_version = get_sdwan_lab_parameters(
                software_version
            )
            log.info(f"Starting node {node.label}...")
            node.start()
            manager_node = node
        elif node.node_definition == "cat-sdwan-controller":
            # Add Controller VPN 0 IP to the list
            vpn0_ip_search = re.search(r"(172.16.0.1\d+)/24", node.configuration)
            system_ip_search = re.search(
                r"<system-ip>([\d.]+)</system-ip>", node.configuration
            )
            if vpn0_ip_search and system_ip_search:
                vpn0_ip = vpn0_ip_search.group(1)
                system_ip = system_ip_search.group(1)
                device_ip_to_system_ip[vpn0_ip] = system_ip
                device_ip_to_system_ip[system_ip] = system_ip
                control_components[vpn0_ip] = "controller"
            log.info(f"Starting node {node.label}...")
            node.start()
        elif node.node_definition == "cat-sdwan-validator":
            # Add Validator VPN 0 IP to the list
            vpn0_ip_search = re.search(r"(172.16.0.2\d+)/24", node.configuration)
            if vpn0_ip_search:
                vpn0_ip = vpn0_ip_search.group(1)
                control_components[vpn0_ip] = "validator"
            log.info(f"Starting node {node.label}...")
            node.start()
        elif node.node_definition == "cat8000v" and node.is_booted() is False:
            # To workaround CML problem, after config export for this node
            # we need to add 'no shutdown' under all interfaces
            node.config = re.sub(
                r"(interface\sGigabitEthernet\d\n)",
                r"\1 no shutdown\n",
                node.configuration,
            )
            node.start()
        elif node.node_definition != "cat-sdwan-edge":
            # Start all the nodes except WAN Edges
            log.info(f"Starting node {node.label}...")
            node.start()

    track_progress(log, "Waiting for SD-WAN Manager to boot...")
    manager_node.wait_until_converged()
    # Wait for SD-WAN Manager API to be available
    manager_session = wait_for_manager_session(
        manager_ip, manager_port, manager_user, manager_password, log
    )
    # Configure basic settings like org-name, validator fqdn etc.
    configure_manager_basic_settings(manager_session, ca_chain, log)

    # Add controllers to SD-WAN Manager and sing certificates
    onboard_control_components(
        manager_session, manager_password, control_components, log
    )

    track_progress(log, "Uploading Serial File...")
    serial_file = SerialFilePayload(
        join(DATA_DIR, f"serial_files/serialFile-v{str(serial_file_version)}.viptela"),
        "valid",
    )
    manager_session.endpoints.configuration_device_inventory.upload_wan_edge_list(
        serial_file
    )

    manager_configs_dir = join(workdir, "manager_configs")
    if os.path.exists(join(manager_configs_dir, "mrf")):
        track_progress(log, "Restoring MRF regions...")
        with Rest(
            base_url=f"https://{manager_ip}",
            username=manager_user,
            password=manager_password,
        ) as api:
            # First enable MRF
            software_version = api.server_version.split(".")
            major_software_release = int(software_version[0])
            minor_software_release = int(software_version[1])
            # noinspection PyTypeChecker
            network_hierarchy: List[Dict[str, Any]] = api.get("v1/network-hierarchy")
            mrf_regions_configured = [
                region
                for region in network_hierarchy
                if region["data"]["label"] in ["REGION"]
            ]
            if not mrf_regions_configured:
                if major_software_release == 20 and minor_software_release in [
                    7,
                    8,
                    9,
                    10,
                    11,
                    12,
                ]:
                    api.post(
                        {"enableMultiRegionFabric": True},
                        "settings/configuration/multiRegionFabric",
                    )
                else:
                    # noinspection PyTypeChecker
                    global_id = api.get("v1/network-hierarchy/nodes?label=GLOBAL")[0][
                        "id"
                    ]
                    api.post(
                        {
                            "data": {
                                "enableMrfInterRegionRouting": {
                                    "optionType": "global",
                                    "value": True,
                                }
                            }
                        },
                        f"v1/network-hierarchy/{global_id}/network-settings/mrf",
                    )
                # Check network hierarchy global ID
                # noinspection PyTypeChecker
                network_hierarchy = api.get("v1/network-hierarchy")
                global_id = next(
                    _["id"] for _ in network_hierarchy if _["data"]["label"] == "GLOBAL"
                )
                existing_region_names = [_["name"] for _ in network_hierarchy]
                old_to_new_id_map: Dict[str, str] = {}
                for folder in ["regions", "subregions"]:
                    path = f"{manager_configs_dir}/mrf/{folder}"
                    if os.path.exists(path):
                        for filename in os.listdir(path):
                            if filename.endswith(".json"):
                                with open(f"{path}/{filename}", "r") as file:
                                    region_dict = json.load(file)
                                    if region_dict["name"] not in existing_region_names:
                                        log.info(
                                            f'Creating MRF region {region_dict["name"]}...'
                                        )
                                        old_id = region_dict["uuid"]
                                        region_dict.pop("uuid", None)
                                        if folder == "regions":
                                            region_dict["data"][
                                                "parentUuid"
                                            ] = global_id
                                        elif folder == "subregions":
                                            region_dict["data"]["parentUuid"] = (
                                                old_to_new_id_map[
                                                    region_dict["data"]["parentUuid"]
                                                ]
                                            )
                                        new_id = api.post(
                                            region_dict, "v1/network-hierarchy"
                                        )["Network Hierarchy UUID"]
                                        old_to_new_id_map[old_id] = new_id
                                    else:
                                        log.info(
                                            f'Region {region_dict["name"]} already exists, skipping...'
                                        )

    track_progress(log, "Restoring device templates/configuration groups...")

    # Before creating template, we need to update SD-WAN Controllers UUID, so it gets attached to the template.
    # Create a dict mapping system_ip to uuid
    device_inventory = manager_session.endpoints.configuration_device_inventory
    controllers_uuids = {
        device_ip_to_system_ip[device.device_ip]: device.uuid
        for device in device_inventory.get_device_details("controllers")
        if device.device_type == "vsmart"
    }

    # Modify SD-WAN Controllers UUID is sastre export
    # so sastre attaches the template automatically during restore operation
    if os.path.isdir(join(manager_configs_dir, "device_templates", "attached")):
        for filename in os.listdir(
            join(manager_configs_dir, "device_templates", "attached")
        ):
            with open(
                join(manager_configs_dir, "device_templates", "attached", filename),
                "r+",
            ) as f1:
                data = json.load(f1)
                if data[0]["personality"] == "vsmart":
                    for device in data:
                        device["uuid"] = controllers_uuids[device["deviceIP"]]
                    f1.seek(0)
                    json.dump(data, f1, indent=2)
                    with open(
                        join(
                            manager_configs_dir, "device_templates", "values", filename
                        ),
                        "r+",
                    ) as f2:
                        data = json.load(f2)
                        for device in data["data"]:
                            device["csv-deviceId"] = controllers_uuids[
                                device["csv-deviceIP"]
                            ]
                        f2.seek(0)
                        json.dump(data, f2, indent=2)

    restore_manager_configuration(
        manager_session,
        manager_ip,
        manager_port,
        manager_user,
        manager_password,
        config_version,
        join(workdir, manager_configs_dir),
        True,
    )

    uuid_to_token = {
        device.uuid: device.serial_number
        for device in device_inventory.get_device_details("vedges")
    }
    wan_edges_to_onboard = []
    for node in lab.nodes():
        if node.node_definition == "cat-sdwan-edge" and node.is_booted() is False:
            # Before we boot Edge we need to update the cloud-init OTP token
            uuid_search = re.search(
                r"vinitparam:[\w\W]+?uuid\s:\s([\w-]+)", node.configuration
            )
            if uuid_search:
                uuid = uuid_search.group(1)
                token = uuid_to_token[uuid]
                # Update node config with new otp token
                node.config = re.sub(
                    r"(vinitparam:[\w\W]+?otp\s:)\s(\w+)",
                    rf"\1 {token}",
                    node.configuration,
                )
                wan_edges_to_onboard.append(uuid)
            node.start()

    # Wait until Edges are onboarded.
    wait_for_wan_edge_onboaring(manager_session, wan_edges_to_onboard, log)

    manager_session.close()
    track_progress(log, "Restore task done\n")

    print(
        f"#############################################\n"
        f"Lab is restored.\n"
        f"CML URL: https://{cml_ip}\n"
        f"SD-WAN Manager URL: https://{manager_ip}:8443\n"
        f"Use the username/password set with the script for CML and SD-WAN Manager login.\n"
        f"All other nodes use default username/password.\n"
        f"#############################################"
    )
    end_time = datetime.datetime.now()
    time_it = end_time - begin_time
    log.info("Time needed for full deployment: " + str(time_it))

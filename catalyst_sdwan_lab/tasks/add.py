# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd

import datetime
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Union

from catalystwan.api.task_status_api import OperationStatus, OperationStatusId, Task
from catalystwan.endpoints.configuration_group import (
    ConfigGroupAssociatePayload,
    DeviceId,
)
from catalystwan.endpoints.troubleshooting_tools.device_connectivity import NPingRequest
from catalystwan.session import ManagerSession, create_manager_session
from jinja2 import Environment, FileSystemLoader
from virl2_client import ClientConfig

from .utils import (
    CML_DEPLOY_LAB_DEFINITION_DIR,
    attach_basic_controller_template,
    find_node_by_label,
    get_cml_sdwan_image_definition,
    get_ip_type,
    load_certificate_details,
    onboard_control_components,
    setup_logging,
    track_progress,
    verify_cml_version,
    wait_for_wan_edge_onboaring,
)


def ping_node(
    vpn_ip: str, manager_session: ManagerSession, manager_system_ip: str
) -> None:
    nping_request = NPingRequest(host=vpn_ip, vpn="0", source="eth1")
    nping = (
        manager_session.endpoints.troubleshooting_tools.device_connectivity.nping_device
    )
    retries = 0
    max_retries = 30
    ping_not_working = True
    while ping_not_working:
        retries += 1
        if retries < max_retries:
            time.sleep(10)
            nping_result = nping(manager_system_ip, nping_request)
            if nping_result.packets_received > 0:
                ping_not_working = False
        else:
            sys.exit(f"Failed to ping new controller {vpn_ip} after 5 minutes")


def main(
    cml_config: ClientConfig,
    manager_ip: str,
    manager_port: int,
    manager_user: str,
    manager_password: str,
    lab_name: str,
    number_of_devices: int,
    device_type: str,
    software_version: str,
    loglevel: Union[int, str],
) -> None:
    # Time the script execution
    begin_time = datetime.datetime.now()

    # Setup logging
    log = setup_logging(loglevel)

    # create cml instance and check version
    cml = cml_config.make_client()
    verify_cml_version(cml)

    track_progress(log, "Preparing add task...")
    # Load CA chain
    ca_cert, ca_key, ca_chain = load_certificate_details()

    if device_type.endswith("s"):
        # Change from plural to singular
        device_type = device_type[:-1]
    if device_type in ["vbond", "validator"]:
        cml_node_type = "cat-sdwan-validator"
        device_type = "vbond"
    elif device_type in ["vsmart", "controller"]:
        cml_node_type = "cat-sdwan-controller"
        device_type = "vsmart"
    elif device_type in ["cedge", "edge"]:
        cml_node_type = "cat-sdwan-edge"
        device_type = "sdwan"
    elif device_type in ["sdrouting", "sd-routing"]:
        cml_node_type = "cat-sdwan-edge"
        device_type = "sdrouting"
    else:
        exit(
            f"Adding {device_type} is not supported by the script. "
            f'Supported options are: ["validator", "controller", "edge", "sdrouting"] or their plural forms'
        )

    # Verify if requested software version is defined in CML
    log.info("Checking software version...")
    image_definition = get_cml_sdwan_image_definition(
        cml, cml_node_type, software_version
    )

    major_software_release = int(software_version.split(".")[0])
    minor_software_release = int(software_version.split(".")[1])
    if cml_node_type in ["cat-sdwan-validator", "cat-sdwan-controller"]:
        if major_software_release <= 19 or (
            major_software_release == 20 and minor_software_release < 4
        ):
            sys.exit("Versions lower than 20.4 are not supported by the script.")
    elif device_type == "sdrouting":
        if major_software_release < 17 or (
            major_software_release == 17 and minor_software_release < 12
        ):
            sys.exit("Versions lower than 17.12 are not supported for sd-routing.")
    log.info("Software version OK")

    file_loader = FileSystemLoader(CML_DEPLOY_LAB_DEFINITION_DIR)
    env = Environment(loader=file_loader, trim_blocks=True)

    log.info("Logging in to SD-WAN Manager...")
    manager_session = create_manager_session(
        url=manager_ip,
        username=manager_user,
        password=manager_password,
        port=manager_port,
    )
    manager_config_settings = manager_session.endpoints.configuration_settings
    org_name = manager_config_settings.get_organizations()[0].org
    validator_fqdn = manager_config_settings.get_devices()[0].domain_ip

    ip_type = get_ip_type(manager_session)

    # Find the lab
    lab = cml.find_labs_by_title(lab_name)
    if lab:
        # If there are multiple labs with same name, we don't know which we should add devices too,
        # so we ask user to make sure the lab names are unique
        if len(lab) > 1:
            exit(
                f'There are multiple labs/topologies with name "{lab_name}". Please make sure '
                f"lab names are unique and rerun the add task."
            )
        else:
            lab = lab[0]

        # Find the transport nodes
        vpn0_switch = find_node_by_label(lab, ["VPN0", "VPN0-172.16.0.0/24"])
        inet_switch = find_node_by_label(lab, ["INET", "INET-172.16.1.0/24"])
        mpls_switch = find_node_by_label(lab, ["MPLS", "MPLS-172.16.2.0/24"])

        device_inventory = manager_session.endpoints.configuration_device_inventory
        if cml_node_type in ["cat-sdwan-validator", "cat-sdwan-controller"]:
            # Onboarding Control Components
            control_components = device_inventory.get_device_details("controllers")
            # Find all devices with this device type that are already onboarded
            # Check the last two digits of system-id. Find the higest one, and pick next one
            # This number will be used to generate system-ip and vpn0 ips
            biggest_num = max(
                [
                    int(device.system_ip.split(".")[3])
                    for device in control_components.filter(device_type=device_type)
                ]
            )
            if len(str(biggest_num)) > 2:
                # Shorten to two digits
                biggest_num = int(str(biggest_num)[1:3])
            # Find the position of the right most controller value
            next_node_x_position = max(
                [
                    node.x
                    for node in lab.nodes()
                    if node.node_definition
                    in [
                        "cat-sdwan-manager",
                        "cat-sdwan-validator",
                        "cat-sdwan-controller",
                    ]
                ]
            )
            next_node_y_position = max(
                [
                    node.y
                    for node in lab.nodes()
                    if node.node_definition
                    in [
                        "cat-sdwan-manager",
                        "cat-sdwan-validator",
                        "cat-sdwan-controller",
                    ]
                ]
            )

            new_nodes_nums = []
            for i in range(1, number_of_devices + 1):
                new_nodes_nums.append(f"{biggest_num + i:02d}")

            log.info("Adding nodes to topology...")
            new_nodes = []
            i = 0
            for next_num_str in new_nodes_nums:
                i += 1
                track_progress(
                    log, f"Adding nodes to topology ({i}/{len(new_nodes_nums)})..."
                )
                # Add device to CML topology, if user requested two devices, this loop runs twice
                # Prepere bootstrap configuration
                bootstrap_template = env.get_template(
                    f'{cml_node_type.split("-")[2]}-cloud-init.j2'
                )
                # note we give manager/controller/validator_num parameters, but depending on device_type,
                # only one will be used in the bootstrap_template
                bootstrap_config = bootstrap_template.render(
                    root_ca=ca_chain,
                    org_name=org_name,
                    validator_fqdn=validator_fqdn,
                    manager_num=next_num_str,
                    controller_num=next_num_str,
                    validator_num=next_num_str,
                    ip_type=ip_type,
                )
                label = f'{cml_node_type.split("-")[2].capitalize()}{next_num_str}'
                next_node_x_position += 120
                # Create a node in CML
                node = lab.create_node(
                    label=label,
                    node_definition=cml_node_type,
                    image_definition=image_definition,
                    configuration=bootstrap_config,
                    populate_interfaces=True,
                    x=next_node_x_position,
                    y=next_node_y_position,
                )
                # Need to wait few seconds as otherwise the next step might fail
                # as interfaces will not yet be added to the new node
                time.sleep(5)
                # Connect the control component to VPN 0 switch
                vpn0_switch_int = vpn0_switch.next_available_interface()
                node_int = None
                if cml_node_type == "cat-sdwan-controller":
                    node_int = node.get_interface_by_label("eth1")
                elif cml_node_type == "cat-sdwan-validator":
                    node_int = node.get_interface_by_label("ge0/0")
                lab.create_link(vpn0_switch_int, node_int)
                new_nodes.append(node)

            for node in new_nodes:
                node.start()

            # Wait until nodes are booted correctly and bootstrap is applied
            track_progress(log, "Waiting for nodes to boot...")
            lab.wait_until_lab_converged()

            # Before adding the control component, we will attempt to ping it
            # Need to prepare for ping, unpack SD-WAN Manager system-ip and create ping url
            control_components = device_inventory.get_device_details("controllers")
            manager_system_ip = control_components.filter(device_type="vmanage")[
                0
            ].system_ip

            vpn0_ip_prefix = {
                "vmanage": "172.16.0.",
                "vsmart": "172.16.0.1",
                "vbond": "172.16.0.2",
            }
            vpn0_ipv6_prefix = {
                "vmanage": "fc00:172:16:0::",
                "vsmart": "fc00:172:16::1",
                "vbond": "fc00:172:16::2",
            }
            if ip_type == "v6":
                nodes_vpn0_ips = [
                    vpn0_ipv6_prefix[device_type] + next_num_str
                    for next_num_str in new_nodes_nums
                ]
            else:
                nodes_vpn0_ips = [
                    vpn0_ip_prefix[device_type] + next_num_str
                    for next_num_str in new_nodes_nums
                ]
            with ThreadPoolExecutor() as executor:
                # Make sure you can ping the devices before attemping to add to the SD-WAN Manager
                ping_node_partial = partial(
                    ping_node,
                    manager_session=manager_session,
                    manager_system_ip=manager_system_ip,
                )
                list(executor.map(ping_node_partial, nodes_vpn0_ips))
                executor.shutdown(wait=True)

            new_control_components = {}
            for vpn0_ip in nodes_vpn0_ips:
                new_control_components[vpn0_ip] = cml_node_type.split("-")[2]
            # Onboard control components to SD-WAN Manager
            onboard_control_components(
                manager_session,
                manager_password,
                new_control_components,
                log,
            )

            if cml_node_type == "cat-sdwan-validator":
                # For Validator, we need to update the DNS mapping on gateway
                track_progress(
                    log, "Updating SD-WAN Validator FQDN entry on Gateway..."
                )
                if ip_type == "dual":
                    # Since nodes_vpn0_ips contains only IPv4 addresses,
                    # we need to add IPv6 addresses as well
                    nodes_vpn0_ipv6s = [
                        vpn0_ipv6_prefix[device_type] + next_num_str
                        for next_num_str in new_nodes_nums
                    ]
                    nodes_vpn0_ips.extend(nodes_vpn0_ipv6s)
                new_validator_ip_list = " ".join(nodes_vpn0_ips)
                cml_user = cml_config.username
                cml_password = cml_config.password
                lab.pyats.sync_testbed(cml_user, cml_password)
                gateway = lab.get_node_by_label("Gateway")
                current_dns_maps = gateway.run_pyats_command(
                    "sh run | in ip host"
                ).split("\r\n")
                new_dns_maps = [
                    f"{current_dns_map} {new_validator_ip_list}"
                    for current_dns_map in current_dns_maps
                ]
                for new_dns_map in new_dns_maps:
                    gateway.run_pyats_config_command(new_dns_map)
                gateway.run_pyats_command("write mem")

            if cml_node_type == "cat-sdwan-controller":
                attach_basic_controller_template(manager_session, ip_type, log)

        elif cml_node_type == "cat-sdwan-edge" and device_type == "sdwan":
            # Onboarding WAN Edges
            # Check SD-WAN Manager version
            manager_version = manager_session.api_version
            major_manager_release = manager_version.major
            minor_manager_release = manager_version.minor
            major_iosxe_release = int(software_version.split(".")[0])
            minor_iosxe_release = int(software_version.split(".")[1])
            if major_manager_release < 20 or major_iosxe_release < 17:
                exit(
                    f"Unsupported software combination: "
                    f"SD-WAN Manager: {manager_version}, WAN Edge: {software_version}"
                )
            if major_manager_release == 20 and minor_manager_release < 12:
                # Device will be onboarded using device template
                edge_config_type = 1
            elif major_iosxe_release == 17 and minor_iosxe_release < 12:
                # Device will be onboarded using device template
                edge_config_type = 1
            else:
                # Device will be onboarded using configuration groups
                edge_config_type = 2

            device_list = device_inventory.get_device_details("vedges")

            # Find all devices with this device type that are already onboarded
            # Check the last octet of system-id. Find the higest one, and pick next one
            # This number will be used to generate system-ip and vpn0 ips
            biggest_num = max(
                [
                    int(device.system_ip.split(".")[3])
                    for device in device_list
                    if device.system_ip
                ],
                default=0,
            )

            # Find the position of the right most control component value
            next_node_x_position = max(
                [node.x for node in lab.nodes() if node.y == 320], default=-400
            )

            free_uuids = [
                device.uuid
                for device in device_list.filter(
                    device_model="vedge-C8000V", cert_install_status=None
                )
            ]

            if number_of_devices > len(free_uuids):
                exit(
                    f"Cannot onboard {number_of_devices} WAN Edges as there are only "
                    f"{len(free_uuids)} unused UUIDs available."
                )

            new_nodes_nums = []
            for i in range(1, number_of_devices + 1):
                new_nodes_nums.append(f"{biggest_num + i}")

            if (
                edge_config_type == 1
            ):  # workaround for https://github.com/CiscoDevNet/sastre/issues/12 and .../13
                log.info("Attaching new routers to device template...")
                track_progress(log, "Attaching new routers to device template...")
                device_templates = manager_session.get(
                    "dataservice/template/device"
                ).json()["data"]
                # Find the template ID for basic template
                template_id = next(
                    (
                        dev_tmpl["templateId"]
                        for dev_tmpl in device_templates
                        if dev_tmpl["templateName"] == "edge_basic"
                    ),
                    None,
                )
                if not template_id:
                    sys.exit(
                        "edge_basic device template not found. "
                        "If you want to recreate it, please run "
                        "csdwan deploy <controller_version> --retry"
                    )
                attach_payload = {
                    "deviceTemplateList": [
                        {
                            "templateId": template_id,
                            "device": [],
                            "isEdited": False,
                            "isMasterEdited": False,
                        }
                    ]
                }
                increment_chassis = 0
                new_routers_uuids = {}
                for next_num_str in new_nodes_nums:
                    uuid = free_uuids[increment_chassis]
                    new_routers_uuids[next_num_str] = uuid
                    dhcp_exlude = f"192.168.{next_num_str}.1-192.168.{next_num_str}.99"
                    # For every SD-WAN Edge, create a payload to attach template
                    variables = {
                        "csv-status": "complete",
                        "csv-deviceId": uuid,
                        "csv-deviceIP": f"10.0.0.{next_num_str}",
                        "csv-host-name": f"Edge{next_num_str}",
                        "//system/host-name": f"Edge{next_num_str}",
                        "//system/system-ip": f"10.0.0.{next_num_str}",
                        "//system/site-id": next_num_str,
                        "csv-templateId": template_id,
                    }
                    if ip_type in ["v4", "dual"]:
                        variables["/0/GigabitEthernet1/interface/ip/address"] = (
                            f"172.16.1.{next_num_str}/24"
                        )
                        variables["/0/GigabitEthernet2/interface/ip/address"] = (
                            f"172.16.2.{next_num_str}/24"
                        )
                        variables["/1/GigabitEthernet3/interface/ip/address"] = (
                            f"192.168.{next_num_str}.1/24"
                        )
                        variables["/1/GigabitEthernet3//dhcp-server/address-pool"] = (
                            f"192.168.{next_num_str}.0/24"
                        )
                        variables["/1/GigabitEthernet3//dhcp-server/exclude"] = (
                            dhcp_exlude
                        )
                        variables[
                            "/1/GigabitEthernet3//dhcp-server/options/default-gateway"
                        ] = f"192.168.{next_num_str}.1"
                    if ip_type in ["v6", "dual"]:
                        variables["/0/GigabitEthernet1/interface/ipv6/address"] = (
                            f"fc00:172:16:1::{next_num_str}/64"
                        )
                        variables["/0/GigabitEthernet2/interface/ipv6/address"] = (
                            f"fc00:172:16:2::{next_num_str}/64"
                        )
                        variables["/1/GigabitEthernet3/interface/ipv6/address"] = (
                            f"fc00:192:168:{next_num_str}::1/64"
                        )
                    attach_payload["deviceTemplateList"][0]["device"].append(variables)
                    increment_chassis += 1

                task_id = manager_session.post(
                    "dataservice/template/device/config/attachfeature",
                    json=attach_payload,
                ).json()["id"]
                success_statuses = [
                    OperationStatus.SUCCESS,
                    OperationStatus.SUCCESS_SCHEDULED,
                ]
                success_statuses_ids = [
                    OperationStatusId.SUCCESS,
                    OperationStatusId.SUCCESS_SCHEDULED,
                ]
                Task(manager_session, task_id).wait_for_completed(
                    success_statuses=success_statuses,
                    success_statuses_ids=success_statuses_ids,
                )
            else:
                track_progress(log, "Attaching new routers to configuration group...")
                configuration_group = manager_session.endpoints.configuration_group
                # Find the configuration group ID for basic configuration group
                # Find the template ID for basic template
                config_group_id = next(
                    (
                        cfg_gr["id"]
                        for cfg_gr in manager_session.get(
                            "dataservice/v1/config-group"
                        ).json()
                        if cfg_gr["name"] == "edge_basic"
                    ),
                    None,
                )
                if not config_group_id:
                    sys.exit(
                        "edge_basic configuration group not found. "
                        "If you want to recreate it, please run "
                        "csdwan deploy <controller_version> --retry"
                    )

                increment_chassis = 0
                new_routers_uuids = {}
                associate_payload = ConfigGroupAssociatePayload(devices=[])
                devices_variables = []
                for next_num_str in new_nodes_nums:
                    # For every WAN Edge create a payload to attach template
                    uuid = free_uuids[increment_chassis]
                    new_routers_uuids[next_num_str] = uuid
                    associate_payload.devices.append(DeviceId(id=uuid))
                    variables = [
                        {
                            "name": "system_ip",
                            "value": f"10.0.0.{next_num_str}",
                        },
                        {"name": "host_name", "value": f"Edge{next_num_str}"},
                        {"name": "site_id", "value": int(next_num_str)},
                        {"name": "pseudo_commit_timer", "value": 300},
                        {"name": "ipv6_strict_control", "value": False},
                        {"name": "aaa_password", "value": "admin"},
                    ]
                    if ip_type in ["v4", "dual"]:
                        variables.append(
                            {
                                "name": "vpn1_gi3_lan_ip",
                                "value": f"192.168.{next_num_str}.1",
                            }
                        )
                        variables.append(
                            {
                                "name": "vpn1_gi3_dhcp_network",
                                "value": f"192.168.{next_num_str}.0",
                            }
                        )
                        variables.append(
                            {
                                "name": "vpn1_gi3_dhcp_address_exclude",
                                "value": [f"192.168.{next_num_str}.1"],
                            }
                        )
                        variables.append(
                            {
                                "name": "vpn1_gi3_dhcp_default_gateway",
                                "value": f"192.168.{next_num_str}.1",
                            }
                        )
                        variables.append(
                            {
                                "name": "vpn0_gi1_inet_ip",
                                "value": f"172.16.1.{next_num_str}",
                            }
                        )
                        variables.append(
                            {
                                "name": "vpn0_gi2_mpls_ip",
                                "value": f"172.16.2.{next_num_str}",
                            }
                        )
                    if ip_type in ["v6", "dual"]:
                        variables.append(
                            {
                                "name": "vpn1_gi3_lan_ipv6",
                                "value": f"fc00:192:168:{next_num_str}::1/64",
                            }
                        )
                        variables.append(
                            {
                                "name": "vpn0_gi1_inet_ipv6",
                                "value": f"fc00:172:16:1::{next_num_str}/64",
                            }
                        )
                        variables.append(
                            {
                                "name": "vpn0_gi2_mpls_ipv6",
                                "value": f"fc00:172:16:2::{next_num_str}/64",
                            }
                        )
                    device_variables = {
                        "device-id": uuid,
                        "variables": variables,
                    }
                    devices_variables.append(device_variables)
                    increment_chassis += 1

                variables_payload = {"solution": "sdwan", "devices": devices_variables}
                # Associate devices with config group
                configuration_group.associate(config_group_id, associate_payload)
                # Fill variables
                manager_session.put(
                    f"dataservice/v1/config-group/{config_group_id}/device/variables",
                    json.dumps(variables_payload),
                )
                # Deploy configuration group
                task_id = configuration_group.deploy(
                    config_group_id, associate_payload
                ).parentTaskId
                success_statuses = [
                    OperationStatus.SUCCESS,
                    OperationStatus.SUCCESS_SCHEDULED,
                ]
                success_statuses_ids = [
                    OperationStatusId.SUCCESS,
                    OperationStatusId.SUCCESS_SCHEDULED,
                ]
                Task(manager_session, task_id).wait_for_completed(
                    success_statuses=success_statuses,
                    success_statuses_ids=success_statuses_ids,
                )

            track_progress(log, "Preparing bootstrap configuration for new routers...")
            bootstrap_configs = {}
            for next_num_str, uuid in new_routers_uuids.items():
                # Generate bootstrap configuration
                bootstrap_configs[next_num_str] = (
                    device_inventory.generate_bootstrap_configuration(
                        uuid
                    ).bootstrap_config
                )
            log.info("Adding devices to topology...")
            new_nodes = []
            i = 0
            for next_num_str in new_nodes_nums:
                i += 1
                track_progress(
                    log, f"Adding nodes to topology ({i}/{len(new_nodes_nums)})..."
                )
                next_node_x_position += 120
                node = lab.create_node(
                    label=f"Edge{next_num_str}",
                    node_definition=cml_node_type,
                    image_definition=image_definition,
                    configuration=bootstrap_configs[next_num_str],
                    populate_interfaces=True,
                    x=next_node_x_position,
                    y=400,
                )
                # Need to wait few seconds as otherwise the next step might fail
                # as interfaces will not yet be added to the new node
                time.sleep(5)
                # Connect the Edge to INET and MPLS switches
                inet_switch_int = inet_switch.next_available_interface()
                mpls_switch_int = mpls_switch.next_available_interface()

                inet_edge_int = node.get_interface_by_label("GigabitEthernet1")
                mpls_edge_int = node.get_interface_by_label("GigabitEthernet2")
                lab.create_link(inet_switch_int, inet_edge_int)
                lab.create_link(mpls_switch_int, mpls_edge_int)
                new_nodes.append(node)

            for node in new_nodes:
                node.start()

            # Wait until Edges are onboarded.
            wan_edges_to_onboard = list(new_routers_uuids.values())
            wait_for_wan_edge_onboaring(manager_session, wan_edges_to_onboard, log)

        elif cml_node_type == "cat-sdwan-edge" and device_type == "sdrouting":
            # Onboarding WAN Edges
            # Check SD-WAN Manager version
            manager_version = manager_session.api_version
            major_manager_release = manager_version.major
            minor_manager_release = manager_version.minor
            if major_manager_release < 20 or (
                major_manager_release == 20 and minor_manager_release < 12
            ):
                sys.exit(
                    "SD-WAN Manager versions lower than 20.12 do not support sd-routing."
                )

            device_list = device_inventory.get_device_details("vedges")
            # Find all devices with this device type that are already onboarded
            # Check the last octet of system-id. Find the higest one, and pick next one
            # This number will be used to generate system-ip and vpn0 ips
            biggest_num = max(
                [
                    int(device.system_ip.split(".")[3])
                    for device in device_list
                    if device.system_ip
                ],
                default=0,
            )

            # Find the position of the right most controller value
            next_node_x_position = max(
                [node.x for node in lab.nodes() if node.y == 320], default=-400
            )

            free_uuids = [
                device.uuid
                for device in device_list.filter(
                    device_model="vedge-C8000V-SD-ROUTING", cert_install_status=None
                )
            ]

            if number_of_devices > len(free_uuids):
                exit(
                    f"Cannot onboard {number_of_devices} WAN Edges as there are only "
                    f"{len(free_uuids)} unused UUIDs available."
                )

            uuid_to_token = {
                device.uuid: device.serial_number
                for device in device_list.filter(
                    device_model="vedge-C8000V-SD-ROUTING", cert_install_status=None
                )
            }

            new_nodes_nums = []
            for i in range(1, number_of_devices + 1):
                new_nodes_nums.append(f"{biggest_num + i}")

            track_progress(log, "Preparing bootstrap configuration for new routers...")
            increment_chassis = 0
            new_routers_uuids = {}
            bootstrap_configs = {}
            for next_num_str in new_nodes_nums:
                # For every WAN Edge assign uuid and create bootstrap
                uuid = free_uuids[increment_chassis]
                token = uuid_to_token[uuid]
                new_routers_uuids[next_num_str] = uuid

                bootstrap_template = env.get_template("sdrouting-cloud-init.j2")
                bootstrap_config = bootstrap_template.render(
                    root_ca=ca_chain,
                    org_name=org_name,
                    validator_fqdn=validator_fqdn,
                    next_num_str=next_num_str,
                    uuid=uuid,
                    token=token,
                    ip_type=ip_type,
                )
                bootstrap_configs[next_num_str] = bootstrap_config
                increment_chassis += 1

            log.info("Adding nodes to topology...")
            new_nodes = []
            i = 0
            for next_num_str in new_nodes_nums:
                i += 1
                track_progress(
                    log, f"Adding nodes to topology ({i}/{len(new_nodes_nums)})..."
                )
                next_node_x_position += 120
                node = lab.create_node(
                    label=f"SD-Edge{next_num_str}",
                    node_definition=cml_node_type,
                    image_definition=f"{cml_node_type}-{software_version}",
                    configuration=bootstrap_configs[next_num_str],
                    populate_interfaces=True,
                    x=next_node_x_position,
                    y=400,
                )
                # Need to wait few seconds as otherwise the next step might fail
                # as interfaces will not yet be added to the new node
                time.sleep(5)
                # Connect the Edge to INET and MPLS switches
                inet_switch_int = inet_switch.next_available_interface()

                inet_edge_int = node.get_interface_by_label("GigabitEthernet1")
                lab.create_link(inet_switch_int, inet_edge_int)
                new_nodes.append(node)

            for node in new_nodes:
                node.start()
            # Wait until Edges are onboarded.
            wan_edges_to_onboard = list(new_routers_uuids.values())
            wait_for_wan_edge_onboaring(manager_session, wan_edges_to_onboard, log)

        track_progress(log, "Add task done\n")
        if cml_node_type == "cat-sdwan-validator":
            print(
                "The validator is now spun up, it takes 5-10 minutes, "
                "before it shows up in the Manager due to DNS and cert updates."
            )

    else:
        exit(f"Could not find a lab with name {lab_name}.")

    manager_session.close()
    end_time = datetime.datetime.now()
    time_it = end_time - begin_time
    log.info("Time needed for task: " + str(time_it))

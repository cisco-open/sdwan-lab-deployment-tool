# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd

import datetime
import json
import os
import re
import sys
from os.path import join
from typing import Any, Dict, List, Union

import unicon.core.errors
from catalystwan.session import create_manager_session
from cisco_sdwan.base.rest_api import Rest
from cisco_sdwan.tasks.implementation import BackupArgs, TaskBackup
from jinja2 import Environment, FileSystemLoader
from ruamel.yaml import YAML
from virl2_client import ClientLibrary
from virl2_client.models.cl_pyats import ClPyats

from .utils import (
    CML_BACKUP_LAB_DEFINITION_DIR,
    load_certificate_details,
    setup_logging,
    track_progress,
)


def validate_credentials(pylab: ClPyats, node_label: str) -> bool:
    try:
        pylab.run_command(node_label, "show version")
        return True
    except unicon.core.errors.ConnectionError as conn_err:
        current_exception = conn_err
        # Loop to inspect the chain of causes
        while current_exception:
            if isinstance(
                current_exception,
                unicon.core.errors.UniconAuthenticationError,
            ) or isinstance(
                current_exception,
                unicon.core.errors.CredentialsExhaustedError,
            ):
                return False
            # Move to the next cause in the chain
            current_exception = getattr(current_exception, "__cause__", None)
        raise


def check_pyats_device_connectivity(
    pylab: ClPyats,
    node_label: str,
    node_definition: str,
    manager_user: str,
    manager_password: str,
) -> List[Union[str, ClPyats]]:
    personality = None
    node_type = None
    if node_definition == "cat-sdwan-manager":
        node_type = "manager"
        pylab._testbed.devices[node_label].credentials.default.username = manager_user
        pylab._testbed.devices[node_label].credentials.default.password = (
            manager_password
        )
        personality = (
            "    <personality>vmanage</personality>\n"
            "    <device-model>vmanage</device-model>"
        )
    elif node_definition == "cat-sdwan-controller":
        node_type = "controller"
        pylab._testbed.devices[node_label].credentials.default.username = "admin"
        pylab._testbed.devices[node_label].credentials.default.password = "admin"
        personality = (
            "    <personality>vsmart</personality>\n"
            "    <device-model>vsmart</device-model>"
        )
    elif node_definition == "cat-sdwan-validator":
        node_type = "validator"
        pylab._testbed.devices[node_label].credentials.default.username = "admin"
        pylab._testbed.devices[node_label].credentials.default.password = "admin"
        personality = (
            "    <personality>vedge</personality>\n"
            "    <device-model>vedge-cloud</device-model>"
        )
    elif node_definition == "cat-sdwan-edge":
        node_type = "sdwan"
        pylab._testbed.devices[node_label].credentials.default.username = "admin"
        pylab._testbed.devices[node_label].credentials.default.password = "admin"
        personality = ""

    try:
        if validate_credentials(pylab, node_label):
            # If credentials are valid, proceed
            return [personality, node_type, pylab]
        else:
            # If default credentials are not valid, try admin user and SD-WAN Manager password
            pylab._testbed.devices[node_label].credentials.default.password = (
                manager_password
            )
            if validate_credentials(pylab, node_label):
                # If credentials are valid, proceed
                return [personality, node_type, pylab]
            else:
                exit(
                    f"Could not login to {node_label} using admin username and default or SD-WAN Manager password."
                    f"Please fix admin user password and rerun the script."
                )
    except unicon.core.errors.ConnectionError as conn_err:
        current_exception = conn_err
        # Loop to inspect the chain of causes
        while current_exception:
            if (
                isinstance(
                    current_exception,
                    unicon.core.errors.SubCommandFailure,
                )
                and node_definition == "cat-sdwan-edge"
            ):
                # show sdwan commands don't work so this device is in autonomous mode
                # need to change pyats platform from "sdwan" to "csr1000v"
                pylab._testbed.devices[node_label].destroy()
                pylab._testbed.devices[node_label].platform = "csr1000v"
                node_type = "sdrouting"
                if validate_credentials(pylab, node_label):
                    return [personality, node_type, pylab]
                else:
                    exit(
                        f"Could not login to {node_label} using default credentials or Manager credentials."
                    )
            # Move to the next cause in the chain
            current_exception = getattr(current_exception, "__cause__", None)
        raise


def update_associated_parcel(
    api: Rest, path: str, parcel: Dict[str, Any]
) -> Dict[str, Any]:
    uuid_pattern = (
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )
    match = re.search(rf"(?<={uuid_pattern}/).*?(?=/{uuid_pattern})", path)
    parcel_type = parcel["parcelType"]
    if match and f"{match[0]}/" in parcel_type:
        parcel_type = re.sub(rf"{match[0]}/", "", parcel_type)

    new_path = join(path, parcel_type, parcel["parcelId"])
    parcel["payload"]["data"] = api.get(new_path)["payload"]["data"]
    for subparcel in parcel.get("subparcels", []):
        update_associated_parcel(api, new_path, subparcel)

    return parcel


def main(
    cml: ClientLibrary,
    cml_user: str,
    cml_password: str,
    manager_ip: str,
    manager_user: str,
    manager_password: str,
    lab_name: str,
    workdir: str,
    loglevel: Union[int, str],
) -> None:
    # Time the script execution
    begin_time = datetime.datetime.now()

    # Setup logging
    log = setup_logging(loglevel)

    track_progress(log, "Preparing for backup...")

    # Setup YAML and file loader
    yaml = YAML(typ="rt")
    file_loader = FileSystemLoader(CML_BACKUP_LAB_DEFINITION_DIR)
    env = Environment(loader=file_loader, trim_blocks=True)

    # Check if workdir folder already exists. If yes, then stop backup process
    workdir = join(os.path.abspath(os.getcwd()), workdir)
    if os.path.isdir(workdir):
        exit(
            f"{workdir} folder is already in use. Please select a different workdir directory."
        )

    # Prepare the CA
    ca_cert, ca_key, ca_chain = load_certificate_details()

    # Login to SD-WAN Manager
    log.info("Logging in to SD-WAN Manager...")
    manager_session = create_manager_session(
        url=manager_ip, username=manager_user, password=manager_password
    )
    manager_config_settings = manager_session.endpoints.configuration_settings
    org_name = manager_config_settings.get_organizations()[0].org
    validator_fqdn = manager_config_settings.get_devices()[0].domain_ip

    # Download all the node definitions and create a list with nodes that support configuration extract
    node_types_supporting_config_extract = []
    for node in cml.definitions.node_definitions():
        if "pyats" in list(node.keys()) and "config_extract_command" in list(
            node["pyats"].keys()
        ):
            # If Pyats is enabled in node definition and config extract command is available
            # we assume this node supports config extract
            node_types_supporting_config_extract.append(node["id"])

    # Find the lab
    lab = cml.find_labs_by_title(lab_name)
    if lab:
        # If there are multiple labs with same name, we don't know which we should back up,
        # so we ask user to make sure the lab names are unique
        if len(lab) > 1:
            exit(
                f'There are multiple labs/topologies with name "{lab_name}". Please make sure '
                f"lab names are unique and rerun the backup task."
            )
        lab = lab[0]
        if lab.is_active():
            # For each running lab, create backup
            # All data will be stored in backup data structure.
            custom_node_backup = {}

            pylab = ClPyats(lab)
            pylab.sync_testbed(cml_user, cml_password)
            all_nodes = lab.nodes()
            i = 0
            for node in all_nodes:
                i += 1
                if node.is_active() is False:
                    # To back up a node, it needs to be booted and active
                    log.info(
                        f"Cannot backup configuration for node {node.label} as it is not active."
                    )
                    continue
                # If the node is an SD-WAN node, we need to do some special steps to back up the configuration as CML
                # doesn't support config extract from SD-WAN nodes natively
                if node.node_definition in [
                    "cat-sdwan-manager",
                    "cat-sdwan-validator",
                    "cat-sdwan-controller",
                ]:
                    log.info(f"Creating backup of {node.label} node...")
                    track_progress(
                        log,
                        f"Creating backup of {node.label} node ({i}/{len(all_nodes)})...",
                    )
                    [personality, node_type, pylab] = check_pyats_device_connectivity(
                        pylab,
                        node.label,
                        node.node_definition,
                        manager_user,
                        manager_password,
                    )

                    software_version = pylab.run_command(node.label, "show version")
                    major_software_release = int(software_version.split(".")[0])
                    minor_software_release = int(software_version.split(".")[1])
                    if major_software_release <= 19 or (
                        major_software_release == 20 and minor_software_release < 4
                    ):
                        sys.exit(
                            "Versions lower than 20.4 are not supported by the script."
                        )

                    running_config = pylab.run_command(
                        node.label, "show run | display xml"
                    )
                    # Adding personality to the config, otherwise it won't properly boot in restore task
                    running_config = re.sub(
                        '(<system xmlns="http://viptela.com/system">)',
                        rf"\1\n{personality}",
                        running_config,
                    )

                    config_template = env.get_template(f"{node_type}-cloud-init.j2")
                    config = config_template.render(
                        org_name=org_name,
                        validator_fqdn=validator_fqdn,
                        config=running_config,
                        root_ca=ca_chain,
                    )
                    custom_node_backup[node.label] = config
                elif node.node_definition == "cat-sdwan-edge":
                    log.info(f"Creating backup of {node.label} node...")
                    track_progress(
                        log,
                        f"Creating backup of {node.label} node ({i}/{len(all_nodes)})...",
                    )
                    [_, node_type, pylab] = check_pyats_device_connectivity(
                        pylab,
                        node.label,
                        node.node_definition,
                        manager_user,
                        manager_password,
                    )
                    if node_type == "sdwan":
                        # Backup SD-WAN Device
                        running_config = pylab.run_command(node.label, "show sdwan run")
                        # Remove all logs that might appear before system command
                        running_config = re.sub(
                            r"^.*?(?=system[\r\n])", "", running_config, flags=re.DOTALL
                        )
                        config_template = env.get_template("edge-cloud-init.j2")
                        serial_output = pylab.run_command(
                            node.label, "show sdwan certificate serial"
                        )
                    else:
                        # Backup SD-Routing Device
                        # From extracted config need to remove "crypto pki certificate", "license udi" commands.
                        # If they are present in the boostrap, the cloud-init will fail during restore
                        running_config = pylab.run_command(node.label, "show run")
                        running_config = re.sub(
                            r"\ncrypto pki[\s\S]+?!", "!", running_config
                        )
                        running_config = re.sub(
                            r"\nlicense udi[\s\S]+?\n",
                            "",
                            running_config,
                            flags=re.DOTALL | re.MULTILINE,
                        )
                        config_template = env.get_template("sdrouting-cloud-init.j2")
                        serial_output = pylab.run_command(
                            node.label, "show sd-routing certificate serial"
                        )

                    uuid_search = re.search(
                        r"Chassis\snumber:\s([\w-]+)\s", serial_output
                    )
                    if uuid_search:
                        uuid = uuid_search.group(1)
                        config = config_template.render(
                            validator_fqdn=validator_fqdn,
                            org_name=org_name,
                            config=running_config,
                            root_ca=ca_chain,
                            uuid=uuid,
                        )
                        custom_node_backup[node.label] = config
                    else:
                        exit(f"Cloud not find UUID for device {node.label}")
                elif node.node_definition in node_types_supporting_config_extract:
                    log.info(f"Creating backup of {node.label} node ...")
                    track_progress(
                        log,
                        f"Creating backup of {node.label} node ({i}/{len(all_nodes)})...",
                    )
                    node.extract_configuration()
                elif node.label not in [
                    "VPN0-172.16.0.0/24",
                    "INET-172.16.1.0/24",
                    "MPLS-172.16.2.0/24",
                    "Internet",
                    "External",
                ]:
                    log.info(
                        f"Node {node.label} does not support configuration extract."
                    )

            lab_extract = yaml.load(lab.download())

            for i in range(len(lab_extract["nodes"])):
                if lab_extract["nodes"][i]["node_definition"] in [
                    "cat-sdwan-manager",
                    "cat-sdwan-validator",
                    "cat-sdwan-controller",
                    "cat-sdwan-edge",
                ]:
                    lab_extract["nodes"][i]["configuration"] = custom_node_backup[
                        lab_extract["nodes"][i]["label"]
                    ]

            os.mkdir(workdir)
            with open(rf"{workdir}/cml_topology.yaml", "w") as file:
                yaml.dump(lab_extract, file)

            track_progress(
                log, "Creating SD-WAN Manager templates/configuration groups backup..."
            )
            task_args = BackupArgs(
                save_running=False,
                no_rollover=True,
                workdir=f"{workdir}/manager_configs",
                tags=["all"],
            )

            with Rest(
                base_url=f"https://{manager_ip}",
                username=manager_user,
                password=manager_password,
            ) as api:
                task = TaskBackup()
                task_output = task.runner(task_args, api)

                if task_output:
                    print("\n\n".join(str(entry) for entry in task_output))

                task.log_info(
                    f'Task completed {task.outcome("successfully", "with caveats: {tally}")}'
                )

                feature_profiles_dir = join(
                    workdir, "manager_configs", "feature_profiles"
                )
                if os.path.isdir(join(workdir, "manager_configs", "feature_profiles")):
                    # Workaround sastre bug https://github.com/CiscoDevNet/sastre/issues/12
                    for root, dirs, files in os.walk(feature_profiles_dir):
                        for filename in files:
                            filepath = join(root, filename)
                            with open(filepath, "r", encoding="utf-8") as file:
                                profile = json.load(file)
                            path = join(
                                "/v1/feature-profile/",
                                profile["solution"],
                                profile["profileType"],
                                profile["profileId"],
                            )
                            for parcel in profile["associatedProfileParcels"]:
                                update_associated_parcel(api, path, parcel)
                            with open(filepath, "w") as file:
                                json.dump(profile, file, indent=2)

                # Check SD-WAN Manager version
                manager_version = api.server_version
                major_manager_release = int(manager_version.split(".")[0])
                minor_manager_release = int(manager_version.split(".")[1])
                if (
                    major_manager_release < 20
                    or major_manager_release == 20
                    and minor_manager_release < 7
                ):
                    # Skip network hierarchy backup
                    pass
                else:
                    # noinspection PyTypeChecker
                    network_hierarchy: List[Dict[str, Any]] = api.get(
                        "v1/network-hierarchy"
                    )
                    mrf_regions = [
                        region
                        for region in network_hierarchy
                        if region["data"]["label"] in ["REGION"]
                    ]
                    mrf_subregions = [
                        region
                        for region in network_hierarchy
                        if region["data"]["label"] == "SUB_REGION"
                    ]
                    if mrf_regions:
                        track_progress(
                            log, "Creating MRF regions and subregions backup..."
                        )
                        os.makedirs(f"{workdir}/manager_configs/mrf", exist_ok=True)
                        os.makedirs(
                            f"{workdir}/manager_configs/mrf/regions", exist_ok=True
                        )
                        for mrf_region in mrf_regions:
                            if mrf_region["data"]["hierarchyId"]["regionId"] != 0:
                                region_data = {
                                    "name": mrf_region["name"],
                                    "uuid": mrf_region["uuid"],
                                    "data": {
                                        "parentUuid": mrf_region["data"]["parentUuid"],
                                        "label": mrf_region["data"]["label"],
                                        "hierarchyId": {
                                            "regionId": mrf_region["data"][
                                                "hierarchyId"
                                            ]["regionId"]
                                        },
                                    },
                                }
                                if "description" in mrf_region.keys():
                                    region_data["description"] = mrf_region[
                                        "description"
                                    ]
                                if "isSecondary" in mrf_region["data"].keys():
                                    region_data["data"]["isSecondary"] = mrf_region[
                                        "data"
                                    ]["isSecondary"]
                                with open(
                                    f'{workdir}/manager_configs/mrf/regions/{mrf_region["name"]}.json',
                                    "w",
                                ) as f:
                                    json.dump(region_data, f, indent=2)
                        if mrf_subregions:
                            os.makedirs(
                                f"{workdir}/manager_configs/mrf/subregions",
                                exist_ok=True,
                            )
                            for mrf_subregion in mrf_subregions:
                                subregion_data = {
                                    "name": mrf_subregion["name"],
                                    "uuid": mrf_subregion["uuid"],
                                    "data": {
                                        "parentUuid": mrf_subregion["data"][
                                            "parentUuid"
                                        ],
                                        "label": mrf_subregion["data"]["label"],
                                        "hierarchyId": {
                                            "subRegionId": mrf_subregion["data"][
                                                "hierarchyId"
                                            ]["subRegionId"]
                                        },
                                    },
                                }
                                if "description" in mrf_subregion.keys():
                                    subregion_data["description"] = mrf_subregion[
                                        "description"
                                    ]
                                with open(
                                    f'{workdir}/manager_configs/mrf/subregions/{mrf_subregion["name"]}.json',
                                    "w",
                                ) as f:
                                    json.dump(subregion_data, f, indent=2)

        track_progress(log, "Backup task done\n")
        end_time = datetime.datetime.now()
        time_it = end_time - begin_time
        log.info("Time needed for task: " + str(time_it))

    else:
        exit("Could not find a lab with specified name.")

    manager_session.close()

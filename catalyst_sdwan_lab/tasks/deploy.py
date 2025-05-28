# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd

import datetime
import re
from os.path import join
from typing import Union

from catalystwan.endpoints.configuration_device_inventory import SerialFilePayload
from jinja2 import Environment, FileSystemLoader
from passlib.hash import sha512_crypt
from virl2_client import ClientConfig

from .utils import (
    CML_DEPLOY_LAB_DEFINITION_DIR,
    DATA_DIR,
    MANAGER_CONFIGS_DIR,
    ORG_NAME,
    VALIDATOR_FQDN,
    attach_basic_controller_template,
    check_manager_ip_is_free,
    configure_manager_basic_settings,
    get_cml_sdwan_image_definition,
    get_sdwan_lab_parameters,
    load_certificate_details,
    onboard_control_components,
    restore_manager_configuration,
    setup_logging,
    track_progress,
    verify_cml_version,
    wait_for_manager_session,
)


def main(
    cml_config: ClientConfig,
    manager_ip: str,
    manager_port: int,
    manager_mask: str,
    manager_gateway: str,
    manager_user: str,
    manager_password: str,
    software_version: str,
    lab_name: str,
    bridge: str,
    dns_server: str,
    patty_used: bool,
    retry: bool,
    loglevel: Union[int, str],
) -> None:
    # Time the script execution
    begin_time = datetime.datetime.now()

    # Setup logging
    log = setup_logging(loglevel)

    # Verify if the SD-WAN Manager password is not using default credentials
    if manager_password == "admin":
        print(
            "Cannot use default credentials. Please update SD-WAN Manager password and run the tool again."
        )
        exit(1)

    # create cml instance and check version
    cml = cml_config.make_client()
    verify_cml_version(cml)

    # Verify if requested software version is defined in CML
    track_progress(log, "Preparing the lab...")

    # Verify if requested SD-WAN Manager/Controller/Validator version is present in CML
    log.info("Checking software version...")
    manager_image = get_cml_sdwan_image_definition(
        cml, "cat-sdwan-manager", software_version
    )
    controller_image = get_cml_sdwan_image_definition(
        cml, "cat-sdwan-controller", software_version
    )
    validator_image = get_cml_sdwan_image_definition(
        cml, "cat-sdwan-validator", software_version
    )
    log.info("Software version OK")

    # Choose the parameters
    # This will determine what devices are included in serial file (SD-WAN/SD-Routing)
    # and what configuration method will be used for Edges (device templates vs config groups)
    serial_file_version, config_version = get_sdwan_lab_parameters(software_version)

    # Prepare the CA for controllers certificate signing
    ca_cert, ca_key, ca_chain = load_certificate_details()

    if not lab_name:
        # User didn't provide lab name, generate by default
        # Find existing sdwan labs names
        lab_list_search = [
            re.search(r"sdwan(\d+)", lab.title)
            for lab in cml.all_labs(show_all=True)
            if lab.title.startswith("sdwan")
        ]
        lab_list = [int(lab.group(1)) for lab in lab_list_search if lab]
        if lab_list:
            # New lab name is 1 number higher than higest existing lab name
            lab_name = f"sdwan{max(lab_list) + 1}"
        else:
            # If there are no vsdwan labs, this will be first vsdwan lab
            lab_name = "sdwan1"

    file_loader = FileSystemLoader(CML_DEPLOY_LAB_DEFINITION_DIR)
    env = Environment(loader=file_loader, trim_blocks=True)

    cml_tp_tmpl = env.get_template("cml-base-topology.j2")

    # Encrypt SD-WAN Manager password to SHA512. The encrypted password will be used in bootstrap configuration.
    encrypted_manager_password = sha512_crypt.encrypt(manager_password, rounds=5000)

    if patty_used:
        # PATty should be used for SD-WAN Manager reachability. This means bridge configuration needs to be set to NAT
        bridge = "NAT"

    cml_topology = cml_tp_tmpl.render(
        title=lab_name,
        manager_image=manager_image,
        controller_image=controller_image,
        validator_image=validator_image,
        root_ca=ca_chain,
        org_name=ORG_NAME,
        validator_fqdn=VALIDATOR_FQDN,
        manager_num="1",
        controller_num="01",
        validator_num="01",
        manager_user=manager_user,
        manager_pass=encrypted_manager_password,
        manager_external_ip=manager_ip,
        external_subnet_mask=manager_mask,
        external_gateway=manager_gateway,
        bridge=bridge,
        dns_server=dns_server,
        manager_port=manager_port,
        patty_used=patty_used,
    )

    if retry:
        # If retry flag is set, skip the lab bringup and move directly to SD-WAN Manager steps
        track_progress(log, "Retry flag set, checking if lab already exists in CML...")
        lab_notes = [lab.notes for lab in cml.all_labs(show_all=True)]
        lab_present = any(manager_ip in note for note in lab_notes)
        if not lab_present:
            exit(
                "\nRetry option is set, but script cloud not find the "
                "lab with specified SD-WAN Manager IP."
            )
    else:
        # Prepare and deploy the lab to CML
        # Verify lab name is not duplicated
        # Although CML allows labs with same name,
        # this crete confusion for other tasks where lab name is used
        existing_lab_names = [lab.title for lab in cml.all_labs(show_all=True)]
        if lab_name in existing_lab_names:
            exit(
                f"Lab with name '{lab_name}' already exists. "
                f"Please provide a different name to avoid confusion."
            )

        if not patty_used:
            # If PATty is not used, check if the IP allocated for SD-WAN Manager is not already it use.
            check_manager_ip_is_free(manager_ip)
        log.info("Importing the lab...")
        lab = cml.import_lab(cml_topology, lab_name)
        track_progress(log, "Waiting for nodes to boot...")
        lab.start()
    cml.logout()

    # Wait for SD-WAN Manager API to be available
    manager_session = wait_for_manager_session(
        manager_ip, manager_port, manager_user, manager_password, log
    )
    # Configure basic settings like org-name, validator fqdn etc.
    configure_manager_basic_settings(manager_session, ca_chain, log)

    # Add controllers to SD-WAN Manager and sing certificates
    onboard_control_components(
        manager_session,
        manager_password,
        {"172.16.0.201": "validator", "172.16.0.101": "controller"},
        log,
    )

    track_progress(log, "Uploading Serial File...")
    serial_file = SerialFilePayload(
        join(DATA_DIR, f"serial_files/serialFile-v{str(serial_file_version)}.viptela"),
        "valid",
    )
    manager_session.endpoints.configuration_device_inventory.upload_wan_edge_list(
        serial_file
    )

    if config_version == 1:
        track_progress(log, "Creating basic device templates...")
    else:
        track_progress(
            log, "Creating basic device templates and configuration groups..."
        )

    restore_manager_configuration(
        manager_session,
        manager_ip,
        manager_port,
        manager_user,
        manager_password,
        config_version,
        join(MANAGER_CONFIGS_DIR, f"v{config_version}"),
        False,
    )

    # If device is SD-WAN Controller we should attach it to the SD-WAN Controller device template
    attach_basic_controller_template(manager_session, log)

    manager_session.close()
    track_progress(log, "Deploy task done\n")

    print(
        f"#############################################\n"
        f"Lab is deployed.\n"
        f"CML URL: https://{cml_config.url}\n"
        f"SD-WAN Manager URL: https://{manager_ip}:{manager_port}\n"
        f"Use the username/password set with the script for CML and SD-WAN Manager login.\n"
        f"All other nodes use default username/password.\n"
        f"#############################################"
    )
    end_time = datetime.datetime.now()
    time_it = end_time - begin_time
    log.info("Time needed for lab deployment: " + str(time_it))

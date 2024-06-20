# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd

import logging
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from logging import Logger
from os.path import abspath, dirname, exists, join
from typing import Dict, List, Union

from catalystwan.api.task_status_api import OperationStatus, OperationStatusId, Task
from catalystwan.endpoints.certificate_management_device import TargetDevice
from catalystwan.endpoints.configuration_device_inventory import (
    DeviceCreationPayload,
    DeviceDetailsResponse,
)
from catalystwan.endpoints.configuration_settings import (
    Certificate,
    CloudX,
    Device,
    Organization,
    VManageDataStream,
)
from catalystwan.exceptions import ManagerHTTPError, ManagerRequestException
from catalystwan.session import ManagerSession, create_manager_session
from catalystwan.vmanage_auth import UnauthorizedAccessError
from cisco_sdwan.base.rest_api import Rest
from cisco_sdwan.tasks.implementation import RestoreArgs, TaskRestore
from OpenSSL import crypto
from requests.exceptions import ConnectionError
from virl2_client import ClientLibrary

# Base directory where utils.py is located
BASE_DIR = dirname(dirname(abspath(__file__)))
DATA_DIR = join(BASE_DIR, "data")
CERTS_DIR = join(DATA_DIR, "certs")
CML_BACKUP_LAB_DEFINITION_DIR = join(DATA_DIR, "cml_lab_definition", "backup")
CML_DEPLOY_LAB_DEFINITION_DIR = join(DATA_DIR, "cml_lab_definition", "deploy")
CML_NODES_DEFINITION_DIR = join(DATA_DIR, "cml_nodes_definition")
MANAGER_CONFIGS_DIR = join(DATA_DIR, "manager_configs")
ORG_NAME = "cml-sdwan-lab-tool"
SOFTWARE_IMAGES_DIR = os.getcwd()
VALIDATOR_FQDN = "validator.sdwan.local"


def attach_basic_controller_template(
    manager_session: ManagerSession, log: Logger
) -> None:
    """
    Attach basic template to all SD-WAN controllers that have no template yet
    """
    track_progress(log, "Attaching SD-WAN Controller to device template...")
    device_templates = manager_session.get("dataservice/template/device").json()["data"]
    # Find the template ID for basic template
    template_id = next(
        dev_tmpl["templateId"]
        for dev_tmpl in device_templates
        if dev_tmpl["templateName"] == "controller_basic"
    )

    # Create dict with new SD-WAN Controllers that have no template attached
    # {uuid}: {last digit of IP}
    device_inventory = manager_session.endpoints.configuration_device_inventory
    control_components = device_inventory.get_device_details("controllers")
    new_controllers_uuids = {
        device.uuid: device.device_ip.split(".")[3]
        for device in control_components
        if device.device_type == "vsmart" and not device.template
    }

    if new_controllers_uuids:
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
        for dev_uuid, ip_4th_oct in new_controllers_uuids.items():
            # For every SD-WAN Controller, create a payload to attach template
            attach_payload["deviceTemplateList"][0]["device"].append(
                {
                    "csv-status": "complete",
                    "csv-deviceId": dev_uuid,
                    "csv-deviceIP": f"100.0.0.{ip_4th_oct}",
                    "csv-host-name": f"Controller{ip_4th_oct[-2:]}",
                    "/0/eth1/interface/ip/address": f"172.16.0.{ip_4th_oct}/24",
                    "//system/host-name": f"Controller{ip_4th_oct[-2:]}",
                    "//system/system-ip": f"100.0.0.{ip_4th_oct}",
                    "//system/site-id": "100",
                    "csv-templateId": template_id,
                }
            )

        task_id = manager_session.post(
            "dataservice/template/device/config/attachfeature", json=attach_payload
        ).json()["id"]
        success_statuses = [OperationStatus.SUCCESS, OperationStatus.SUCCESS_SCHEDULED]
        success_statuses_ids = [
            OperationStatusId.SUCCESS,
            OperationStatusId.SUCCESS_SCHEDULED,
        ]
        Task(manager_session, task_id).wait_for_completed(
            success_statuses=success_statuses, success_statuses_ids=success_statuses_ids
        )


def check_manager_ip_is_free(ip: str) -> None:
    """
    Ping the IP address that will be allocated to SD-WAN Manager
    to verify it's not already used
    """
    ping_parameter = "-n" if platform.system().lower() == "windows" else "-c"

    if (
        subprocess.call(["ping", ping_parameter, "1", ip], stdout=subprocess.DEVNULL)
        == 0
    ):
        sys.exit(
            f"IP address {ip} allocated for SD-WAN Manager is already in use. "
            f"Please pick a different IP or resolve IP conflict."
        )


def configure_manager_basic_settings(
    manager_session: ManagerSession, ca_chain: str, log: Logger
) -> None:
    """
    Configure basic settings for SD-WAN Manager
    """
    track_progress(log, "Configuring basic settings...")
    manager_config_settings = manager_session.endpoints.configuration_settings
    if manager_config_settings.get_organizations().first().org is None:
        manager_config_settings.edit_organizations(Organization(org=ORG_NAME))
    else:
        log.info("Org-name is already set")
    manager_config_settings.edit_devices(Device(domain_ip=VALIDATOR_FQDN))
    manager_config_settings.edit_certificates(
        Certificate(certificate_signing="enterprise")
    )
    manager_session.put(
        "dataservice/settings/configuration/certificate/enterpriserootca",
        json={"enterpriseRootCA": ca_chain},
    )
    manager_config_settings.edit_vmanage_data_stream(
        VManageDataStream(
            enable=True,
            ip_type="systemIp",
            serverHostName="systemIp",
            vpn=0,
        )
    )
    manager_config_settings.edit_cloudx(CloudX(mode="on"))


def create_cert(ca_cert_bytes: bytes, ca_key_bytes: bytes, csr_bytes: bytes) -> bytes:
    """
    Sign a CSR with the CAcert and CAkey.
    Certificate validity will be from -1 day to +2 years
    Return the resulting signed cert
    """
    ca_cert = crypto.load_certificate(crypto.FILETYPE_PEM, ca_cert_bytes)
    ca_key = crypto.load_privatekey(crypto.FILETYPE_PEM, ca_key_bytes)
    csr = crypto.load_certificate_request(crypto.FILETYPE_PEM, csr_bytes)

    cert = crypto.X509()
    cert.set_serial_number(uuid.uuid4().int)
    cert.gmtime_adj_notBefore(-1 * 24 * 60 * 60)
    cert.gmtime_adj_notAfter(2 * 365 * 24 * 60 * 60)
    cert.set_issuer(ca_cert.get_subject())
    cert.set_subject(csr.get_subject())
    cert.set_pubkey(csr.get_pubkey())
    cert.sign(ca_key, "sha256")
    return crypto.dump_certificate(crypto.FILETYPE_PEM, cert)


def get_cml_sdwan_image_definition(
    cml: ClientLibrary, node_definition: str, software_version: str
) -> str:
    """
    Verify if requested SD-WAN software version
    is present in CML for device type
    """
    requested_image_definition = f"{node_definition}-{software_version}"
    existing_image_definitions = [
        image["id"]
        for image in cml.definitions.image_definitions_for_node_definition(
            node_definition
        )
    ]
    if requested_image_definition in existing_image_definitions:
        return requested_image_definition
    else:
        if node_definition in ["cat-sdwan-controller", "cat-sdwan-validator"]:
            # If there's no requested Controller image, try one version lower
            # For example sometimes Manager is 20.9.3.2 and Controller/Validator is 20.9.3.1
            new_software_version_elements = software_version.split(".")
            if (
                int(new_software_version_elements[-1]) == 1
                and len(new_software_version_elements) == 4
            ):
                # If last digit is one and there are four digits try without this digit
                new_software_version_elements = new_software_version_elements[:-1]
            else:
                new_software_version_elements[-1] = (
                    f"{(int(new_software_version_elements[-1]) - 1)}"
                )
            new_software_version = ".".join(new_software_version_elements)
            new_requested_image_definition = f"{node_definition}-{new_software_version}"
            if new_requested_image_definition in existing_image_definitions:
                return new_requested_image_definition
            else:
                sys.exit(
                    f'Requested SD-WAN {node_definition.split("-")[2].title()} software image version '
                    f"{software_version} or {new_software_version} is not found in CML. "
                    f"Use setup task to upload the correct images."
                )
        else:
            available_software_versions = [
                image_id.split("-")[3] for image_id in existing_image_definitions
            ]
            sys.exit(
                f'Requested SD-WAN {node_definition.split("-")[2].title()} software image version '
                f"{software_version} is not found in CML. Use setup task to upload the correct images or "
                f"use any available image: {available_software_versions}"
            )


def get_sdwan_lab_parameters(software_version: str) -> List[int]:
    major_release = int(software_version.split(".")[0])
    minor_release = int(software_version.split(".")[1])
    if major_release <= 19 or (major_release == 20 and minor_release < 4):
        sys.exit("Versions lower than 20.4 are not supported by the script.")
    elif major_release == 20 and minor_release in [4, 5, 6, 7, 8, 9, 10, 11]:
        # This versions require C8000v (SD-WAN) serial file and device templates
        serial_file_version = 1
        config_version = 1
    else:
        # This versions require C8000v (SD-WAN and SD-Routing) serial file and configuration groups for SD-WAN
        serial_file_version = 2
        config_version = 2

    return [serial_file_version, config_version]


def load_certificate_details() -> List[str]:
    ca_cert_name = "signCA.pem"
    ca_key_name = "signCA.key"
    ca_chain_name = "chainCA.pem"
    if (
        not exists(join(CERTS_DIR, ca_cert_name))
        or not exists(join(CERTS_DIR, ca_key_name))
        or not exists(join(CERTS_DIR, ca_chain_name))
    ):
        sys.exit("Sign CA not found")
    else:
        file = open(join(CERTS_DIR, ca_cert_name), "r")
        ca_cert = file.read()
        file.close()
        file = open(join(CERTS_DIR, ca_key_name), "r")
        ca_key = file.read()
        file.close()
        file = open(join(CERTS_DIR, ca_chain_name), "r")
        ca_chain = file.read()
        file.close()
        return [ca_cert, ca_key, ca_chain]


def onboard_control_components(
    manager_session: ManagerSession,
    manager_password: str,
    new_control_components: Dict[str, str],
    log: Logger,
) -> None:
    """
    Add new Validators and Controllers to SD-WAN Manager
    new_control_components is a dict with ip: node_type
    """
    # Check which control components are already added
    already_added_vpn0_ips = []
    device_inventory = manager_session.endpoints.configuration_device_inventory
    for device in device_inventory.get_device_details("controllers"):
        config = manager_session.get(
            f"dataservice/template/config/attached/{device.uuid}"
        ).json()["config"]
        match = re.search(
            r"vpn 0[\s\S]+?ip\saddress\s(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", config
        )
        if match:
            already_added_vpn0_ips.append(match.group(1))
        else:
            already_added_vpn0_ips.append(device.device_ip)
    i = 0
    log.info("Adding control components...")
    for ip, node_type in new_control_components.items():
        track_progress(
            log,
            f"Adding control components ({i}/{len(new_control_components.keys())})...",
        )
        if ip in already_added_vpn0_ips:
            log.info(f"{node_type.title()} {ip} is already onboarded. Skipping...")
        else:
            if node_type.lower() == "validator":
                personality = "vbond"
            elif node_type.lower() == "controller":
                personality = "vsmart"
            else:
                exit(
                    f"Expected node_type validator or controller, but got {node_type} instead."
                )
            try:
                device_inventory.create_device(
                    payload=DeviceCreationPayload(
                        device_ip=ip,
                        username="admin",
                        password="admin",
                        generateCSR=False,
                        personality=personality,
                    )
                )
            except ManagerHTTPError:
                # default credentials didn't work, try Manager username and password
                try:
                    device_inventory.create_device(
                        payload=DeviceCreationPayload(
                            device_ip=ip,
                            username="admin",
                            password=manager_password,
                            generateCSR=False,
                            personality=personality,
                        )
                    )
                except ManagerHTTPError:
                    exit(
                        f"Could not add controller {node_type.title()} to SD-WAN Manager "
                        f"using admin username and default or SD-WAN Manager password. "
                        f"Please fix admin user password and rerun the script."
                    )

    track_progress(log, "Generating certificates for control components...")
    # Prepare the CA for controllers certificate signing
    ca_cert, ca_key, ca_chain = load_certificate_details()
    control_components = device_inventory.get_device_details("controllers")
    with ThreadPoolExecutor() as executor:
        # Generate CSR and sign in the certificates for all control components at the same time
        sign_certificate_partial = partial(
            sign_certificate,
            log=log,
            manager_session=manager_session,
            ca_cert=ca_cert,
            ca_key=ca_key,
        )
        executor.map(sign_certificate_partial, control_components)
        executor.shutdown(wait=True)


def restore_manager_configuration(
    manager_ip: str,
    manager_port: int,
    manager_user: str,
    manager_password: str,
    workdir: str,
    attach: bool,
) -> None:
    """
    Restore configuration using Sastre
    """
    sastre_task_args = RestoreArgs(workdir=workdir, attach=attach, tag="all")

    with Rest(
        base_url=f"https://{manager_ip}:{manager_port}",
        username=manager_user,
        password=manager_password,
    ) as api:
        task = TaskRestore()
        task_output = task.runner(sastre_task_args, api)

        if task_output:
            print("\n\n".join(str(entry) for entry in task_output))

        task.log_info(
            f'Template Restore task completed {task.outcome("successfully", "with caveats: {tally}")}'
        )


def setup_logging(loglevel: Union[int, str]) -> Logger:
    # Setup logging
    log = logging.getLogger(__name__)
    log.setLevel(loglevel)
    # When script wait for SD-WAN Manager to come up, filter the connection error logs as they are expected
    catalystwan_logger = logging.getLogger("catalystwan.session")
    catalystwan_logger.addFilter(
        lambda record: "Max retries exceeded" not in record.getMessage()
    )
    catalystwan_logger.addFilter(
        lambda record: "Failed to establish a new connection: [Errno 61]"
        not in record.getMessage()
    )
    urllib3_logger = logging.getLogger("urllib3.connectionpool")
    urllib3_logger.addFilter(
        lambda record: "Max retries exceeded" not in record.getMessage()
    )
    urllib3_logger.addFilter(
        lambda record: "Failed to establish a new connection: [Errno 61]"
        not in record.getMessage()
    )
    return log


def sign_certificate(
    device: DeviceDetailsResponse,
    log: Logger,
    manager_session: ManagerSession,
    ca_cert: bytes,
    ca_key: bytes,
) -> None:
    """
    Generate CSR and sign the certificate
    for SD-WAN control component
    if certificate is not yet installed
    """
    if (
        device.device_type in ["vmanage", "vsmart", "vbond"]
        and device.serial_number == "No certificate installed"
    ):
        csr = manager_session.endpoints.certificate_management_device.generate_csr(
            TargetDevice(device_ip=device.device_ip)
        )[0].deviceCSR
        cert = create_cert(ca_cert, ca_key, csr)
        task_id = manager_session.post(
            "dataservice/certificate/install/signedCert", data=cert
        ).json()["id"]
        Task(manager_session, task_id).wait_for_completed()
    else:
        log.info("Certificate is already signed for " + device.host_name + ".")


def track_progress(log: Logger, message: str) -> None:
    """
    Print progress status message depending on the log
    settings either to stdout or to a log
    """
    if log.level > logging.INFO:
        # User is getting a summary feedback about task status in single line
        print(f"\r\033[K{message}", end="", flush=True)
    else:
        # Same message is passed to a log if it doesn't count attempts
        # as this would create too many logs
        if "Waiting for SD-WAN Manager API " not in message and not re.match(
            r"\([\w\s]?\d/\d\)", message
        ):
            log.info(message)


def wait_for_manager_session(
    manager_ip: str,
    manager_patty_port: int,
    manager_user: str,
    manager_password: str,
    log: Logger,
) -> ManagerSession:
    """
    Retry to log in to SD-WAN Manager until API is up
    """
    # Different SD-WAN Manager versions requires different time to boot and bring up application-server with REST API
    # Start trying to log in to SD-WAN Manager until it's succesful or until 60 minutes passes.
    track_progress(log, "Waiting for SD-WAN Manager API...")
    retries = 0
    max_retries = 120
    manager_session = None
    while True:
        try:
            manager_session = create_manager_session(
                url=manager_ip,
                port=manager_patty_port,
                username=manager_user,
                password=manager_password,
            )
        except (
            ConnectionRefusedError,
            ConnectionError,
            UnauthorizedAccessError,
            ManagerRequestException,
        ):
            retries += 1
            if retries < max_retries:
                track_progress(
                    log, f"Waiting for SD-WAN Manager API (attempt {retries})..."
                )
                if retries % 10 == 0:
                    log.info(
                        f"Waiting for SD-WAN Manager API (attempt {retries}/{max_retries})..."
                    )
                time.sleep(30)
                continue
            else:
                sys.exit("Failed to login to SD-WAN Manager after 60 minutes.")
        break
    log.info("SD-WAN Manager login successful")
    return manager_session


def wait_for_wan_edge_onboaring(
    manager_session: ManagerSession, wan_edges_to_onboard: List[str], log: Logger
) -> None:
    """
    Wait until WAN Edge routers are onboarded
    """
    log.info("Waiting for WAN Edge onboarding...")
    retries = 0
    max_retries = 120
    wan_edges_onboarded: List[str] = []
    while not set(wan_edges_to_onboard) == set(wan_edges_onboarded):
        retries += 1
        track_progress(
            log,
            f"Waiting for WAN Edge onboarding "
            f"({len(wan_edges_onboarded)}/{len(wan_edges_to_onboard)})...",
        )
        if retries < max_retries:
            if retries % 10 == 0:
                log.info(
                    f"WAN Edges not onboarded, attempt {retries}/{max_retries}, waiting..."
                )
            time.sleep(30)
            devices = manager_session.endpoints.configuration_device_inventory.get_device_details(
                "vedges"
            )
            for dev_uuid in wan_edges_to_onboard:
                device = devices.filter(uuid=dev_uuid).single_or_default()
                if (
                    device.cert_install_status == "Installed"
                    and device.reachability == "reachable"
                    and dev_uuid not in wan_edges_onboarded
                ):
                    log.info(f"Onboarded WAN Edge with UUID: {dev_uuid}")
                    wan_edges_onboarded.append(dev_uuid)
        else:
            break

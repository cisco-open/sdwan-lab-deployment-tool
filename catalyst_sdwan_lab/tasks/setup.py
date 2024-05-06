# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd

import json
import os
import re
from logging import Logger
from os.path import join
from typing import List, Union

from ruamel.yaml import YAML
from virl2_client import ClientLibrary

from .utils import (
    CML_NODES_DEFINITION_DIR,
    SOFTWARE_IMAGES_DIR,
    setup_logging,
    track_progress,
)


def upload_image_and_create_definition(
    log: Logger,
    cml: ClientLibrary,
    existing_image_definitions_ids: List[str],
    node_type: str,
    software_version: str,
    node_label: str,
    software_images_dir: str,
    filename: str,
) -> None:
    if f"{node_type}-{software_version}" in existing_image_definitions_ids:
        log.info(
            f"Skipping {filename} as {node_type}-{software_version} image definition already exists."
        )
    else:
        log.info(f"Creating image definition {node_type}-{software_version}...")
        cml.definitions.upload_image_file(join(software_images_dir, filename), filename)
        image_def = {
            "id": f"{node_type}-{software_version}",
            "node_definition_id": node_type,
            "label": node_label + " " + software_version,
            "disk_image": filename,
        }
        cml.definitions.upload_image_definition(body=json.dumps(image_def))


def main(cml: ClientLibrary, loglevel: Union[int, str], list: bool) -> None:
    # Setup logging
    log = setup_logging(loglevel)

    # Setup YAML
    yaml = YAML(typ="rt")

    track_progress(log, "Verifying Node Definitions...")
    # Collect node definitions from CML
    node_definitions = cml.definitions.node_definitions()
    for filename in os.listdir(CML_NODES_DEFINITION_DIR):
        if filename.endswith(".yaml"):
            # For every YAML file in the node definition folder, we need to do below steps
            # Load node definition from the file
            with open(join(CML_NODES_DEFINITION_DIR, filename), "r") as f:
                new_node_definition = yaml.load(f.read())

            # Check if the node is already defined in CML
            # This returns node definition or None if node with this id doesn't exist
            current_node_definition = next(
                (
                    node
                    for node in node_definitions
                    if node["id"] == new_node_definition["id"]
                ),
                None,
            )
            if current_node_definition:
                # If node already exists, we need to check if it requires update
                if new_node_definition == current_node_definition:
                    # If dicts are same then no update is required
                    log.info(
                        f'[KEEP] Node {current_node_definition["id"]} is already defined and up to date.'
                    )
                else:
                    # If dicts are not the same, then we need to update the node definition
                    # Before update, check if node definition is read only
                    if current_node_definition["general"]["read_only"]:
                        # Remove read-only flag
                        cml.definitions.set_node_definition_read_only(
                            current_node_definition["id"], False
                        )

                    log.info(
                        f'[UPDATE] Updating node {new_node_definition["id"]} with '
                        f'{new_node_definition["sim"]["linux_native"]["cpus"]} CPUs and '
                        f'{new_node_definition["sim"]["linux_native"]["ram"]} MB RAM.'
                    )
                    try:
                        # For virl2_client lower than 2.7.0
                        cml.session.put("node_definitions/", json=new_node_definition)
                    except AttributeError:
                        # For virl2_client 2.7.0 and higher
                        cml.definitions.upload_node_definition(
                            new_node_definition, True
                        )
            else:
                # If node is not yet created, we need to create it
                log.info(f'[CREATE] Creating node {new_node_definition["id"]}...')
                cml.definitions.upload_node_definition(new_node_definition, json=True)

    # Refresh node definitions
    node_definitions = cml.definitions.node_definitions()
    track_progress(log, "Verifying software images...")
    # Get the list of all image definitions already created in CML.
    # This is to avoid image duplication during upload.
    existing_image_definitions = cml.definitions.image_definitions()
    existing_image_definitions_ids = [
        image_definition["id"] for image_definition in existing_image_definitions
    ]
    for image_definition in existing_image_definitions:
        match = re.match(
            r"^cat-sdwan-(edge|controller|validator|manager)-([\w\-]+)$",
            image_definition["id"],
        )
        if match:
            # Migrate image from using - in software version to using .
            # For example from cat-sdwan-manager-20-13-1 to cat-sdwan-manager-20.13.1
            # Before update, check if node definition is read only
            if image_definition["read_only"]:
                # Remove read-only flag
                cml.definitions.set_image_definition_read_only(
                    image_definition["id"], False
                )
            cml.definitions.remove_image_definition(image_definition["id"])
            # Set new ID and disk folder
            image_definition["id"] = (
                f"cat-sdwan-{match.group(1)}-{match.group(2).replace('-', '.')}"
            )
            image_definition["disk_subfolder"] = image_definition["id"]
            cml.definitions.upload_image_definition(image_definition)

    log.info(f"Looking for new software images in {os.getcwd()}...")
    software_type_to_node_type_mapping = {
        "edge": "cat-sdwan-validator",
        "bond": "cat-sdwan-validator",
        "smart": "cat-sdwan-controller",
        "vmanage": "cat-sdwan-manager",
    }

    # Check for any software that is present in software_images folder.
    for filename in os.listdir(SOFTWARE_IMAGES_DIR):
        if software_parser := re.match(r"viptela-(\w+)-([\d.]+)-", filename):
            # For viptela software we need to extract image type (vmanage, smart, edge) and version.
            node_type = software_type_to_node_type_mapping[software_parser.group(1)]
            node_label = next(
                node["ui"]["label"]
                for node in node_definitions
                if node["id"] == node_type
            )
            software_version = software_parser.group(2)
            upload_image_and_create_definition(
                log,
                cml,
                existing_image_definitions_ids,
                node_type,
                software_version,
                node_label,
                SOFTWARE_IMAGES_DIR,
                filename,
            )

        elif software_parser := re.match(
            r"c8000v-universalk9_\d+G_serial\.([.\w]+)\.qcow2$", filename
        ):
            # For C8000v we need to make sure it is a serial image.
            node_type = "cat-sdwan-edge"
            node_label = next(
                node["ui"]["label"]
                for node in node_definitions
                if node["id"] == node_type
            )
            software_version = software_parser.group(1)
            upload_image_and_create_definition(
                log,
                cml,
                existing_image_definitions_ids,
                node_type,
                software_version,
                node_label,
                SOFTWARE_IMAGES_DIR,
                filename,
            )

        else:
            log.debug(f"Skipping file {filename} (not a valid image).")

    track_progress(log, "Setup task done\n")

    if list:
        print("Available Software Versions:")
        for node_definition_id in [
            "cat-sdwan-manager",
            "cat-sdwan-controller",
            "cat-sdwan-validator",
            "cat-sdwan-edge",
        ]:
            available_software_versions = []
            # List available SD-WAN software
            for (
                image_definition
            ) in cml.definitions.image_definitions_for_node_definition(
                node_definition_id
            ):
                available_software_versions.append(image_definition["id"].split("-")[3])
            print(f"- {node_definition_id}: {available_software_versions}")

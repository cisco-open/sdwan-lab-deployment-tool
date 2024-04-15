# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd

import json
import os
import re
from os.path import join

from httpx import HTTPStatusError
from ruamel.yaml import YAML

from .utils import (CML_NODES_DEFINITION_DIR, SOFTWARE_IMAGES_DIR,
                    setup_logging, track_progress)


def upload_image_and_create_definition(log, cml, existing_image_definitions, node_type, software_version,
                                       node_label, software_images_dir, filename):
    if f'{node_type}-{software_version}' in existing_image_definitions:
        log.info(f'Skipping {filename} as {node_type}-{software_version} image definition already exists.')
    else:
        log.info(f'Creating image definition {node_type}-{software_version}...')
        cml.definitions.upload_image_file(join(software_images_dir, filename), filename)
        image_def = {
            'id': f'{node_type}-{software_version}',
            'node_definition_id': node_type,
            'label': node_label + ' ' + software_version,
            'disk_image': filename,
        }
        cml.definitions.upload_image_definition(body=json.dumps(image_def))


def main(cml, loglevel, migrate):
    # Setup logging
    log = setup_logging(loglevel)

    # Setup YAML
    yaml = YAML(typ='rt')

    track_progress(log, 'Verifying Node Definitions...')
    # Collect node definitions from CML
    node_definitions = cml.definitions.node_definitions()
    for filename in os.listdir(CML_NODES_DEFINITION_DIR):
        if filename.endswith('.yaml'):
            # For every YAML file in the node definition folder, we need to do below steps
            # Load node definition from the file
            with open(join(CML_NODES_DEFINITION_DIR, filename), 'r') as f:
                new_node_definition = yaml.load(f.read())

            # Check if the node is already defined in CML
            # This returns node definition or None if node with this id doesn't exist
            current_node_definition = next((node for node in node_definitions
                                            if node['id'] == new_node_definition['id']), None)
            if current_node_definition:
                # If node already exists, we need to check if it requires update
                if new_node_definition == current_node_definition:
                    # If dicts are same then no update is required
                    log.info(f'[KEEP] Node {current_node_definition["id"]} is already defined and up to date.')
                else:
                    # If dicts are not the same, then we need to update the node definition
                    log.info(f'[UPDATE] Updating node {new_node_definition["id"]} with '
                             f'{new_node_definition["sim"]["linux_native"]["cpus"]} CPUs and '
                             f'{new_node_definition["sim"]["linux_native"]["ram"]} MB RAM.')
                    cml.session.put('node_definitions/', json=new_node_definition)
            else:
                # If node is not yet created, we need to create it
                log.info(f'[CREATE] Creating node {new_node_definition["id"]}...')
                cml.definitions.upload_node_definition(new_node_definition, json=True)
    track_progress(log, 'Verifying software images...')
    # Get the list of all image definitions already created in CML.
    # This is to avoid image duplication during upload.
    existing_image_definitions = [image_definition['id'] for image_definition in cml.definitions.image_definitions()]
    log.info(f'Looking for new software images in {os.getcwd()}...')
    software_type_to_node_type_mapping = {
        'edge': 'cat-sdwan-validator',
        'bond': 'cat-sdwan-validator',
        'smart': 'cat-sdwan-controller',
        'vmanage': 'cat-sdwan-manager'
    }
    # Refresh node definitions
    node_definitions = cml.definitions.node_definitions()
    # Check for any software that is present in software_images folder.
    for filename in os.listdir(SOFTWARE_IMAGES_DIR):
        if software_parser := re.match(r'viptela-(\w+)-([\d.]+)-', filename):
            # For viptela software we need to extract image type (vmanage, smart, edge) and version.
            node_type = software_type_to_node_type_mapping[software_parser.group(1)]
            node_label = next((node['ui']['label'] for node in node_definitions if node['id'] == node_type), None)
            software_version = software_parser.group(2)
            upload_image_and_create_definition(log, cml, existing_image_definitions, node_type, software_version,
                                               node_label, SOFTWARE_IMAGES_DIR, filename)

        elif software_parser := re.match(r'c8000v-universalk9_\d+G_serial\.([.\w]+)\.qcow2$', filename):
            # For C8000v we need to make sure it is a serial image.
            node_type = 'cat-sdwan-edge'
            node_label = next((node['ui']['label'] for node in node_definitions if node['id'] == node_type), None)
            software_version = software_parser.group(1)
            upload_image_and_create_definition(log, cml, existing_image_definitions, node_type, software_version,
                                               node_label, SOFTWARE_IMAGES_DIR, filename)

        else:
            log.debug(f'Skipping file {filename} (not a valid image).')

    if migrate:
        node_definition_map = {
            'vmanage': 'cat-sdwan-manager',
            'vsmart': 'cat-sdwan-controller',
            'vedge': 'cat-sdwan-validator',
            'cedge': 'cat-sdwan-edge'
        }
        node_label_map = {
            'vmanage': 'Catalyst SD-WAN Manager',
            'vsmart': 'Catalyst SD-WAN Controller',
            'vedge': 'Catalyst SD-WAN Validator',
            'cedge': 'Catalyst SD-WAN Edge'
        }
        track_progress(log, 'Running migration of node/image definitions...')
        # Migrate SD-WAN Lab Tool v1.x image definitions to v2.x
        all_migrated = True
        for image_definition in cml.definitions.image_definitions():
            current_id = image_definition['id']
            current_node_definition = image_definition['node_definition_id']
            if current_node_definition in ['vmanage', 'vsmart', 'vedge', 'cedge']:
                try:
                    software_version = image_definition['id'].split('-')[1]
                    new_image_definition = {
                        'node_definition_id': node_definition_map[current_node_definition],
                        'id': f'{node_definition_map[current_node_definition]}-{software_version}',
                        'read_only': False,
                        'label': f'{node_label_map[current_node_definition]} {software_version}',
                        'disk_image': image_definition['disk_image']}

                    cml.definitions.remove_image_definition(current_id)
                    cml.definitions.upload_image_definition(new_image_definition)
                    log.info(f'Migrated image definition {current_id} to '
                             f'{node_definition_map[current_node_definition]}-{software_version}')

                except HTTPStatusError as e:
                    all_migrated = False
                    error = e.response.json()
                    if error['code'] == 400 and error['description'].startswith('Image Definition in use:'):
                        log.info(f'Cannot migrate image definition {current_id} as it is currently in use.')

        for node_definition in cml.definitions.node_definitions():
            node_id = node_definition['id']
            if node_id in ['vmanage', 'vsmart', 'vedge', 'cedge']:
                if cml.definitions.image_definitions_for_node_definition(node_id):
                    log.info(f'Cannot delete node definition {node_id} as it is currently in use.')
                    all_migrated = False
                else:
                    cml.definitions.remove_node_definition(node_id)
                    log.info(f'Node definition {node_id} successfully removed.')

        if not all_migrated:
            print('\rSome node/image definitions could not be migrated as they are in use. '
                  'Please remove the labs using the old definitions are rerun the setup with --migrate option.')

    track_progress(log, 'Setup task done\n')

# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd

from .utils import setup_logging, track_progress


def main(cml, lab_name, force, loglevel):

    # Setup logging
    log = setup_logging(loglevel)
    track_progress(log, 'Preparing delete task...')

    # Find the lab
    lab = cml.find_labs_by_title(lab_name)
    if lab:
        # If there are multiple labs with same name, we don't know which we should back up,
        # so we ask user to make sure the lab names are unique
        if len(lab) > 1:
            exit(f'There are multiple labs/topologies with name "{lab_name}". Please make sure '
                 f'lab names are unique and rerun the delete task.')
        lab = lab[0]

        if not force:
            confirmation = input('\nThis will remove lab and all its data. '
                                 'Are you sure you want to proceed? (yes/no): ')
            if confirmation.lower() != 'yes':
                exit('You did not confirm yes.')

        track_progress(log, 'Deleting the lab...')
        log.info('Stopping the lab...')
        lab.stop()
        log.info('Wiping the lab...')
        lab.wipe()
        log.info('Removing the lab...')
        lab.remove()
        track_progress(log, 'Delete task done')

    else:
        exit('Could not find a lab with specified name.')

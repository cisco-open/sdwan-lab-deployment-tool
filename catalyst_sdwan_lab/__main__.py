# Copyright (c) 2024 Cisco Systems, Inc. and its affiliates.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.
#
# SPDX-License-Identifier: bsd

"""
Catalyst SD-WAN Lab - Automation Tool for managing Cisco Catalyst SD-WAN labs inside Cisco Modelling Labs

catalyst_sdwan_lab.__main__
This module implements the command line top-level parser and task dispatcher
"""

import argparse
import logging
import sys
import urllib3
from urllib3.exceptions import InsecureRequestWarning

from cisco_sdwan.tasks.utils import EnvVar, non_empty_type, PromptArg
from virl2_client import ClientLibrary

import catalyst_sdwan_lab
from .tasks import add, backup, delete, deploy, restore, setup


# Setup logging
log = logging.getLogger(__name__)
# Filter unnecesarry warnings
urllib3.disable_warnings(InsecureRequestWarning)


def main():
    # Define main parser and options that are required regardless of selected task
    main_parser = argparse.ArgumentParser(description='Catalyst SD-WAN Lab - Automation Tool for managing '
                                                      'Cisco Catalyst SD-WAN labs inside Cisco Modelling Labs.')
    main_parser.add_argument('-c', '--cml', metavar='<cml-ip>', action=EnvVar, required=False,
                             envvar='CML_IP', type=non_empty_type,
                             help='CML IP address, can also be defined via CML_IP environment variable. '
                                  'If neither is provided user is prompted for CML IP.')
    main_parser.add_argument('-u', '--user', metavar='<cml-user>', action=EnvVar, required=False,
                             envvar='CML_USER', type=non_empty_type,
                             help='CML username, can also be defined via CML_USER environment variable. '
                                  'If neither is provided user is prompted for CML username.')
    main_parser.add_argument('-p', '--password', metavar='<cml-password>', action=EnvVar, required=False,
                             envvar='CML_PASSWORD', type=non_empty_type,
                             help='CML password, can also be defined via CML_PASSWORD environment variable. '
                                  ' If neither is provided user is prompted for CML password.')
    main_parser.add_argument('-v', '--verbose', action="store_const", dest="loglevel", const=logging.INFO,
                             default=logging.WARNING, help='Increase output verbosity.')
    main_parser.add_argument('--version', action='version',
                             version=f'SD-WAN Lab Version {catalyst_sdwan_lab.__version__}')
    main_parser.set_defaults(prompt_main_arguments=[
        PromptArg('cml', 'CML IP address: '),
        PromptArg('user', 'CML user: '),
        PromptArg('password', 'CML password: ', secure_prompt=True),
    ])

    # Create subparser for each task, as each task might require different parameters
    task_subparsers = main_parser.add_subparsers(help='Task to be performed.', dest='task')
    task_subparsers.required = True

    setup_parser = task_subparsers.add_parser('setup', help='Setup on-prem CML to use Catalyst SD-WAN Lab automation.')
    setup_parser.add_argument('--migrate', action="store_const", dest="migrate", const=True,
                              default=False, help='Migrate node and image definitions from SD-WAN Lab v1.x to v2.x. '
                                                  'This task should be run once if CML server was using '
                                                  'SD-WAN LAb Tool v1.x in the past.')

    deploy_parser = task_subparsers.add_parser('deploy', help='Deploy a new Catalyst SD-WAN lab pod.')
    deploy_parser.add_argument('software_version', metavar='<software-version>',
                               help='Software version that will be used on SD-WAN Control Components.')
    deploy_parser.add_argument('--manager', metavar='<manager-ip>', action=EnvVar, required=False,
                               envvar='MANAGER_IP', type=non_empty_type,
                               help='SD-WAN Manager IP address, can also be defined via MANAGER_IP environment variable. '
                                    'If neither is provided user is prompted for SD-WAN Manager IP.')
    deploy_parser.add_argument('--mmask', metavar='<manager-mask>', action=EnvVar, required=False,
                               envvar='MANAGER_MASK', type=non_empty_type,
                               help='Subnet mask for given SD-WAN Manager IP (e.g. /24), can also be defined via MANAGER_MASK '
                                    'environment variable. '
                                    'If neither is provided user is prompted for SD-WAN Manager subnet mask.')
    deploy_parser.add_argument('--mgateway', metavar='<manager-gateway>', action=EnvVar, required=False,
                               envvar='MANAGER_GATEWAY', type=non_empty_type,
                               help='Gateway IP for given SD-WAN Manager IP, can also be defined via MANAGER_GATEWAY '
                                    'environment variable. '
                                    'If neither is provided user is prompted for Manager gateway IP.')
    deploy_parser.add_argument('--muser', metavar='<manager-user>', action=EnvVar, required=False,
                               envvar='MANAGER_USER', type=non_empty_type,
                               help='SD-WAN Manager username, can also be defined via MANAGER_USER environment variable. '
                                    'If neither is provided user is prompted for SD-WAN Manager username.')
    deploy_parser.add_argument('--mpassword', metavar='<manager-password>', action=EnvVar, required=False,
                               envvar='MANAGER_PASSWORD', type=non_empty_type,
                               help='SD-WAN Manager password, can also be defined via MANAGER_PASSWORD environment variable. '
                                    ' If neither is provided user is prompted for SD-WAN Manager password.')
    deploy_parser.add_argument('--lab', metavar='<lab_name>', action=EnvVar, required=False, envvar='LAB_NAME',
                               default=None, help='Set CML Lab name, can also be defined via LAB_NAME environment variable. '
                               'If not provided, default name "sdwan<number>" will be assigned.')
    deploy_parser.add_argument('--bridge', metavar='<custom-bridge-name>', required=False,
                               default="System Bridge", help='Set custom bridge for SD-WAN Manager external connection. '
                                                             'Default is System Bridge')
    deploy_parser.add_argument('--dns', metavar='<dns-server-ip>', required=False,
                               default="192.168.255.1", help='Set custom DNS server for Internet/VPN0 transport. '
                                                             'Default is same as CML DNS')
    deploy_parser.add_argument('--retry', action="store_const", dest='retry', const=True,
                                default=False, help='If for some reason your script lost connectivity during SD-WAN Manager '
                                                    'boot, you can add --retry to continue onboarding the lab that is '
                                                    'already in CML')

    deploy_parser.set_defaults(prompt_deploy_arguments=[
        PromptArg('manager', 'SD-WAN Manager IP address: '),
        PromptArg('mmask', 'SD-WAN Manager subnet mask (e.g. /24): '),
        PromptArg('mgateway', 'SD-WAN Manager gateway IP: '),
        PromptArg('muser', 'SD-WAN Manager user: '),
        PromptArg('mpassword', 'SD-WAN Manager password: ', secure_prompt=True),
    ])

    add_parser = task_subparsers.add_parser('add', help='Add Catalyst SD-WAN device to running lab pod.')
    add_parser.add_argument('--manager', metavar='<manager-ip>', action=EnvVar, required=False,
                            envvar='MANAGER_IP', type=non_empty_type,
                            help='SD-WAN Manager IP address, can also be defined via MANAGER_IP environment variable. '
                                 'If neither is provided user is prompted for SD-WAN Manager IP.')
    add_parser.add_argument('--muser', metavar='<manager-user>', action=EnvVar, required=False,
                            envvar='MANAGER_USER', type=non_empty_type,
                            help='SD-WAN Manager username, can also be defined via MANAGER_USER environment variable. '
                                 'If neither is provided user is prompted for SD-WAN Manager username.')
    add_parser.add_argument('--mpassword', metavar='<manager-password>', action=EnvVar, required=False,
                            envvar='MANAGER_PASSWORD', type=non_empty_type,
                            help='SD-WAN Manager password, can also be defined via MANAGER_PASSWORD environment variable. '
                                 'If neither is provided user is prompted for SD-WAN Manager password.')
    add_parser.add_argument('--lab', metavar='<lab_name>', action=EnvVar, required=False,
                            envvar='LAB_NAME', type=non_empty_type,
                            help='CML Lab name, can also be defined via LAB_NAME environment variable. '
                            'If neither is provided user is prompted for lab name.')
    add_parser.add_argument('number_of_devices', metavar='<number-of-devices>', type=int,
                            help='Number of devices to be added.')
    add_parser.add_argument('device_type', metavar='<device-type>',
                            help='Type of device/s to be added (e.g. validator, controller, edge, sdrouting).')
    add_parser.add_argument('software_version', metavar='<software-version>',
                            help='Software version that will be used.')

    add_parser.set_defaults(prompt_add_arguments=[
        PromptArg('manager', 'SD-WAN Manager IP address: '),
        PromptArg('muser', 'SD-WAN Manager user: '),
        PromptArg('mpassword', 'SD-WAN Manager password: ', secure_prompt=True),
        PromptArg('lab', 'CML lab name: '),
    ])

    backup_parser = task_subparsers.add_parser('backup', help='Backup running Catalyst SD-WAN lab pod.')
    backup_parser.add_argument('--manager', metavar='<manager-ip>', action=EnvVar, required=False,
                               envvar='MANAGER_IP', type=non_empty_type,
                               help='SD-WAN Manager IP address, can also be defined via MANAGER_IP environment variable. '
                                    'If neither is provided user is prompted for SD-WAN Manager IP.')
    backup_parser.add_argument('--muser', metavar='<manager-user>', action=EnvVar, required=False,
                               envvar='MANAGER_USER', type=non_empty_type,
                               help='SD-WAN Manager username, can also be defined via MANAGER_USER environment variable. '
                                    'If neither is provided user is prompted for SD-WAN Manager username.')
    backup_parser.add_argument('--mpassword', metavar='<manager-password>', action=EnvVar, required=False,
                               envvar='MANAGER_PASSWORD', type=non_empty_type,
                               help='SD-WAN Manager password, can also be defined via MANAGER_PASSWORD environment variable. '
                                    ' If neither is provided user is prompted for SD-WAN Manager password.')
    backup_parser.add_argument('--lab', metavar='<lab_name>', action=EnvVar, required=False,
                               envvar='LAB_NAME', type=non_empty_type,
                               help='CML Lab name, can also be defined via LAB_NAME environment variable. '
                               'If neither is provided user is prompted for lab name.')
    backup_parser.add_argument('--workdir', metavar='<directory>', type=non_empty_type,
                               default='backup', help='Backup destination folder')

    backup_parser.set_defaults(prompt_backup_arguments=[
        PromptArg('manager', 'SD-WAN Manager IP address: '),
        PromptArg('muser', 'SD-WAN Manager user: '),
        PromptArg('mpassword', 'SD-WAN Manager password: ', secure_prompt=True),
        PromptArg('lab', 'CML lab name: '),
        PromptArg('workdir', 'Directory to save backup: '),
    ])

    restore_parser = task_subparsers.add_parser('restore', help='Restore Catalyst SD-WAN POD from backup.')
    restore_parser.add_argument('--manager', metavar='<manager-ip>', action=EnvVar, required=False,
                                envvar='MANAGER_IP', type=non_empty_type,
                                help='SD-WAN Manager IP address, can also be defined via MANAGER_IP environment variable. '
                                     'If neither is provided user is prompted for SD-WAN Manager IP.')
    restore_parser.add_argument('--mmask', metavar='<manager-mask>', action=EnvVar, required=False,
                                envvar='MANAGER_MASK', type=non_empty_type,
                                help='Subnet mask for given SD-WAN Manager IP (e.g. /24), can also be defined via MANAGER_MASK '
                                     'environment variable. '
                                     'If neither is provided user is prompted for SD-WAN Manager subnet mask.')
    restore_parser.add_argument('--mgateway', metavar='<manager-gateway>', action=EnvVar, required=False,
                                envvar='MANAGER_GATEWAY', type=non_empty_type,
                                help='Gateway IP for given SD-WAN Manager IP, can also be defined via MANAGER_GATEWAY '
                                     'environment variable. '
                                     'If neither is provided user is prompted for Manager gateway IP.')
    restore_parser.add_argument('--muser', metavar='<manager-user>', action=EnvVar, required=False,
                                envvar='MANAGER_USER', type=non_empty_type,
                                help='SD-WAN Manager username, can also be defined via MANAGER_USER environment variable. '
                                     'If neither is provided user is prompted for SD-WAN Manager username.')
    restore_parser.add_argument('--mpassword', metavar='<manager-password>', action=EnvVar, required=False,
                                envvar='MANAGER_PASSWORD', type=non_empty_type,
                                help='SD-WAN Manager password, can also be defined via MANAGER_PASSWORD environment variable. '
                                     ' If neither is provided user is prompted for SD-WAN Manager password.')
    restore_parser.add_argument('--lab', metavar='<lab_name>', action=EnvVar, required=False,
                                envvar='LAB_NAME', type=non_empty_type,
                                help='CML Lab name, can also be defined via LAB_NAME environment variable. '
                                'If neither is provided user is prompted for lab name.')
    restore_parser.add_argument('--workdir', metavar='<directory>', type=non_empty_type,
                                default='backup', help='Restore source folder')
    restore_parser.add_argument('--deleteexisting', action="store_const", dest='deleteexisting', const=True,
                                default=False, help='If there is already lab running with same name and using same '
                                                    'SD-WAN Manager IP, delete this lab before restoring. '
                                                    'Note the all running lab data will be lost!')
    restore_parser.add_argument('--retry', action="store_const", dest='retry', const=True,
                                default=False, help='If for some reason your script lost connectivity during SD-WAN Manager '
                                                    'boot, you can add --retry to continue restoring the lab that is '
                                                    'already in CML')

    restore_parser.set_defaults(prompt_restore_arguments=[
        PromptArg('manager', 'SD-WAN Manager IP address: '),
        PromptArg('mmask', 'SD-WAN Manager subnet mask (e.g. /24): '),
        PromptArg('mgateway', 'SD-WAN Manager gateway IP: '),
        PromptArg('muser', 'SD-WAN Manager user: '),
        PromptArg('mpassword', 'SD-WAN Manager password: ', secure_prompt=True),
        PromptArg('lab', 'CML lab name: '),
        PromptArg('workdir', 'Directory to restore: '),
    ])

    delete_parser = task_subparsers.add_parser('delete', help='Delete the CML lab and all the lab data.')
    delete_parser.add_argument('--lab', metavar='<lab_name>', action=EnvVar, required=False,
                               envvar='LAB_NAME', type=non_empty_type,
                               help='CML Lab name, can also be defined via LAB_NAME environment variable. '
                               'If neither is provided user is prompted for lab name.')
    delete_parser.add_argument('--force', action="store_const", dest='force', const=True,
                               default=False, help='Delete the lab without asking for confirmation. '
                                                   'Note the all lab data will be lost!')

    delete_parser.set_defaults(prompt_delete_arguments=[
        PromptArg('lab', 'CML lab name: '),
    ])

    cli_args = main_parser.parse_args()

    # Depending on the selected task, prompt for additional arguments (if needed).
    prompt_args_list = getattr(cli_args, 'prompt_main_arguments', [])
    if cli_args.task in ['deploy', 'backup', 'restore', 'add', 'delete']:
        prompt_args_list += getattr(cli_args, f'prompt_{cli_args.task}_arguments', [])

    try:
        for prompt_arg in prompt_args_list:
            if getattr(cli_args, prompt_arg.argument) is None:
                setattr(cli_args, prompt_arg.argument, prompt_arg())
    except KeyboardInterrupt:
        sys.exit(1)

    # Setup logging
    logging.basicConfig(format='%(levelname)s - %(message)s')
    log.setLevel(cli_args.loglevel)
    logger = logging.getLogger('virl2_client.virl2_client')
    # Filter SSL Warning from virl2-client
    logger.addFilter(lambda record: "SSL Verification disabled" not in record.getMessage())

    # Check CML version
    log.info('Logging in to CML...')
    cml = ClientLibrary('https://' + cli_args.cml, cli_args.user, cli_args.password, ssl_verify=False)
    verify_cml_version(cml)
    if cli_args.task == 'setup':
        setup.main(cml, cli_args.loglevel, cli_args.migrate)
    elif cli_args.task == 'deploy':
        deploy.main(cml, cli_args.cml, cli_args.manager, cli_args.mmask, cli_args.mgateway,
                    cli_args.muser, cli_args.mpassword, cli_args.software_version, cli_args.lab, cli_args.bridge, cli_args.dns,
                    cli_args.retry, cli_args.loglevel)
    elif cli_args.task == 'add':
        add.main(cml, cli_args.user, cli_args.password, cli_args.manager, cli_args.muser, cli_args.mpassword, cli_args.lab,
                 cli_args.number_of_devices, cli_args.device_type.lower(), cli_args.software_version, cli_args.loglevel)
    elif cli_args.task == 'backup':
        backup.main(cml, cli_args.user, cli_args.password, cli_args.manager, cli_args.muser, cli_args.mpassword,
                    cli_args.lab, cli_args.workdir, cli_args.loglevel)
    elif cli_args.task == 'restore':
        restore.main(cml, cli_args.cml, cli_args.manager, cli_args.mmask, cli_args.mgateway,
                     cli_args.muser, cli_args.mpassword, cli_args.workdir, cli_args.lab,
                     cli_args.deleteexisting, cli_args.retry, cli_args.loglevel)
    elif cli_args.task == 'delete':
        delete.main(cml, cli_args.lab, cli_args.force, cli_args.loglevel)


def verify_cml_version(cml):
    if cml.VERSION.major == 2 and cml.VERSION.minor >= 6:
        pass
    else:
        exit('Upgrade CML to 2.6 or later to use the tool.')


if __name__ == '__main__':
    main()

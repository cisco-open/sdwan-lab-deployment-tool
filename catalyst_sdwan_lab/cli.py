import functools
import logging
import re
import sys
from collections.abc import Callable
from typing import Any

import rich_click as click
import urllib3
from urllib3.exceptions import InsecureRequestWarning
from virl2_client import ClientConfig

import catalyst_sdwan_lab

from .tasks import add, backup, delete, deploy, restore, setup, sign

urllib3.disable_warnings(InsecureRequestWarning)


def set_manager_details(cml_ip: str, manager_ip: str) -> tuple[bool, str, int]:
    patty_used = False
    manager_port = 443
    if manager_ip.startswith("pat:"):
        # PATty should be used for SD-WAN Manager reachability.
        patty_used = True
        manager_port_search = re.search(r"pat:(\d+)", manager_ip)
        if manager_port_search:
            manager_port = int(manager_port_search.group(1))
        else:
            exit(
                "Wrong PATty configuration for manager_ip (expected pat:<outside_port>)."
            )
        manager_ip = cml_ip

    return patty_used, manager_ip, manager_port


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(version=catalyst_sdwan_lab.__version__, prog_name="SD-WAN Lab")
@click.option(
    "-v",
    "--verbose",
    metavar="<verbosity level>",
    count=True,
    help="Verbose mode. Multiple -v options increase the verbosity.",
)
@click.option(
    "--cml",
    "-c",
    metavar="<cml-ip>",
    envvar="CML_IP",
    help="CML IP address, can also be defined via CML_IP environment variable. ",
)
@click.option(
    "--user",
    "-u",
    metavar="<cml-user>",
    envvar="CML_USER",
    help="CML username, can also be defined via CML_USER environment variable. ",
)
@click.option(
    "--password",
    "-p",
    metavar="<cml-password>",
    envvar="CML_PASSWORD",
    hide_input=True,
    help="CML password, can also be defined via CML_PASSWORD environment variable. ",
)
@click.pass_context
def cli(
    ctx: click.Context,
    verbose: int,
    cml: str,
    user: str,
    password: str,
) -> None:

    if ctx.resilient_parsing or ctx.invoked_subcommand is None:
        return
    if "-h" in sys.argv or "--help" in sys.argv:
        return

    subcommand = ctx.command
    if (
        sys.argv[-1] == ctx.invoked_subcommand
        and isinstance(subcommand, click.Group)
        and subcommand.commands[ctx.invoked_subcommand].no_args_is_help
    ):
        return

    ctx.ensure_object(dict)
    loglevel = max(logging.DEBUG, logging.WARNING - 10 * verbose)
    ctx.obj["LOGLEVEL"] = loglevel
    log = logging.getLogger(__name__)
    log.setLevel(loglevel)
    virl2_client_logger = logging.getLogger("virl2_client.virl2_client")
    virl2_client_logger.addFilter(
        lambda record: "SSL Verification disabled" not in record.getMessage()
    )
    pyats_logger = logging.getLogger("pyats.utils.fileutils.bases.fileutils")
    pyats_logger.addFilter(
        lambda record: not any(
            msg in record.getMessage()
            for msg in [
                "Could not find details in testbed for server terminal_server.",
                "No details found in testbed for hostname terminal_server.",
            ]
        )
    )
    logging.basicConfig(format="%(levelname)s - %(message)s")
    urllib3.disable_warnings(InsecureRequestWarning)

    if ctx.invoked_subcommand == "sign":
        return

    cml = cml if cml else click.prompt("CML IP address")
    user = user if user else click.prompt("CML username")
    password = password if password else click.prompt("CML password", hide_input=True)
    cml_config = ClientConfig(cml, user, password, ssl_verify=False)
    ctx.obj["CML_CONFIG"] = cml_config


@cli.command(
    name="setup",
    short_help="Setup CML to use Catalyst SD-WAN Lab automation.",
)
@click.option(
    "--delete",
    "-d",
    metavar="<software_versions>",
    help="Delete all image definitions for the specified software version(s). "
    "To specify multiple versions, separate them with a comma.",
)
@click.option(
    "--list",
    "-l",
    "list_",
    is_flag=True,
    help="List the available SD-WAN software per node type and exit.",
)
@click.pass_context
def cli_setup(ctx: click.Context, list_: bool, delete: str = "") -> None:
    software_versions_to_delete = []
    if delete:
        software_versions_to_delete = delete.split(",")
    setup.main(
        ctx.obj["CML_CONFIG"], ctx.obj["LOGLEVEL"], list_, software_versions_to_delete
    )


def manager_options(f: Callable[..., Any]) -> Callable[..., Any]:
    @click.option(
        "--manager",
        metavar="<manager-ip>",
        envvar="MANAGER_IP",
        prompt="SD-WAN Manager IP address",
        help="SD-WAN Manager IP address, can also be defined via MANAGER_IP environment ",
    )
    @click.option(
        "--muser",
        metavar="<manager-user>",
        envvar="MANAGER_USER",
        prompt="SD-WAN Manager user",
        help="SD-WAN Manager username, can also be defined via "
        "MANAGER_USER environment variable. ",
    )
    @click.option(
        "--mpassword",
        metavar="<manager-password>",
        envvar="MANAGER_PASSWORD",
        prompt="SD-WAN Manager password",
        hide_input=True,
        help="SD-WAN Manager password, can also be defined via "
        "MANAGER_PASSWORD environment variable. ",
    )
    @functools.wraps(f)
    def wrapper_common_options(*args: Any, **kwargs: Any) -> Any:
        return f(*args, **kwargs)

    return wrapper_common_options


def gateway_mask_options(f: Callable[..., Any]) -> Callable[..., Any]:
    @click.option(
        "--mmask",
        metavar="<manager-mask>",
        envvar="MANAGER_MASK",
        help="Subnet mask for given SD-WAN Manager IP (e.g. /24), can also be defined "
        "via MANAGER_MASK environment variable. ",
    )
    @click.option(
        "--mgateway",
        metavar="<manager-gateway>",
        envvar="MANAGER_GATEWAY",
        help="Gateway IP for given SD-WAN Manager IP, can also be defined via MANAGER_GATEWAY "
        "environment variable. ",
    )
    @functools.wraps(f)
    def wrapper_common_options(*args: Any, **kwargs: Any) -> Any:
        return f(*args, **kwargs)

    return wrapper_common_options


@cli.command(
    name="deploy",
    short_help="Deploy a new Catalyst SD-WAN lab pod.",
    no_args_is_help=True,
)
@manager_options
@gateway_mask_options
@click.argument("software_version", metavar="<software-version>")
@click.option(
    "--lab",
    metavar="<lab_name>",
    envvar="LAB_NAME",
    help="Set CML Lab name, can also be defined via "
    "LAB_NAME environment variable. "
    'If not provided, default name "sdwan<number>" will be assigned.',
)
@click.option(
    "--bridge",
    metavar="<custom-bridge-name>",
    default="System Bridge",
    help="Set custom bridge for SD-WAN Manager external connection."
    " Default is System Bridge",
)
@click.option(
    "--dns",
    metavar="<dns-server-ip>",
    default="192.168.255.1",
    help="Set custom DNS server for Internet/VPN0 transport. "
    "Default is same as CML DNS",
)
@click.option(
    "--retry",
    is_flag=True,
    default=False,
    help="If for some reason your script lost connectivity "
    "during SD-WAN Manager boot, you can add --retry to continue "
    "onboarding the lab that is already in CML",
)
@click.pass_context
def cli_deploy(
    ctx: click.Context,
    manager: str,
    mmask: str,
    mgateway: str,
    muser: str,
    mpassword: str,
    software_version: str,
    lab: str,
    bridge: str,
    dns: str,
    retry: bool,
) -> None:
    """
    \b
    positional arguments:
      <software-version>    Software version that will be used on SD-WAN Control Components.
    """
    cml_config = ctx.obj["CML_CONFIG"]
    cml_ip = cml_config.url
    loglevel = ctx.obj["LOGLEVEL"]
    patty_used, manager_ip, manager_port = set_manager_details(cml_ip, manager)

    if not patty_used and not mgateway:
        mmask = click.prompt("SD-WAN Manager subnet mask (e.g. /24)")
    if not patty_used and not mgateway:
        mgateway = click.prompt("SD-WAN Manager gateway IP")

    deploy.main(
        cml_config,
        manager_ip,
        manager_port,
        mmask,
        mgateway,
        muser,
        mpassword,
        software_version,
        lab,
        bridge,
        dns,
        patty_used,
        retry,
        loglevel,
    )


@cli.command(
    name="add",
    short_help="Add Catalyst SD-WAN device to running lab pod.",
    no_args_is_help=True,
)
@manager_options
@click.option(
    "--lab",
    metavar="<lab_name>",
    envvar="LAB_NAME",
    prompt="CML lab name",
    help="CML Lab name, can also be defined via LAB_NAME environment variable. ",
)
@click.argument(
    "number_of_devices",
    metavar="<number-of-devices>",
    type=int,
)
@click.argument(
    "device_type",
    metavar="<device-type>",
)
@click.argument(
    "software_version",
    metavar="<software-version>",
)
@click.pass_context
def cli_add(
    ctx: click.Context,
    manager: str,
    muser: str,
    mpassword: str,
    lab: str,
    number_of_devices: int,
    device_type: str,
    software_version: str,
) -> None:
    """
    \b
    positional arguments:
      <number-of-devices>   Number of devices to be added.
      <device-type>         Type of device/s to be added (e.g. validator, controller, edge, sdrouting).
      <software-version>    Software version that will be used.
    """
    cml_config = ctx.obj["CML_CONFIG"]
    cml_ip = cml_config.url
    _, manager_ip, manager_port = set_manager_details(cml_ip, manager)
    device_type = device_type.lower()
    loglevel = ctx.obj["LOGLEVEL"]

    add.main(
        cml_config,
        manager_ip,
        manager_port,
        muser,
        mpassword,
        lab,
        number_of_devices,
        device_type,
        software_version,
        loglevel,
    )


@cli.command(
    name="backup",
    short_help="Backup running Catalyst SD-WAN lab pod.",
)
@manager_options
@click.option(
    "--lab",
    metavar="<lab_name>",
    envvar="LAB_NAME",
    prompt="CML lab name",
    help="CML Lab name, can also be defined via LAB_NAME environment variable. ",
)
@click.option(
    "--workdir",
    metavar="<directory>",
    prompt="Directory to save backup",
    default="backup",
    help="Backup destination folder",
)
@click.pass_context
def cli_backup(
    ctx: click.Context, manager: str, muser: str, mpassword: str, lab: str, workdir: str
) -> None:
    cml_config = ctx.obj["CML_CONFIG"]
    cml_ip = cml_config.url
    _, manager_ip, manager_port = set_manager_details(cml_ip, manager)
    loglevel = ctx.obj["LOGLEVEL"]

    backup.main(
        cml_config,
        manager_ip,
        manager_port,
        muser,
        mpassword,
        lab,
        workdir,
        loglevel,
    )


@cli.command(
    name="restore",
    short_help="Restore Catalyst SD-WAN POD from backup.",
)
@manager_options
@gateway_mask_options
@click.option(
    "--lab",
    metavar="<lab_name>",
    envvar="LAB_NAME",
    prompt="CML lab name",
    help="CML Lab name, can also be defined via LAB_NAME environment variable. ",
)
@click.option(
    "--workdir",
    metavar="<directory>",
    prompt="Directory to restore",
    default="backup",
    help="Restore source folder",
)
@click.option(
    "--deleteexisting",
    is_flag=True,
    default=False,
    help="If there is already lab running with same name and using same "
    "SD-WAN Manager IP, delete this lab before restoring. "
    "Note the all running lab data will be lost!",
)
@click.option(
    "--retry",
    is_flag=True,
    default=False,
    help="If for some reason your script lost connectivity "
    "during SD-WAN Manager boot, you can add --retry to continue "
    "onboarding the lab that is already in CML",
)
@click.pass_context
def cli_restore(
    ctx: click.Context,
    manager: str,
    mmask: str,
    mgateway: str,
    muser: str,
    mpassword: str,
    lab: str,
    workdir: str,
    deleteexisting: bool,
    retry: bool,
) -> None:
    cml_config = ctx.obj["CML_CONFIG"]
    cml_ip = cml_config.url
    patty_used, manager_ip, manager_port = set_manager_details(cml_ip, manager)

    if not patty_used and not mgateway:
        mmask = click.prompt("SD-WAN Manager subnet mask (e.g. /24)")
    if not patty_used and not mgateway:
        mgateway = click.prompt("SD-WAN Manager gateway IP")

    loglevel = ctx.obj["LOGLEVEL"]

    restore.main(
        cml_config,
        manager_ip,
        manager_port,
        mmask,
        mgateway,
        muser,
        mpassword,
        workdir,
        lab,
        patty_used,
        deleteexisting,
        retry,
        loglevel,
    )


@cli.command(
    name="delete",
    short_help="Delete the CML lab and all the lab data.",
)
@click.option(
    "--lab",
    metavar="<lab_name>",
    envvar="LAB_NAME",
    prompt="CML lab name",
    help="CML Lab name, can also be defined via LAB_NAME environment variable. ",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Delete the lab without asking for confirmation. "
    "Note the all lab data will be lost!",
)
@click.pass_context
def cli_delete(ctx: click.Context, lab: str, force: bool) -> None:
    cml_config = ctx.obj["CML_CONFIG"]
    loglevel = ctx.obj["LOGLEVEL"]
    delete.main(cml_config, lab, force, loglevel)


@cli.command(
    name="sign",
    short_help="Sign CSR using the SD-WAN Lab Deployment Tool Root CA.",
    no_args_is_help=True,
)
@click.argument(
    "csr_file",
    metavar="<csr_file>",
)
@click.pass_context
def cli_sign(ctx: click.Context, csr_file: str) -> None:
    """
    \b
    positional arguments:
      <csr_file>  Certificate Signing Request (CSR) File
    """
    loglevel = ctx.obj["LOGLEVEL"]
    sign.main(
        csr_file,
        loglevel,
    )

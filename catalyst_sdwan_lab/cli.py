import functools
import logging
import re
import sys
from collections.abc import Callable
from typing import Any

import rich_click as click
import urllib3
from urllib3.exceptions import InsecureRequestWarning
from virl2_client import ClientLibrary

import catalyst_sdwan_lab

from .tasks import add, backup, delete, deploy, restore, setup, sign

urllib3.disable_warnings(InsecureRequestWarning)


def verify_cml_version(cml: ClientLibrary) -> None:
    if cml.VERSION.major == 2 and cml.VERSION.minor >= 6:
        pass
    else:
        exit("Upgrade CML to 2.6 or later to use the tool.")


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


@click.group()
@click.version_option(version=f"SD-WAN Lab Version {catalyst_sdwan_lab.__version__}")
@click.option("-v", "--verbose", count=True)
@click.option(
    "--cml",
    "-c",
    metavar="<cml-ip>",
    envvar="CML_IP",
    help="CML IP address, can also be defined via CML_IP environment variable. "
    "If neither is provided user is prompted for CML IP.",
)
@click.option(
    "--user",
    "-u",
    metavar="<cml-user>",
    envvar="CML_USER",
    help="CML username, can also be defined via CML_USER environment variable. "
    "If neither is provided user is prompted for CML username.",
)
@click.option(
    "--password",
    "-p",
    metavar="<cml-password>",
    envvar="CML_PASSWORD",
    hide_input=True,
    help="CML password, can also be defined via CML_PASSWORD environment variable. "
    " If neither is provided user is prompted for CML password.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    verbose: int,
    cml: str,
    user: str,
    password: str,
) -> None:
    if ctx.resilient_parsing or ctx.invoked_subcommand is None or "--help" in sys.argv:
        return

    ctx.ensure_object(dict)
    loglevel = max(logging.DEBUG, logging.WARNING - 10 * verbose)
    ctx.obj["LOGLEVEL"] = loglevel
    log = logging.getLogger(__name__)
    log.setLevel(loglevel)
    logger = logging.getLogger("virl2_client.virl2_client")
    logger.addFilter(
        lambda record: "SSL Verification disabled" not in record.getMessage()
    )
    logging.basicConfig(format="%(levelname)s - %(message)s")
    urllib3.disable_warnings(InsecureRequestWarning)

    if ctx.invoked_subcommand == "sign":
        return

    cml = cml if cml else click.prompt("CML IP address")
    user = user if user else click.prompt("CML username")
    password = password if password else click.prompt("CML password", hide_input=True)
    ctx.obj["CML_IP"] = cml
    ctx.obj["CML_USER"] = user
    ctx.obj["CML_PASSWORD"] = password
    cml_instance = ClientLibrary(cml, user, password, ssl_verify=False)
    ctx.obj["CML"] = cml_instance
    verify_cml_version(cml_instance)


@cli.command(
    name="setup", short_help="Setup on-prem CML to use Catalyst SD-WAN Lab automation."
)
@click.option(
    "--list",
    "-l",
    "list_",
    is_flag=True,
    help="After running setup task, list the available SD-WAN software per node type.",
)
@click.pass_context
def cli_setup(ctx: click.Context, list_: bool) -> None:
    setup.main(ctx.obj["CML"], ctx.obj["LOGLEVEL"], list_)


def manager_options(f: Callable[..., Any]) -> Callable[..., Any]:
    @click.option(
        "--manager",
        metavar="<manager-ip>",
        envvar="MANAGER_IP",
        prompt="SD-WAN Manager IP address",
        help="SD-WAN Manager IP address, can also be defined via MANAGER_IP environment "
        "variable. If neither is provided user is prompted for SD-WAN Manager IP.",
    )
    @click.option(
        "--muser",
        metavar="<manager-user>",
        envvar="MANAGER_USER",
        prompt="SD-WAN Manager user",
        help="SD-WAN Manager username, can also be defined via "
        "MANAGER_USER environment variable. "
        "If neither is provided user is prompted for SD-WAN Manager username.",
    )
    @click.option(
        "--mpassword",
        metavar="<manager-password>",
        envvar="MANAGER_PASSWORD",
        prompt="SD-WAN Manager password",
        hide_input=True,
        help="SD-WAN Manager password, can also be defined via "
        "MANAGER_PASSWORD environment variable. "
        " If neither is provided user is prompted for SD-WAN Manager password.",
    )
    @functools.wraps(f)
    def wrapper_common_options(*args: Any, **kwargs: Any) -> Any:
        return f(*args, **kwargs)

    return wrapper_common_options


@cli.command(name="deploy", short_help="Deploy a new Catalyst SD-WAN lab pod.")
@manager_options
@click.argument("software_version", metavar="<software-version>")
@click.option(
    "--mmask",
    metavar="<manager-mask>",
    envvar="MANAGER_MASK",
    prompt="SD-WAN Manager subnet mask (e.g. /24)",
    help="Subnet mask for given SD-WAN Manager IP (e.g. /24), can also be defined "
    "via MANAGER_MASK environment variable. "
    "If neither is provided user is prompted for SD-WAN Manager subnet mask.",
)
@click.option(
    "--mgateway",
    metavar="<manager-gateway>",
    envvar="MANAGER_GATEWAY",
    prompt="SD-WAN Manager gateway IP",
    help="Gateway IP for given SD-WAN Manager IP, can also be defined via MANAGER_GATEWAY "
    "environment variable. "
    "If neither is provided user is prompted for Manager gateway IP.",
)
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
    cml = ctx.obj["CML"]
    cml_ip = ctx.obj["CML_IP"]
    loglevel = ctx.obj["LOGLEVEL"]
    patty_used, manager_ip, manager_port = set_manager_details(cml_ip, manager)

    deploy.main(
        cml,
        cml_ip,
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


@cli.command(name="add", short_help="Add Catalyst SD-WAN device to running lab pod.")
@manager_options
@click.option(
    "--lab",
    metavar="<lab_name>",
    envvar="LAB_NAME",
    prompt="CML lab name",
    help="CML Lab name, can also be defined via LAB_NAME environment variable. "
    "If neither is provided user is prompted for lab name.",
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
    cml = ctx.obj["CML"]
    user = ctx.obj["CML_USER"]
    password = ctx.obj["CML_PASSWORD"]
    cml_ip = ctx.obj["CML_IP"]
    _, manager_ip, manager_port = set_manager_details(cml_ip, manager)
    device_type = device_type.lower()
    loglevel = ctx.obj["LOGLEVEL"]
    add.main(
        cml,
        user,
        password,
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


@cli.command(name="backup", short_help="Backup running Catalyst SD-WAN lab pod.")
@manager_options
@click.option(
    "--lab",
    metavar="<lab_name>",
    envvar="LAB_NAME",
    prompt="CML lab name",
    help="CML Lab name, can also be defined via LAB_NAME environment variable. "
    "If neither is provided user is prompted for lab name.",
)
@click.option(
    "--workdir",
    metavar="<directory>",
    prompt="Directory to save backup",
    help="Backup destination folder",
)
@click.pass_context
def cli_backup(
    ctx: click.Context, manager: str, muser: str, mpassword: str, lab: str, workdir: str
) -> None:
    cml = ctx.obj["CML"]
    user = ctx.obj["CML_USER"]
    password = ctx.obj["CML_PASSWORD"]
    cml_ip = ctx.obj["CML_IP"]
    _, manager_ip, manager_port = set_manager_details(cml_ip, manager)
    loglevel = ctx.obj["LOGLEVEL"]

    backup.main(
        cml,
        user,
        password,
        manager_ip,
        manager_port,
        muser,
        mpassword,
        lab,
        workdir,
        loglevel,
    )


@cli.command(name="restore", short_help="Restore Catalyst SD-WAN POD from backup.")
@manager_options
@click.option(
    "--mmask",
    metavar="<manager-mask>",
    envvar="MANAGER_MASK",
    prompt="SD-WAN Manager subnet mask (e.g. /24)",
    help="Subnet mask for given SD-WAN Manager IP (e.g. /24), can also be defined "
    "via MANAGER_MASK environment variable. "
    "If neither is provided user is prompted for SD-WAN Manager subnet mask.",
)
@click.option(
    "--mgateway",
    metavar="<manager-gateway>",
    envvar="MANAGER_GATEWAY",
    prompt="SD-WAN Manager gateway IP",
    help="Gateway IP for given SD-WAN Manager IP, can also be defined via MANAGER_GATEWAY "
    "environment variable. "
    "If neither is provided user is prompted for Manager gateway IP.",
)
@click.option(
    "--lab",
    metavar="<lab_name>",
    envvar="LAB_NAME",
    prompt="CML lab name",
    help="CML Lab name, can also be defined via LAB_NAME environment variable. "
    "If neither is provided user is prompted for lab name.",
)
@click.option(
    "--workdir",
    metavar="<directory>",
    prompt="Directory to restore",
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
    cml = ctx.obj["CML"]
    cml_ip = ctx.obj["CML_IP"]
    patty_used, manager_ip, manager_port = set_manager_details(cml_ip, manager)
    loglevel = ctx.obj["LOGLEVEL"]

    restore.main(
        cml,
        cml_ip,
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


@cli.command(name="delete", short_help="Delete the CML lab and all the lab data.")
@click.option(
    "--lab",
    metavar="<lab_name>",
    envvar="LAB_NAME",
    prompt="CML lab name",
    help="CML Lab name, can also be defined via LAB_NAME environment variable. "
    "If neither is provided user is prompted for lab name.",
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
    cml = ctx.obj["CML"]
    loglevel = ctx.obj["LOGLEVEL"]
    delete.main(cml, lab, force, loglevel)


@cli.command(
    name="sign", short_help="Sign CSR using the SD-WAN Lab Deployment Tool Root CA."
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

from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.logging import RichHandler

from catalyst_sdwan_lab import __version__
from catalyst_sdwan_lab.tasks import add as _add
from catalyst_sdwan_lab.tasks import delete as _delete
from catalyst_sdwan_lab.tasks import deploy as _deploy
from catalyst_sdwan_lab.tasks import images as _images
from catalyst_sdwan_lab.tasks import setup as _setup
from catalyst_sdwan_lab.tasks import sign as _sign
from catalyst_sdwan_lab.tasks.utils import DEFAULT_SERIAL_FILE, console

app = typer.Typer(no_args_is_help=True)
images_app = typer.Typer(no_args_is_help=True)
app.add_typer(images_app, name="images", help="Manage SD-WAN software images in CML.")


@dataclass
class _State:
    cml_host: str | None = None
    cml_user: str | None = None
    cml_password: str | None = None
    verbose: bool = False
    debug: bool = False


_state = _State()
log = logging.getLogger(__name__)


def _configure_logging(verbose: bool, debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO if verbose else logging.WARNING
    handler = RichHandler(console=console, show_time=False, show_path=False)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(level=level, handlers=[handler])
    logging.getLogger("virl2_client.virl2_client").addFilter(
        lambda r: "SSL Verification disabled" not in r.getMessage()
        and "Unable to authenticate" not in r.getMessage()
    )
    logging.getLogger("urllib3.connectionpool").addFilter(
        lambda r: "Max retries exceeded" not in r.getMessage()
        and "Failed to establish a new connection" not in r.getMessage()
    )
    if not debug:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _cml_credentials() -> tuple[str, str, str]:
    if not _state.cml_host or not _state.cml_user or not _state.cml_password:
        log.error(
            "CML credentials required. Use --cml / --user / --password "
            "or set CML_IP / CML_USER / CML_PASSWORD environment variables."
        )
        raise typer.Exit(1)
    return _state.cml_host, _state.cml_user, _state.cml_password


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"catalyst-sdwan-lab {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    cml_host: Annotated[
        Optional[str], typer.Option("--cml", "-c", envvar="CML_IP", help="CML hostname or IP")
    ] = None,
    cml_user: Annotated[
        Optional[str], typer.Option("--user", "-u", envvar="CML_USER", help="CML username")
    ] = None,
    cml_password: Annotated[
        Optional[str],
        typer.Option(
            "--password", "-p", envvar="CML_PASSWORD", help="CML password", hide_input=True
        ),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show INFO level output")
    ] = False,
    debug: Annotated[
        bool, typer.Option("--debug", help="Show DEBUG level output including HTTP requests")
    ] = False,
    _version: Annotated[
        Optional[bool],
        typer.Option(
            "--version", callback=_version_callback, is_eager=True,
            expose_value=False, help="Show version and exit",
        ),
    ] = None,
) -> None:
    _state.cml_host = cml_host
    _state.cml_user = cml_user
    _state.cml_password = cml_password
    _state.verbose = verbose
    _state.debug = debug
    _configure_logging(verbose, debug)


@app.command()
def setup() -> None:
    """Prepare CML for SD-WAN lab deployment. Run after install and after tool upgrades."""
    _setup.run(*_cml_credentials())


@app.command()
def deploy(
    version: Annotated[str, typer.Argument(help="SD-WAN software version (e.g. 20.15.1)")],
    manager_ip: Annotated[
        Optional[str],
        typer.Option("--manager-ip", envvar="MANAGER_IP", help="Manager IP (direct mode)"),
    ] = None,
    manager_port: Annotated[
        Optional[int],
        typer.Option("--manager-port", envvar="MANAGER_PORT", help="PATty port; enables PATty"),
    ] = None,
    manager_user: Annotated[
        str, typer.Option("--manager-user", envvar="MANAGER_USER", help="Manager username")
    ] = "admin",
    manager_pass: Annotated[
        str,
        typer.Option(
            "--manager-pass", envvar="MANAGER_PASSWORD", help="Manager password", hide_input=True
        ),
    ] = ...,  # type: ignore[assignment]
    manager_mask: Annotated[
        Optional[str],
        typer.Option("--manager-mask", envvar="MANAGER_MASK", help="Manager mask (direct mode)"),
    ] = None,
    manager_gateway: Annotated[
        Optional[str],
        typer.Option(
            "--manager-gateway", envvar="MANAGER_GATEWAY", help="Manager gateway (direct mode)"
        ),
    ] = None,
    lab_name: Annotated[
        str,
        typer.Option("--lab", envvar="LAB_NAME", help="CML lab name"),
    ] = ...,  # type: ignore[assignment]
    bridge: Annotated[
        Optional[str], typer.Option("--bridge", help="CML bridge name (direct mode)")
    ] = None,
    dns_server: Annotated[
        str, typer.Option("--dns", help="DNS server for lab nodes")
    ] = "192.168.255.1",
    ip_type: Annotated[
        str, typer.Option("--ip-type", help="IP addressing: v4, v6, or dual")
    ] = "v4",
    retry: Annotated[
        bool, typer.Option("--retry", help="Skip lab creation and resume Manager onboarding")
    ] = False,
    serial_file: Annotated[
        Optional[Path],
        typer.Option("--serial-file", help="Custom .viptela file (org name extracted for file)"),
    ] = None,
) -> None:
    """Deploy a Catalyst SD-WAN lab in CML."""
    if manager_ip and manager_ip.lower().startswith("pat:"):
        log.error(
            "The 'pat:<port>' syntax is no longer supported. "
            "Use --manager-port <port> instead."
        )
        raise typer.Exit(1)
    if manager_ip:
        try:
            ipaddress.ip_address(manager_ip)
        except ValueError:
            log.error("--manager-ip must be a valid IP address, got: %s", manager_ip)
            raise typer.Exit(1)
    patty = manager_port is not None
    if patty:
        if manager_ip or manager_mask or manager_gateway or bridge:
            log.error(
                "--manager-port (PATty mode) cannot be combined with "
                "--manager-ip, --manager-mask, --manager-gateway, or --bridge."
            )
            raise typer.Exit(1)
    elif not any([manager_ip, manager_mask, manager_gateway]):
        log.error(
            "Specify --manager-port for PATty mode, or "
            "--manager-ip / --manager-mask / --manager-gateway for direct mode."
        )
        raise typer.Exit(1)
    else:
        missing = [
            name
            for name, val in [
                ("--manager-ip", manager_ip),
                ("--manager-mask", manager_mask),
                ("--manager-gateway", manager_gateway),
            ]
            if not val
        ]
        if missing:
            log.error("Direct mode requires: %s", ", ".join(missing))
            raise typer.Exit(1)
    cml_host, cml_user, cml_password = _cml_credentials()
    _deploy.run(
        cml_host=cml_host,
        cml_user=cml_user,
        cml_password=cml_password,
        manager_ip=cml_host if patty else manager_ip or "",
        manager_port=manager_port or 443,
        manager_user=manager_user,
        manager_password=manager_pass,
        manager_mask=manager_mask or "",
        manager_gateway=manager_gateway or "",
        version=version,
        lab_name=lab_name,
        bridge=bridge or "System Bridge",
        dns_server=dns_server,
        ip_type=ip_type,
        retry=retry,
        patty=patty,
        serial_file=serial_file or DEFAULT_SERIAL_FILE,
    )


@app.command()
def delete(
    lab_name: Annotated[
        str, typer.Option("--lab", envvar="LAB_NAME", help="CML lab name")
    ] = ...,  # type: ignore[assignment]
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Skip confirmation prompt")
    ] = False,
) -> None:
    """Delete a Catalyst SD-WAN lab from CML."""
    _delete.run(*_cml_credentials(), lab_name, force=force)


@app.command()
def sign(
    csr_file: Annotated[Path, typer.Argument(help="Path to CSR file (.txt or .pem)")],
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Write signed certificate to file")
    ] = None,
) -> None:
    """Sign a CSR with the lab CA and print the certificate to stdout."""
    _sign.run(csr_file, output)


_DEVICE_TYPES: dict[str, str] = {
    "controller": "controller",
    "controllers": "controller",
    "validator": "validator",
    "validators": "validator",
    "edge": "edge",
    "edges": "edge",
    "sdrouting": "sdrouting",
}


@app.command()
def add(
    count: Annotated[int, typer.Argument(help="Number of devices to add")],
    device_type: Annotated[
        str, typer.Argument(help="Device type: controller(s), validator(s), edge(s), sdrouting")
    ],
    version: Annotated[str, typer.Argument(help="SD-WAN software version (e.g. 20.15.1)")],
    lab_name: Annotated[
        str, typer.Option("--lab", envvar="LAB_NAME", help="CML lab name")
    ] = ...,  # type: ignore[assignment]
    manager_user: Annotated[
        str, typer.Option("--manager-user", envvar="MANAGER_USER", help="Manager username")
    ] = "admin",
    manager_pass: Annotated[
        str,
        typer.Option(
            "--manager-pass", envvar="MANAGER_PASSWORD", help="Manager password", hide_input=True
        ),
    ] = ...,  # type: ignore[assignment]
) -> None:
    """Add devices to an existing SD-WAN lab."""
    device = _DEVICE_TYPES.get(device_type.lower())
    if device is None:
        log.error(
            "Unknown device type '%s'. Valid: controller, validator, edge, sdrouting.", device_type
        )
        raise typer.Exit(1)
    if device == "controller":
        _add.run_controller(
            *_cml_credentials(),
            lab_name=lab_name,
            version=version,
            manager_user=manager_user,
            manager_password=manager_pass,
            count=count,
        )
    elif device == "validator":
        _add.run_validator(
            *_cml_credentials(),
            lab_name=lab_name,
            version=version,
            manager_user=manager_user,
            manager_password=manager_pass,
            count=count,
        )
    else:
        log.error("Device type '%s' not yet implemented.", device)
        raise typer.Exit(1)


@images_app.command(name="list")
def images_list() -> None:
    """List SD-WAN software versions installed in CML."""
    _images.list_versions(*_cml_credentials())


@images_app.command(name="upload")
def images_upload(
    images_dir: Annotated[
        Optional[Path], typer.Option("--dir", "-d", help="Directory containing .qcow2 files")
    ] = None,
) -> None:
    """Upload SD-WAN software images to CML."""
    _images.upload(*_cml_credentials(), images_dir or Path.cwd())


@images_app.command(name="delete")
def images_delete(
    versions: Annotated[
        list[str], typer.Argument(help="Software versions to delete (e.g. 20.12.1)")
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be deleted without deleting")
    ] = False,
) -> None:
    """Delete SD-WAN image definitions and files from CML."""
    _images.delete(*_cml_credentials(), versions, dry_run=dry_run)

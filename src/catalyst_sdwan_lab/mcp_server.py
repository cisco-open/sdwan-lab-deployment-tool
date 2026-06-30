"""
Catalyst SD-WAN Lab – MCP Server

Exposes the tool's capabilities as MCP tools so that an LLM-based agent
can deploy, manage, and query SD-WAN labs in CML conversationally.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

from catalyst_sdwan_lab.tasks import add as _add
from catalyst_sdwan_lab.tasks import backup as _backup
from catalyst_sdwan_lab.tasks import delete as _delete
from catalyst_sdwan_lab.tasks import deploy as _deploy
from catalyst_sdwan_lab.tasks import images as _images
from catalyst_sdwan_lab.tasks import restore as _restore
from catalyst_sdwan_lab.tasks import setup as _setup
from catalyst_sdwan_lab.tasks import sign as _sign
from catalyst_sdwan_lab.tasks.utils import DEFAULT_SERIAL_FILE

from ._mcp_adapter import capture_task_async, poll_job, start_job

log = logging.getLogger(__name__)

mcp = FastMCP(
    "catalyst-sdwan-lab",
    instructions=(
        "Automates Cisco Catalyst SD-WAN lab deployment and management inside "
        "Cisco Modeling Labs (CML). Provides tools to deploy full control-plane "
        "topologies, add edge devices, manage software images, back up and "
        "restore labs, and more.\n\n"
        "Key workflow: setup (once) → deploy → add_devices.\n"
        "Two connectivity modes: PATty (manager_port) or Direct (manager_ip + mask + gateway).\n"
        "Always confirm with the user before calling delete_lab.\n\n"
        "IMPORTANT — long-running tools run as BACKGROUND JOBS. deploy, restore, "
        "add_devices, and images_upload return immediately with a job_id instead "
        "of blocking until completion. To follow progress, repeatedly call "
        "job_status(job_id=...): each call long-polls and returns the log events "
        "that occurred since your last poll, plus the final result once status is "
        "'done' or 'error'. Keep polling until the job finishes.\n"
        "Some jobs surface ACTION REQUIRED events mid-run (e.g. Cisco PKI "
        "registration prints a URL the user must open in a browser). Relay such "
        "events to the user as soon as job_status returns them, then continue "
        "polling — the job resumes automatically once the user completes the action."
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _started(label: str, job_id: str) -> str:
    """Standard response when a long-running tool is dispatched as a background job."""
    return (
        f"Started background job '{job_id}' ({label}). It runs asynchronously; "
        f"results are NOT returned by this call. Call job_status(job_id=\"{job_id}\") "
        f"to stream progress events and retrieve the final result. Poll repeatedly "
        f"until status is 'done' or 'error'. Watch for ACTION REQUIRED events and "
        f"relay them to the user immediately."
    )


def _cml_creds(
    cml_host: str | None = None,
    cml_user: str | None = None,
    cml_password: str | None = None,
) -> tuple[str, str, str]:
    """Resolve CML credentials from arguments or environment variables."""
    host = cml_host or os.environ.get("CML_IP", "")
    user = cml_user or os.environ.get("CML_USER", "")
    password = cml_password or os.environ.get("CML_PASSWORD", "")
    if not host:
        raise ValueError("CML host is required (pass cml_host or set CML_IP env var)")
    if not user:
        raise ValueError("CML user is required (pass cml_user or set CML_USER env var)")
    if not password:
        raise ValueError("CML password is required (pass cml_password or set CML_PASSWORD env var)")
    return host, user, password


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def setup(
    ctx: Context,
    cml_host: str | None = None,
    cml_user: str | None = None,
    cml_password: str | None = None,
) -> str:
    """
    Prepare CML for SD-WAN lab deployment.

    Creates the required SD-WAN node definitions in CML. Run this once after
    installing the tool and after upgrades.

    Args:
        cml_host: CML hostname or IP (or set CML_IP env var)
        cml_user: CML username (or set CML_USER env var)
        cml_password: CML password (or set CML_PASSWORD env var)
    """
    host, user, password = _cml_creds(cml_host, cml_user, cml_password)
    return await capture_task_async(ctx, _setup.run, host, user, password)


@mcp.tool()
async def deploy(
    ctx: Context,
    version: str,
    manager_password: str,
    lab_name: str,
    manager_port: int | None = None,
    manager_ip: str | None = None,
    manager_mask: str | None = None,
    manager_gateway: str | None = None,
    manager_user: str = "admin",
    bridge: str | None = None,
    dns_server: str = "192.168.255.1",
    ip_type: str = "v4",
    retry: bool = False,
    serial_file: str | None = None,
    pki: str = "enterprise",
    proxy_ip: str = "",
    proxy_port: str = "80",
    no_proxy: str = "",
    cml_host: str | None = None,
    cml_user: str | None = None,
    cml_password: str | None = None,
) -> str:
    """
    Deploy a complete Catalyst SD-WAN lab in CML.

    Creates a CML topology with INET/MPLS underlay, Manager, Validator,
    Controller, and a Gateway router. Then waits for all nodes to boot and
    configures the SD-WAN control plane (certificates, onboarding, templates).

    This is a long-running operation (5-15 minutes typically).

    Connectivity modes:
    - PATty mode: provide manager_port (Manager accessed via CML port mapping)
    - Direct mode: provide manager_ip, manager_mask, and manager_gateway

    Args:
        version: SD-WAN software version (e.g. "20.15.1")
        manager_password: SD-WAN Manager password (must not be "admin")
        lab_name: Name for the CML lab
        manager_port: PATty external port (enables PATty mode)
        manager_ip: Manager IP address (direct mode)
        manager_mask: Subnet mask e.g. "/24" (direct mode)
        manager_gateway: Default gateway (direct mode)
        manager_user: Manager username (default: "admin")
        bridge: CML bridge name (direct mode, default: "System Bridge")
        dns_server: DNS server for lab nodes (default: "192.168.255.1")
        ip_type: Overlay IP type: "v4", "v6", or "dual"
        retry: Resume Manager onboarding without recreating the lab
        serial_file: Path to custom .viptela serial file
        pki: Certificate signing mode: "enterprise" or "cisco"
        proxy_ip: HTTP proxy hostname or IP
        proxy_port: HTTP proxy port (default: "80")
        no_proxy: Additional no-proxy entries
        cml_host: CML hostname or IP (or set CML_IP env var)
        cml_user: CML username (or set CML_USER env var)
        cml_password: CML password (or set CML_PASSWORD env var)
    """
    host, user, password = _cml_creds(cml_host, cml_user, cml_password)

    patty = manager_port is not None
    if patty and any([manager_ip, manager_mask, manager_gateway, bridge]):
        return (
            "Error: manager_port (PATty mode) cannot be combined with "
            "manager_ip/mask/gateway/bridge."
        )
    if not patty and not all([manager_ip, manager_mask, manager_gateway]):
        return (
            "Error: Provide manager_port for PATty mode, or all of "
            "manager_ip + manager_mask + manager_gateway for direct mode."
        )

    job_id = start_job(
        "deploy",
        _deploy.run,
        cml_host=host,
        cml_user=user,
        cml_password=password,
        manager_ip=host if patty else manager_ip or "",
        manager_port=manager_port or 443,
        manager_user=manager_user,
        manager_password=manager_password,
        manager_mask=manager_mask or "",
        manager_gateway=manager_gateway or "",
        version=version,
        lab_name=lab_name,
        bridge=bridge or "System Bridge",
        dns_server=dns_server,
        ip_type=ip_type,
        retry=retry,
        patty=patty,
        serial_file=Path(serial_file) if serial_file else DEFAULT_SERIAL_FILE,
        pki=pki,
        proxy_ip=proxy_ip,
        proxy_port=proxy_port,
        no_proxy=no_proxy,
    )
    return _started("deploy", job_id)


@mcp.tool()
async def add_devices(
    ctx: Context,
    count: int,
    device_type: str,
    version: str,
    lab_name: str,
    manager_password: str,
    manager_user: str = "admin",
    persona: str = "compute-and-data",
    cpus: int | None = None,
    ram: int | None = None,
    cml_host: str | None = None,
    cml_user: str | None = None,
    cml_password: str | None = None,
) -> str:
    """
    Add and onboard SD-WAN devices to an existing lab.

    Detects the lab's IP type (v4/v6/dual) automatically.
    This is a long-running operation for edge devices and managers (boots VMs,
    generates certificates, waits for onboarding).

    Args:
        count: Number of devices to add (minimum 1)
        device_type: One of "manager", "controller", "validator", "edge", "sdrouting"
        version: SD-WAN software version (e.g. "20.15.1")
        lab_name: Name of the existing CML lab
        manager_password: SD-WAN Manager password
        manager_user: Manager username (default: "admin")
        persona: Manager cluster persona — "compute-and-data" (default), "compute", or "data"
                 Only applies when device_type is "manager"
        cpus: Override number of CPUs per node
        ram: Override RAM in MB per node
        cml_host: CML hostname or IP (or set CML_IP env var)
        cml_user: CML username (or set CML_USER env var)
        cml_password: CML password (or set CML_PASSWORD env var)
    """
    host, user, password = _cml_creds(cml_host, cml_user, cml_password)

    valid_types = {"manager", "controller", "validator", "edge", "sdrouting"}
    device = device_type.lower().rstrip("s")  # allow plurals
    if device not in valid_types:
        valid = ", ".join(sorted(valid_types))
        return f"Error: Unknown device_type '{device_type}'. Valid: {valid}"
    if count < 1:
        return "Error: count must be at least 1."

    valid_personas = {"compute-and-data", "compute", "data"}
    if persona not in valid_personas:
        return f"Error: Unknown persona '{persona}'. Valid: {', '.join(sorted(valid_personas))}"

    if device == "manager":
        from .cli import ManagerPersona
        job_id = start_job(
            "add_devices",
            _add.run_managers,
            host, user, password,
            lab_name=lab_name,
            version=version,
            manager_user=manager_user,
            manager_password=manager_password,
            count=count,
            persona=ManagerPersona(persona).to_api_value(),
            cpus=cpus,
            ram=ram,
        )
    elif device in ("controller", "validator"):
        job_id = start_job(
            "add_devices",
            _add.run_control_component,
            host, user, password,
            lab_name=lab_name,
            version=version,
            manager_user=manager_user,
            manager_password=manager_password,
            count=count,
            device_type=device,
            cpus=cpus,
            ram=ram,
        )
    elif device == "edge":
        job_id = start_job(
            "add_devices",
            _add.run_edge,
            host, user, password,
            lab_name=lab_name,
            version=version,
            manager_user=manager_user,
            manager_password=manager_password,
            count=count,
            cpus=cpus,
            ram=ram,
        )
    else:  # sdrouting
        job_id = start_job(
            "add_devices",
            _add.run_sdrouting,
            host, user, password,
            lab_name=lab_name,
            version=version,
            manager_user=manager_user,
            manager_password=manager_password,
            count=count,
            cpus=cpus,
            ram=ram,
        )
    return _started("add_devices", job_id)


@mcp.tool()
async def delete_lab(
    ctx: Context,
    lab_name: str,
    cml_host: str | None = None,
    cml_user: str | None = None,
    cml_password: str | None = None,
) -> str:
    """
    Delete a Catalyst SD-WAN lab from CML.

    Stops, wipes, and removes the lab. This is destructive and irreversible.

    Args:
        lab_name: Name of the CML lab to delete
        cml_host: CML hostname or IP (or set CML_IP env var)
        cml_user: CML username (or set CML_USER env var)
        cml_password: CML password (or set CML_PASSWORD env var)
    """
    host, user, password = _cml_creds(cml_host, cml_user, cml_password)
    # Force=True because the LLM has already confirmed with the user
    return await capture_task_async(ctx, _delete.run, host, user, password, lab_name, force=True)


@mcp.tool()
async def backup_lab(
    ctx: Context,
    lab_name: str,
    manager_password: str,
    manager_user: str = "admin",
    output: str | None = None,
    directory: bool = False,
    cml_host: str | None = None,
    cml_user: str | None = None,
    cml_password: str | None = None,
) -> str:
    """
    Back up a running SD-WAN lab (topology + Manager configuration).

    Args:
        lab_name: Name of the CML lab
        manager_password: SD-WAN Manager password
        manager_user: Manager username (default: "admin")
        output: Output path (default: <lab>-<date>.zip)
        directory: Save as unpacked directory instead of zip
        cml_host: CML hostname or IP (or set CML_IP env var)
        cml_user: CML username (or set CML_USER env var)
        cml_password: CML password (or set CML_PASSWORD env var)
    """
    host, user, password = _cml_creds(cml_host, cml_user, cml_password)
    return await capture_task_async(
        ctx,
        _backup.run,
        host, user, password,
        lab_name=lab_name,
        manager_user=manager_user,
        manager_password=manager_password,
        output=Path(output) if output else None,
        directory=directory,
    )


@mcp.tool()
async def restore_lab(
    ctx: Context,
    backup_path: str,
    lab_name: str,
    manager_password: str,
    manager_port: int | None = None,
    manager_ip: str | None = None,
    manager_mask: str | None = None,
    manager_gateway: str | None = None,
    manager_user: str = "admin",
    serial_file: str | None = None,
    control_version: str | None = None,
    edge_version: str | None = None,
    delete_existing: bool = False,
    retry: bool = False,
    pki: str = "enterprise",
    proxy_ip: str = "",
    proxy_port: str = "80",
    no_proxy: str = "",
    cml_host: str | None = None,
    cml_user: str | None = None,
    cml_password: str | None = None,
) -> str:
    """
    Restore a Catalyst SD-WAN lab from a backup archive.

    Args:
        backup_path: Path to backup zip file or directory
        lab_name: Name for the restored lab
        manager_password: SD-WAN Manager password
        manager_port: PATty external port (enables PATty mode)
        manager_ip: Manager IP address (direct mode)
        manager_mask: Subnet mask (direct mode)
        manager_gateway: Default gateway (direct mode)
        manager_user: Manager username (default: "admin")
        serial_file: Path to custom .viptela serial file
        control_version: Override control plane version
        edge_version: Override edge version
        delete_existing: Delete existing lab with same name before restoring
        retry: Resume from Manager boot, skipping lab import
        pki: Certificate signing mode: "enterprise" or "cisco"
        proxy_ip: HTTP proxy hostname or IP
        proxy_port: HTTP proxy port
        no_proxy: Additional no-proxy entries
        cml_host: CML hostname or IP (or set CML_IP env var)
        cml_user: CML username (or set CML_USER env var)
        cml_password: CML password (or set CML_PASSWORD env var)
    """
    host, user, password = _cml_creds(cml_host, cml_user, cml_password)

    patty = manager_port is not None
    if not patty and not all([manager_ip, manager_mask, manager_gateway]):
        return (
            "Error: Provide manager_port for PATty mode, or all of "
            "manager_ip + manager_mask + manager_gateway for direct mode."
        )

    job_id = start_job(
        "restore_lab",
        _restore.run,
        cml_host=host,
        cml_user=user,
        cml_password=password,
        lab_name=lab_name,
        manager_user=manager_user,
        manager_password=manager_password,
        manager_ip=host if patty else manager_ip or "",
        manager_port=manager_port or 443,
        manager_mask=manager_mask or "",
        manager_gateway=manager_gateway or "",
        backup=Path(backup_path),
        serial_file=Path(serial_file) if serial_file else DEFAULT_SERIAL_FILE,
        control_version=control_version,
        edge_version=edge_version,
        delete_existing=delete_existing,
        retry=retry,
        patty=patty,
        pki=pki,
        proxy_ip=proxy_ip,
        proxy_port=proxy_port,
        no_proxy=no_proxy,
    )
    return _started("restore_lab", job_id)


@mcp.tool()
async def images_list(
    ctx: Context,
    cml_host: str | None = None,
    cml_user: str | None = None,
    cml_password: str | None = None,
) -> str:
    """
    List SD-WAN software versions installed in CML.

    Returns a table of node types and their available software versions.

    Args:
        cml_host: CML hostname or IP (or set CML_IP env var)
        cml_user: CML username (or set CML_USER env var)
        cml_password: CML password (or set CML_PASSWORD env var)
    """
    host, user, password = _cml_creds(cml_host, cml_user, cml_password)
    return await capture_task_async(ctx, _images.list_versions, host, user, password)


@mcp.tool()
async def images_upload(
    ctx: Context,
    images_dir: str = ".",
    cml_host: str | None = None,
    cml_user: str | None = None,
    cml_password: str | None = None,
) -> str:
    """
    Upload SD-WAN .qcow2 image files to CML.

    Scans the specified directory for recognized SD-WAN image files and
    uploads them with the correct image definitions.

    Args:
        images_dir: Directory containing .qcow2 files (default: current directory)
        cml_host: CML hostname or IP (or set CML_IP env var)
        cml_user: CML username (or set CML_USER env var)
        cml_password: CML password (or set CML_PASSWORD env var)
    """
    host, user, password = _cml_creds(cml_host, cml_user, cml_password)
    job_id = start_job("images_upload", _images.upload, host, user, password, Path(images_dir))
    return _started("images_upload", job_id)


@mcp.tool()
async def images_delete(
    ctx: Context,
    versions: list[str],
    dry_run: bool = False,
    cml_host: str | None = None,
    cml_user: str | None = None,
    cml_password: str | None = None,
) -> str:
    """
    Delete SD-WAN image definitions and files from CML.

    Args:
        versions: List of software versions to delete (e.g. ["20.12.1"])
        dry_run: If true, show what would be deleted without actually deleting
        cml_host: CML hostname or IP (or set CML_IP env var)
        cml_user: CML username (or set CML_USER env var)
        cml_password: CML password (or set CML_PASSWORD env var)
    """
    host, user, password = _cml_creds(cml_host, cml_user, cml_password)
    return await capture_task_async(
        ctx, _images.delete, host, user, password, versions, dry_run=dry_run
    )


@mcp.tool()
async def sign_csr(
    ctx: Context,
    csr_file: str,
    output: str | None = None,
) -> str:
    """
    Sign a CSR with the lab CA certificate.

    Returns the signed certificate in PEM format.

    Args:
        csr_file: Path to the CSR file (.txt or .pem)
        output: Optional path to write the signed certificate to
    """
    return await capture_task_async(
        ctx,
        _sign.run,
        Path(csr_file),
        Path(output) if output else None,
    )


@mcp.tool()
async def job_status(
    ctx: Context,
    job_id: str,
) -> str:
    """
    Poll a background job started by a long-running tool (deploy, restore,
    add_devices, images_upload).

    Long-polls server-side: blocks briefly until new log events occur or the
    job finishes, then returns the events emitted since your last poll plus the
    job status. Call this repeatedly until status is 'done' or 'error'.

    If an event begins with "ACTION REQUIRED", relay it to the user immediately
    (e.g. a Cisco PKI registration URL to open in a browser), then keep polling —
    the job continues automatically once the user completes the action.

    Args:
        job_id: The job id returned when the long-running tool was started.
    """
    return await poll_job(job_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()

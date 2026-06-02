import logging
from typing import Any

import typer
import yaml
from rich.progress import Progress, SpinnerColumn, TaskID, TextColumn
from virl2_client import ClientLibrary

from .utils import CML_NODES_DEFINITION_DIR, connect_cml, console

log = logging.getLogger(__name__)
_IOL_NODE_IDS = ("iol-xe", "ioll2-xe")


def run(cml_host: str, cml_user: str, cml_password: str) -> None:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Connecting to CML...")
        cml = connect_cml(cml_host, cml_user, cml_password)
        try:
            progress.update(task, description="Checking license...")
            _check_license(cml)
            log.info("License OK")

            progress.update(task, description="Loading node definitions...")
            existing = {nd["id"]: nd for nd in cml.definitions.node_definitions()}
            log.debug("Loaded %d node definitions from CML", len(existing))

            progress.update(task, description="Checking IOL definitions...")
            _check_iol_definitions(existing)
            log.info("IOL definitions found")

            _sync_node_definitions(cml, existing, progress, task)
        finally:
            cml.logout()

    console.print("[green]Setup complete.[/green]")


def _check_license(cml: ClientLibrary) -> None:
    status = cml.licensing.status()["authorization"]["status"]
    log.debug("License status: %s", status)
    if status in ("INIT", "EVAL"):
        log.error("CML Free license detected. This tool requires a minimum of 9 nodes.")
        raise typer.Exit(1)


def _check_iol_definitions(existing: dict[str, Any]) -> None:
    for iol_id in _IOL_NODE_IDS:
        if iol_id not in existing:
            log.error(
                "Node definition '%s' not found. Please upload the latest CML refplat ISO.",
                iol_id,
            )
            raise typer.Exit(1)
    log.debug("IOL node definitions present: %s", _IOL_NODE_IDS)


def _sync_node_definitions(
    cml: ClientLibrary,
    existing: dict[str, Any],
    progress: Progress,
    task: TaskID,
) -> None:
    for path in sorted(CML_NODES_DEFINITION_DIR.glob("*.yaml")):
        definition = yaml.safe_load(path.read_text())
        node_id = definition["id"]
        progress.update(task, description=f"Checking {node_id}...")
        if node_id not in existing:
            log.debug("%s: not found in CML, uploading", node_id)
            cml.definitions.upload_node_definition(definition)
            console.print(f"  [green]CREATED[/green] {node_id}")
        elif definition != existing[node_id]:
            log.debug("%s: definition differs, updating", node_id)
            if existing[node_id]["general"]["read_only"]:
                cml.definitions.set_node_definition_read_only(node_id, False)
            cml.definitions.upload_node_definition(definition, update=True)
            console.print(f"  [yellow]UPDATED[/yellow] {node_id}")
        else:
            log.debug("%s: up to date", node_id)
            if log.isEnabledFor(logging.INFO):
                console.print(f"  [dim]OK[/dim]      {node_id}")

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import typer
from rich.markup import escape
from rich.progress import (
    BarColumn,
    FileSizeColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from virl2_client import ClientLibrary
from virl2_client.exceptions import APIError

from catalyst_sdwan_lab.cml_client import upload_image_file

from .utils import console

log = logging.getLogger(__name__)

_NODE_TYPES = (
    "cat-sdwan-manager",
    "cat-sdwan-controller",
    "cat-sdwan-validator",
    "cat-sdwan-edge",
)

_VIPTELA_TYPE_MAP = {
    "vmanage": "cat-sdwan-manager",
    "smart": "cat-sdwan-controller",
    "edge": "cat-sdwan-validator",
    "bond": "cat-sdwan-validator",
}

_VIPTELA_RE = re.compile(r"viptela-(vmanage|smart|edge|bond)-([\d.]+)-")
_C8000V_RE = re.compile(r"c8000v-universalk9_\d+G_serial\.([\w.]+)\.qcow2$")


def _normalize_id(image_id: str) -> str:
    # CML stores image IDs with dashes (e.g. cat-sdwan-manager-20-13-1); normalize to dots.
    for node_type in _NODE_TYPES:
        prefix = f"{node_type}-"
        if image_id.startswith(prefix):
            version = image_id[len(prefix):]
            return f"{node_type}-{version.replace('-', '.')}"
    return image_id


def _parse_filename(filename: str) -> tuple[str, str] | None:
    m = _VIPTELA_RE.match(filename)
    if m:
        return _VIPTELA_TYPE_MAP[m.group(1)], m.group(2)
    m = _C8000V_RE.match(filename)
    if m:
        return "cat-sdwan-edge", m.group(1)
    return None


def upload(cml: ClientLibrary, images_dir: Path) -> None:
    existing = {_normalize_id(img["id"]) for img in cml.definitions.image_definitions()}

    candidates: list[tuple[Path, str, str]] = []
    for path in sorted(images_dir.glob("*.qcow2")):
        parsed = _parse_filename(path.name)
        if parsed is not None:
            node_type, version = parsed
            candidates.append((path, node_type, version))

    if not candidates:
        log.warning("No SD-WAN image files found in %s", images_dir)
        return

    log.info("Found %d candidate image file(s) in %s", len(candidates), images_dir)
    node_defs: dict[str, Any] | None = None
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        FileSizeColumn(),
        TransferSpeedColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        for path, node_type, version in candidates:
            norm_id = f"{node_type}-{version}"
            if norm_id in existing:
                log.debug("%s: already exists, skipping", norm_id)
                console.print(f"  [dim]SKIPPED[/dim]  {norm_id} (already exists)")
            else:
                if node_defs is None:
                    node_defs = {nd["id"]: nd for nd in cml.definitions.node_definitions()}
                if node_type not in node_defs:
                    log.error("Node definition '%s' not found. Run 'setup' first.", node_type)
                    raise typer.Exit(1)
                log.debug("Uploading %s as %s", path.name, norm_id)
                task = progress.add_task(f"Uploading {path.name}", total=path.stat().st_size)
                upload_image_file(
                    cml._session,
                    path,
                    on_progress=lambda sent, _, t=task: progress.update(t, completed=sent),
                )
                progress.remove_task(task)
                label = f"{node_defs[node_type]['ui']['label']} {version}"
                cml.definitions.upload_image_definition({
                    "id": norm_id,
                    "node_definition_id": node_type,
                    "label": label,
                    "disk_image": path.name,
                })
                console.print(f"  [green]UPLOADED[/green] {norm_id}")
    console.print("[green]✓[/green] Upload complete.")


def list_versions(cml: ClientLibrary) -> None:
    table = Table(title="Catalyst SD-WAN Software Versions")
    table.add_column("Node Type", style="cyan")
    table.add_column("Versions")
    for node_type in _NODE_TYPES:
        versions = [
            _normalize_id(img["id"])[len(node_type) + 1:]
            for img in cml.definitions.image_definitions_for_node_definition(node_type)
        ]
        table.add_row(node_type, ", ".join(versions) if versions else "[dim]none[/dim]")
    console.print(table)


def delete(cml: ClientLibrary, versions: list[str], dry_run: bool = False) -> None:
    image_map = {
        _normalize_id(img["id"]): img
        for img in cml.definitions.image_definitions()
    }
    to_delete = [
        (f"{node_type}-{version}", image_map[f"{node_type}-{version}"])
        for version in versions
        for node_type in _NODE_TYPES
        if f"{node_type}-{version}" in image_map
    ]
    if not to_delete:
        log.warning("No matching image definitions found.")
        return

    log.info("Deleting %d image definition(s)", len(to_delete))
    files_to_delete: list[str] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Deleting images", total=len(to_delete))
        for norm_id, img in to_delete:
            progress.update(task, description=f"Deleting {norm_id}")
            if dry_run:
                console.print(f"  [dim]DRY RUN[/dim]  {norm_id} ({img['disk_image']})")
            else:
                if img.get("read_only"):
                    cml.definitions.set_image_definition_read_only(img["id"], False)
                try:
                    cml.definitions.remove_image_definition(img["id"])
                    files_to_delete.append(img["disk_image"])
                    console.print(f"  [green]DELETED[/green]  {norm_id}")
                except APIError as e:
                    console.print(f"  [red]FAILED[/red]   {norm_id}: {escape(str(e))}")
            progress.advance(task)

    for filename in files_to_delete:
        cml.definitions.remove_dropfolder_image(filename)
        console.print(f"  [green]DELETED FILE[/green] {filename}")
    if dry_run:
        console.print("[dim]Dry run — nothing was deleted.[/dim]")
    else:
        console.print("[green]✓[/green] Delete complete.")

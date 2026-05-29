import logging

import typer
from rich.markup import escape
from rich.progress import Progress, SpinnerColumn, TextColumn

from .utils import connect_cml, console

log = logging.getLogger(__name__)


def run(cml_host: str, cml_user: str, cml_password: str, lab_name: str, *, force: bool) -> None:
    if not force:
        confirmed = typer.confirm(
            f"Remove lab '{lab_name}' and all its data?", default=False
        )
        if not confirmed:
            raise typer.Exit(0)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Connecting to CML...")
        cml = connect_cml(cml_host, cml_user, cml_password)

        progress.update(task, description="Finding lab...")
        labs = cml.find_labs_by_title(lab_name)
        if not labs:
            log.error("No lab found with name '%s'.", lab_name)
            raise typer.Exit(1)
        if len(labs) > 1:
            log.error(
                "Multiple labs found with name '%s'. Ensure lab names are unique.", lab_name
            )
            raise typer.Exit(1)
        lab = labs[0]

        progress.update(task, description="Stopping lab...")
        lab.stop()
        progress.update(task, description="Wiping lab...")
        lab.wipe()
        progress.update(task, description="Removing lab...")
        lab.remove()

    log.info("Lab '%s' deleted.", lab_name)
    console.print(f"[green]Deleted.[/green] Lab '{escape(lab_name)}' removed.")

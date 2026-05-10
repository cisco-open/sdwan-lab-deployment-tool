import logging
from pathlib import Path

import typer
from rich.console import Console
from virl2_client import ClientLibrary

console = Console()
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CERTS_DIR = DATA_DIR / "certs"
CML_DEPLOY_TEMPLATES_DIR = DATA_DIR / "cml_lab_definition" / "deploy"
CML_BACKUP_TEMPLATES_DIR = DATA_DIR / "cml_lab_definition" / "backup"
CML_NODES_DEFINITION_DIR = DATA_DIR / "cml_nodes_definition"
MANAGER_CONFIGS_DIR = DATA_DIR / "manager_configs"
CONTROLLER_TEMPLATES_DIR = MANAGER_CONFIGS_DIR / "controller_templates"
DEFAULT_SERIAL_FILE = DATA_DIR / "serial_files" / "serialFile.viptela"


def basic_configuration_path(ip_type: str) -> Path:
    return MANAGER_CONFIGS_DIR / f"basic_configuration_{ip_type}.tar.gz"


def verify_cml_version(cml: ClientLibrary) -> None:
    version = cml.check_controller_version()
    if version is None or (version.major, version.minor) < (2, 7):
        log.error("CML 2.7 or later is required.")
        raise typer.Exit(1)

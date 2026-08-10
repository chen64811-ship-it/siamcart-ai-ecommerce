"""Programmatic Alembic entry point used by application startup."""

from pathlib import Path
from alembic import command
from alembic.config import Config


def upgrade_database() -> None:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")

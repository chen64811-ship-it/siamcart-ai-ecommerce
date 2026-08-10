"""Programmatic Alembic entry point used by application startup."""

from pathlib import Path
from alembic import command
from alembic.config import Config


def upgrade_database() -> None:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    # The web process already owns logging configuration. Alembic's default
    # fileConfig would otherwise replace the JSON request logger at startup.
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")

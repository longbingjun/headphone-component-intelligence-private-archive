from __future__ import annotations

from alembic import command
from alembic.config import Config

from server.config import get_settings


def upgrade_database() -> None:
    settings = get_settings()
    if not settings.database_configured:
        raise RuntimeError("DATABASE_URL is not configured")
    config = Config(str(settings.root / "alembic.ini"))
    config.set_main_option("script_location", str(settings.root / "server" / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    command.upgrade(config, "head")

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


ROOT = Path(__file__).resolve().parent.parent


class MigrationTests(unittest.TestCase):
    def test_fresh_database_upgrades_to_video_intelligence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "migration.db"
            url = f"sqlite+pysqlite:///{database.as_posix()}"
            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ROOT / "server" / "migrations"))
            config.set_main_option("sqlalchemy.url", url)
            command.upgrade(config, "head")

            inspection_engine = create_engine(url)
            try:
                inspector = inspect(inspection_engine)
                self.assertIn("video_facts", inspector.get_table_names())
                columns = {column["name"] for column in inspector.get_columns("videos")}
                self.assertIn("processing_status", columns)
                self.assertIn("candidate_product_id", columns)
                bom_columns = {column["name"] for column in inspector.get_columns("bom_items")}
                self.assertIn("component_key", bom_columns)
                self.assertIn("material", bom_columns)
                self.assertIn("manufacturer", bom_columns)
                self.assertIn("manufacturer_basis", bom_columns)
                self.assertIn("manufacturer_evidence_quote", bom_columns)
                self.assertIn("bom_item_parameters", inspector.get_table_names())
                self.assertIn("data_migrations", inspector.get_table_names())
            finally:
                inspection_engine.dispose()


if __name__ == "__main__":
    unittest.main()

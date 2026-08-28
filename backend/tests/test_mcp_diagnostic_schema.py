import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import UniqueConstraint

from app.models.diagnostic import DiagnosticReport


class McpDiagnosticSchemaTests(unittest.TestCase):
    def test_diagnostic_reports_store_tenant_and_idempotency_identity(self) -> None:
        table = DiagnosticReport.__table__

        self.assertIn("tenant_id", table.c)
        self.assertTrue(table.c.tenant_id.nullable)
        self.assertIn("idempotency_key", table.c)
        self.assertIn("request_fingerprint", table.c)

        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        self.assertIn(("tenant_id", "idempotency_key"), unique_columns)

    def test_alembic_has_one_head_with_the_diagnostic_scope_migration(self) -> None:
        backend_dir = Path(__file__).resolve().parents[1]
        config = Config(str(backend_dir / "alembic.ini"))
        config.set_main_option("script_location", str(backend_dir / "alembic"))
        scripts = ScriptDirectory.from_config(config)

        self.assertEqual(scripts.get_heads(), ["017_mcp_diagnostic_scope"])


if __name__ == "__main__":
    unittest.main()

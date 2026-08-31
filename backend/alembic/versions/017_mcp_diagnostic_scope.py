"""Scope MCP diagnostics by tenant and make submissions idempotent."""

from alembic import op
import sqlalchemy as sa


revision = "017_mcp_diagnostic_scope"
down_revision = "016_merge_platform_iterations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "diagnostic_reports",
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "diagnostic_reports",
        sa.Column("idempotency_key", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "diagnostic_reports",
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_diagnostic_reports_tenant_id",
        "diagnostic_reports",
        ["tenant_id"],
    )
    op.create_unique_constraint(
        "uq_diagnostic_reports_tenant_idempotency",
        "diagnostic_reports",
        ["tenant_id", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_diagnostic_reports_tenant_idempotency",
        "diagnostic_reports",
        type_="unique",
    )
    op.drop_index("ix_diagnostic_reports_tenant_id", table_name="diagnostic_reports")
    op.drop_column("diagnostic_reports", "request_fingerprint")
    op.drop_column("diagnostic_reports", "idempotency_key")
    op.drop_column("diagnostic_reports", "tenant_id")

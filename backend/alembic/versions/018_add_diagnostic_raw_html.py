"""Persist bounded diagnostic input without external object storage."""

from alembic import op
import sqlalchemy as sa


revision = "018_add_diagnostic_raw_html"
down_revision = "017_mcp_diagnostic_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "diagnostic_reports",
        sa.Column("raw_html", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("diagnostic_reports", "raw_html")

"""Bind the local authorization projection to the canonical SSO subject."""

from alembic import op
import sqlalchemy as sa


revision = "019_add_user_sso_subject"
down_revision = "018_add_diagnostic_raw_html"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("sso_subject", sa.String(length=200), nullable=True))
    op.create_index("ix_users_sso_subject", "users", ["sso_subject"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_sso_subject", table_name="users")
    op.drop_column("users", "sso_subject")

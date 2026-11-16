"""gateway desired configuration and safe update command payloads

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from app.migrations import util

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    for col in (
        sa.Column("config_desired", sa.JSON, nullable=True),
        sa.Column("config_ver", sa.Integer, nullable=False, server_default="0"),
        sa.Column("config_applied_ver", sa.Integer, nullable=False, server_default="0"),
        sa.Column("config_status", sa.String(12), nullable=False, server_default="idle"),
        sa.Column("config_error", sa.String(240), nullable=False, server_default=""),
        sa.Column("config_updated_at", TS, nullable=True),
        sa.Column("config_applied_at", TS, nullable=True),
    ): util.add_column("sos_gateways", col)
    util.add_column("sos_gateway_commands", sa.Column("payload", sa.JSON, nullable=True))


def downgrade() -> None:
    # Forward-only in production; SQLite cannot safely drop these columns on older deployments.
    pass

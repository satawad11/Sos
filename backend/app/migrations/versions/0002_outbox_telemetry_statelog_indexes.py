"""LINE outbox, device telemetry history, durable state log for reports, indexes on the busy columns

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from app.migrations import util

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
STATE_ACTIONS = ("gateway_offline", "gateway_online", "gateway_maint_on", "gateway_maint_off")
INDEXES = [
    ("ix_sos_cases_state", "sos_cases", ["state"]),
    ("ix_sos_cases_created_at", "sos_cases", ["created_at"]),
    ("ix_sos_cases_patient_id", "sos_cases", ["patient_id"]),
    ("ix_sos_cases_device_id", "sos_cases", ["device_id"]),
    ("ix_sos_devices_patient_id", "sos_devices", ["patient_id"]),
    ("ix_sos_audit_log_at", "sos_audit_log", ["at"]),
    ("ix_sos_audit_log_action", "sos_audit_log", ["action"]),
]


def upgrade() -> None:
    if not util.has_table("sos_outbox"):
        op.create_table(
            "sos_outbox",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("kind", sa.String(12), nullable=False),
            sa.Column("case_id", sa.Integer, nullable=True),
            sa.Column("caregiver_id", sa.Integer, nullable=True),
            sa.Column("payload", sa.JSON, nullable=False),
            sa.Column("status", sa.String(10), nullable=False),
            sa.Column("attempts", sa.Integer, nullable=False),
            sa.Column("next_try_at", TS, nullable=False),
            sa.Column("last_error", sa.String(240), nullable=False),
            sa.Column("created_at", TS, nullable=False),
            sa.Column("done_at", TS, nullable=True),
        )
    util.create_index("ix_sos_outbox_case_id", "sos_outbox", ["case_id"])
    util.create_index("ix_sos_outbox_status", "sos_outbox", ["status"])

    if not util.has_table("sos_device_telemetry"):
        op.create_table(
            "sos_device_telemetry",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("device_id", sa.Integer, sa.ForeignKey("sos_devices.id"), nullable=False),
            sa.Column("at", TS, nullable=False),
            sa.Column("battery", sa.Integer, nullable=True),
            sa.Column("mains_ok", sa.Boolean, nullable=True),
            sa.Column("rssi", sa.Integer, nullable=True),
            sa.Column("snr", sa.Float, nullable=True),
            sa.Column("gateway_id", sa.String(16), nullable=False),
            sa.Column("n_gateways", sa.Integer, nullable=True),
            sa.Column("msg_type", sa.Integer, nullable=True),
        )
    util.create_index("ix_sos_device_telemetry_at", "sos_device_telemetry", ["at"])
    util.create_index("ix_sos_device_telemetry_device_at", "sos_device_telemetry", ["device_id", "at"])

    if not util.has_table("sos_state_log"):
        op.create_table(
            "sos_state_log",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("at", TS, nullable=False),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("target", sa.String(128), nullable=False),
        )
    util.create_index("ix_sos_state_log_action_at", "sos_state_log", ["action", "at"])
    empty = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM sos_state_log")).scalar() == 0
    if empty and util.has_table("sos_audit_log"):
        # keep the history the reports already had: copy the rows they were computed from
        a = sa.table("sos_audit_log", sa.column("id"), sa.column("at"), sa.column("action"), sa.column("target"))
        s = sa.table("sos_state_log", sa.column("at"), sa.column("action"), sa.column("target"))
        op.execute(s.insert().from_select(
            ["at", "action", "target"],
            sa.select(a.c.at, a.c.action, sa.func.coalesce(a.c.target, "")).where(a.c.action.in_(STATE_ACTIONS)).order_by(a.c.at, a.c.id)))

    for name, table, cols in INDEXES:
        util.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _ in INDEXES:
        util.drop_index(name, table)
    for t in ("sos_state_log", "sos_device_telemetry", "sos_outbox"):
        if util.has_table(t):
            op.drop_table(t)

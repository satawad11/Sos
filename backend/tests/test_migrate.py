"""app.db.migrate(): empty database, a pre-Alembic database (adopted + upgraded, history kept), and running it twice."""
from datetime import datetime, timezone, timedelta
from sqlalchemy import inspect, text
from app import db as appdb
from app.db import Base, SessionLocal, engine
from app.models import AuditLog, StateLog

NEW_TABLES = ("sos_outbox", "sos_device_telemetry", "sos_state_log")


def _wipe():
    Base.metadata.drop_all(engine)
    with engine.begin() as cx:
        cx.execute(text("DROP TABLE IF EXISTS alembic_version"))


def _legacy():
    """The schema as the previous release left it: no alembic_version, none of the new tables or indexes."""
    _wipe()
    Base.metadata.create_all(engine)
    with engine.begin() as cx:
        for t in NEW_TABLES:
            cx.execute(text(f"DROP TABLE {t}"))
        for ix in ("ix_sos_cases_state", "ix_sos_cases_created_at", "ix_sos_cases_patient_id", "ix_sos_cases_device_id",
                   "ix_sos_devices_patient_id", "ix_sos_audit_log_at", "ix_sos_audit_log_action"):
            cx.execute(text(f"DROP INDEX {ix}"))
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i, (act, tgt) in enumerate([("gateway_offline", "aa"), ("login", "x"), ("gateway_online", "aa"), ("gateway_maint_on", "bb")]):
            cx.execute(text("INSERT INTO sos_audit_log (at, actor, action, target, detail) VALUES (:at, 'system', :a, :t, '{}')"),
                       {"at": t0 + timedelta(minutes=i), "a": act, "t": tgt})


def _version():
    with engine.connect() as cx:
        return cx.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_empty_database_is_created_at_head():
    _wipe()
    assert appdb.migrate() == "created"
    insp = inspect(engine)
    assert all(insp.has_table(t) for t in NEW_TABLES) and _version() == "0002"
    assert appdb.migrate() == "upgraded"          # second start: nothing to do, no error


def test_legacy_database_is_adopted_and_upgraded():
    _legacy()
    assert appdb.migrate() == "adopted"
    insp = inspect(engine)
    assert all(insp.has_table(t) for t in NEW_TABLES) and _version() == "0002"
    assert "ix_sos_cases_state" in {i["name"] for i in insp.get_indexes("sos_cases")}
    assert "ix_sos_audit_log_action" in {i["name"] for i in insp.get_indexes("sos_audit_log")}
    with SessionLocal() as db:
        rows = [(r.action, r.target) for r in db.query(StateLog).order_by(StateLog.at).all()]
        assert rows == [("gateway_offline", "aa"), ("gateway_online", "aa"), ("gateway_maint_on", "bb")]
        assert db.query(AuditLog).count() == 4     # the audit log itself is untouched
    assert appdb.migrate() == "upgraded"
    with SessionLocal() as db:
        assert db.query(StateLog).count() == 3     # the backfill does not run twice


def test_state_log_mirrors_new_audit_rows():
    _wipe(); appdb.migrate()
    with SessionLocal() as db:
        db.add(AuditLog(actor="system", action="gateway_offline", target="cc"))
        db.add(AuditLog(actor="admin", action="login", target=""))
        db.commit()
        assert [(r.action, r.target) for r in db.query(StateLog).all()] == [("gateway_offline", "cc")]
    _wipe()

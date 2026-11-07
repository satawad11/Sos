"""Monthly button self-test: status per device, LINE reminders (scheduled + manual), confirmation on a test press."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app import codec, settings_store
from app.db import SessionLocal
from app.models import AuditLog, Device, Patient
from tests.test_all import login, up

EUI = "70b3d57ed0000001"
BKK = ZoneInfo("Asia/Bangkok")


def _on(db, **kw):
    settings_store.put(db, "self_test", {**settings_store.DEFAULTS["self_test"], **kw}, "t")


def test_status_and_confirmation(env_):
    eng, downs, notif, client, ids = env_
    H = login(client)
    with SessionLocal() as db:
        p2 = Patient(full_name="นายสอง", village="ม.2"); db.add(p2); db.flush()
        db.add(Device(dev_eui="70b3d57ed0000002", lifecycle="active", patient_id=p2.id, label="B2"))
        db.add(Device(dev_eui="70b3d57ed0000003", lifecycle="ready", label="spare")); db.commit()
    s = client.get("/api/selftest", headers=H).json()
    assert s["active"] == 2 and s["tested"] == 0 and s["pct"] == 0.0
    assert {d["label"] for d in s["devices"]} == {"A1", "B2"}            # only active devices
    with SessionLocal() as db:
        _on(db, confirm_line=True)
        assert eng.handle_uplink(db, EUI, up(codec.T_TEST, alert=9)) == "test"
    assert notif.system and notif.system[-1][0] == "อสม.หนึ่ง" and "ทดสอบปุ่มผ่าน" in notif.system[-1][1]
    s = client.get("/api/selftest", headers=H).json()
    a1 = next(d for d in s["devices"] if d["label"] == "A1")
    assert a1["tested"] and a1["tests_this_month"] == 1 and a1["caregiver"] == "อสม.หนึ่ง" and a1["last_test_at"]
    assert s["devices"][0]["label"] == "B2"                              # untested first
    assert s["tested"] == 1 and s["pct"] == 50.0
    # a test press never becomes an emergency
    assert client.get("/api/cases?state=new,accepted", headers=H).json() == []
    # viewer sees no patient names
    assert all(d["patient"] == "" for d in client.get("/api/selftest", headers=login(client, "viewer")).json()["devices"])
    assert client.get("/api/selftest?month=2026-13", headers=H).status_code == 422


def test_scheduled_and_manual_reminders(env_):
    eng, downs, notif, client, ids = env_
    H = login(client)
    with SessionLocal() as db:
        _on(db, remind_day=1, second_day=15, remind_time="10:00")
        day1_early = datetime(2026, 12, 1, 9, 0, tzinfo=BKK).astimezone(timezone.utc)
        eng.tick(db, day1_early); assert notif.system == []                       # before 10:00
        eng.tick(db, day1_early + timedelta(hours=1, minutes=5))
        assert len(notif.system) == 1 and "ยังไม่ได้ทดสอบ" in notif.system[0][1] and "A1" in notif.system[0][1]
        eng.tick(db, day1_early + timedelta(hours=2)); assert len(notif.system) == 1   # once per round
        # the device is tested → round 2 skips (everything tested)
        dev = db.query(Device).filter_by(dev_eui=EUI).first()
        from app.models import Case
        db.add(Case(device_id=dev.id, patient_id=dev.patient_id, alert_id=1, is_test=True, state="closed",
                    close_reason="test", created_at=datetime(2026, 12, 3, 4, 0, tzinfo=timezone.utc))); db.commit()
        eng.tick(db, datetime(2026, 12, 15, 11, 0, tzinfo=BKK).astimezone(timezone.utc)); assert len(notif.system) == 1
        marks = [a.target for a in db.query(AuditLog).filter_by(action="selftest_reminder").all()]
        assert marks == ["2026-12#1", "2026-12#2"]
        # weeks after a due date (e.g. the day the system is installed) → nothing is sent
        notif.system.clear()
        eng.tick(db, datetime(2027, 1, 8, 12, 0, tzinfo=BKK).astimezone(timezone.utc)); assert notif.system == []
        # server down on the 15th, back on the 17th → sent late, once
        eng.tick(db, datetime(2027, 1, 17, 12, 0, tzinfo=BKK).astimezone(timezone.utc))
        eng.tick(db, datetime(2027, 1, 18, 12, 0, tzinfo=BKK).astimezone(timezone.utc))
        assert len(notif.system) == 1
        assert db.query(AuditLog).filter_by(action="selftest_reminder", target="2027-01#2").count() == 1
        # disabled → nothing
        _on(db, enabled=False); notif.system.clear()
        eng.tick(db, datetime(2027, 2, 2, 12, 0, tzinfo=BKK).astimezone(timezone.utc)); assert notif.system == []
    r = client.post("/api/selftest/remind", headers=H).json()
    assert r["devices"] == 1 and r["caregivers"] == 1
    assert client.post("/api/selftest/remind", headers=login(client, "viewer")).status_code == 403
    assert len(client.get("/api/selftest", headers=H).json()["reminders"]) >= 1


def test_settings_validation(env_):
    *_, client, ids = env_
    H = login(client)
    base = {"enabled": True, "remind_day": 1, "second_day": 15, "remind_time": "10:00", "confirm_line": True}
    assert client.put("/api/settings/self_test", json=base, headers=H).status_code == 200
    for bad in ({"remind_day": 0}, {"remind_day": 30}, {"second_day": 1}, {"remind_time": "25:00"}, {"enabled": "yes"}):
        assert client.put("/api/settings/self_test", json={**base, **bad}, headers=H).status_code == 422, bad
    assert client.put("/api/settings/self_test", json={**base, "second_day": 0}, headers=H).status_code == 200

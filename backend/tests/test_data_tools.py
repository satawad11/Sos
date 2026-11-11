"""Health endpoints, backup status, device history, case search/paging, CSV import, data readiness."""
import json
from datetime import datetime, timedelta, timezone
from app import api, backupinfo, codec, health, telemetry
from app.config import env
from app.db import SessionLocal
from app.models import Case, Caregiver, Device, Patient, Telemetry, utcnow

EUI = "70b3d57ed0000001"


def up(t, alert, batt):
    return codec.encode_uplink(codec.Uplink(t, alert, codec.F_MAINS_OK, batt, 0, 2, 1, 0))


def _h(client, user="admin"):
    return {"authorization": "Bearer " + client.post("/api/login", json={"username": user, "password": "password12345"}).json()["token"]}


def test_healthz_reports_real_state(env_, monkeypatch):
    eng, downs, notifier, client, ids = env_
    r = client.get("/healthz"); assert r.status_code == 200 and r.json() == {"ok": True, "db": True, "loop": True, "mqtt": True, "line_queue": True}
    eng.mqtt_ok = lambda: False
    assert client.get("/healthz").status_code == 200                       # MQTT blip: still up for the updater / Docker
    monkeypatch.setitem(health.state, "mqtt_down_since", health.time.time() - 500)
    r = client.get("/healthz?strict=1"); assert r.status_code == 503 and r.json()["mqtt"] is False
    eng.mqtt_ok = lambda: True
    monkeypatch.setitem(health.state, "loop", True); monkeypatch.setitem(health.state, "tick_at", health.time.time() - 600)
    assert client.get("/healthz").status_code == 503                       # escalation loop stopped = nobody would be escalated to
    monkeypatch.setitem(health.state, "loop", False)
    def boom(): raise RuntimeError("db down")
    monkeypatch.setattr(health, "snapshot", lambda *a, **k: {"ok": False, "db": False, "loop": True, "mqtt": True, "line_queue": True, "all_ok": False})
    assert client.get("/healthz").status_code == 503


def test_system_health_and_backup_status(env_, tmp_path, monkeypatch):
    eng, downs, notifier, client, ids = env_
    monkeypatch.setattr(env, "update_dir", str(tmp_path))
    h = _h(client)
    assert client.get("/api/system/health", headers=_h(client, "viewer")).status_code == 403
    r = client.get("/api/system/health", headers=h).json()
    assert r["ok"] and r["backup"]["known"] is False and r["heartbeat"]["configured"] is False
    now = datetime.now(timezone.utc)
    def write(**kw):
        (tmp_path / "backup-status.json").write_text(json.dumps({"at": now.isoformat(), "ok": True, "encrypted": True,
            "offsite": {"configured": True, "ok": True, "detail": "rclone x"}, "files": [{"name": "sos-1.dump.enc", "size": 10}], **kw}))
    write(); b = backupinfo.state(36)
    assert b["known"] and b["ok"] and b["problem"] == "" and b["encrypted"] and b["files"][0]["size"] == 10
    write(at=(now - timedelta(hours=50)).isoformat()); assert "50" in backupinfo.state(36)["problem"]
    write(ok=False, error="pg_dump"); assert "ล้มเหลว" in backupinfo.state(36)["problem"]
    write(offsite={"configured": True, "ok": False, "detail": "scp refused"}); assert "นอกเครื่อง" in backupinfo.state(36)["problem"]
    assert client.put("/api/settings/monitoring", headers=h, json={"heartbeat_url": "http://x", "interval_min": 5, "backup_max_age_h": 36}).status_code == 422
    ok = client.put("/api/settings/monitoring", headers=h, json={"heartbeat_url": "https://hc-ping.com/abc", "interval_min": 5, "backup_max_age_h": 48})
    assert ok.status_code == 200 and client.get("/api/system/health", headers=h).json()["heartbeat"]["configured"] is True


def test_telemetry_history_trend_and_export(env_):
    eng, downs, notifier, client, ids = env_
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_HEARTBEAT, 0, 88), {"gateway_id": "aa", "rssi": -90, "snr": 7.5, "gateways": ["aa", "bb"]})
        eng.handle_uplink(db, EUI, up(codec.T_POLL, 0, 88), {})        # polls are not history
        rows = db.query(Telemetry).all()
        assert len(rows) == 1 and (rows[0].battery, rows[0].rssi, rows[0].n_gateways, rows[0].mains_ok) == (88, -90, 2, True)
        did = db.query(Device).first().id; now = utcnow()
        for i in range(10):                                   # on battery, losing 2 % a day
            db.add(Telemetry(device_id=did, at=now - timedelta(days=9 - i), battery=80 - 2 * i, mains_ok=False, rssi=-100))
        db.add(Telemetry(device_id=did, at=now - timedelta(days=500), battery=99)); db.commit()
    h = _h(client)
    r = client.get(f"/api/devices/{did}/telemetry?days=30", headers=h).json()
    assert r["count"] == 11 and len(r["points"]) == 11
    tr = r["summary"]["trend"]
    assert tr["per_day"] == -2.0 and tr["days_to_low"] == round((62 - r["battery_low_pct"]) / 2)
    assert r["summary"]["rssi_min"] == -100 and r["summary"]["battery_min"] == 62
    assert client.get("/api/devices/999/telemetry", headers=h).status_code == 404
    month = datetime.now(timezone.utc).astimezone().strftime("%Y-%m")
    csvr = client.get(f"/api/reports/telemetry.csv?from={(now - timedelta(days=12)).date()}&to={(now + timedelta(days=1)).date()}", headers=h)
    assert csvr.status_code == 200 and csvr.text.count(EUI) == 11 and "battery_pct" in csvr.text
    assert client.get("/api/reports/telemetry.csv?month=2026-01", headers=_h(client, "viewer")).status_code == 403
    with SessionLocal() as db:
        assert telemetry.purge(db) == 1


def test_case_search_and_paging(env_):
    eng, downs, notifier, client, ids = env_
    now = utcnow()
    with SessionLocal() as db:
        p2 = Patient(full_name="นายทดลอง ค้นหา", village="หมู่ 9"); db.add(p2); db.flush()
        d2 = Device(dev_eui="70b3d57ed0000002", lifecycle="active", patient_id=p2.id, label="B7"); db.add(d2); db.flush()
        did = db.query(Device).filter_by(dev_eui=EUI).one().id
        for i in range(6):
            db.add(Case(device_id=did, patient_id=ids["p"], alert_id=i, created_at=now - timedelta(days=i * 10), state="closed",
                        close_reason="false_alarm" if i % 2 else "resolved"))
        db.add(Case(device_id=d2.id, patient_id=p2.id, alert_id=1, created_at=now - timedelta(days=1), state="new"))
        db.add(Case(device_id=d2.id, patient_id=p2.id, alert_id=2, created_at=now, state="closed", is_test=True)); db.commit()
    h = _h(client)
    r = client.get("/api/cases", headers=h); assert len(r.json()) == 7 and r.headers["x-total-count"] == "7"
    r = client.get("/api/cases?limit=3&offset=3", headers=h); assert len(r.json()) == 3 and r.headers["x-total-count"] == "7"
    assert {c["id"] for c in r.json()}.isdisjoint({c["id"] for c in client.get("/api/cases?limit=3", headers=h).json()})
    assert client.get(f"/api/cases?patient_id={ids['p']}", headers=h).headers["x-total-count"] == "6"
    assert client.get("/api/cases?village=หมู่ 9", headers=h).headers["x-total-count"] == "1"
    assert client.get("/api/cases?q=ค้นหา", headers=h).headers["x-total-count"] == "1"
    assert client.get("/api/cases?q=b7", headers=h).headers["x-total-count"] == "1"
    assert client.get("/api/cases?reason=false_alarm", headers=h).headers["x-total-count"] == "3"
    frm = (now - timedelta(days=25)).astimezone().date()
    assert client.get(f"/api/cases?from={frm}", headers=h).headers["x-total-count"] in ("3", "4")
    assert client.get(f"/api/cases?to={(now - timedelta(days=35)).date()}", headers=h).headers["x-total-count"] in ("2", "3")
    assert client.get("/api/cases?from=bad", headers=h).status_code == 422
    assert client.get("/api/cases?state=new", headers=h).headers["x-total-count"] == "1"      # old callers keep working
    one = client.get("/api/cases?limit=1", headers=h).json()[0]["id"]
    assert client.get(f"/api/cases?q=%23{one}", headers=h).headers["x-total-count"] == "1"


def test_import_patients_and_caregivers(env_):
    eng, downs, notifier, client, ids = env_
    h = _h(client)
    tpl = client.get("/api/import/patients/template.csv", headers=h)
    assert tpl.status_code == 200 and "ชื่อ-สกุล" in tpl.text and tpl.text.startswith("﻿")
    csv_ = ("﻿ชื่อ-สกุล,อายุ,ที่อยู่,หมู่บ้าน,โทร,ละติจูด,ลองจิจูด,หมายเหตุ,ระดับ,คอลัมน์อื่น\n"
            "นายใหม่ หนึ่ง,80,1 ม.1,หมู่ 1,0811111111,13.9,100.6,,เฝ้าระวัง,x\n"
            "นางสมศรี  ใจดี,70,,,,,,,,\n"                   # already in the system (extra space ignored)
            "นายใหม่ หนึ่ง,,,,,,,,,\n"                      # twice in the file
            ",50,,,,,,,,\n"                                  # no name
            "นายผิด พิกัด,abc,,,,95,100,,ด่วน,\n"
            "นางใหม่ สอง,,,,,,,,,\n")
    r = client.post("/api/import/patients", headers=h, json={"csv": csv_, "dry_run": True}).json()
    assert [x["status"] for x in r["rows"]] == ["new", "duplicate", "duplicate_in_file", "error", "error", "new"]
    assert r["counts"] == {"new": 2, "duplicate": 1, "duplicate_in_file": 1, "error": 2} and r["created"] == 0
    assert r["ignored_columns"] == ["คอลัมน์อื่น"] and len(r["rows"][4]["errors"]) == 3 and r["rows"][0]["line"] == 2
    with SessionLocal() as db:
        assert db.query(Patient).count() == 1
    r = client.post("/api/import/patients", headers=h, json={"csv": csv_, "dry_run": False}).json()
    assert r["created"] == 2
    with SessionLocal() as db:
        p = db.query(Patient).filter_by(full_name="นายใหม่ หนึ่ง").one()
        assert (p.age, p.village, p.lat, p.priority, p.consent_at) == (80, "หมู่ 1", 13.9, "watch", None)
    assert client.post("/api/import/patients", headers=h, json={"csv": csv_, "dry_run": False}).json()["created"] == 0     # safe to repeat
    r = client.post("/api/import/caregivers", headers=h, json={"csv": "ชื่อ\tตำแหน่ง\tเบอร์โทร\nอสม.ใหม่\t\t0822222222\nอสม.หนึ่ง\t\t\n", "dry_run": False}).json()
    assert r["created"] == 1 and [x["status"] for x in r["rows"]] == ["new", "duplicate"]
    with SessionLocal() as db:
        c = db.query(Caregiver).filter_by(full_name="อสม.ใหม่").one(); assert (c.role, c.phone) == ("อสม.", "0822222222")
    assert client.post("/api/import/patients", headers=h, json={"csv": "a,b\n1,2\n"}).status_code == 422
    assert client.post("/api/import/patients", headers=_h(client, "viewer"), json={"csv": csv_}).status_code == 403
    assert client.post("/api/import/users", headers=h, json={"csv": csv_}).status_code == 404


def test_readiness(env_):
    eng, downs, notifier, client, ids = env_
    with SessionLocal() as db:
        p = db.get(Patient, ids["p"]); p.consent_at = utcnow(); p.consent_by = "admin"; p.lat, p.lon = 13.9, 100.6
        db.query(Device).first().last_seen = utcnow()
        db.add(Patient(full_name="นายไม่พร้อม")); db.add(Patient(full_name="ปิด", active=False))
        db.get(Caregiver, ids["c"]).line_user_id = ""; db.commit()
    r = client.get("/api/readiness", headers=_h(client)).json()
    assert (r["total"], r["at_risk"], r["ready"]) == (2, 1, 0)
    bad, good = r["patients"]
    assert bad["full_name"] == "นายไม่พร้อม" and bad["level"] == "red" and {"caregiver", "device", "consent"} <= set(bad["missing"])
    assert good["level"] == "amb" and good["missing"] == ["selftest"] and good["caregiver"] == "อสม.หนึ่ง"
    chk = {c["key"]: c for c in r["checks"]}
    assert chk["caregiver"]["ok"] == 1 and chk["caregiver"]["total"] == 2 and chk["caregiver"]["critical"] is True
    assert r["caregivers_no_line"] == [{"id": ids["c"], "full_name": "ผอ.รพ.สต.", "in_use": False}]

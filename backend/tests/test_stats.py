"""Per-patient statistics and caregiver workload."""
from datetime import datetime, timedelta, timezone
from app.db import SessionLocal
from app.models import Assignment, Case, Device, Notification, Patient


def _login(client, user="admin"):
    t = client.post("/api/login", json={"username": user, "password": "password12345"}).json()["token"]
    return {"authorization": "Bearer " + t}


def _case(db, pid, did, when, **kw):
    c = Case(device_id=did, patient_id=pid, alert_id=1, created_at=when, **kw); db.add(c); db.flush(); return c


def test_patient_stats(env_):
    eng, downs, notifier, client, ids = env_
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        did = db.query(Device).first().id
        _case(db, ids["p"], did, now - timedelta(days=2), state="closed", close_reason="resolved",
              accepted_at=now - timedelta(days=2) + timedelta(seconds=60), closed_at=now - timedelta(days=2) + timedelta(minutes=30), accepted_by="x")
        _case(db, ids["p"], did, now - timedelta(days=20), state="closed", close_reason="false_alarm",
              accepted_at=now - timedelta(days=20) + timedelta(seconds=120), closed_at=now - timedelta(days=20) + timedelta(minutes=10), after_hours=True)
        _case(db, ids["p"], did, now - timedelta(days=200), state="cancelled")
        _case(db, ids["p"], did, now - timedelta(days=1), state="closed", is_test=True)     # self-test: never counted
        db.commit()
    r = client.get(f"/api/patients/{ids['p']}/stats", headers=_login(client)).json()
    t = r["totals"]
    assert (t["all"], t["d7"], t["d30"], t["d365"]) == (3, 1, 2, 3)
    assert (t["false_alarm"], t["cancelled"], t["resolved"], t["after_hours"], t["open"]) == (1, 1, 1, 1, 0)
    assert r["response"]["avg_accept_s"] == 90.0 and r["response"]["n_accepted"] == 2 and r["response"]["avg_close_min"] == 20.0
    assert len(r["months"]) == 12 and sum(m["n"] for m in r["months"]) == 3
    assert sum(d["n"] for d in r["dayparts"]) == 3 and sum(r["weekdays"]) == 3
    assert r["tests"] == 1 and r["last_test_at"] and len(r["recent"]) == 3 and r["recent"][0]["created_at"] > r["recent"][1]["created_at"]
    assert [c["name"] for c in r["caregivers"]] == ["อสม.หนึ่ง", "อสม.สอง"] and r["device"]["label"] == "A1"
    assert r["patient"]["phone"] == "0800000000"
    v = client.get(f"/api/patients/{ids['p']}/stats", headers=_login(client, "viewer")).json()
    assert v["patient"]["address"] == "" and v["patient"]["phone"] != "0800000000" and v["totals"]["all"] == 3
    assert client.get("/api/patients/999/stats", headers=_login(client)).status_code == 404


def test_caregiver_workload(env_):
    eng, downs, notifier, client, ids = env_
    with SessionLocal() as db:
        p2 = Patient(full_name="นายสอง", priority="critical"); p3 = Patient(full_name="นายสาม"); p4 = Patient(full_name="ปิดแล้ว", active=False)
        db.add_all([p2, p3, p4]); db.flush()
        db.add_all([Assignment(patient_id=p2.id, caregiver_id=ids["a"], priority=1),
                    Assignment(patient_id=p4.id, caregiver_id=ids["a"], priority=1)])       # inactive patient: not counted
        did = db.query(Device).first().id
        c = _case(db, ids["p"], did, datetime.now(timezone.utc))
        db.add(Notification(case_id=c.id, caregiver_id=ids["a"])); db.add(Notification(case_id=c.id, caregiver_id=ids["a"]))
        db.commit()
    h = _login(client)
    cg = {c["id"]: c for c in client.get("/api/caregivers", headers=h).json()}
    a, b, c3 = cg[ids["a"]], cg[ids["b"]], cg[ids["c"]]
    assert (a["n_patients"], a["n_primary"], a["n_backup"], a["n_critical"]) == (2, 2, 0, 1)
    assert (b["n_patients"], b["n_primary"], b["n_backup"]) == (1, 0, 1)
    assert c3["n_patients"] == 0 and c3["patients"] == []
    assert a["cases_notified_30d"] == 1 and sorted(x["full_name"] for x in a["patients"]) == ["นางสมศรี ใจดี", "นายสอง"]
    s = client.get("/api/caregivers/summary", headers=h).json()
    assert (s["patients"], s["patients_covered"], s["patients_uncovered"]) == (3, 2, 1)
    assert [x["full_name"] for x in s["uncovered"]] == ["นายสาม"]
    assert (s["active"], s["with_patients"], s["without_patients"], s["avg_per_caregiver"], s["max_per_caregiver"]) == (3, 2, 1, 1.5, 2)

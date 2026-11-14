"""Dashboard users bind their own LINE and choose what they are told (app/routing.py): system / device / cases (all|overdue),
limited by role; an admin can set it for others; the contact follows the account."""
from datetime import timedelta
from app import codec, settings_store
from app.auth import hash_pw
from app.db import SessionLocal
from app.models import Caregiver, Case, User
from tests.test_all import EUI, NOON_TUE, _dev, login, patch_now, post_line, up


def _staff(name="nurse", role="staff", display="พยาบาลสมใจ"):
    with SessionLocal() as db:
        u = User(username=name, password_hash=hash_pw("password12345"), role=role, display_name=display); db.add(u); db.commit()
        return u.id


def _bind(client, h, uid):
    code = client.post("/api/me/line/bind-code", headers=h).json()["code"]
    r = post_line(client, {"events": [{"type": "message", "replyToken": "r", "source": {"userId": uid}, "message": {"type": "text", "text": code}}]})
    assert r.status_code == 200


def test_staff_binds_own_line_and_gets_every_patient_alert(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    _staff(); h = login(client, "nurse")
    st = client.get("/api/me/line", headers=h).json()
    assert st == {"allowed": ["device", "cases"], "bound": False, "contact_id": None, "prefs": {"system": False, "device": False, "cases": "off"}}
    assert client.put("/api/me/line", json={"system": True}, headers=h).status_code == 403       # system alerts are for admins
    assert client.put("/api/me/line", json={"cases": "sometimes"}, headers=h).status_code == 422
    st = client.put("/api/me/line", json={"cases": "all"}, headers=h).json()
    assert st["prefs"] == {"system": False, "device": False, "cases": "all"} and st["contact_id"] and not st["bound"]
    _bind(client, h, "UNURSE")
    assert client.get("/api/me/line", headers=h).json()["bound"] is True
    assert "บัญชีของฉัน" in notif.replies[-1]
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT)) == "created"
    assert sorted(n for _, n, _ in notif.sent) == ["พยาบาลสมใจ", "อสม.หนึ่ง"]
    # the contact shows in the caregiver list, marked as a user's; it is not "an อสม. without patients"
    row = [c for c in client.get("/api/caregivers", headers=h).json() if c["user_id"]][0]
    assert row["full_name"] == "พยาบาลสมใจ" and row["is_central"] and row["line_bound"]
    assert client.get("/api/caregivers/summary", headers=h).json()["without_patients"] == 1      # ผอ.รพ.สต. only
    st = client.post("/api/me/line/unbind", headers=h).json()
    assert st["bound"] is False and st["prefs"]["cases"] == "all"


def test_viewer_cannot_bind_or_subscribe(env_):
    *_, client, ids = env_
    h = login(client, "viewer")
    assert client.get("/api/me/line", headers=h).json()["allowed"] == []
    assert client.post("/api/me/line/bind-code", headers=h).status_code == 403
    assert client.put("/api/me/line", json={"cases": "all"}, headers=h).status_code == 403
    assert client.put("/api/me/line", json={"device": True}, headers=h).status_code == 403
    with SessionLocal() as db:
        assert db.query(Caregiver).filter(Caregiver.user_id.isnot(None)).count() == 0


def test_overdue_only_is_told_once_when_nobody_accepted(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    _staff(); h = login(client, "nurse")
    assert client.put("/api/me/line", json={"cases": "overdue"}, headers=h).status_code == 200
    _bind(client, h, "UNURSE")
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        after = settings_store.get(db, "escalation")["overdue_after_s"]
    assert [n for _, n, _ in notif.sent] == ["อสม.หนึ่ง"]                     # not at the first press
    for dt in (after + 1, after + 6):
        with SessionLocal() as db:
            eng.tick(db, NOON_TUE + timedelta(seconds=dt))
    told = [(n, rem) for _, n, rem in notif.sent if n == "พยาบาลสมใจ"]
    assert told == [("พยาบาลสมใจ", True)]                                   # once, as a reminder card


def test_device_problems_go_to_whoever_asked_gateway_stays_with_system(env_):
    eng, downs, notif, client, ids = env_
    _staff(); h = login(client, "nurse"); ha = login(client)
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["c"]], "repeat_min": 60}, "t")      # system = ผอ.
    assert client.put("/api/me/line", json={"device": True}, headers=h).status_code == 200
    _bind(client, h, "UNURSE")
    with SessionLocal() as db:
        d = _dev(db); d.last_seen, d.battery, d.mains_ok = NOON_TUE, 80, False; db.commit()
    for t in (NOON_TUE, NOON_TUE + timedelta(minutes=11)):
        with SessionLocal() as db:
            eng.dev_watch.tick(db, t)
    assert sorted({n for n, _ in notif.system}) == ["พยาบาลสมใจ", "อสม.หนึ่ง"]      # who asked + the patient's own caregiver; not ผอ.
    notif.system.clear()
    with SessionLocal() as db:
        eng.gw.send_system(db, "เกตเวย์ขาดการเชื่อมต่อ")
    assert [n for n, _ in notif.system] == ["ผอ.รพ.สต."]
    # an admin may take system alerts for himself; a caregiver row can be given device alerts from the caregiver page
    assert client.put("/api/me/line", json={"system": True}, headers=ha).json()["prefs"]["system"] is True
    r = client.put(f"/api/caregivers/{ids['b']}", json={"full_name": "อสม.สอง", "alert_device": True}, headers=ha)
    assert r.status_code == 200 and r.json()["alert_device"] is True
    assert client.put(f"/api/caregivers/{ids['b']}", json={"full_name": "อสม.สอง", "alert_device": False}, headers=h).status_code == 403


def test_admin_sets_for_others_and_contact_follows_the_account(env_):
    eng, downs, notif, client, ids = env_
    uid = _staff(); ha = login(client)
    r = client.put(f"/api/users/{uid}/line", json={"device": True, "cases": "all"}, headers=ha)
    assert r.status_code == 200 and r.json()["line"]["prefs"] == {"system": False, "device": True, "cases": "all"}
    assert client.put(f"/api/users/{uid}/line", json={"system": True}, headers=ha).status_code == 422       # staff role: not allowed
    assert client.put(f"/api/users/{uid}/line", json={"cases": "all"}, headers=login(client, "nurse")).status_code == 403
    row = [x for x in client.get("/api/users", headers=ha).json() if x["id"] == uid][0]
    assert row["line"]["allowed"] == ["device", "cases"] and row["line"]["bound"] is False
    # promoted to admin: may now get system alerts; demoted to viewer: loses everything
    assert client.put(f"/api/users/{uid}", json={"role": "admin"}, headers=ha).status_code == 200
    assert client.put(f"/api/users/{uid}/line", json={"system": True}, headers=ha).json()["line"]["prefs"]["system"] is True
    assert client.put(f"/api/users/{uid}", json={"role": "viewer", "display_name": "สมใจ ใหม่"}, headers=ha).status_code == 200
    with SessionLocal() as db:
        cg = db.query(Caregiver).filter_by(user_id=uid).one()
        assert (cg.is_central, cg.alert_device, cg.alert_overdue) == (False, False, False) and cg.full_name == "สมใจ ใหม่"
        assert cg.id not in settings_store.get(db, "system_alert")["caregiver_ids"]
    # closed account: the contact is told nothing; deleted: unlinked and unbound
    client.put(f"/api/users/{uid}", json={"role": "staff"}, headers=ha); client.put(f"/api/users/{uid}/line", json={"cases": "all"}, headers=ha)
    client.put(f"/api/users/{uid}", json={"active": False}, headers=ha)
    with SessionLocal() as db:
        cg = db.query(Caregiver).filter_by(user_id=uid).one(); cid = cg.id
        assert cg.active is False and cg not in eng.central(db)
    assert client.delete(f"/api/users/{uid}", headers=ha).status_code == 200
    with SessionLocal() as db:
        cg = db.get(Caregiver, cid)
        assert cg.user_id is None and not cg.is_central and cg.line_user_id == "" and cg.active is False

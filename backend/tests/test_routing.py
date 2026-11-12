"""Who is told what: system alerts -> admins (+ a button problem also to that patient's own caregiver); patient alerts ->
the patient's caregiver and every central staff member; closing a case from LINE."""
from datetime import timedelta
from app import codec, settings_store
from app.db import SessionLocal
from app.models import AuditLog, Caregiver, Case, CaseEvent, Notification, Outbox
from tests.test_all import EUI, NOON_TUE, _dev, login, patch_now, post_line, up


def _flag(ids, **who):
    with SessionLocal() as db:
        for k, v in who.items():
            db.get(Caregiver, ids[k]).is_central = v
        db.commit()


def _postback(client, uid, data):
    return post_line(client, {"events": [{"type": "postback", "replyToken": "r", "source": {"userId": uid}, "postback": {"data": data}}]})


def _text(client, uid, text):
    return post_line(client, {"events": [{"type": "message", "replyToken": "r", "source": {"userId": uid}, "message": {"type": "text", "text": text}}]})


def _alert(eng, alert=5):
    with SessionLocal() as db:
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=alert)) == "created"
        return db.query(Case).order_by(Case.id.desc()).first().id


# ---------- patient alerts: own caregiver + central ----------
def test_alert_goes_to_own_caregiver_and_central_only(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE); _flag(ids, c=True)
    cid = _alert(eng)
    assert sorted(n for _, n, _ in notif.sent) == ["ผอ.รพ.สต.", "อสม.หนึ่ง"]        # อสม.สอง (step 2) is not told yet
    assert notif.system == []                                                     # nothing on the system channel
    with SessionLocal() as db:
        steps = [e.detail["step"] for e in db.query(CaseEvent).filter_by(case_id=cid, kind="notified")]
        assert steps == [0, "central"]
        assert db.query(Notification).filter_by(case_id=cid).count() == 2


def test_central_who_is_first_in_chain_is_told_once(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE); _flag(ids, a=True)
    _alert(eng)
    assert [n for _, n, _ in notif.sent] == ["อสม.หนึ่ง"]


def test_no_central_means_behaviour_as_before_and_tests_never_reach_central(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE)
    _alert(eng)
    assert [n for _, n, _ in notif.sent] == ["อสม.หนึ่ง"]
    notif.sent.clear(); _flag(ids, c=True)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_TEST, alert=9))
    assert notif.sent == []


def test_inactive_central_is_skipped(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE); _flag(ids, c=True)
    with SessionLocal() as db:
        db.get(Caregiver, ids["c"]).active = False; db.commit()
    _alert(eng)
    assert [n for _, n, _ in notif.sent] == ["อสม.หนึ่ง"]


def test_unreachable_central_does_not_skip_the_caregivers_waiting_time(env_):
    """Outbox: a final failure for the chain's person escalates at once; for someone who is only central it must not."""
    from app.outbox import NotifyWorker
    eng, downs, notif, client, ids = env_
    _flag(ids, c=True)
    with SessionLocal() as db:
        case = Case(device_id=_dev(db).id, patient_id=ids["p"], alert_id=1, next_escalation_at=NOON_TUE + timedelta(seconds=120))
        db.add(case); db.commit()
        w = NotifyWorker(SessionLocal, notif)
        for who, moved in (("c", False), ("a", True)):
            row = Outbox(kind="case", case_id=case.id, caregiver_id=ids[who], payload={}, attempts=9); db.add(row); db.flush()
            case.next_escalation_at = NOON_TUE + timedelta(seconds=120)
            w._finish(db, row, "failed", "HTTP 500", NOON_TUE, case); db.commit()
            assert (case.next_escalation_at.replace(tzinfo=None) == NOON_TUE.replace(tzinfo=None)) is moved, who


# ---------- system alerts: admins; button problems also to the patient's own caregiver ----------
def test_button_problem_goes_to_admin_and_own_caregiver_gateway_to_admin_only(env_):
    eng, downs, notif, client, ids = env_
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["c"]], "repeat_min": 60}, "t")      # admin = ผอ.
        d = _dev(db); d.last_seen, d.battery, d.mains_ok = NOON_TUE, 80, False; db.commit()
    for t in (NOON_TUE, NOON_TUE + timedelta(minutes=11)):
        with SessionLocal() as db:
            eng.dev_watch.tick(db, t)
    assert sorted({n for n, _ in notif.system}) == ["ผอ.รพ.สต.", "อสม.หนึ่ง"]        # admin + first caregiver, not อสม.สอง
    notif.system.clear()
    with SessionLocal() as db:
        res = eng.gw.send_system(db, "เกตเวย์ขาดการเชื่อมต่อ")
    assert [n for n, _ in notif.system] == ["ผอ.รพ.สต."] and len(res) == 1


def test_routing_switches_are_admin_only_and_show_in_the_list(env_):
    eng, downs, notif, client, ids = env_
    h = login(client)
    body = {"full_name": "ผอ.รพ.สต.", "role": "ผอ.", "phone": "", "active": True}
    r = client.put(f"/api/caregivers/{ids['c']}", json={**body, "is_central": True, "system_alert": True}, headers=h)
    assert r.status_code == 200 and r.json()["is_central"] is True and r.json()["system_alert"] is True
    row = [c for c in client.get("/api/caregivers", headers=h).json() if c["id"] == ids["c"]][0]
    assert row["is_central"] and row["system_alert"]
    assert client.get("/api/settings", headers=h).json()["system_alert"]["caregiver_ids"] == [ids["c"]]
    sm = client.get("/api/caregivers/summary", headers=h).json()
    assert sm["central"] == 1 and sm["system_admins"] == 1 and sm["system_admins_line"] == 1
    # an edit that does not mention the switches leaves them alone
    assert client.put(f"/api/caregivers/{ids['c']}", json=body, headers=h).json()["is_central"] is True
    # staff may edit the person but not who is told what
    with SessionLocal() as db:
        from app.auth import hash_pw
        from app.models import User
        db.add(User(username="staff1", password_hash=hash_pw("password12345"), role="staff")); db.commit()
    hs = login(client, "staff1")
    assert client.put(f"/api/caregivers/{ids['c']}", json={**body, "phone": "0811111111"}, headers=hs).status_code == 200
    assert client.put(f"/api/caregivers/{ids['c']}", json={**body, "is_central": False}, headers=hs).status_code == 403
    assert client.put(f"/api/caregivers/{ids['c']}", json={**body, "system_alert": False}, headers=hs).status_code == 403
    # switching off removes the person from the system-alert list
    assert client.put(f"/api/caregivers/{ids['c']}", json={**body, "system_alert": False}, headers=h).json()["system_alert"] is False
    assert client.get("/api/settings", headers=h).json()["system_alert"]["caregiver_ids"] == []


# ---------- closing from LINE ----------
def test_acceptor_closes_from_line_and_adds_a_note(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE)
    cid = _alert(eng)
    _postback(client, "U1", f"accept:{cid}")
    assert notif.cards[-1]["contents"]["footer"]["contents"][0]["action"]["data"] == f"close:{cid}:resolved"
    _postback(client, "U2", f"close:{cid}:resolved")                       # อสม.สอง did not accept and is not central
    assert "ปิดเคสนี้ไม่ได้" in notif.replies[-1]
    n_down = len(downs)
    _postback(client, "U1", f"close:{cid}:resolved")
    assert "ปิดเคส" in notif.replies[-1] and "ช่วยเหลือแล้ว" in notif.replies[-1]
    assert len(downs) > n_down                                              # the device is told as well (ACK with the result)
    _text(client, "U1", "ผู้ป่วยหกล้ม ทำแผลแล้ว")
    assert "บันทึกในเคส" in notif.replies[-1]
    _text(client, "U1", "ข้อความถัดไปไม่ใช่บันทึก")
    assert "บันทึกในเคส" not in notif.replies[-1]
    with SessionLocal() as db:
        c = db.get(Case, cid)
        assert (c.state, c.close_reason, c.accepted_cg_id) == ("closed", "resolved", ids["a"])
        assert "หกล้ม" in c.note and "ถัดไป" not in c.note
        ev = db.query(CaseEvent).filter_by(case_id=cid, kind="closed").one()
        assert ev.actor.endswith("อสม.หนึ่ง") and ev.detail["reason"] == "resolved"
        assert db.query(AuditLog).filter_by(action="case_close", actor=f"line:{ids['a']}").count() == 1
    _postback(client, "U1", f"close:{cid}:false_alarm")                    # pressing again changes nothing
    assert "ปิดไปแล้ว" in notif.replies[-1]
    with SessionLocal() as db:
        assert db.get(Case, cid).close_reason == "resolved"


def test_central_can_close_any_case_others_cannot(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE); _flag(ids, c=True)
    cid = _alert(eng)
    _postback(client, "U2", f"close:{cid}:false_alarm")                    # nobody accepted, U2 is an ordinary caregiver
    assert "ปิดเคสนี้ไม่ได้" in notif.replies[-1]
    _postback(client, "U1", f"accept:{cid}")
    _postback(client, "U3", f"accept:{cid}")                               # central presses the old card: gets the close buttons
    assert "ถูกรับโดย" in notif.replies[-1] and notif.cards[-1]["contents"]["footer"]["contents"][1]["action"]["data"] == f"close:{cid}:false_alarm"
    _postback(client, "U2", f"accept:{cid}")                               # ordinary caregiver: text only
    n_cards = len(notif.cards)
    assert "ถูกรับโดย" in notif.replies[-1] and len(notif.cards) == n_cards
    _postback(client, "U3", f"close:{cid}:false_alarm")
    with SessionLocal() as db:
        c = db.get(Case, cid)
        assert (c.state, c.close_reason) == ("closed", "false_alarm")
    cid2 = _alert(eng, alert=6)
    _postback(client, "U3", f"close:{cid2}:resolved")                      # central may close one nobody accepted
    with SessionLocal() as db:
        assert db.get(Case, cid2).state == "closed"


def test_close_postback_rejects_strangers_and_garbage(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE)
    cid = _alert(eng)
    _postback(client, "STRANGER", f"close:{cid}:resolved")
    assert "ไม่พบบัญชี" in notif.replies[-1]
    for bad in (f"close:{cid}:deleted", "close:abc:resolved", "close:999:resolved", "close:"):
        assert _postback(client, "U1", bad).status_code == 200
    with SessionLocal() as db:
        assert db.get(Case, cid).state == "new"


def test_button_problem_to_admin_only_when_switched_off(env_):
    eng, downs, notif, client, ids = env_
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["c"]], "repeat_min": 60}, "t")
        settings_store.put(db, "device_alert", {**settings_store.get(db, "device_alert"), "to_caregiver": False}, "t")
        d = _dev(db); d.last_seen, d.battery, d.mains_ok = NOON_TUE, 80, False; db.commit()
    for t in (NOON_TUE, NOON_TUE + timedelta(minutes=11)):
        with SessionLocal() as db:
            eng.dev_watch.tick(db, t)
    assert {n for n, _ in notif.system} == {"ผอ.รพ.สต."}
    h = {"Authorization": "Bearer " + client.post("/api/login", json={"username": "admin", "password": "password12345"}).json()["token"]}
    old = {"enabled": True, "battery_low_pct": 20, "mains_grace_min": 10}                 # an older dashboard sends fewer fields
    assert client.put("/api/settings/device_alert", json=old, headers=h).status_code == 200
    with SessionLocal() as db:
        assert settings_store.get(db, "device_alert")["to_caregiver"] is False             # and does not flip the switch back

import base64, hashlib, hmac, json
from datetime import datetime, timedelta, timezone
from app import codec, settings_store
from app.db import SessionLocal
from app.models import Case, Device, Holiday
from app.mqtt_worker import parse_up_event

EUI = "70b3d57ed0000001"
NOON_TUE = datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)      # 12:00 Bangkok, Tuesday
NIGHT = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)        # 22:00 Bangkok


def up(t, alert=5, flags=1, batt=87, cfg=1, poll=0):
    return codec.encode_uplink(codec.Uplink(t, alert, flags, batt, 1, 0, cfg, poll))


def patch_now(monkeypatch, when):
    monkeypatch.setattr("app.engine.utcnow", lambda: when)


# ---------- codec ----------
def test_contract_vectors():
    assert codec.b64(bytes.fromhex("1105015700010100")) == "EQUBVwABAQA="
    u = codec.decode_uplink(bytes.fromhex("1105015700010100"))
    assert (u.type_name, u.alert_id, u.battery, u.mains_ok, u.fw) == ("ALERT", 5, 87, True, "0.1")
    assert codec.b64(codec.encode_ack_l2(5, 1)) == "FgUB"


def test_codec_rejects_bad():
    import pytest
    for bad in (b"", b"\x11" * 7, bytes([0x21, 0, 0, 0, 0, 0, 0, 0]), bytes([0x1F] + [0] * 7)):
        with pytest.raises(codec.CodecError):
            codec.decode_uplink(bad)


def test_config_encoding():
    assert codec.encode_config(3, 30, 10, 2, 1500) == bytes([0x17, 3, 6, 10, 2, 15])


def test_parse_chirpstack_event():
    ev = {"deviceInfo": {"devEui": "70B3D57ED0000001"}, "fPort": 1, "data": "EQUBVwABAQA=",
          "rxInfo": [{"gatewayId": "aa", "rssi": -90, "snr": 7}, {"gatewayId": "bb", "rssi": -80, "snr": 9}]}
    eui, port, data, meta = parse_up_event(json.dumps(ev).encode())
    assert eui == EUI and port == 1 and len(data) == 8 and meta["gateway_id"] == "bb"
    assert parse_up_event(b"garbage") is None


# ---------- engine ----------
def test_alert_creates_case_and_notifies_first(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT)) == "created"
        c = db.query(Case).one()
        assert c.state == "new" and not c.after_hours and c.patient_id == ids["p"]
    assert notif.sent == [(1, "อสม.หนึ่ง", False)]


def test_duplicate_press_no_second_case(env_, monkeypatch):
    eng, downs, notif, *_ = env_
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=6)) == "duplicate"
        assert db.query(Case).count() == 1


def test_accept_sends_ack_l2_and_poll_resends(env_, monkeypatch):
    eng, downs, *_ = env_
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=5))
        assert eng.handle_uplink(db, EUI, up(codec.T_POLL, alert=5)) == "pending"
        eng.accept(db, 1, "staff1")
        assert downs[-1] == (EUI, 10, bytes([0x16, 5, 1]))
        n = len(downs)
        # poll right after: ACK already in flight, no duplicate
        assert eng.handle_uplink(db, EUI, up(codec.T_POLL, alert=5)) == "ack_in_flight"
        assert len(downs) == n
        # 40 s later still polling -> resend
        patch_now(monkeypatch, NOON_TUE + timedelta(seconds=40))
        assert eng.handle_uplink(db, EUI, up(codec.T_POLL, alert=5)) == "ack_sent"
        assert downs[-1][2] == bytes([0x16, 5, 1])


def test_accept_idempotent_first_wins(env_, monkeypatch):
    eng, *_ = env_
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        eng.accept(db, 1, "A"); c = eng.accept(db, 1, "B")
        assert c.accepted_by == "A"


def test_after_hours_result4_and_still_notifies(env_, monkeypatch):
    eng, downs, notif, *_ = env_
    patch_now(monkeypatch, NIGHT)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        assert db.query(Case).one().after_hours is True
        assert eng.handle_uplink(db, EUI, up(codec.T_POLL)) == "ack_sent"
        assert downs[-1][2] == bytes([0x16, 5, 4])
    assert len(notif.sent) == 1


def test_holiday_is_after_hours(env_, monkeypatch):
    eng, *_ = env_
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        db.add(Holiday(day="2026-10-13", name="วันนวมินทรมหาราช")); db.commit()
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        assert db.query(Case).one().after_hours


def test_escalation_chain_then_final_and_overdue(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        settings_store.put(db, "escalation", {"escalate_after_s": 120, "overdue_after_s": 300,
                                              "final_caregiver_id": ids["c"], "max_rounds": 1}, "t")
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        eng.tick(db, NOON_TUE + timedelta(seconds=60))
        assert len(notif.sent) == 1
        eng.tick(db, NOON_TUE + timedelta(seconds=125))
        assert [s[1] for s in notif.sent] == ["อสม.หนึ่ง", "อสม.สอง"]
        eng.tick(db, NOON_TUE + timedelta(seconds=250))
        assert notif.sent[-1][1] == "ผอ.รพ.สต."
        eng.tick(db, NOON_TUE + timedelta(seconds=310))
        assert db.query(Case).one().overdue is True


def test_accept_stops_escalation(env_, monkeypatch):
    eng, downs, notif, *_ = env_
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        eng.accept(db, 1, "x")
        eng.tick(db, NOON_TUE + timedelta(seconds=500))
    assert len(notif.sent) == 1


def test_close_false_alarm_ack3(env_, monkeypatch):
    eng, downs, *_ = env_
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        eng.close(db, 1, "s", "false_alarm")
        assert downs[-1][2] == bytes([0x16, 5, 3])


def test_cancel_and_test_and_unknown(env_, monkeypatch):
    eng, downs, notif, *_ = env_
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        assert eng.handle_uplink(db, EUI, up(codec.T_CANCEL)) == "cancelled"
        assert db.query(Case).one().state == "cancelled"
        assert eng.handle_uplink(db, EUI, up(codec.T_TEST, alert=9)) == "test"
        assert eng.handle_uplink(db, "deadbeefdeadbeef", up(codec.T_ALERT)) == "unknown_device"
        assert eng.handle_uplink(db, EUI, b"\x00") == "bad_payload"
    assert len(notif.sent) == 1          # test case never notifies


def test_inactive_device_does_not_alert(env_, monkeypatch):
    eng, *_ = env_
    with SessionLocal() as db:
        db.query(Device).update({"lifecycle": "repair"}); db.commit()
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT)) == "inactive_device"
        assert db.query(Case).count() == 0


def test_heartbeat_config_sync(env_):
    eng, downs, *_ = env_
    with SessionLocal() as db:
        db.query(Device).update({"config_ver": 4}); db.commit()
        eng.handle_uplink(db, EUI, up(codec.T_HEARTBEAT, cfg=1))
        assert downs[-1][1] == 11 and downs[-1][2][1] == 4
        n = len(downs)
        eng.handle_uplink(db, EUI, up(codec.T_HEARTBEAT, cfg=4))
        assert len(downs) == n


# ---------- API ----------
def login(client, u="admin"):
    r = client.post("/api/login", json={"username": u, "password": "password12345"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_auth_required_and_roles(env_):
    *_, client, ids = env_
    assert client.get("/api/cases").status_code == 401
    h = login(client, "viewer")
    assert client.get("/api/cases", headers=h).status_code == 200
    assert client.post("/api/patients", json={"full_name": "x"}, headers=h).status_code == 403
    assert client.put("/api/settings/office_hours", json={}, headers=h).status_code == 403


def test_lockout(env_):
    *_, client, ids = env_
    for _ in range(5):
        assert client.post("/api/login", json={"username": "admin", "password": "bad"}).status_code == 401
    assert client.post("/api/login", json={"username": "admin", "password": "password12345"}).status_code == 429


def test_api_case_flow(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE)
    h = login(client)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
    cs = client.get("/api/cases?state=new", headers=h).json()
    assert len(cs) == 1 and cs[0]["patient"]["full_name"].startswith("นาง")
    r = client.post("/api/cases/1/accept", headers=h)
    assert r.json()["state"] == "accepted"
    r = client.post("/api/cases/1/close", json={"reason": "resolved", "note": "ok"}, headers=h)
    assert r.json()["state"] == "closed"
    assert client.get("/api/stats", headers=h).json()["cases_30d"] == 1
    acts = [a["action"] for a in client.get("/api/audit", headers=h).json()]
    assert "case_accept" in acts and "case_close" in acts


def test_device_and_assignment_validation(env_):
    *_, client, ids = env_
    h = login(client)
    assert client.post("/api/devices", json={"dev_eui": "xyz"}, headers=h).status_code == 422
    assert client.post("/api/devices", json={"dev_eui": "70:B3:D5:7E:D0:00:00:02"}, headers=h).status_code == 200
    assert client.post("/api/devices", json={"dev_eui": "70b3d57ed0000002"}, headers=h).status_code == 409
    r = client.put(f"/api/patients/{ids['p']}/assignments", json={"caregiver_ids": [ids["b"], ids["a"]]}, headers=h)
    assert [x["name"] for x in r.json()["assignments"]] == ["อสม.สอง", "อสม.หนึ่ง"]
    assert client.put(f"/api/patients/{ids['p']}/assignments", json={"caregiver_ids": [ids["a"], ids["a"]]},
                      headers=h).status_code == 422


def test_settings_change_bumps_config_ver(env_):
    *_, client, ids = env_
    h = login(client)
    r = client.put("/api/settings/device_defaults",
                   json={"heartbeat_min": 15, "poll_s": 10, "buzzer": 3, "hold_ms": 1500}, headers=h)
    assert r.status_code == 200 and r.json()["buzzer"] == 3
    with SessionLocal() as db:
        assert db.query(Device).one().config_ver == 2


# ---------- LINE ----------
def sign(body: bytes):
    return base64.b64encode(hmac.new(b"linesecret", body, hashlib.sha256).digest()).decode()


def post_line(client, payload):
    body = json.dumps(payload).encode()
    return client.post("/api/line/webhook", content=body, headers={"x-line-signature": sign(body)})


def test_line_bad_signature(env_):
    *_, client, ids = env_
    r = client.post("/api/line/webhook", content=b"{}", headers={"x-line-signature": "nope"})
    assert r.status_code == 401


def test_line_bind_and_accept(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE)
    h = login(client)
    code = client.post(f"/api/caregivers/{ids['c']}/bind-code", headers=h).json()["code"]
    post_line(client, {"events": [{"type": "message", "replyToken": "r", "source": {"userId": "UNEW"},
                                   "message": {"type": "text", "text": code}}]})
    assert "ผูกบัญชีสำเร็จ" in notif.replies[-1]
    # reuse of code fails
    post_line(client, {"events": [{"type": "message", "replyToken": "r", "source": {"userId": "UX"},
                                   "message": {"type": "text", "text": code}}]})
    assert "ไม่ถูกต้อง" in notif.replies[-1]
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
    # stranger cannot accept
    post_line(client, {"events": [{"type": "postback", "replyToken": "r", "source": {"userId": "STRANGER"},
                                   "postback": {"data": "accept:1"}}]})
    assert "ไม่พบบัญชี" in notif.replies[-1]
    # bound อสม. accepts -> downlink
    post_line(client, {"events": [{"type": "postback", "replyToken": "r", "source": {"userId": "U1"},
                                   "postback": {"data": "accept:1"}}]})
    assert downs[-1][2] == bytes([0x16, 5, 1])
    with SessionLocal() as db:
        assert db.query(Case).one().accepted_by.endswith("อสม.หนึ่ง")


def test_line_empty_secret_rejected(monkeypatch):
    from app import line
    monkeypatch.setattr(line.env, "line_channel_secret", "")
    forged = base64.b64encode(hmac.new(b"", b"{}", hashlib.sha256).digest()).decode()
    assert line.verify_signature(b"{}", forged) is False


# ---------- per-device config ----------
def test_device_config_override_flow(env_):
    eng, downs, notif, client, ids = env_
    h = login(client)
    did = client.get("/api/devices", headers=h).json()[0]["id"]
    d0 = client.get("/api/devices", headers=h).json()[0]
    assert d0["config"] == {"heartbeat_min": 30, "poll_s": 10, "buzzer": 2, "hold_ms": 1500} and not d0["config_pending"]
    # validation
    for bad in ({"poll_s": 1}, {"buzzer": 9}, {"x": 1}, {"hold_ms": "a"}, {"heartbeat_min": 120}, {"poll_s": True}):
        assert client.put(f"/api/devices/{did}/config", json={"override": bad}, headers=h).status_code == 422
    assert client.put(f"/api/devices/{did}/config", json={"override": {"buzzer": 0}},
                      headers=login(client, "viewer")).status_code == 403
    r = client.put(f"/api/devices/{did}/config", json={"override": {"buzzer": 3, "hold_ms": 2000}}, headers=h)
    assert r.status_code == 200 and r.json()["changed"] and r.json()["effective"]["buzzer"] == 3
    d1 = client.get("/api/devices", headers=h).json()[0]
    assert d1["config"]["hold_ms"] == 2000 and d1["config"]["heartbeat_min"] == 30 and d1["config_ver"] == 2
    # same value again = no change, no version bump
    assert client.put(f"/api/devices/{did}/config", json={"override": {"buzzer": 3, "hold_ms": 2000}},
                      headers=h).json()["changed"] is False
    with SessionLocal() as db:
        # device still reports old ver -> pending, and CONFIG downlink carries the merged values
        eng.handle_uplink(db, EUI, up(codec.T_HEARTBEAT, cfg=1))
        assert downs[-1][1] == 11 and downs[-1][2] == codec.encode_config(2, 30, 10, 3, 2000)
    assert client.get("/api/devices", headers=h).json()[0]["config_pending"] is True
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_HEARTBEAT, cfg=2))
    d2 = client.get("/api/devices", headers=h).json()[0]
    assert d2["config_pending"] is False and d2["config_applied_at"]
    # reset to shared defaults
    client.put(f"/api/devices/{did}/config", json={"override": {}}, headers=h)
    assert client.get("/api/devices", headers=h).json()[0]["config"]["buzzer"] == 2
    acts = [a["action"] for a in client.get("/api/audit", headers=h).json()]
    assert acts.count("device_config") == 2


def test_default_settings_validated(env_):
    *_, client, ids = env_
    h = login(client)
    assert client.put("/api/settings/device_defaults", json={"heartbeat_min": 15, "poll_s": 2, "buzzer": 2, "hold_ms": 1500},
                      headers=h).status_code == 422
    assert client.put("/api/settings/device_defaults", json={"heartbeat_min": 15}, headers=h).status_code == 422


def test_ensure_columns_adds_missing():
    from sqlalchemy import text
    from app.db import engine as e, ensure_columns
    with e.begin() as cx:
        cx.execute(text("DROP TABLE IF EXISTS sos_devices_old"))
        cx.execute(text("CREATE TABLE sos_devices_old (id INTEGER PRIMARY KEY)"))
    from app.db import Base
    # simulate: drop a column-bearing table, recreate old shape, then migrate
    with e.begin() as cx:
        cx.execute(text("DROP TABLE IF EXISTS sos_devices"))
        cx.execute(text("CREATE TABLE sos_devices (id INTEGER PRIMARY KEY, dev_eui VARCHAR(16), label VARCHAR(64), "
                        "lifecycle VARCHAR(16), patient_id INTEGER, fw_version VARCHAR(16), config_ver INTEGER)"))
    ensure_columns()
    from sqlalchemy import inspect
    cols = {c["name"] for c in inspect(e).get_columns("sos_devices")}
    assert {"config_override", "reported_config_ver", "last_rssi"} <= cols


# ---------- provisioning API ----------
class FakeCS:
    configured = True
    def __init__(self): self.reg = []; self.deleted = []; self.fail = False
    def register(self, eui, key, name, desc=""):
        if self.fail:
            from app.provision import ProvisionError
            raise ProvisionError("ChirpStack: down")
        self.reg.append((eui, key, name))
    def delete(self, eui): self.deleted.append(eui)


def test_provision_flow(env_, monkeypatch):
    *_, client, ids = env_
    cs = FakeCS(); monkeypatch.setattr("app.provision.get_cs", lambda: cs)
    h = login(client)
    assert client.post("/api/provision/start", json={"label": "SOS-9"}, headers=login(client, "viewer")).status_code == 403
    r = client.post("/api/provision/start", json={"label": "SOS-9"}, headers=h)
    assert r.status_code == 200
    j = r.json(); eui = j["dev_eui"]
    assert len(eui) == 16 and eui.startswith("02") and len(j["app_key"]) == 32 and cs.reg == [(eui, j["app_key"], "SOS-9")]
    with SessionLocal() as db:
        d = db.query(Device).filter_by(dev_eui=eui).one()
        assert d.lifecycle == "ready" and d.label == "SOS-9"
    assert j["app_key"] not in str(client.get("/api/audit", headers=h).json())
    assert client.post("/api/provision/start", json={"label": "SOS-9"}, headers=h).status_code == 409
    # confirm checks readback
    assert client.post(f"/api/provision/{eui}/confirm", json={"info": "INFO deveui=0000 provisioned=1"}, headers=h).status_code == 422
    ok = client.post(f"/api/provision/{eui}/confirm", json={"info": f"INFO fw=0.2 provisioned=1 deveui={eui} cfg_ver=1"}, headers=h)
    assert ok.status_code == 200
    # abort refused once the device was seen
    with SessionLocal() as db:
        from app.models import utcnow
        db.query(Device).filter_by(dev_eui=eui).update({"last_seen": utcnow()}); db.commit()
    assert client.post(f"/api/provision/{eui}/abort", headers=h).status_code == 409


def test_provision_abort_and_failures(env_, monkeypatch):
    *_, client, ids = env_
    cs = FakeCS(); monkeypatch.setattr("app.provision.get_cs", lambda: cs)
    h = login(client)
    eui = client.post("/api/provision/start", json={"label": "SOS-A"}, headers=h).json()["dev_eui"]
    assert client.post(f"/api/provision/{eui}/abort", headers=h).status_code == 200
    assert cs.deleted == [eui]
    with SessionLocal() as db:
        assert db.query(Device).filter_by(dev_eui=eui).first() is None
    cs.fail = True
    assert client.post("/api/provision/start", json={"label": "SOS-B"}, headers=h).status_code == 502
    with SessionLocal() as db:
        assert db.query(Device).filter_by(label="SOS-B").first() is None
    cs.configured = False
    assert client.post("/api/provision/start", json={"label": "SOS-C"}, headers=h).status_code == 503
    assert client.get("/api/provision/status", headers=h).json() == {"configured": False}


def test_chirpstack_rest_client_rollback():
    import httpx
    from app.provision import ChirpStackRest, ProvisionError
    calls = []
    def handler(req):
        calls.append((req.method, req.url.path))
        if req.url.path.endswith("/keys"): return httpx.Response(500)
        return httpx.Response(200, json={})
    c = ChirpStackRest("http://cs", "k", "app", "prof", client=httpx.Client(transport=httpx.MockTransport(handler)))
    import pytest
    with pytest.raises(ProvisionError): c.register("02" + "ab" * 7, "00" * 16, "x")
    assert calls[-1][0] == "DELETE"


def test_label_svg(env_):
    *_, client, ids = env_
    h = login(client)
    r = client.get(f"/api/provision/{EUI}/label.svg", headers=h)
    assert r.status_code == 200 and b"<svg" in r.content and r.headers["content-type"].startswith("image/svg")
    assert client.get("/api/provision/ffffffffffffffff/label.svg", headers=h).status_code == 404
    assert client.get(f"/api/provision/{EUI}/label.svg").status_code in (401, 403)

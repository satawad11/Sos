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
    monkeypatch.setattr(line.linecfg.env, "line_channel_secret", "")
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


# ---------- gateways: health reports, offline detection, LINE alerts ----------
GW = "0016c001f1500001"
GOOD = {"uptime_s": 5000, "cpu_temp_c": 51.2, "throttled": "0x0", "disk_pct": 31, "mem_pct": 22, "load1": 0.2,
        "radio_fail": 0, "services": {"concentratord": True, "forwarder": True}, "spi": True, "ntp": True,
        "ip": "192.168.1.50", "sw": "sos-gw-report 1"}


def _gw(db):
    from app.models import Gateway
    return db.query(Gateway).filter_by(gateway_id=GW).one()


def test_sanitize_report_and_topic():
    from app import gateways as g
    bad = {"uptime_s": "x", "cpu_temp_c": 9999, "evil": "<script>", "ip": "<b>", "throttled": "zz", "spi": "yes",
           "services": {"concentratord": "no", "other": True}}
    assert g.sanitize_report(bad) is None or "evil" not in g.sanitize_report(bad)
    assert g.sanitize_report("nope") is None and g.sanitize_report({}) is None
    clean = g.sanitize_report({**GOOD, "evil": 1, "cpu_temp_c": None, "ip": "10.0.0.2"})
    assert "evil" not in clean and "cpu_temp_c" not in clean and clean["ip"] == "10.0.0.2" and clean["spi"] is True
    from app.mqtt_worker import parse_gw_health
    ok = parse_gw_health(f"as923/gateway/{GW.upper()}/sos/health", json.dumps(GOOD).encode(), "as923")
    assert ok and ok[0] == GW
    for t in (f"as923/gateway/{GW}/event/up", f"x/gateway/{GW}/sos/health", "as923/gateway/zz/sos/health",
              f"as923/gateway/{GW}/sos/health/extra"):
        assert parse_gw_health(t, b"{}", "as923") is None
    assert parse_gw_health(f"as923/gateway/{GW}/sos/health", b"not json", "as923") is None
    assert parse_gw_health(f"as923/gateway/{GW}/sos/health", b"[1]", "as923") is None
    assert parse_gw_health(f"as923/gateway/{GW}/sos/health", b"{" + b" " * 5000 + b"}", "as923") is None


def test_gateway_offline_alert_repeat_and_recovery(env_):
    eng, downs, notif, client, ids = env_
    t0 = NOON_TUE
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 30}, "t")
        assert eng.gw.on_report(db, GW, GOOD, t0) == "ok"
        eng.gw.tick(db, t0 + timedelta(minutes=1))
        assert _gw(db).state == "online" and notif.system == []
        # silent 4 min < 5 min threshold: still fine
        eng.gw.tick(db, t0 + timedelta(minutes=4)); assert notif.system == []
        # silent > 5 min: one alert only, even if tick runs many times
        for m in (6, 6.1, 7, 20):
            eng.gw.tick(db, t0 + timedelta(minutes=m))
        assert len(notif.system) == 1 and "ขาดการเชื่อมต่อ" in notif.system[0][1] and GW in notif.system[0][1]
        assert _gw(db).state == "offline"
        # reminder after repeat_min (30), not before
        eng.gw.tick(db, t0 + timedelta(minutes=35)); assert len(notif.system) == 1
        eng.gw.tick(db, t0 + timedelta(minutes=37)); assert len(notif.system) == 2 and "เตือนซ้ำ" in notif.system[1][1]
        # it comes back: one recovery message, no more alerts
        eng.gw.on_report(db, GW, GOOD, t0 + timedelta(minutes=40))
        eng.gw.tick(db, t0 + timedelta(minutes=41))
        assert len(notif.system) == 3 and "กลับมาออนไลน์" in notif.system[2][1] and _gw(db).state == "online"
        eng.gw.tick(db, t0 + timedelta(minutes=42)); assert len(notif.system) == 3
        from app.models import AuditLog
        acts = [a.action for a in db.query(AuditLog).order_by(AuditLog.id)]
        assert acts == ["gateway_offline", "gateway_offline_repeat", "gateway_online"]


def test_gateway_device_uplink_counts_as_alive_and_never_seen_is_silent(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        from app.models import Gateway
        db.add(Gateway(gateway_id=GW, enabled=True)); db.commit()       # registered, never connected
        eng.gw.tick(db, NOON_TUE + timedelta(hours=5)); assert notif.system == [] and _gw(db).state == "unknown"
        eng.handle_uplink(db, EUI, up(codec.T_HEARTBEAT), {"gateway_id": GW, "rssi": -80, "snr": 5})
        g = _gw(db); assert g.uplinks_total == 1 and g.last_seen is not None and g.last_report_at is None
        eng.gw.tick(db, NOON_TUE + timedelta(minutes=3)); assert _gw(db).state == "online"


def test_gateway_radio_dead_counts_as_offline_until_a_frame_arrives(env_):
    eng, downs, notif, client, ids = env_
    t0 = NOON_TUE
    dead = {**GOOD, "services": {"concentratord": False, "forwarder": True}}
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 0}, "t")
        eng.gw.on_report(db, GW, GOOD, t0); eng.gw.tick(db, t0)
        for m in (1, 2, 3):                                   # keeps reporting, but radio is dead
            eng.gw.on_report(db, GW, dead, t0 + timedelta(minutes=m))
        eng.gw.tick(db, t0 + timedelta(minutes=3)); assert notif.system == []     # < 3 min since it first reported dead (t0+1)
        eng.gw.on_report(db, GW, dead, t0 + timedelta(minutes=4)); eng.gw.tick(db, t0 + timedelta(minutes=4.5))
        assert len(notif.system) == 1 and "ตัวรับสัญญาณ LoRa" in notif.system[0][1]
        assert _gw(db).offline_reason == "radio"
        eng.gw.on_report(db, GW, GOOD, t0 + timedelta(minutes=6)); eng.gw.tick(db, t0 + timedelta(minutes=6.5))
        assert len(notif.system) == 2 and "กลับมาออนไลน์" in notif.system[1][1]


def test_gateway_no_judging_while_our_mqtt_is_down_or_just_reconnected(env_):
    eng, downs, notif, client, ids = env_
    t0 = NOON_TUE
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 0}, "t")
        eng.gw.on_report(db, GW, GOOD, t0)
        eng.gw.tick(db, t0 + timedelta(minutes=30), mqtt_ok=False); assert notif.system == []     # our link is down
        eng.gw.mqtt_connected(t0 + timedelta(minutes=30))
        eng.gw.tick(db, t0 + timedelta(minutes=31)); assert notif.system == []                    # grace after reconnect
        eng.gw.tick(db, t0 + timedelta(minutes=33)); assert len(notif.system) == 1                # grace over, still silent


def test_gateway_recipients_fallback_disabled_and_engine_tick(env_):
    eng, downs, notif, client, ids = env_
    t0 = NOON_TUE
    with SessionLocal() as db:
        # nobody configured -> falls back to the final caregiver of the escalation setting
        settings_store.put(db, "escalation", {**settings_store.get(db, "escalation"), "final_caregiver_id": ids["c"]}, "t")
        eng.gw.on_report(db, GW, GOOD, t0)
        eng.tick(db, t0 + timedelta(minutes=10))                     # Engine.tick drives the gateway state machine too
        assert [n for n, _ in notif.system] == ["ผอ.รพ.สต."]
        # retired gateway: no more alerts
        g = _gw(db); g.enabled = False; g.state = "unknown"; db.commit()
        n = len(notif.system); eng.tick(db, t0 + timedelta(hours=3)); assert len(notif.system) == n


def test_gateway_no_recipients_is_logged_not_crash(env_):
    eng, downs, notif, client, ids = env_
    with SessionLocal() as db:
        eng.gw.on_report(db, GW, GOOD, NOON_TUE); eng.gw.tick(db, NOON_TUE + timedelta(minutes=10))
        from app.models import AuditLog
        a = db.query(AuditLog).filter_by(action="gateway_offline").one()
        assert a.detail["no_recipients"] is True and notif.system == []


def test_gateway_api(env_):
    eng, downs, notif, client, ids = env_
    h = login(client); hv = login(client, "viewer")
    with SessionLocal() as db:
        eng.gw.on_report(db, GW, {**GOOD, "throttled": "0x50005"}, utcnow_())
    j = client.get("/api/gateways", headers=h).json()
    assert len(j) == 1 and j[0]["status"] == "warning" and j[0]["report"]["ip"] == "192.168.1.50"
    assert any("ไฟเลี้ยงต่ำ" in w for w in j[0]["warnings"])
    gid = j[0]["id"]
    # add: admin only, validated, unique
    assert client.post("/api/gateways", json={"gateway_id": "0016c001f1500002", "label": "x"}, headers=hv).status_code == 403
    assert client.post("/api/gateways", json={"gateway_id": "nothex"}, headers=h).status_code == 422
    assert client.post("/api/gateways", json={"gateway_id": "0016C001F1500002", "label": "ม.3"}, headers=h).status_code == 200
    assert client.post("/api/gateways", json={"gateway_id": "0016c001f1500002"}, headers=h).status_code == 409
    new = [g for g in client.get("/api/gateways", headers=h).json() if g["gateway_id"].endswith("02")][0]
    assert new["status"] == "unknown" and new["label"] == "ม.3"
    # label: staff/admin; viewer cannot; enabled: admin only
    assert client.put(f"/api/gateways/{gid}", json={"label": "รพ.สต."}, headers=hv).status_code == 403
    assert client.put(f"/api/gateways/{gid}", json={"label": "รพ.สต."}, headers=h).status_code == 200
    assert client.put(f"/api/gateways/{gid}", json={"enabled": False}, headers=h).status_code == 200
    g = [x for x in client.get("/api/gateways", headers=h).json() if x["id"] == gid][0]
    assert g["label"] == "รพ.สต." and g["status"] == "disabled" and g["enabled"] is False
    assert client.put("/api/gateways/999", json={"label": "z"}, headers=h).status_code == 404


def utcnow_():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def test_system_alert_settings_and_test_button(env_):
    eng, downs, notif, client, ids = env_
    h = login(client); hv = login(client, "viewer")
    assert client.post("/api/system-alert/test", headers=h).status_code == 422          # nobody configured
    P = lambda k, v, hh=h: client.put(f"/api/settings/{k}", json=v, headers=hh)
    assert P("system_alert", {"caregiver_ids": [9999], "repeat_min": 60}).status_code == 422
    assert P("system_alert", {"caregiver_ids": "x", "repeat_min": 60}).status_code == 422
    assert P("system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 5}).status_code == 422
    assert P("system_alert", {"caregiver_ids": [ids["a"], ids["a"]], "repeat_min": 0}).json()["caregiver_ids"] == [ids["a"]]
    assert P("system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 60}, hv).status_code == 403
    P("system_alert", {"caregiver_ids": [ids["a"], ids["c"]], "repeat_min": 60})
    r = client.post("/api/system-alert/test", headers=h).json()
    assert [x["caregiver"] for x in r["results"]] == ["อสม.หนึ่ง", "ผอ.รพ.สต."] and all(x["ok"] for x in r["results"])
    assert len(notif.system) == 2
    # offline thresholds validated
    assert P("offline", {"device_offline_min": 90, "gateway_offline_min": 1}).status_code == 422
    assert P("offline", {"device_offline_min": 90, "gateway_offline_min": "5"}).status_code == 422
    assert P("offline", {"device_offline_min": 3, "gateway_offline_min": 5}).status_code == 422
    assert P("offline", {"device_offline_min": 90, "gateway_offline_min": 7}).json()["gateway_offline_min"] == 7


def test_ensure_columns_backfills_defaults_for_existing_rows():
    from sqlalchemy import text
    from app.db import engine as e, ensure_columns
    with e.begin() as cx:
        cx.execute(text("DROP TABLE IF EXISTS sos_gateways"))
        cx.execute(text("CREATE TABLE sos_gateways (id INTEGER PRIMARY KEY, gateway_id VARCHAR(16), label VARCHAR(64), last_seen DATETIME)"))
        cx.execute(text("INSERT INTO sos_gateways (gateway_id, label) VALUES ('aaaaaaaaaaaaaaaa', 'old')"))
    ensure_columns()
    with e.begin() as cx:
        row = cx.execute(text("SELECT enabled, state, uplinks_total FROM sos_gateways")).one()
    assert tuple(row) == (1, "unknown", 0)


# ---------- passwords & user management ----------
def _login_raw(client, user, pw):
    return client.post("/api/login", json={"username": user, "password": pw})


def _hdr(r): return {"authorization": "Bearer " + r.json()["token"]}


def test_password_policy_and_forced_change(env_):
    eng, downs, notif, client, ids = env_
    h = login(client)
    assert client.post("/api/users", json={"username": "bob", "password": "bob1234567"}, headers=h).status_code == 422   # contains username
    assert client.post("/api/users", json={"username": "bob", "password": "aaaaaaaaaaaa"}, headers=h).status_code == 422
    assert client.post("/api/users", json={"username": "bob", "password": "password12345"}, headers=h).status_code == 422
    assert client.post("/api/users", json={"username": "b o b", "password": "Zx9-long-pass"}, headers=h).status_code == 422
    assert client.post("/api/users", json={"username": "bob", "password": "Zx9-long-pass"}, headers=h).status_code == 200
    r = _login_raw(client, "bob", "Zx9-long-pass"); assert r.status_code == 200 and r.json()["must_change"] is True
    hb = _hdr(r)
    assert client.get("/api/cases", headers=hb).status_code == 403                  # blocked until the password is changed
    assert client.get("/api/me", headers=hb).json()["must_change"] is True
    bad = lambda cur, new: client.post("/api/me/password", json={"current_password": cur, "new_password": new}, headers=hb)
    assert bad("wrong-current", "Another-Pass-77").status_code == 403
    assert bad("Zx9-long-pass", "short").status_code == 422
    assert bad("Zx9-long-pass", "Zx9-long-pass").status_code == 422                 # same as current
    assert bad("Zx9-long-pass", "bobbob-Pass-1").status_code == 422                 # contains username
    ok = bad("Zx9-long-pass", "Another-Pass-77"); assert ok.status_code == 200
    assert client.get("/api/cases", headers=hb).status_code == 401                  # old token revoked
    hb2 = {"authorization": "Bearer " + ok.json()["token"]}
    assert client.get("/api/cases", headers=hb2).status_code == 200
    assert _login_raw(client, "bob", "Zx9-long-pass").status_code == 401
    assert _login_raw(client, "bob", "Another-Pass-77").json()["must_change"] is False
    from app.models import AuditLog
    with SessionLocal() as db:
        txt = " ".join(str(a.detail) + a.action for a in db.query(AuditLog))
        assert "Zx9-long-pass" not in txt and "Another-Pass-77" not in txt        # passwords never reach the audit log


def test_admin_reset_unlocks_and_revokes(env_):
    eng, downs, notif, client, ids = env_
    h = login(client); hv = login(client, "viewer")
    users = {x["username"]: x for x in client.get("/api/users", headers=h).json()}
    vid, aid = users["viewer"]["id"], users["admin"]["id"]
    assert client.post(f"/api/users/{vid}/reset-password", headers=hv).status_code == 403
    assert client.post(f"/api/users/{aid}/reset-password", headers=h).status_code == 422          # not for yourself
    assert client.post("/api/users/999/reset-password", headers=h).status_code == 404
    for _ in range(5): _login_raw(client, "viewer", "wrong-password")
    assert _login_raw(client, "viewer", "password12345").status_code == 429                       # locked
    assert [x for x in client.get("/api/users", headers=h).json() if x["username"] == "viewer"][0]["locked"] is True
    r = client.post(f"/api/users/{vid}/reset-password", headers=h); assert r.status_code == 200
    tmp = r.json()["temp_password"]; assert len(tmp) == 14
    assert hv and client.get("/api/me", headers=hv).status_code == 401                            # old session killed
    lr = _login_raw(client, "viewer", tmp); assert lr.status_code == 200 and lr.json()["must_change"] is True
    assert _login_raw(client, "viewer", "password12345").status_code == 401
    from app.models import AuditLog
    with SessionLocal() as db:
        assert tmp not in " ".join(str(a.detail) for a in db.query(AuditLog))
    for _ in range(5): _login_raw(client, "viewer", "wrong-password")
    assert client.post(f"/api/users/{vid}/unlock", headers=h).json()["locked"] is False


def test_user_patch_guards(env_):
    eng, downs, notif, client, ids = env_
    h = login(client)
    users = {x["username"]: x for x in client.get("/api/users", headers=h).json()}
    aid, vid = users["admin"]["id"], users["viewer"]["id"]
    assert client.put(f"/api/users/{aid}", json={"role": "staff"}, headers=h).status_code == 409   # last admin / self
    assert client.put(f"/api/users/{aid}", json={"active": False}, headers=h).status_code == 409
    assert client.put(f"/api/users/{vid}", json={"role": "boss"}, headers=h).status_code == 422
    hv = login(client, "viewer")
    assert client.put(f"/api/users/{vid}", json={"role": "staff"}, headers=h).json()["role"] == "staff"
    assert client.get("/api/me", headers=hv).status_code == 401                                    # role change revokes the old token
    hv = login(client, "viewer"); assert client.get("/api/me", headers=hv).json()["role"] == "staff"
    assert client.put(f"/api/users/{vid}", json={"role": "admin"}, headers=h).status_code == 200
    h2 = login(client, "viewer")                                                                   # the second admin
    assert client.put(f"/api/users/{aid}", json={"role": "staff"}, headers=h2).status_code == 200  # another admin may demote the first
    assert client.put(f"/api/users/{vid}", json={"active": False}, headers=h2).status_code == 409  # but not itself / the last admin
    assert client.put(f"/api/users/{aid}", json={"active": False}, headers=h2).status_code == 200
    assert _login_raw(client, "admin", "password12345").status_code == 401                         # disabled user cannot log in
    assert client.get("/api/me", headers=h).status_code == 401                                     # and its old token is dead


def test_cli_reset(env_, capsys):
    from app import cli
    assert cli.main(["reset-password", "viewer"]) == 0
    out = capsys.readouterr().out; tmp = out.split(": ")[1].split("\n")[0]
    r = _login_raw(__import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(__import__("app.main", fromlist=["create_app"]).create_app(False)), "viewer", tmp)
    assert r.status_code == 200 and r.json()["must_change"] is True
    assert cli.main(["reset-password", "nobody"]) == 1 and cli.main(["list-users"]) == 0 and cli.main([]) == 2


# ---------- PDPA ----------
def _pid(ids): return ids["p"]


def test_consent_gate_for_activating_devices(env_):
    eng, downs, notif, client, ids = env_
    h = login(client); p = _pid(ids)
    r = client.post("/api/patients", json={"full_name": "นายสมชาย", "village": "ม.5", "phone": "0811112222"}, headers=h).json()
    new = r["id"]; assert r["consent_ok"] is False
    dev = client.post("/api/devices", json={"dev_eui": "70b3d57ed0000009", "label": "B1"}, headers=h).json()["id"]
    put = lambda lc: client.put(f"/api/devices/{dev}", json={"dev_eui": "70b3d57ed0000009", "label": "B1", "lifecycle": lc, "patient_id": new}, headers=h)
    assert put("active").status_code == 409 and "ความยินยอม" in put("active").json()["detail"]
    assert put("ready").status_code == 200                                    # binding without activating is fine
    assert client.put(f"/api/patients/{new}/consent", json={"given": True, "note": "แบบ ก-012"}, headers=login(client, "viewer")).status_code == 403
    c = client.put(f"/api/patients/{new}/consent", json={"given": True, "note": "แบบ ก-012"}, headers=h).json()["patient"]
    assert c["consent_ok"] is True and c["consent_by"] == "admin" and c["consent_note"] == "แบบ ก-012"
    assert put("active").status_code == 200
    # the requirement can be switched off by the admin (e.g. the hospital collects consent on paper elsewhere)
    s = client.get("/api/settings", headers=h).json()["privacy"]
    assert client.put("/api/settings/privacy", json={**s, "require_consent": False}, headers=h).status_code == 200
    assert client.put(f"/api/patients/{new}/consent", json={"given": False}, headers=h).status_code == 200
    # withdrawal frees the device, deactivates the patient and removes the notification chain
    pt = [x for x in client.get("/api/patients", headers=h).json() if x["id"] == new][0]
    assert pt["active"] is False and pt["consent_withdrawn_at"] and pt["device"] is None
    d = [x for x in client.get("/api/devices", headers=h).json() if x["id"] == dev][0]
    assert d["lifecycle"] == "ready" and d["patient_id"] is None
    assert client.put(f"/api/patients/{new}", json={"full_name": "นายสมชาย", "active": True}, headers=h).status_code == 409


def test_export_and_anonymize(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    h = login(client); hv = login(client, "viewer"); p = _pid(ids)
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        cid = db.query(Case).one().id
    client.post(f"/api/cases/{cid}/accept", headers=h)
    client.post(f"/api/cases/{cid}/close", json={"reason": "resolved", "note": "ผู้ป่วยหกล้ม ปวดสะโพก โทร 0800000000"}, headers=h)
    assert client.get(f"/api/patients/{p}/export", headers=hv).status_code == 403
    ex = client.get(f"/api/patients/{p}/export", headers=h)
    assert ex.status_code == 200 and "attachment" in ex.headers["content-disposition"]
    j = ex.json()
    assert j["patient"]["full_name"] == "นางสมศรี ใจดี" and j["devices"][0]["dev_eui"] == EUI
    assert j["cases"][0]["note"].startswith("ผู้ป่วยหกล้ม") and [e["kind"] for e in j["cases"][0]["events"]][0] == "created"
    assert [a["name"] for a in j["caregivers_assigned"]] == ["อสม.หนึ่ง", "อสม.สอง"]
    # anonymise: guarded
    assert client.post(f"/api/patients/{p}/anonymize", json={"confirm_id": 999}, headers=h).status_code == 422
    assert client.post(f"/api/patients/{p}/anonymize", json={"confirm_id": p}, headers=hv).status_code == 403
    r = client.post(f"/api/patients/{p}/anonymize", json={"confirm_id": p, "keep_village": False}, headers=h)
    assert r.status_code == 200 and r.json()["cases_scrubbed"] == 1 and r.json()["devices_freed"] == 1
    assert client.post(f"/api/patients/{p}/anonymize", json={"confirm_id": p}, headers=h).status_code == 409
    pt = [x for x in client.get("/api/patients", headers=h).json() if x["id"] == p][0]
    assert pt["full_name"].startswith("(ลบข้อมูลแล้ว") and pt["phone"] == "" and pt["address"] == "" and pt["anonymized_at"]
    assert pt["lat"] is None and pt["assignments"] == [] and pt["device"] is None and pt["village"] == ""
    ex2 = client.get(f"/api/patients/{p}/export", headers=h).json()
    blob = json.dumps(ex2, ensure_ascii=False)
    assert "นางสมศรี" not in blob and "หกล้ม" not in blob and "0800000000" not in blob
    assert len(ex2["cases"]) == 1 and ex2["cases"][0]["state"] == "closed"           # the case itself (statistics) remains
    assert client.put(f"/api/patients/{p}", json={"full_name": "x"}, headers=h).status_code == 409
    assert client.put(f"/api/patients/{p}/consent", json={"given": True}, headers=h).status_code == 409
    from app.models import AuditLog
    with SessionLocal() as db:
        assert {"patient_export", "patient_anonymize"} <= {a.action for a in db.query(AuditLog)}
    # an open case blocks anonymisation
    with SessionLocal() as db:
        from app.models import Patient
        p2 = Patient(full_name="คนใหม่", active=True); db.add(p2); db.flush()
        dv = Device(dev_eui="70b3d57ed0000077", lifecycle="active", patient_id=p2.id); db.add(dv); db.commit()
        eng.handle_uplink(db, "70b3d57ed0000077", up(codec.T_ALERT, alert=9)); p2id = p2.id
    assert client.post(f"/api/patients/{p2id}/anonymize", json={"confirm_id": p2id}, headers=h).status_code == 409


def test_viewer_sees_masked_patient_data(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    hv = login(client, "viewer"); h = login(client)
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
    pv = client.get("/api/patients", headers=hv).json()[0]
    assert pv["full_name"] == "นางสมศรี ใจดี" and pv["phone"] == "xxx-xxx-0000" and pv["address"] == "" and pv["lat"] is None
    cv = client.get("/api/cases", headers=hv).json()[0]["patient"]
    assert cv["phone"].startswith("xxx") and cv["address"] == ""
    assert client.get("/api/cases/1", headers=hv).json()["patient"]["address"] == ""
    assert client.get("/api/patients", headers=h).json()[0]["phone"] == "0800000000"            # staff/admin see everything


def test_retention_purge(env_):
    eng, downs, notif, client, ids = env_
    h = login(client); now = utcnow_()
    with SessionLocal() as db:
        from app.models import Notification, CaseEvent, AuditLog
        dv = db.query(Device).one()
        old = Case(device_id=dv.id, patient_id=ids["p"], alert_id=1, state="closed", created_at=now - timedelta(days=800))
        mid = Case(device_id=dv.id, patient_id=ids["p"], alert_id=2, state="closed", created_at=now - timedelta(days=100))
        openold = Case(device_id=dv.id, patient_id=ids["p"], alert_id=3, state="new", created_at=now - timedelta(days=900))
        oldtest = Case(device_id=dv.id, patient_id=ids["p"], alert_id=4, state="closed", is_test=True, created_at=now - timedelta(days=40))
        db.add_all([old, mid, openold, oldtest]); db.flush()
        db.add_all([CaseEvent(case_id=old.id, kind="created"), Notification(case_id=old.id, caregiver_id=ids["a"])])
        db.add_all([AuditLog(actor="x", action="login", at=now - timedelta(days=400)),
                    AuditLog(actor="x", action="login", at=now - timedelta(days=10)),
                    AuditLog(actor="x", action="patient_anonymize", at=now - timedelta(days=900))])
        db.commit()
    assert client.post("/api/privacy/retention", json={"dry_run": True}, headers=login(client, "viewer")).status_code == 403
    dry = client.post("/api/privacy/retention", json={"dry_run": True}, headers=h).json()
    assert dry["cases"] == 2 and dry["case_events"] == 1 and dry["notifications"] == 1 and dry["audit_rows"] == 1 and dry["dry_run"] is True
    with SessionLocal() as db: assert db.query(Case).count() == 4                       # dry run deletes nothing
    real = client.post("/api/privacy/retention", json={"dry_run": False}, headers=h).json()
    assert real["cases"] == 2
    with SessionLocal() as db:
        assert sorted(c.alert_id for c in db.query(Case)) == [2, 3]                      # recent + the OPEN old case survive
        acts = [a.action for a in db.query(AuditLog)]
        assert "patient_anonymize" in acts and acts.count("login") >= 1 and "retention_run" in acts
        assert db.query(CaseEvent).filter_by(case_id=1).count() == 0 and db.query(Notification).count() == 0
    # validation
    s = client.get("/api/settings", headers=h).json()["privacy"]
    for bad in ({**s, "case_retention_days": 5}, {**s, "audit_retention_days": 10}, {**s, "line_detail": "all"}, {**s, "auto_purge": "yes"}):
        assert client.put("/api/settings/privacy", json=bad, headers=h).status_code == 422, bad
    ov = client.get("/api/privacy/overview", headers=h).json()
    assert ov["active"] == 1 and ov["without_consent"][0]["has_active_device"] is True and ov["retention_preview"]["dry_run"] is True


def test_line_minimal_detail():
    from app.line import flex_for_case
    p = type("P", (), dict(full_name="นางสมศรี", address="12 ม.3", village="ม.3", phone="0800000000", lat=13.9, lon=100.6))()
    c = type("C", (), dict(id=7, patient=p))()
    full = json.dumps(flex_for_case(c), ensure_ascii=False); mini = json.dumps(flex_for_case(c, detail="minimal"), ensure_ascii=False)
    assert "12 ม.3" in full and "tel:0800000000" in full and "maps.google.com" in full
    assert "นางสมศรี" in mini and "ม.3" in mini and "12 ม.3" not in mini and "0800000000" not in mini and "maps.google" not in mini and "accept:7" in mini


# ---------- reports ----------
def test_report_range_parsing():
    from app import reports
    s, e, d1, d2 = reports.parse_range("2026-12", None, None)
    assert (d1.isoformat(), d2.isoformat()) == ("2026-12-01", "2026-12-31") and (e - s).days == 31
    assert s == datetime(2026, 11, 30, 17, 0, tzinfo=timezone.utc)                    # midnight Bangkok = 17:00 UTC the day before
    s, e, *_ = reports.parse_range(None, "2026-10-05", "2026-10-05"); assert (e - s) == timedelta(days=1)
    import pytest
    for args in (("2026-13", None, None), ("26-10", None, None), (None, "2026-10-05", "2026-10-01"), (None, "2026-01-01", "2027-12-31"),
                 (None, "x", "y"), (None, None, None), (None, "2026-10-01", None)):
        with pytest.raises(reports.RangeError): reports.parse_range(*args)
    assert reports.be_date(datetime(2026, 9, 30, 17, 30, tzinfo=timezone.utc)) == "01/10/2569"      # Bangkok date, Buddhist Era


def test_monthly_report_and_csv(env_):
    eng, downs, notif, client, ids = env_
    h = login(client); hv = login(client, "viewer")
    from app.models import AuditLog, Gateway, Patient, Notification
    T = lambda d, hh, mm=0: datetime(2026, 10, d, hh, mm, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.get(Patient, ids["p"]).village = "ม.3"
        p2 = Patient(full_name="=HYPERLINK(\"http://x\")", village="ม.5", active=True); db.add(p2); db.flush()
        dv = db.query(Device).one(); mk = lambda **k: Case(device_id=dv.id, alert_id=k.pop("alert_id"), **k)
        db.add_all([
            mk(alert_id=1, patient_id=ids["p"], state="closed", close_reason="resolved", created_at=T(5, 3), accepted_at=T(5, 3, 1), accepted_by="อสม.หนึ่ง", closed_at=T(5, 3, 31)),
            mk(alert_id=2, patient_id=ids["p"], state="closed", close_reason="false_alarm", created_at=T(6, 15), accepted_at=T(6, 15, 5), after_hours=True, esc_step=1, overdue=True, closed_at=T(6, 15, 20)),
            mk(alert_id=3, patient_id=p2.id, state="new", created_at=T(20, 5), note="+cmd|' /C calc'!A0"),
            mk(alert_id=4, patient_id=ids["p"], state="closed", close_reason="resolved", created_at=datetime(2026, 9, 30, 17, 30, tzinfo=timezone.utc), accepted_at=datetime(2026, 9, 30, 17, 31, tzinfo=timezone.utc)),   # 01/10 00:30 Bangkok
            mk(alert_id=5, patient_id=ids["p"], state="closed", close_reason="resolved", created_at=datetime(2026, 9, 30, 16, 30, tzinfo=timezone.utc)),    # 30/09 23:30 Bangkok -> September
            mk(alert_id=6, patient_id=ids["p"], state="closed", is_test=True, close_reason="test", created_at=T(7, 4)),
        ])
        db.flush(); db.add(Notification(case_id=1, caregiver_id=ids["a"], status="sent")); db.add(Notification(case_id=2, caregiver_id=ids["a"], status="failed"))
        db.add(Gateway(gateway_id=GW, label="รพ.สต.", enabled=True))
        db.add_all([AuditLog(actor="system", action="gateway_offline", target=GW, at=T(10, 1)),
                    AuditLog(actor="system", action="gateway_offline_repeat", target=GW, at=T(10, 2)),
                    AuditLog(actor="system", action="gateway_online", target=GW, at=T(10, 1, 30)),
                    AuditLog(actor="system", action="gateway_offline", target=GW, at=datetime(2026, 9, 30, 23, 0, tzinfo=timezone.utc)),
                    AuditLog(actor="system", action="gateway_online", target=GW, at=datetime(2026, 10, 1, 1, 0, tzinfo=timezone.utc))])
        db.commit()
    r = client.get("/api/reports/summary?month=2026-10", headers=h); assert r.status_code == 200
    j = r.json()
    assert j["period"]["from_be"] == "01/10/2569" and j["period"]["to_be"] == "31/10/2569"
    # October (Bangkok) holds cases 1-4; alert 5 (30/09 23:30 Bangkok) belongs to September; alert 6 is a device self-test
    c = j["cases"]
    assert (c["total"], c["resolved"], c["false_alarm"], c["still_open"], c["after_hours"], c["overdue"], c["escalated"], c["cancelled_by_device"], c["tests"]) == (4, 2, 1, 1, 1, 1, 1, 0, 1)
    r_ = j["response"]                                       # accept times 60 s, 300 s, 60 s against a 120 s target
    assert (r_["accepted"], r_["accept_avg_s"], r_["accept_median_s"], r_["accept_p90_s"], r_["within_target_pct"], r_["target_s"]) == (3, 140.0, 60.0, 300.0, 66.7, 120)
    assert r_["close_avg_min"] == 25.5                       # closed after 31 and 20 minutes
    assert {v["village"]: v["cases"] for v in j["by_village"]} == {"ม.3": 3, "ม.5": 1}
    assert j["line"] == {"sent": 1, "failed": 1}
    assert [i for i, n in enumerate(j["by_hour"]) if n] == [0, 10, 12, 22]                   # Bangkok local hours of cases 4, 1, 3, 2
    assert sum(w["cases"] for w in j["by_weekday"]) == 4
    g = j["gateways"]
    assert len(g) == 1 and g[0]["outages"] == 2 and g[0]["label"] == "รพ.สต." and g[0]["ongoing"] is False
    assert g[0]["downtime_min"] == 150.0                     # 23:00Z-01:00Z (2 h, crosses the month start) + 30 min on the 10th
    assert [(t["cases"]) for t in j["top_patients"]] == [3, 1] and j["top_patients"][0]["name"] == "นางสมศรี ใจดี"
    # viewer: aggregates only, no names
    jv = client.get("/api/reports/summary?month=2026-10", headers=hv).json(); assert jv["top_patients"] == [] and jv["cases"]["total"] == 4
    assert client.get("/api/reports/summary?month=bad", headers=h).status_code == 422
    assert client.get("/api/reports/summary", headers=h).status_code == 422
    # CSV
    assert client.get("/api/reports/cases.csv?month=2026-10", headers=hv).status_code == 403
    r = client.get("/api/reports/cases.csv?month=2026-10", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"\xef\xbb\xbf") and "sos-cases-2569-10.csv" in r.headers["content-disposition"]
    import csv as _csv, io as _io
    rows = list(_csv.reader(_io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][0] == "เลขเคส" and len(rows) == 5 and rows[1][1] == "05/10/2569"
    flat = [c for row in rows[1:] for c in row]
    assert "'=HYPERLINK(\"http://x\")" in flat and "'+cmd|' /C calc'!A0" in flat and not any(c.startswith(("=", "+")) for c in flat)
    with SessionLocal() as db: assert db.query(AuditLog).filter_by(action="report_export").one().detail["rows"] == 4


# ---------- GPS / location ----------
LOC_VECTOR = "180593c906c788d582"        # encode_loc(5, 13.9115, 100.5954, sats=8, hdop=1.2): the same vector is checked in firmware/test


def test_loc_codec_vectors_and_rejections():
    import pytest
    b = codec.encode_loc(5, 13.9115, 100.5954, 8, 1.2)
    assert b.hex() == LOC_VECTOR and len(b) == 9
    l = codec.decode_loc(b); assert (l.alert_id, l.sats, l.hdop) == (5, 8, 1.0) and abs(l.lat - 13.9115) < 2e-5 and abs(l.lon - 100.5954) < 3e-5
    for lat, lon in ((-33.8688, 151.2093), (89.99, -179.99), (-89.99, 179.99), (13.75, 100.5)):      # extremes survive the round trip
        d = codec.decode_loc(codec.encode_loc(1, lat, lon, 4, 2.0)); assert abs(d.lat - lat) < 1.2e-5 and abs(d.lon - lon) < 2.2e-5
    assert codec.decode_loc(codec.encode_loc(1, 1.0, 1.0, 99, 99)).sats == 15 and codec.decode_loc(codec.encode_loc(1, 1.0, 1.0, 5, 99)).hdop == 7.5
    for bad in (b"", b[:8], b + b"\0", bytes([0x17]) + b[1:], bytes([0x28]) + b[1:],        # length / type / protocol version
                codec.encode_loc(1, 13.9, 100.5, 0, 1.0),                                      # 0 satellites = no fix
                codec.encode_loc(1, 0.0, 0.0, 6, 1.0)):                                        # null island
        with pytest.raises(codec.CodecError): codec.decode_loc(bad)


def _loc_case(eng, monkeypatch, state_new=True):
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=5)); return db.query(Case).one().id


def test_location_attached_to_case_and_pushed_once(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    h = login(client); hv = login(client, "viewer")
    with SessionLocal() as db:                                                         # patient home ~ 1.1 km from the fix below
        from app.models import Patient
        p = db.get(Patient, ids["p"]); p.lat, p.lon = 13.9015, 100.5954; db.commit()
    cid = _loc_case(eng, monkeypatch)
    with SessionLocal() as db:
        assert eng.handle_location(db, EUI, bytes.fromhex(LOC_VECTOR), {"gateway_id": GW, "rssi": -90, "snr": 5}) == "ok"
        c = db.get(Case, cid); d = db.query(Device).one()
        assert abs(c.loc_lat - 13.9115) < 2e-5 and c.loc_sats == 8 and abs(d.loc_lon - 100.5954) < 3e-5 and d.last_gateway == GW
        ev = [e for e in c.events if e.kind == "location"][0]
        assert ev.detail["sats"] == 8 and 1100 < ev.detail["away_from_home_m"] < 1300 and "lat" not in ev.detail   # no coordinates in the event log
    assert notif.loc == [(cid, "อสม.หนึ่ง", notif.loc[0][2], notif.loc[0][3], 8, notif.loc[0][5])] and 1100 < notif.loc[0][5] < 1300
    # the same fix again (retransmission) does not page people a second time
    with SessionLocal() as db: assert eng.handle_location(db, EUI, bytes.fromhex(LOC_VECTOR), {}) == "ok"
    assert len(notif.loc) == 1
    # a clearly different position does
    with SessionLocal() as db: eng.handle_location(db, EUI, codec.encode_loc(5, 13.9215, 100.5954, 7, 1.0), {})
    assert len(notif.loc) == 2
    # API: staff see the position + a map link; viewers do not
    c = client.get(f"/api/cases/{cid}", headers=h).json()
    assert c["loc"]["sats"] == 7 and c["loc"]["url"].startswith("https://maps.google.com/?q=13.92") and c["loc"]["home_distance_m"] > 2000
    assert client.get(f"/api/cases/{cid}", headers=hv).json()["loc"] is None
    d = client.get("/api/devices", headers=h).json()[0]["loc"]
    assert d["fresh"] is False                         # the fix is dated "NOON_TUE", days before the real clock the API uses
    assert d["away"] is True and client.get("/api/devices", headers=hv).json()[0]["loc"] is None


def test_location_edge_cases(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    patch_now(monkeypatch, NOON_TUE)
    with SessionLocal() as db:
        assert eng.handle_location(db, "70b3d57e00000000", bytes.fromhex(LOC_VECTOR), {}) == "unknown_device"
        assert eng.handle_location(db, EUI, b"\x18\x05", {}) == "bad_payload"
        assert eng.handle_location(db, EUI, bytes.fromhex(LOC_VECTOR), {}) == "stored_no_case"        # fix with no matching alert: kept on the device only
        assert db.query(Device).one().loc_lat is not None and notif.loc == []
    cid = _loc_case(eng, monkeypatch)
    with SessionLocal() as db:
        eng.accept(db, cid, "อสม.หนึ่ง"); eng.close(db, cid, "x")
        eng.handle_location(db, EUI, bytes.fromhex(LOC_VECTOR), {})              # case closed already: stored, nobody is paged
        assert db.get(Case, cid).loc_lat is not None
    assert notif.loc == []
    # the notifier may decline (minimal privacy mode): recorded as "skipped", not as a failure
    notif.loc_skip = True
    with SessionLocal() as db:
        db.query(Case).delete(); db.commit()
        eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=5)); eng.handle_location(db, EUI, bytes.fromhex(LOC_VECTOR), {})
        from app.models import Notification
        assert [n.status for n in db.query(Notification).filter_by(channel="line_loc")] == ["skipped"]


def test_line_location_message_respects_privacy_mode():
    from app.line import LineNotifier
    from app.models import Caregiver, Patient
    calls = []
    class H:
        def post(self, url, json=None, headers=None):
            calls.append(json); return type("R", (), {"status_code": 200, "text": ""})()
    n = LineNotifier(token="t", client=H())
    cg = Caregiver(full_name="อสม.", line_user_id="U1"); case = type("C", (), {"id": 3, "patient": Patient(full_name="นางสมศรี")})()
    with SessionLocal() as db:
        ok, _ = n.notify_location(db, case, cg, 13.9115, 100.5954, 8, 1200)
        txt = calls[0]["messages"][0]["text"]
        assert ok and "https://maps.google.com/?q=13.911500,100.595400" in txt and "นางสมศรี" in txt and "8 ดวง" in txt and "1200" in txt
        settings_store.put(db, "privacy", {**settings_store.get(db, "privacy"), "line_detail": "minimal"}, "t")
        calls.clear()
        ok, detail = n.notify_location(db, case, cg, 13.9115, 100.5954, 8, None)
        assert not ok and detail.startswith("skipped") and calls == []                 # nothing leaves the server
        ok, detail = n.notify_location(db, case, Caregiver(full_name="x"), 1.0, 1.0, 5, None); assert not ok and "line_user_id" in detail


def test_fport4_routed_to_location_handler(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    from app.mqtt_worker import MqttWorker
    w = MqttWorker(lambda dl: eng)
    cid = _loc_case(eng, monkeypatch)
    ev = {"deviceInfo": {"devEui": EUI}, "fPort": 4, "data": codec.b64(bytes.fromhex(LOC_VECTOR)),
          "rxInfo": [{"gatewayId": GW, "rssi": -80, "snr": 6}]}
    w._on_message(None, None, type("M", (), {"topic": "application/x/device/" + EUI + "/event/up", "payload": json.dumps(ev).encode()})())
    with SessionLocal() as db: assert db.get(Case, cid).loc_sats == 8
    assert len(notif.loc) == 1


def test_gateway_gps_and_moved_warning(env_):
    eng, downs, notif, client, ids = env_
    h = login(client); hv = login(client, "viewer"); now = utcnow_()
    fix = lambda lat, lon, sats=7: {**GOOD, "gps": {"fix": True, "lat": lat, "lon": lon, "sats": sats, "alt": 12.3}}
    with SessionLocal() as db:
        eng.gw.on_report(db, GW, fix(13.9100, 100.5900, sats=3), now)                     # 3 satellites: not trusted
        assert _gw(db).gps_lat is None
        eng.gw.on_report(db, GW, {**GOOD, "gps": {"fix": True, "lat": 0, "lon": 0, "sats": 9}}, now)    # null island: dropped
        assert _gw(db).gps_lat is None and "gps" not in _gw(db).report
        eng.gw.on_report(db, GW, {**GOOD, "gps": {"fix": False}}, now); assert _gw(db).report["gps"] == {"fix": False}
        eng.gw.on_report(db, GW, fix(13.9100, 100.5900), now)
        assert abs(_gw(db).gps_lat - 13.91) < 1e-6 and _gw(db).gps_sats == 7
    g = client.get("/api/gateways", headers=h).json()[0]; gid = g["id"]
    assert g["loc"]["gps"]["fresh"] is True and g["loc"]["registered"] is None and g["loc"]["gps_attached"] is True and g["status"] == "online"
    P = lambda body, hh=h: client.put(f"/api/gateways/{gid}", json=body, headers=hh)
    assert P({"lat": 13.91}).status_code == 422 and P({"lat": 0, "lon": 0}).status_code == 422 and P({"lat": 200, "lon": 1}).status_code == 422
    assert P({"lat": 13.91, "lon": 100.59}, hv).status_code == 403
    assert P({"use_gps": True}).status_code == 200                                        # install day: adopt the GPS position
    g = client.get("/api/gateways", headers=h).json()[0]
    assert g["loc"]["registered"] == {"lat": 13.91, "lon": 100.59} and g["loc"]["moved_m"] is None and g["status"] == "online"
    # the gateway is carried ~1.1 km away
    with SessionLocal() as db: eng.gw.on_report(db, GW, fix(13.9200, 100.5900), utcnow_())
    g = client.get("/api/gateways", headers=h).json()[0]
    assert g["status"] == "warning" and 1100 < g["loc"]["moved_m"] < 1120 and any("ห่างจากที่ลงทะเบียน" in w for w in g["warnings"])
    # raise the tolerance above the distance -> no warning
    s = client.get("/api/settings", headers=h).json()["geo"]
    assert client.put("/api/settings/geo", json={**s, "gateway_move_m": 2000}, headers=h).status_code == 200
    assert client.get("/api/gateways", headers=h).json()[0]["status"] == "online"
    assert client.put("/api/settings/geo", json={**s, "gateway_move_m": 5}, headers=h).status_code == 422
    # a stale GPS fix is not judged
    with SessionLocal() as db:
        gg = _gw(db); gg.gps_at = utcnow_() - timedelta(hours=2); db.commit()
    client.put("/api/settings/geo", json={**s, "gateway_move_m": 200}, headers=h)
    assert client.get("/api/gateways", headers=h).json()[0]["loc"]["moved_m"] is None
    assert P({"clear_location": True}).status_code == 200 and client.get("/api/gateways", headers=h).json()[0]["loc"]["registered"] is None
    assert client.put(f"/api/gateways/{gid}", json={"use_gps": True}, headers=h).status_code == 200


def test_use_gps_without_any_fix_is_refused(env_):
    eng, downs, notif, client, ids = env_
    h = login(client)
    with SessionLocal() as db: eng.gw.on_report(db, GW, GOOD, utcnow_())
    gid = client.get("/api/gateways", headers=h).json()[0]["id"]
    assert client.put(f"/api/gateways/{gid}", json={"use_gps": True}, headers=h).status_code == 409


def test_anonymize_and_unbind_clear_locations(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    h = login(client)
    cid = _loc_case(eng, monkeypatch)
    with SessionLocal() as db: eng.handle_location(db, EUI, bytes.fromhex(LOC_VECTOR), {}); eng.accept(db, cid, "x"); eng.close(db, cid, "x")
    assert client.post(f"/api/patients/{ids['p']}/anonymize", json={"confirm_id": ids["p"]}, headers=h).status_code == 200
    with SessionLocal() as db:
        assert db.get(Case, cid).loc_lat is None and db.query(Device).one().loc_lat is None


def test_loc_vectors_match_firmware_file():
    """firmware/test/loc_vectors.inc is what the C++ host test checks; it must equal what the backend codec produces."""
    import re, pathlib
    from app import codec
    inc = pathlib.Path(__file__).resolve().parents[2] / "firmware" / "test" / "loc_vectors.inc"
    rows = re.findall(r'\{(\d+), ([-\d.]+), ([-\d.]+), (\d+), ([-\d.]+), "([0-9a-f]+)"\}', inc.read_text())
    assert len(rows) >= 7
    for a, la, lo, s, h, hx in rows:
        assert codec.encode_loc(int(a), float(la), float(lo), int(s), float(h)).hex() == hx
        d = codec.decode_loc(bytes.fromhex(hx))
        assert abs(d.lat - float(la)) < 3e-5 and abs(d.lon - float(lo)) < 3e-5


# ---------------- device watch: silent / low battery / mains lost ----------------
EUI1 = "70b3d57ed0000001"


def _dev(db):
    from app.models import Device
    return db.query(Device).filter_by(dev_eui=EUI1).one()


def test_device_silent_alert_repeat_recovery(env_):
    eng, downs, notif, client, ids = env_
    t0 = NOON_TUE
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 30}, "t")
        d = _dev(db); d.last_seen = t0; d.battery = 90; d.mains_ok = True; db.commit()
        eng.dev_watch.tick(db, t0 + timedelta(minutes=60)); assert notif.system == []              # < 90 min threshold
        for m in (95, 95.1, 100):
            eng.dev_watch.tick(db, t0 + timedelta(minutes=m))
        assert len(notif.system) == 1 and "เงียบหาย" in notif.system[0][1] and "A1" in notif.system[0][1]
        assert "นางสมศรี" in notif.system[0][1]                                                    # privacy.line_detail = full
        eng.dev_watch.tick(db, t0 + timedelta(minutes=120)); assert len(notif.system) == 1
        eng.dev_watch.tick(db, t0 + timedelta(minutes=127)); assert len(notif.system) == 2 and "เตือนซ้ำ" in notif.system[1][1]
        d = _dev(db); d.last_seen = t0 + timedelta(minutes=130); db.commit()
        eng.dev_watch.tick(db, t0 + timedelta(minutes=131))
        assert len(notif.system) == 3 and "กลับมาส่งสัญญาณ" in notif.system[2][1]
        eng.dev_watch.tick(db, t0 + timedelta(minutes=132)); assert len(notif.system) == 3
        from app.models import AuditLog
        assert [a.action for a in db.query(AuditLog).order_by(AuditLog.id)] == ["device_offline", "device_offline_repeat", "device_offline_ok"]


def test_device_watch_minimal_privacy_hides_patient_name(env_):
    eng, downs, notif, client, ids = env_
    t0 = NOON_TUE
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 0}, "t")
        settings_store.put(db, "privacy", {**settings_store.get(db, "privacy"), "line_detail": "minimal"}, "t")
        d = _dev(db); d.last_seen = t0; db.commit()
        eng.dev_watch.tick(db, t0 + timedelta(minutes=100))
        assert len(notif.system) == 1 and "A1" in notif.system[0][1] and "นางสมศรี" not in notif.system[0][1]


def test_device_battery_hysteresis_and_mains_grace(env_):
    eng, downs, notif, client, ids = env_
    t0 = NOON_TUE
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 0}, "t")
        d = _dev(db); d.last_seen = t0; d.battery = 15; d.mains_ok = False; db.commit()
        eng.dev_watch.tick(db, t0)                                                                   # battery alert at once, mains waits 10 min
        assert len(notif.system) == 1 and "แบตเตอรี่ต่ำ" in notif.system[0][1]
        eng.dev_watch.tick(db, t0 + timedelta(minutes=5)); assert len(notif.system) == 1
        eng.dev_watch.tick(db, t0 + timedelta(minutes=11))
        assert len(notif.system) == 2 and "ไฟบ้านดับ" in notif.system[1][1] and "15%" in notif.system[1][1]
        d = _dev(db); d.battery = 25; d.last_seen = t0 + timedelta(minutes=12); db.commit()          # 25% is inside the hysteresis band: stay alerted
        eng.dev_watch.tick(db, t0 + timedelta(minutes=12)); assert len(notif.system) == 2
        d = _dev(db); d.battery = 40; d.mains_ok = True; db.commit()
        eng.dev_watch.tick(db, t0 + timedelta(minutes=13))
        assert len(notif.system) == 4 and all(m[1].startswith("✅") for m in notif.system[2:])
        assert {"แบตเตอรี่กลับมาปกติ", "ไฟบ้านกลับมาแล้ว"} == {k for m in notif.system[2:] for k in ("แบตเตอรี่กลับมาปกติ", "ไฟบ้านกลับมาแล้ว") if k in m[1]}
        assert _dev(db).watch is None


def test_device_short_mains_blip_is_silent_and_never_seen_is_silent(env_):
    eng, downs, notif, client, ids = env_
    t0 = NOON_TUE
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 0}, "t")
        d = _dev(db); d.last_seen = None; d.mains_ok = False; db.commit()
        eng.dev_watch.tick(db, t0); eng.dev_watch.tick(db, t0 + timedelta(minutes=3))
        d = _dev(db); d.mains_ok = True; db.commit()
        eng.dev_watch.tick(db, t0 + timedelta(minutes=4)); eng.dev_watch.tick(db, t0 + timedelta(minutes=500))
        assert notif.system == []                                                                    # blip < grace; never-seen device not judged


def test_device_watch_suppressed_when_mqtt_down_no_gateway_up_or_disabled(env_):
    eng, downs, notif, client, ids = env_
    t0 = NOON_TUE
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 0}, "t")
        d = _dev(db); d.last_seen = t0; db.commit()
        eng.dev_watch.tick(db, t0 + timedelta(minutes=200), mqtt_ok=False); assert notif.system == []
        # a registered gateway that has been silent for hours: the gateway alert explains it, devices are not blamed
        eng.gw.on_report(db, GW, GOOD, t0)
        eng.dev_watch.tick(db, t0 + timedelta(minutes=200)); assert notif.system == []
        eng.gw.on_report(db, GW, GOOD, t0 + timedelta(minutes=199))                                  # gateway is back, device still silent
        eng.dev_watch.tick(db, t0 + timedelta(minutes=200)); assert len(notif.system) == 1
        settings_store.put(db, "device_alert", {"enabled": False, "battery_low_pct": 20, "mains_grace_min": 10}, "t")
        d = _dev(db); d.battery = 3; db.commit()
        eng.dev_watch.tick(db, t0 + timedelta(minutes=201)); assert len(notif.system) == 1


def test_device_watch_ignores_inactive_devices_and_api_validation(env_):
    eng, downs, notif, client, ids = env_
    t0 = NOON_TUE
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 0}, "t")
        d = _dev(db); d.last_seen = t0; d.lifecycle = "repair"; db.commit()
        eng.dev_watch.tick(db, t0 + timedelta(minutes=500)); assert notif.system == []
    tok = client.post("/api/login", json={"username": "admin", "password": "password12345"}).json()["token"]
    H = {"authorization": "Bearer " + tok}
    P = lambda v: client.put("/api/settings/device_alert", json=v, headers=H)
    assert P({"enabled": True, "battery_low_pct": 20, "mains_grace_min": 10}).status_code == 200
    for bad in ({"enabled": "yes", "battery_low_pct": 20, "mains_grace_min": 10}, {"enabled": True, "battery_low_pct": 2, "mains_grace_min": 10},
                {"enabled": True, "battery_low_pct": 20, "mains_grace_min": 0}, {"enabled": True, "battery_low_pct": True, "mains_grace_min": 10}, {}):
        assert P(bad).status_code == 422, bad


# ---------- system update (dashboard → shared folder → host updater) ----------
def test_system_update_request_flow(env_, tmp_path, monkeypatch):
    import json
    from app.config import env as cfg
    from app.models import AuditLog
    *_, client, ids = env_
    monkeypatch.setattr(cfg, "update_dir", str(tmp_path))
    H, V = login(client), login(client, "viewer")
    assert client.get("/api/system/update", headers=V).status_code == 403
    assert client.post("/api/system/update", json={"action": "update"}, headers=V).status_code == 403
    # host updater not set up yet → clear 503, nothing written
    s = client.get("/api/system/update", headers=H).json()
    assert s["installed"] is False and s["busy"] is False
    assert client.post("/api/system/update", json={"action": "update"}, headers=H).status_code == 503
    assert not (tmp_path / "request.json").exists()
    # host has written its first status → requests are accepted
    (tmp_path / "status.json").write_text(json.dumps({"state": "idle", "behind": 2,
        "current": {"commit": "a" * 40}, "latest": {"commit": "b" * 40}}))
    (tmp_path / "update.log").write_text("line1\nline2\n")
    assert client.post("/api/system/update", json={"action": "rm -rf"}, headers=H).status_code == 422
    r = client.post("/api/system/update", json={"action": "update"}, headers=H)
    assert r.status_code == 200 and r.json()["action"] == "update"
    req = json.loads((tmp_path / "request.json").read_text())
    assert req["by"] == "admin" and req["action"] == "update"
    s = client.get("/api/system/update", headers=H).json()
    assert s["installed"] and s["busy"] and s["pending"]["id"] == req["id"] and s["log"] == ["line1", "line2"]
    # second request while one is pending/running → 409
    assert client.post("/api/system/update", json={"action": "check"}, headers=H).status_code == 409
    (tmp_path / "request.json").unlink()
    (tmp_path / "status.json").write_text(json.dumps({"state": "updating"}))
    assert client.post("/api/system/update", json={"action": "update"}, headers=H).status_code == 409
    (tmp_path / "status.json").write_text(json.dumps({"state": "failed"}))
    assert client.post("/api/system/update", json={"action": "check"}, headers=H).status_code == 200
    with SessionLocal() as db:
        a = db.query(AuditLog).filter_by(action="system_update_requested").all()
        assert len(a) == 1 and a[0].actor == "admin"


def test_map_endpoint_positions_links_and_viewer_masking(env_):
    eng, downs, notif, client, ids = env_
    h = login(client); hv = login(client, "viewer")
    from app.models import Gateway, Patient, utcnow
    with SessionLocal() as db:
        db.add(Gateway(gateway_id=GW, label="GW-บ้านผู้ใหญ่", lat=13.9000, lon=100.5900, last_seen=utcnow(), uplinks_total=1))
        p = db.get(Patient, ids["p"]); p.lat, p.lon = 13.9100, 100.5900
        d = db.query(Device).one(); d.last_gateway, d.last_rssi, d.last_snr, d.last_seen = GW, -92, 6.0, utcnow(); db.commit()
    m = client.get("/api/map", headers=h).json()
    assert m["restricted"] is False and len(m["gateways"]) == 1 and m["gateways"][0]["lat"] == 13.9
    dv = m["devices"][0]
    assert dv["pos_src"] == "home" and dv["lat"] == 13.91 and dv["quality"] == "good" and dv["gateway_id"] == GW
    lk = m["links"][0]
    assert lk["gateway_id"] == GW and lk["quality"] == "good" and 1000 < lk["distance_m"] < 1200
    # a fresh GPS fix wins over the registered home
    with SessionLocal() as db:
        d = db.query(Device).one(); d.loc_lat, d.loc_lon, d.loc_at = 13.9200, 100.6000, utcnow(); db.commit()
    assert client.get("/api/map", headers=h).json()["devices"][0]["pos_src"] == "gps"
    # weak link grading
    with SessionLocal() as db:
        d = db.query(Device).one(); d.last_rssi, d.last_snr = -118, -10.0; db.commit()
    assert client.get("/api/map", headers=h).json()["links"][0]["quality"] == "weak"
    # read-only role: connectivity yes, any coordinates of people/devices no
    mv = client.get("/api/map", headers=hv).json()
    assert mv["restricted"] is True and mv["devices"][0]["lat"] is None and mv["devices"][0]["lon"] is None and mv["devices"][0]["pos_src"] is None
    assert mv["links"][0]["gateway_id"] == GW and mv["links"][0]["distance_m"] is None
    assert mv["gateways"][0]["lat"] == 13.9                                                     # gateways are infrastructure, not personal data
    assert client.get("/api/map").status_code in (401, 403)


# ---------------- gateway settings + remote actions ----------------
def _gw_row(db, **kw):
    from app.models import Gateway, utcnow
    g = Gateway(gateway_id=GW, label="GW", last_seen=utcnow(), last_report_at=utcnow(), uplinks_total=1, enabled=True, state="online", **kw)
    db.add(g); db.commit(); return g.id


def test_gateway_settings_and_roles(env_):
    eng, downs, notif, client, ids = env_
    from app.models import User
    from app.auth import hash_pw
    with SessionLocal() as db:
        db.add(User(username="staff", password_hash=hash_pw("password12345"), role="staff")); db.commit(); gid = _gw_row(db)
    h, hs, hv = login(client), login(client, "staff"), login(client, "viewer")
    assert client.put(f"/api/gateways/{gid}", json={"notes": "  ติดหน้าต่างชั้น 2  "}, headers=hs).status_code == 200      # staff: label/notes/location
    assert client.put(f"/api/gateways/{gid}", json={"offline_min": 20}, headers=hs).status_code == 403                  # admin only
    assert client.put(f"/api/gateways/{gid}", json={"alert_ids": [ids["a"]]}, headers=hs).status_code == 403
    assert client.put(f"/api/gateways/{gid}", json={"notes": "x"}, headers=hv).status_code == 403
    for bad in (2, 241, 0):
        assert client.put(f"/api/gateways/{gid}", json={"offline_min": bad}, headers=h).status_code == 422
    assert client.put(f"/api/gateways/{gid}", json={"alert_ids": [999]}, headers=h).status_code == 422
    assert client.put(f"/api/gateways/{gid}", json={"offline_min": 20, "alert_ids": [ids["b"], ids["b"], ids["a"]]}, headers=h).status_code == 200
    g = client.get("/api/gateways", headers=h).json()[0]
    assert g["notes"] == "ติดหน้าต่างชั้น 2" and g["offline_min"] == 20 and g["offline_min_effective"] == 20 and g["alert_ids"] == sorted([ids["a"], ids["b"]])
    assert client.put(f"/api/gateways/{gid}", json={"reset_offline": True, "reset_alerts": True}, headers=h).status_code == 200
    g = client.get("/api/gateways", headers=h).json()[0]
    assert g["offline_min"] is None and g["alert_ids"] is None and g["offline_min_effective"] == 5


def test_gateway_own_offline_threshold_and_recipients(env_):
    eng, downs, notif, client, ids = env_
    from datetime import timedelta
    from app.models import Gateway, utcnow
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 0}, "t")      # global: อสม.หนึ่ง
        gid = _gw_row(db, offline_min=30, alert_ids=[ids["b"]])
        g = db.get(Gateway, gid); g.last_seen = g.last_report_at = utcnow() - timedelta(minutes=12); db.commit()
        eng.gw.tick(db, utcnow(), True)                                      # 12 min < own 30 min (global would be 5) -> still fine
        assert notif.system == []
        g.last_seen = g.last_report_at = utcnow() - timedelta(minutes=40); db.commit()
        eng.gw.tick(db, utcnow(), True)
        assert [n[0] for n in notif.system] == ["อสม.สอง"]                   # the gateway's OWN recipient, not the global one
        g.offline_min, g.alert_ids, g.state = None, None, "online"; g.last_seen = g.last_report_at = utcnow() - timedelta(minutes=40); db.commit()
        notif.system.clear(); eng.gw.tick(db, utcnow(), True)
        assert [n[0] for n in notif.system] == ["อสม.หนึ่ง"]                 # reset -> global


def test_gateway_command_signing_and_lifecycle(env_):
    eng, downs, notif, client, ids = env_
    from app import gwcmd
    from app.models import Gateway, GatewayCommand, User
    from app.auth import hash_pw
    sent = []; eng.gw_cmd_publish = lambda gid_, p: (sent.append((gid_, p)), True)[1]
    with SessionLocal() as db:
        db.add(User(username="staff", password_hash=hash_pw("password12345"), role="staff")); db.commit()
        gid = _gw_row(db)
    h, hs, hv = login(client), login(client, "staff"), login(client, "viewer")
    # no agent reported yet -> refused (nothing is published)
    assert client.post(f"/api/gateways/{gid}/commands", json={"action": "reboot"}, headers=h).status_code == 409 and sent == []
    with SessionLocal() as db:
        g = db.get(Gateway, gid); g.report = {"agent": True, "sw": "sos-gw-report 3"}; db.commit()
    for hh in (hs, hv):                                                                       # admin only (a reboot drops every patient's link)
        assert client.post(f"/api/gateways/{gid}/commands", json={"action": "logs"}, headers=hh).status_code == 403
        assert client.get(f"/api/gateways/{gid}/commands", headers=hh).status_code == 403
        assert client.get(f"/api/gateways/{gid}/agent-key", headers=hh).status_code == 403
    assert client.post(f"/api/gateways/{gid}/commands", json={"action": "rm -rf /"}, headers=h).status_code == 422
    r = client.post(f"/api/gateways/{gid}/commands", json={"action": "restart_services"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "pending"
    (g_, p), = sent
    assert g_ == GW and p["action"] == "restart_services" and set(p) == {"id", "action", "ts", "sig"}
    key = client.get(f"/api/gateways/{gid}/agent-key", headers=h).json()["key"]
    assert key == gwcmd.agent_key(GW) and p["sig"] == gwcmd.sign(key, p["id"], "restart_services", p["ts"])
    assert gwcmd.agent_key("0016c001f1500002") != key                                         # keys are per gateway
    assert client.post(f"/api/gateways/{gid}/commands", json={"action": "logs"}, headers=h).status_code == 409   # one at a time
    # answers: wrong gateway / unknown id are ignored; the right one completes it
    with SessionLocal() as db:
        assert eng.handle_gateway_result(db, "0016c001f1500002", {"id": p["id"], "ok": True, "detail": "", "output": ""}) == "ignored"
        assert eng.handle_gateway_result(db, GW, {"id": "0" * 16, "ok": True, "detail": "", "output": ""}) == "ignored"
        assert eng.handle_gateway_result(db, GW, {"id": p["id"], "ok": True, "detail": "restarted", "output": ""}) == "ok"
        assert eng.handle_gateway_result(db, GW, {"id": p["id"], "ok": False, "detail": "again", "output": ""}) == "ignored"   # only once
    c = client.get(f"/api/gateways/{gid}/commands", headers=h).json()["commands"][0]
    assert c["status"] == "ok" and c["detail"] == "restarted" and c["by"] == "admin"
    # no answer for 3 minutes -> expired, and a new command is allowed
    assert client.post(f"/api/gateways/{gid}/commands", json={"action": "logs"}, headers=h).status_code == 200
    from datetime import timedelta
    from app.models import utcnow
    with SessionLocal() as db:
        for c in db.query(GatewayCommand).filter_by(status="pending"): c.created_at = utcnow() - timedelta(seconds=gwcmd.EXPIRE_S + 5)
        db.commit()
    assert client.get(f"/api/gateways/{gid}/commands", headers=h).json()["commands"][0]["status"] == "expired"
    assert client.post(f"/api/gateways/{gid}/commands", json={"action": "report_now"}, headers=h).status_code == 200
    # MQTT down -> 503, nothing stored as pending
    eng.mqtt_ok = lambda: False
    with SessionLocal() as db:
        for c in db.query(GatewayCommand).filter_by(status="pending"): c.status = "ok"
        db.commit()
    assert client.post(f"/api/gateways/{gid}/commands", json={"action": "logs"}, headers=h).status_code == 503
    with SessionLocal() as db:
        from app.models import AuditLog
        acts = [a.action for a in db.query(AuditLog).all()]
        assert acts.count("gateway_command") == 3 and "gateway_agent_key_view" in acts


def test_gateway_result_parsing_and_redaction():
    from app import gwcmd
    t = f"as923/gateway/{GW}/sos/cmd-result"
    ok = gwcmd.parse_result(t, json.dumps({"id": "ab" * 8, "ok": True, "detail": "done", "output": "mqtt password=hunter2 user=x\nline2"}).encode(), "as923")
    assert ok and ok[0] == GW and "hunter2" not in ok[1]["output"] and "password=***" in ok[1]["output"]
    for bad in (f"as923/gateway/{GW}/sos/cmd", f"x/gateway/{GW}/sos/cmd-result", "as923/gateway/zz/sos/cmd-result", t + "/x"):
        assert gwcmd.parse_result(bad, b"{}", "as923") is None
    assert gwcmd.parse_result(t, b"not json", "as923") is None
    assert gwcmd.parse_result(t, json.dumps({"id": "nothex", "ok": True}).encode(), "as923") is None
    assert gwcmd.parse_result(t, json.dumps({"id": "ab" * 8, "ok": "yes"}).encode(), "as923") is None
    assert len(gwcmd.parse_result(t, json.dumps({"id": "ab" * 8, "ok": True, "output": "x" * 7000}).encode(), "as923")[1]["output"]) == 4000
    assert gwcmd.parse_result(t, b" " * 9000, "as923") is None


def test_gateway_agent_dryrun_accepts_only_valid_signed_commands(tmp_path):
    import hashlib, hmac, json, os, shutil, subprocess, time
    if not (shutil.which("jq") and shutil.which("openssl")): pytest.skip("jq/openssl missing")
    agent = os.path.join(os.path.dirname(__file__), "..", "..", "gateway", "pi", "bin", "sos-gateway-agent")
    key = "k" * 40
    (tmp_path / "env").write_text(f"GATEWAY_ID=0016c001f1234567\nTOPIC_PREFIX=as923\nAGENT_KEY={key}\n")
    now = int(time.time())

    def mk(i, action, ts=now, k=key):
        sig = hmac.new(k.encode(), f"{i}|{action}|{ts}".encode(), hashlib.sha256).hexdigest()
        return json.dumps({"id": i, "action": action, "ts": ts, "sig": sig})
    lines = [mk("a" * 16, "report_now"), mk("a" * 16, "report_now"), mk("b" * 16, "logs", now - 900),
             mk("c" * 16, "reboot", k="x" * 40), mk("d" * 16, "rm_rf"), mk("e" * 16, "restart_services")]
    env = dict(os.environ, SOS_AGENT_CONF=str(tmp_path / "env"), SOS_AGENT_STATE=str(tmp_path / "st"), SOS_AGENT_DRYRUN="1")
    out = subprocess.run(["bash", agent, "--stdin"], input="\n".join(lines) + "\n", env=env, capture_output=True, text=True, timeout=30)
    results = [json.loads(l[7:]) for l in out.stdout.splitlines() if l.startswith("RESULT ")]
    assert [r["id"] for r in results] == ["a" * 16, "e" * 16]          # replay, stale, bad sig and unknown action all ignored
    assert "DRYRUN systemctl reboot" not in out.stdout
    assert "BAD SIGNATURE" in out.stderr and "replayed" in out.stderr and "stale" in out.stderr and "unknown action" in out.stderr


# ---------- ChirpStack management from the dashboard ----------
class FakeCSMgmt(FakeCS):
    def __init__(self):
        super().__init__()
        self.cs_devices = {"70b3d57ed0000001": {"devEui": "70B3D57ED0000001", "name": "A1", "isDisabled": False},
                           "02aaaaaaaaaaaaaa": {"devEui": "02aaaaaaaaaaaaaa", "name": "stray", "isDisabled": False}}
        self.cs_gws = {}; self.boom = False
    def remove_device(self, eui):
        if self.boom: raise RuntimeError("down")
        return self.cs_devices.pop(eui, None) is not None
    def remove_gateway(self, gid):
        if self.boom: raise RuntimeError("down")
        return self.cs_gws.pop(gid, None) is not None
    def status(self): return {"configured": True, "reachable": True, "error": "", "application": "sos-patients"}
    def list_devices(self): return list(self.cs_devices.values())
    def list_gateways(self): return [{"gatewayId": g} for g in self.cs_gws]
    def set_disabled(self, eui, disabled):
        if self.boom: raise RuntimeError("down")
        d = self.cs_devices[eui]; ch = d["isDisabled"] != disabled; d["isDisabled"] = disabled; return ch
    def ensure_gateway(self, gid, name, lat=None, lon=None):
        if gid in self.cs_gws: return "exists"
        self.cs_gws[gid] = name; return "created"


def test_chirpstack_page_compare_and_sync(env_, monkeypatch):
    *_, client, ids = env_
    cs = FakeCSMgmt(); monkeypatch.setattr("app.provision.get_cs", lambda: cs)
    h = login(client)
    assert client.get("/api/chirpstack", headers=login(client, "viewer")).status_code == 403
    with SessionLocal() as db:
        from app.models import Gateway
        db.add(Device(dev_eui="70b3d57ed0000002", lifecycle="ready", label="B2"))          # never registered in CS
        db.add(Gateway(gateway_id="0016c001ff10aaaa", label="รพ.สต. ม.3", enabled=True, state="unknown", uplinks_total=0))
        db.commit()
        db.query(Device).filter_by(dev_eui="70b3d57ed0000001").update({"lifecycle": "retired"}); db.commit()
    st = client.get("/api/chirpstack", headers=h).json()
    c = st["compare"]
    assert st["reachable"] and c["devices_cs"] == 2 and c["devices_sos"] == 2
    assert [x["dev_eui"] for x in c["missing_in_cs"]] == ["70b3d57ed0000002"]
    assert [x["dev_eui"] for x in c["unknown_in_sos"]] == ["02aaaaaaaaaaaaaa"]
    assert c["disabled_mismatch"] == ["70b3d57ed0000001"]
    assert [g["gateway_id"] for g in c["gateways_missing_in_cs"]] == ["0016c001ff10aaaa"]
    r = client.post("/api/chirpstack/sync", headers=h).json()
    assert r == {"gateways_added": ["0016c001ff10aaaa"], "devices_disabled": ["70b3d57ed0000001"], "devices_enabled": [], "errors": []}
    assert cs.cs_devices["70b3d57ed0000001"]["isDisabled"] is True
    c2 = client.get("/api/chirpstack", headers=h).json()["compare"]
    assert c2["disabled_mismatch"] == [] and c2["gateways_missing_in_cs"] == []
    # nothing is created/deleted for devices
    assert cs.reg == [] and cs.deleted == [] and "02aaaaaaaaaaaaaa" in cs.cs_devices
    cs.configured = False
    assert client.post("/api/chirpstack/sync", headers=h).status_code == 503


def test_retire_device_disables_in_chirpstack(env_, monkeypatch):
    *_, client, ids = env_
    cs = FakeCSMgmt(); monkeypatch.setattr("app.provision.get_cs", lambda: cs)
    h = login(client)
    with SessionLocal() as db: did = db.query(Device).filter_by(dev_eui="70b3d57ed0000001").one().id
    r = client.put(f"/api/devices/{did}", json={"dev_eui": "70b3d57ed0000001", "label": "A1", "lifecycle": "retired"}, headers=h)
    assert r.json()["chirpstack"] == "disabled" and cs.cs_devices["70b3d57ed0000001"]["isDisabled"] is True
    r = client.put(f"/api/devices/{did}", json={"dev_eui": "70b3d57ed0000001", "label": "A1", "lifecycle": "ready"}, headers=h)
    assert r.json()["chirpstack"] == "enabled" and cs.cs_devices["70b3d57ed0000001"]["isDisabled"] is False
    r = client.put(f"/api/devices/{did}", json={"dev_eui": "70b3d57ed0000001", "label": "A2", "lifecycle": "repair"}, headers=h)
    assert r.json()["chirpstack"] == "unchanged"
    cs.boom = True       # ChirpStack down: the dashboard change still saves, error is reported + audited
    r = client.put(f"/api/devices/{did}", json={"dev_eui": "70b3d57ed0000001", "label": "A1", "lifecycle": "retired"}, headers=h)
    assert r.status_code == 200 and r.json()["chirpstack"].startswith("error")
    assert any(a["action"] == "chirpstack_device_failed" for a in client.get("/api/audit", headers=h).json())


def test_add_gateway_registers_in_chirpstack(env_, monkeypatch):
    *_, client, ids = env_
    cs = FakeCSMgmt(); monkeypatch.setattr("app.provision.get_cs", lambda: cs)
    h = login(client)
    r = client.post("/api/gateways", json={"gateway_id": "00:16:C0:01:FF:10:BB:BB", "label": "GW-B"}, headers=h).json()
    assert r["chirpstack"] == "created" and cs.cs_gws == {"0016c001ff10bbbb": "GW-B"}


def test_chirpstack_rest_management_calls():
    import httpx, json as _j, pytest
    from app.provision import ChirpStackRest, ProvisionError
    calls = []
    dev = {"devEui": "02aaaaaaaaaaaaaa", "name": "x", "isDisabled": False}
    def handler(req):
        calls.append((req.method, req.url.path, dict(req.url.params), _j.loads(req.content) if req.content else None))
        assert req.headers["authorization"] == "Bearer k"
        p = req.url.path
        if p == "/api/applications/app": return httpx.Response(200, json={"application": {"name": "sos-patients", "tenantId": "t1"}})
        if p == "/api/device-profiles/prof": return httpx.Response(200, json={"deviceProfile": {"name": "SOS-button", "region": "AS923"}})
        if p == "/api/devices" and req.method == "GET":
            off = int(req.url.params["offset"])
            return httpx.Response(200, json={"totalCount": 150, "result": [{"devEui": f"{i:016x}"} for i in range(off, min(off + 100, 150))]})
        if p == "/api/devices/02aaaaaaaaaaaaaa" and req.method == "GET": return httpx.Response(200, json={"device": dict(dev)})
        if p == "/api/devices/02aaaaaaaaaaaaaa" and req.method == "PUT": return httpx.Response(200, json={})
        if p == "/api/gateways" and req.method == "POST":
            gid = _j.loads(req.content)["gateway"]["gatewayId"]
            return httpx.Response(409, json={"code": 6, "message": "Object already exists"}) if gid.endswith("ff") else httpx.Response(200, json={})
        return httpx.Response(404, json={"code": 5, "message": "Object does not exist"})
    c = ChirpStackRest("http://cs", "k", "app", "prof", client=httpx.Client(transport=httpx.MockTransport(handler)))
    st = c.status()
    assert st["reachable"] and st["application"] == "sos-patients" and st["device_profile"] == "SOS-button"
    assert len(c.list_devices()) == 150
    assert c.set_disabled("02aaaaaaaaaaaaaa", True) is True and calls[-1][3]["device"]["isDisabled"] is True
    assert c.set_disabled("02aaaaaaaaaaaaaa", False) is False                  # already enabled: no PUT
    assert calls[-1][0] == "GET"
    assert c.ensure_gateway("00000000000000aa", "GW", 13.9, 100.6) == "created"
    body = calls[-1][3]["gateway"]
    assert body["tenantId"] == "t1" and body["location"]["latitude"] == 13.9
    assert c.ensure_gateway("00000000000000ff", "GW") == "exists"
    with pytest.raises(ProvisionError) as e: c.set_disabled("0000000000000001", True)
    assert e.value.not_found
    bad = ChirpStackRest("http://cs", "k", "nope", "prof", client=httpx.Client(transport=httpx.MockTransport(handler))).status()
    assert bad["configured"] and not bad["reachable"] and "404" in bad["error"]
    assert ChirpStackRest("http://cs", "", "", "").status()["configured"] is False


def test_delete_rules_for_caregiver_patient_device_gateway_user(env_):
    eng, downs, notif, client, ids = env_
    from app.models import User
    from app.auth import hash_pw
    with SessionLocal() as db:
        db.add(User(username="staff", password_hash=hash_pw("password12345"), role="staff")); db.commit(); gid = _gw_row(db)
    h, hs = login(client), login(client, "staff")
    # caregiver: staff may not delete; a fresh one can be removed, also from gateway recipient lists
    cg = client.post("/api/caregivers", json={"full_name": "ลบได้", "role": "อสม.", "phone": ""}, headers=h).json()["id"]
    assert client.delete(f"/api/caregivers/{cg}", headers=hs).status_code == 403
    assert client.put(f"/api/gateways/{gid}", json={"alert_ids": [cg]}, headers=h).status_code == 200
    assert client.delete(f"/api/caregivers/{cg}", headers=h).status_code == 200
    assert [g for g in client.get("/api/gateways", headers=h).json() if g["id"] == gid][0]["alert_ids"] in (None, [])
    assert client.delete(f"/api/caregivers/{cg}", headers=h).status_code == 404
    # patient / device: unlinked and without history -> deletable; with device -> 409
    p = client.post("/api/patients", json={"full_name": "ทดลองลบ"}, headers=h).json()["id"]
    d = client.post("/api/devices", json={"dev_eui": "0016c001f1999999", "label": "x"}, headers=h).json()["id"]
    assert client.put(f"/api/devices/{d}", json={"dev_eui": "0016c001f1999999", "label": "x", "lifecycle": "ready", "patient_id": p}, headers=h).status_code == 200
    assert client.delete(f"/api/patients/{p}", headers=h).status_code == 409
    assert client.delete(f"/api/devices/{d}", headers=hs).status_code == 403
    assert client.delete(f"/api/devices/{d}", headers=h).status_code == 200
    assert client.delete(f"/api/patients/{p}", headers=h).status_code == 200
    # gateway
    assert client.delete(f"/api/gateways/{gid}", headers=hs).status_code == 403
    assert client.delete(f"/api/gateways/{gid}", headers=h).status_code == 200
    # user: must be disabled first, never yourself
    me = [u for u in client.get("/api/users", headers=h).json() if u["username"] == "admin"][0]["id"]
    st = [u for u in client.get("/api/users", headers=h).json() if u["username"] == "staff"][0]["id"]
    assert client.delete(f"/api/users/{me}", headers=h).status_code == 409
    assert client.delete(f"/api/users/{st}", headers=h).status_code == 409
    client.put(f"/api/users/{st}", json={"active": False}, headers=h)
    assert client.delete(f"/api/users/{st}", headers=h).status_code == 200


def test_delete_device_and_gateway_also_removes_from_chirpstack(env_, monkeypatch):
    *_, client, ids = env_
    cs = FakeCSMgmt(); monkeypatch.setattr("app.provision.get_cs", lambda: cs)
    h = login(client)
    d = client.post("/api/devices", json={"dev_eui": "70b3d57ed0000099", "label": "tmp"}, headers=h).json()["id"]
    cs.cs_devices["70b3d57ed0000099"] = {"devEui": "70b3d57ed0000099", "isDisabled": False}
    r = client.delete(f"/api/devices/{d}", headers=h).json()
    assert r["chirpstack"] == "deleted" and "70b3d57ed0000099" not in cs.cs_devices
    g = client.post("/api/gateways", json={"gateway_id": "0016c001ff10cccc", "label": "G"}, headers=h).json()["id"]
    assert client.delete(f"/api/gateways/{g}", headers=h).json()["chirpstack"] == "deleted" and not cs.cs_gws
    d2 = client.post("/api/devices", json={"dev_eui": "70b3d57ed0000098", "label": "t2"}, headers=h).json()["id"]
    cs.boom = True          # ChirpStack down: the local delete still succeeds and the problem is reported + audited
    r = client.delete(f"/api/devices/{d2}", headers=h)
    assert r.status_code == 200 and r.json()["chirpstack"].startswith("error")
    assert any(a["action"] == "chirpstack_device_delete_failed" for a in client.get("/api/audit", headers=h).json())


def test_line_credentials_from_dashboard(env_, monkeypatch):
    import base64, hashlib, hmac, json
    *_, client, ids = env_
    from app import linecfg
    from app.models import Secret
    h = login(client)
    try:
        s0 = client.get("/api/integrations/line", headers=h).json()
        assert s0["webhook_url"].endswith("/api/line/webhook")
        tok, sec = "T" * 40 + "wxyz", "S" * 30 + "abcd"
        assert client.put("/api/integrations/line", json={"token": "short"}, headers=h).status_code == 422
        r = client.put("/api/integrations/line", json={"token": tok, "secret": sec}, headers=h).json()
        assert r["token_set"] and r["token_hint"] == "…wxyz" and r["secret_hint"] == "…abcd" and r["ready"]
        assert tok not in json.dumps(r) and sec not in json.dumps(r)
        with SessionLocal() as db:                                   # encrypted at rest, not plaintext
            assert tok not in db.get(Secret, "line_channel_token").value
        assert not any(tok in json.dumps(a) or sec in json.dumps(a) for a in client.get("/api/audit", headers=h).json())
        assert linecfg.token() == tok and linecfg.secret() == sec
        # the webhook now verifies against the dashboard secret
        body = json.dumps({"events": []}).encode()
        sig = base64.b64encode(hmac.new(sec.encode(), body, hashlib.sha256).digest()).decode()
        assert client.post("/api/line/webhook", content=body, headers={"x-line-signature": sig}).status_code == 200
        assert client.post("/api/line/webhook", content=body, headers={"x-line-signature": "bad"}).status_code == 401
        # LINE token check (network mocked)
        class R:  # noqa
            def __init__(s, c, j=None): s.status_code, s._j = c, j or {}
            def json(s): return s._j
        monkeypatch.setattr("app.api.httpx.get", lambda *a, **k: R(200, {"displayName": "SOS Bot", "basicId": "@abc"}))
        t = client.post("/api/integrations/line/test", headers=h).json()
        assert t["ok"] and t["bot"] == "SOS Bot"
        monkeypatch.setattr("app.api.httpx.get", lambda *a, **k: R(401))
        assert client.post("/api/integrations/line/test", headers=h).json()["ok"] is False
        # roles: staff may not read/change it
        from app.models import User
        from app.auth import hash_pw
        with SessionLocal() as db: db.add(User(username="staff2", password_hash=hash_pw("password12345"), role="staff")); db.commit()
        hs = login(client, "staff2")
        assert client.get("/api/integrations/line", headers=hs).status_code == 403
        assert client.put("/api/integrations/line", json={"token": tok}, headers=hs).status_code == 403
        # removing the dashboard copy falls back to .env
        client.put("/api/integrations/line", json={"token": "", "secret": ""}, headers=h)
        assert linecfg.token() == linecfg.env.line_channel_token
    finally:
        with SessionLocal() as db:
            linecfg.save(db, "token", ""); linecfg.save(db, "secret", "")

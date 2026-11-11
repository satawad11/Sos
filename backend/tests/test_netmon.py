"""Radio/network monitoring: uplink history, link quality, ChirpStack events, ping tests, diagnosis, weak-signal alert."""
import json
from datetime import timedelta
from types import SimpleNamespace

from app import netmon, settings_store
from app.db import SessionLocal
from app.models import Device, NetEvent, PingTest, UplinkLog, AuditLog, utcnow
from tests.test_all import login

EUI = "70b3d57ed0000001"


def up(fcnt, snr=5.0, rssi=-90, sf=9, gw="0016c001ff10aaaa", fport=2, n_gw=1):
    rx = [{"gatewayId": gw, "rssi": rssi, "snr": snr}] + [{"gatewayId": "0016c001ff10bbbb", "rssi": rssi - 10, "snr": snr - 5}] * (n_gw - 1)
    return {"deviceInfo": {"devEui": EUI}, "fCnt": fcnt, "fPort": fport, "data": "", "rxInfo": rx,
            "txInfo": {"frequency": 923200000, "modulation": {"lora": {"spreadingFactor": sf, "bandwidth": 125000}}}}


class FakeCS:
    configured = True
    def __init__(self): self.q = []; self.known = True; self.n = 0
    def enqueue(self, eui, fport, data, confirmed=False):
        self.n += 1; self.q.append((eui, fport, data, confirmed)); return f"q-{self.n}"
    def device_known(self, eui): return self.known
    def flush_queue(self, eui): self.q = [x for x in self.q if x[0] != eui]


def test_margin_quality_loss():
    assert netmon.margin(-5, 9) == 7.5 and netmon.margin(None, 9) is None
    assert netmon.quality(2.0) == "bad" and netmon.quality(4) == "weak" and netmon.quality(8) == "fair" and netmon.quality(12) == "good"
    assert netmon.quality(12, loss=40) == "bad"
    assert netmon.loss_pct([1, 2, 3, 4, 5, 6]) == 0.0
    assert netmon.loss_pct([1, 3, 5, 7, 9, 11]) == 50.0              # every other frame missing
    assert netmon.loss_pct([10, 11, 12, 1, 2, 3, 4, 5]) == 0.0       # counter reset (re-join) is not loss
    assert netmon.loss_pct([1, 2]) is None                            # too little to judge
    assert netmon.parse_topic(f"application/abc/device/{EUI.upper()}/event/join") == (EUI, "join")
    assert netmon.parse_topic("as923/gateway/x/sos/health") is None


def test_uplink_history_and_sig(env_):
    eng, *_ = env_
    with SessionLocal() as db:
        for i in range(10):
            eng.netmon.on_uplink(db, EUI, up(i + 1, snr=-11.0 if i % 2 else -10.0, sf=9))   # margin ~1.5-2.5 dB = bad
        d = db.query(Device).filter_by(dev_eui=EUI).one()
        assert d.sig["n"] == 10 and d.sig["q"] == "bad" and d.sig["loss"] == 0.0 and d.sig["sf"] == 9
        st = eng.netmon.stats(db, EUI, 7)
        assert st["n"] == 10 and st["q"] == "bad" and st["gateways"][0]["gateway_id"] == "0016c001ff10aaaa"
        assert len(st["daily"]) >= 1 and len(st["points"]) == 10


def test_events_and_diagnosis(env_):
    eng, *_ = env_
    with SessionLocal() as db:
        eng.netmon.on_uplink(db, EUI, up(1))
        eng.netmon.on_event(db, "log", EUI, {"level": "ERROR", "code": "UPLINK_MIC",
                                             "description": "MIC of join-request is invalid, make sure keys are correct"})
        eng.netmon.on_event(db, "log", EUI, {"level": "ERROR", "code": "OTAA", "description": "DevNonce has already been used"})
        eng.netmon.on_event(db, "bogus", EUI, {})
        evs = db.query(NetEvent).filter_by(dev_eui=EUI).all()
        assert {e.code for e in evs} == {"UPLINK_MIC", "OTAA"} and all(e.level == "error" for e in evs)
        d = db.query(Device).filter_by(dev_eui=EUI).one()
        f = netmon.diagnose(db, d, True, 30)
        titles = [x["title"] for x in f]
        assert any("AppKey" in t for t in titles) and any("DevNonce" in t for t in titles)
        assert {x["fix"] for x in f} >= {"reprovision", "flush_nonces"}
        # a successful join afterwards clears both
        eng.netmon.on_event(db, "join", EUI, {"devAddr": "01020304"})
        f = netmon.diagnose(db, d, True, 30)
        assert not any("AppKey" in x["title"] or "DevNonce" in x["title"] for x in f)
        assert netmon.diagnose(db, d, False, 30)[0]["title"] == "ไม่มีปุ่มนี้ใน ChirpStack"
        for _ in range(3): eng.netmon.on_event(db, "join", EUI, {"devAddr": "01020304"})
        assert any("เข้าเครือข่ายใหม่บ่อย" in x["title"] for x in netmon.diagnose(db, d, True, 30))


def test_ping_flow(env_, monkeypatch):
    eng, downs, notifier, client, ids = env_
    cs = FakeCS(); monkeypatch.setattr("app.provision.get_cs", lambda: cs)
    h = login(client)
    with SessionLocal() as db: did = db.query(Device).filter_by(dev_eui=EUI).one().id
    assert client.post(f"/api/devices/{did}/ping", headers=login(client, "viewer")).status_code == 403
    r = client.post(f"/api/devices/{did}/ping", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "queued" and r.json()["eta_min"] == [30, 60]
    assert cs.q == [(EUI, netmon.PING_FPORT, netmon.PING_PAYLOAD, True)]
    assert client.post(f"/api/devices/{did}/ping", headers=h).status_code == 409      # one at a time
    with SessionLocal() as db:
        eng.netmon.on_event(db, "txack", EUI, {"queueItemId": "q-1", "gatewayId": "0016C001FF10AAAA", "fCntDown": 3})
        assert db.query(PingTest).one().status == "sent"
        eng.netmon.on_event(db, "ack", EUI, {"queueItemId": "q-1", "acknowledged": True})
        p = db.query(PingTest).one()
        assert p.status == "acked" and p.gateway_id == "0016c001ff10aaaa"
    net = client.get(f"/api/devices/{did}/network", headers=h).json()
    assert net["pings"][0]["status"] == "acked" and net["chirpstack"] is True
    kinds = [e["kind"] for e in net["events"]]
    assert "down" in kinds and "txack" in kinds and "ack" in kinds
    assert "events" not in client.get(f"/api/devices/{did}/network", headers=login(client, "viewer")).json()
    # second ping: never answered -> expires after 2 heartbeats + 15 min
    client.post(f"/api/devices/{did}/ping", headers=h)
    with SessionLocal() as db:
        assert eng.netmon.expire_pings(db, utcnow() + timedelta(minutes=80), 30) == 1
        assert db.query(PingTest).order_by(PingTest.id.desc()).first().status == "expired"
    # third: an emergency flushes the queue -> ping cancelled with a reason
    client.post(f"/api/devices/{did}/ping", headers=h)
    with SessionLocal() as db:
        assert eng.netmon.cancel_pings(db, EUI, "ยกเลิก: มีเหตุฉุกเฉิน") == 1
    assert any(a["action"] == "device_ping" for a in client.get("/api/audit", headers=h).json())


def test_no_ping_during_open_case(env_, monkeypatch):
    eng, downs, notifier, client, ids = env_
    cs = FakeCS(); monkeypatch.setattr("app.provision.get_cs", lambda: cs)
    from app import codec
    with SessionLocal() as db:
        from tests.test_all import up as alert_up
        eng.handle_uplink(db, EUI, alert_up(codec.T_ALERT, 7))
        did = db.query(Device).filter_by(dev_eui=EUI).one().id
    r = client.post(f"/api/devices/{did}/ping", headers=login(client))
    assert r.status_code == 409 and "เหตุฉุกเฉิน" in r.json()["detail"] and cs.q == []


def test_mqtt_routing(env_):
    eng, *_ = env_
    from app.mqtt_worker import MqttWorker
    w = MqttWorker.__new__(MqttWorker); w.engine = eng
    msg = lambda t, j: SimpleNamespace(topic=t, payload=json.dumps(j).encode())
    w._on_message(None, None, msg(f"application/a/device/{EUI}/event/up", up(5)))
    w._on_message(None, None, msg(f"application/a/device/{EUI}/event/join", {"devAddr": "aa"}))
    w._on_message(None, None, msg(f"application/a/device/{EUI}/event/log", "not-a-dict"))
    with SessionLocal() as db:
        assert db.query(UplinkLog).filter_by(dev_eui=EUI).count() == 1
        assert [e.kind for e in db.query(NetEvent).all()] == ["join"]


def test_weak_signal_alert(env_):
    eng, downs, notifier, client, ids = env_
    from app.models import Caregiver
    with SessionLocal() as db:
        c = db.query(Caregiver).first()
        settings_store.put(db, "system_alert", {**settings_store.get(db, "system_alert"), "caregiver_ids": [c.id]}, "t")
        d = db.query(Device).filter_by(dev_eui=EUI).one()
        d.last_seen = utcnow(); d.lifecycle = "active"
        d.sig = {"n": 12, "q": "bad", "margin": 1.5, "loss": 35.0}; db.commit()
    noon = utcnow().replace(hour=5, minute=0)          # 12:00 Bangkok: not quiet night
    with SessionLocal() as db:
        eng.dev_watch.tick(db, noon)
        acts = [a.action for a in db.query(AuditLog).all()]
        assert "device_signal" in acts
        d = db.query(Device).filter_by(dev_eui=EUI).one(); d.sig = {**d.sig, "q": "good", "margin": 12}; db.commit()
        eng.dev_watch.tick(db, noon + timedelta(minutes=1))
        assert "device_signal_ok" in [a.action for a in db.query(AuditLog).all()]
        settings_store.put(db, "device_alert", {**settings_store.get(db, "device_alert"), "signal_alert": False}, "t")
        d = db.query(Device).filter_by(dev_eui=EUI).one(); d.sig = {**d.sig, "q": "bad"}; db.commit()
        eng.dev_watch.tick(db, noon + timedelta(hours=2))
        assert [a.action for a in db.query(AuditLog).all()].count("device_signal") == 1


def test_prune(env_):
    eng, *_ = env_
    with SessionLocal() as db:
        db.add(UplinkLog(dev_eui=EUI, at=utcnow() - timedelta(days=100)))
        db.add(NetEvent(dev_eui=EUI, at=utcnow() - timedelta(days=20), kind="join"))
        db.add(UplinkLog(dev_eui=EUI, at=utcnow())); db.commit()
        eng.netmon.prune(db, force=True)
        assert db.query(UplinkLog).count() == 1 and db.query(NetEvent).count() == 0


# ---------------- coverage map (measured links + range) and gateway GPS -> ChirpStack ----------------
GW_A, GW_B = "0016c001ff10aaaa", "0016c001ff10bbbb"


def _setup_geo(db):
    from app.models import Gateway, Patient
    db.add(Gateway(gateway_id=GW_A, label="GW-A", enabled=True, state="online", uplinks_total=0, lat=13.90, lon=100.60))
    db.add(Gateway(gateway_id=GW_B, label="GW-B", enabled=True, state="online", uplinks_total=0, lat=13.95, lon=100.60,
                   gps_lat=13.9501, gps_lon=100.6002, gps_at=utcnow(), gps_sats=9))
    p = db.query(Patient).first(); p.lat, p.lon = 13.91, 100.60            # ~1.1 km from A, ~4.4 km from B
    d = db.query(Device).filter_by(dev_eui=EUI).one(); d.last_gateway = GW_A; d.last_seen = utcnow()
    db.commit()


def test_map_measured_links_and_range(env_):
    eng, downs, notifier, client, ids = env_
    with SessionLocal() as db:
        _setup_geo(db)
        for i in range(5):     # each uplink heard by A (margin 14.5 = good) and B (margin 1.5 = bad)
            j = up(i + 1, snr=2.0, sf=9, gw=GW_A)
            j["rxInfo"].append({"gatewayId": GW_B, "rssi": -118, "snr": -11.0})
            eng.netmon.on_uplink(db, EUI, j)
    m = client.get("/api/map", headers=login(client)).json()
    lk = {l["gateway_id"]: l for l in m["links"]}
    assert set(lk) == {GW_A, GW_B}
    assert lk[GW_A]["primary"] and lk[GW_A]["quality"] == "good" and lk[GW_A]["n"] == 5 and lk[GW_A]["distance_m"] > 1000
    assert not lk[GW_B]["primary"] and lk[GW_B]["quality"] == "bad" and lk[GW_B]["link_margin"] == 1.5
    g = {x["gateway_id"]: x for x in m["gateways"]}
    assert g[GW_A]["range_m"] == lk[GW_A]["distance_m"] and g[GW_B]["range_m"] is None     # a bad link is not "range"
    v = client.get("/api/map", headers=login(client, "viewer")).json()
    assert all(l["distance_m"] is None for l in v["links"])                 # PDPA: no home-derived distance
    assert {x["gateway_id"]: x for x in v["gateways"]}[GW_A]["range_m"]      # the aggregate range is fine


class FakeCSGw(FakeCS):
    def __init__(self):
        super().__init__(); self.loc = {"latitude": 13.0, "longitude": 100.0}
    def set_gateway_location(self, gid, lat, lon, alt=0.0):
        if abs(self.loc["latitude"] - lat) < 1e-5: return False
        self.loc = {"latitude": lat, "longitude": lon}; return True


def test_gateway_gps_to_chirpstack(env_, monkeypatch):
    eng, downs, notifier, client, ids = env_
    cs = FakeCSGw(); monkeypatch.setattr("app.provision.get_cs", lambda: cs)
    from app.models import Gateway
    with SessionLocal() as db:
        _setup_geo(db)
        ga = db.query(Gateway).filter_by(gateway_id=GW_A).one().id
        gb = db.query(Gateway).filter_by(gateway_id=GW_B).one().id
    h = login(client)
    assert client.post(f"/api/gateways/{ga}/push-location", headers=h).status_code == 409        # A has no GPS
    r = client.post(f"/api/gateways/{gb}/push-location", headers=h).json()
    assert r["changed"] is True and cs.loc["latitude"] == 13.9501
    assert client.post(f"/api/gateways/{gb}/push-location", headers=h).json()["changed"] is False
    assert client.post(f"/api/gateways/{gb}/push-location", headers=login(client, "viewer")).status_code == 403
    with SessionLocal() as db:                    # a stale fix (> 30 min) is not pushed
        db.query(Gateway).filter_by(id=gb).update({"gps_at": utcnow() - timedelta(hours=2)}); db.commit()
    assert client.post(f"/api/gateways/{gb}/push-location", headers=h).status_code == 409


def test_provision_set_gateway_location():
    import httpx, json as _j
    from app.provision import ChirpStackRest
    calls = []
    def handler(req):
        calls.append((req.method, _j.loads(req.content) if req.content else None))
        if req.method == "GET": return httpx.Response(200, json={"gateway": {"gatewayId": "aa", "name": "x", "location": {"latitude": 1.0, "longitude": 2.0, "altitude": 15}}})
        return httpx.Response(200, json={})
    c = ChirpStackRest("http://cs", "k", "app", "prof", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert c.set_gateway_location("aa", 1.0, 2.0) is False and calls[-1][0] == "GET"
    assert c.set_gateway_location("aa", 13.95, 100.6) is True
    loc = calls[-1][1]["gateway"]["location"]
    assert loc["latitude"] == 13.95 and loc["altitude"] == 15 and loc["source"] == "GPS" and calls[-1][1]["gateway"]["name"] == "x"

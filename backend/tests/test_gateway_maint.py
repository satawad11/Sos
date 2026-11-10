"""Gateway LINE alerts can be switched off: for all gateways (Settings) or per gateway with maintenance mode (until it
is back online, until a time, or until switched off; the dashboard still shows the real state; reports keep maintenance
apart from downtime). A broken gateway must also not turn into one "button silent" LINE per patient
device behind it — including after someone switched the gateway's monitoring off."""
from datetime import timedelta
from app import settings_store
from app.db import SessionLocal
from datetime import datetime
from app.models import AuditLog, Device, Gateway
from app import reports
from tests.test_all import GOOD, GW, NOON_TUE, _gw, login

T0 = NOON_TUE


def _start(eng, ids, **sa):
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 30, **sa}, "t")
        assert eng.gw.on_report(db, GW, GOOD, T0) == "ok"
        eng.gw.tick(db, T0 + timedelta(minutes=1))


def _acts(db, prefix="gateway_"):
    return [(a.action, (a.detail or {}).get("line")) for a in db.query(AuditLog).filter(AuditLog.action.like(prefix + "%")).order_by(AuditLog.id)]


def test_muted_gateway_sends_no_line_and_unmutes_itself(env_):
    eng, downs, notif, client, ids = env_
    _start(eng, ids)
    H = login(client)
    with SessionLocal() as db:
        eng.gw.tick(db, T0 + timedelta(minutes=7))
        assert len(notif.system) == 1                                    # the first alert went out
        gid = _gw(db).id
    r = client.put(f"/api/gateways/{gid}", headers=H, json={"alerts_muted": True}); assert r.status_code == 200
    g = next(x for x in client.get("/api/gateways", headers=H).json() if x["gateway_id"] == GW)
    assert g["alerts_muted"] is True
    with SessionLocal() as db:
        for m in range(8, 300, 5):                                       # hours of being broken: no reminders
            eng.gw.tick(db, T0 + timedelta(minutes=m))
        assert len(notif.system) == 1
        # repaired: comes back, mute clears by itself, the recovery message is sent
        eng.gw.on_report(db, GW, GOOD, T0 + timedelta(minutes=301))
        eng.gw.tick(db, T0 + timedelta(minutes=302))
        db.expire_all()
        assert _gw(db).alerts_muted is False and len(notif.system) == 2 and "ซ่อมบำรุงเสร็จ" in notif.system[1][1]
        assert [a for a, _ in _acts(db)] == ["gateway_offline", "gateway_maint_on", "gateway_update", "gateway_maint_off", "gateway_online"]


def test_muted_before_it_breaks_logs_but_does_not_send(env_):
    eng, downs, notif, client, ids = env_
    _start(eng, ids)
    with SessionLocal() as db:
        _gw(db).alerts_muted = True; db.commit()
        eng.gw.tick(db, T0 + timedelta(minutes=7))
        assert notif.system == [] and _gw(db).state == "offline"         # dashboard still shows it offline
        assert _acts(db) == [("gateway_offline", "maintenance")]


def test_global_switch_off(env_):
    eng, downs, notif, client, ids = env_
    H = login(client)
    r = client.put("/api/settings/system_alert", headers=H, json={"caregiver_ids": [ids["a"]], "repeat_min": 30, "gateway_line": False})
    assert r.status_code == 200, r.text
    # saving without the key keeps the stored value (older dashboards)
    r = client.put("/api/settings/system_alert", headers=H, json={"caregiver_ids": [ids["a"]], "repeat_min": 30}); assert r.status_code == 200
    with SessionLocal() as db:
        assert settings_store.get(db, "system_alert")["gateway_line"] is False
        assert eng.gw.on_report(db, GW, GOOD, T0) == "ok"
        for m in (1, 7, 40, 80):
            eng.gw.tick(db, T0 + timedelta(minutes=m))
        eng.gw.on_report(db, GW, GOOD, T0 + timedelta(minutes=90)); eng.gw.tick(db, T0 + timedelta(minutes=91))
        assert notif.system == []
        assert _acts(db) == [("gateway_offline", "off"), ("gateway_online", "off")]
    assert client.put("/api/settings/system_alert", headers=H, json={"caregiver_ids": [], "repeat_min": 30, "gateway_line": "no"}).status_code == 422


def test_viewer_cannot_mute(env_):
    eng, downs, notif, client, ids = env_
    _start(eng, ids)
    with SessionLocal() as db:
        gid = _gw(db).id
    V = login(client, "viewer")
    assert client.put(f"/api/gateways/{gid}", headers=V, json={"alerts_muted": True}).status_code == 403


def _device_behind(db, gw_id, t):
    d = db.query(Device).one(); d.last_seen = t; d.last_gateway = gw_id; d.battery = 90; d.mains_ok = True; db.commit()


def test_devices_behind_a_dead_gateway_do_not_each_alert(env_):
    eng, downs, notif, client, ids = env_
    _start(eng, ids)
    with SessionLocal() as db:
        db.add(Gateway(gateway_id="0016c001f1500002", enabled=True, last_seen=T0, state="online")); db.commit()
        _device_behind(db, GW, T0)
        for m in range(2, 400, 5):                                       # GW dies; the second gateway stays up
            db.get(Gateway, 2).last_seen = T0 + timedelta(minutes=m); db.commit()
            eng.gw.tick(db, T0 + timedelta(minutes=m)); eng.dev_watch.tick(db, T0 + timedelta(minutes=m))
        assert _acts(db, "device_") == []                                 # the gateway alert covers its devices


def test_switching_monitoring_off_does_not_unleash_device_alerts(env_):
    """The reported bug: monitoring of the only (broken) gateway switched off -> every device looked silent and LINE kept coming."""
    eng, downs, notif, client, ids = env_
    _start(eng, ids)
    with SessionLocal() as db:
        _device_behind(db, GW, T0)
        _gw(db).enabled = False; db.commit()
        for m in range(2, 400, 5):
            eng.gw.tick(db, T0 + timedelta(minutes=m)); eng.dev_watch.tick(db, T0 + timedelta(minutes=m))
        assert notif.system == [] and _acts(db, "device_") == []


def _maint(client, H, gid, **m):
    return client.put(f"/api/gateways/{gid}", headers=H, json={"maintenance": {"on": True, **m}})


def test_timed_maintenance_survives_reboots_then_reports_overdue(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    _start(eng, ids)
    H = login(client)
    import app.api as api_mod
    monkeypatch.setattr(api_mod, "utcnow", lambda: T0 + timedelta(minutes=2))
    with SessionLocal() as db:
        gid = _gw(db).id
    assert _maint(client, H, gid, end="time", until=(T0 + timedelta(minutes=3)).isoformat()).status_code == 422   # too soon
    r = _maint(client, H, gid, end="time", until=(T0 + timedelta(hours=2, minutes=2)).isoformat(), reason="เปลี่ยนสายแลน")
    assert r.status_code == 200, r.text
    g = next(x for x in client.get("/api/gateways", headers=H).json() if x["gateway_id"] == GW)
    assert g["maint"]["end"] == "time" and g["maint"]["reason"] == "เปลี่ยนสายแลน" and g["maint"]["by"] == "admin"
    with SessionLocal() as db:
        eng.gw.tick(db, T0 + timedelta(minutes=10))                       # goes down: silent
        eng.gw.on_report(db, GW, GOOD, T0 + timedelta(minutes=30)); eng.gw.tick(db, T0 + timedelta(minutes=31))   # rebooted
        db.expire_all(); assert _gw(db).alerts_muted is True               # a timed maintenance does not end on a reboot
        for m in range(40, 125, 5): eng.gw.tick(db, T0 + timedelta(minutes=m))   # down again until past the end time
        assert notif.system == []
        eng.gw.tick(db, T0 + timedelta(minutes=126))
        db.expire_all(); assert _gw(db).alerts_muted is False
        assert len(notif.system) == 1 and "หมดเวลาซ่อมบำรุง" in notif.system[0][1] and "เปลี่ยนสายแลน" in notif.system[0][1]
        eng.gw.tick(db, T0 + timedelta(minutes=140)); assert len(notif.system) == 1
        eng.gw.tick(db, T0 + timedelta(minutes=157)); assert len(notif.system) == 2 and "เตือนซ้ำ" in notif.system[1][1]


def test_manual_end_while_down_sends_once(env_):
    eng, downs, notif, client, ids = env_
    _start(eng, ids)
    H = login(client)
    with SessionLocal() as db:
        gid = _gw(db).id
    assert _maint(client, H, gid, end="manual").status_code == 200
    with SessionLocal() as db:
        for m in range(7, 600, 10): eng.gw.tick(db, T0 + timedelta(minutes=m))
        assert notif.system == []
    assert client.put(f"/api/gateways/{gid}", headers=H, json={"maintenance": {"on": False}}).status_code == 200
    assert len(notif.system) == 1 and "ปิดโหมดซ่อมบำรุงแล้ว" in notif.system[0][1]


def test_report_keeps_maintenance_apart(env_):
    eng, downs, notif, client, ids = env_
    with SessionLocal() as db:
        add = lambda a, m: db.add(AuditLog(actor="system", action=a, target=GW, at=T0 + timedelta(minutes=m), detail={}))
        add("gateway_offline", 0); add("gateway_maint_on", 10); add("gateway_maint_off", 70); add("gateway_online", 100)
        add("gateway_maint_on", 200); add("gateway_offline", 205); add("gateway_online", 230); add("gateway_maint_off", 240)
        db.commit()
        g = reports._gateway_outages(db, T0 - timedelta(hours=1), T0 + timedelta(hours=10))[0]
        assert g["outages"] == 1 and g["downtime_min"] == 40 and g["maint_min"] == 85, g


def test_impact_lists_patients_that_depend_on_this_gateway(env_):
    eng, downs, notif, client, ids = env_
    _start(eng, ids)
    H = login(client)
    from app import codec
    from tests.test_all import up
    other = "0016c001f1500002"
    with SessionLocal() as db:
        db.add(Gateway(gateway_id=other, enabled=True, last_seen=T0 + timedelta(minutes=1), state="online")); db.commit()
        gid = _gw(db).id
        eng.handle_uplink(db, "70b3d57ed0000001", up(codec.T_HEARTBEAT), {"gateway_id": GW, "rssi": -80, "gateways": [GW]})
    import app.api as api_mod
    r = client.get(f"/api/gateways/{gid}/impact", headers=H).json()
    assert r["total"] == 1 and r["only_here"] == 1 and r["devices"][0]["patient"]["full_name"] == "นางสมศรี ใจดี"
    with SessionLocal() as db:                                           # now the second gateway hears it as well
        db.get(Gateway, 2).last_seen = api_mod.utcnow(); db.commit()
        eng.handle_uplink(db, "70b3d57ed0000001", up(codec.T_HEARTBEAT), {"gateway_id": GW, "rssi": -80, "gateways": [GW, other]})
    r = client.get(f"/api/gateways/{gid}/impact", headers=H).json()
    assert r["only_here"] == 0 and r["devices"][0]["other_gateways"] == [other]
    assert client.get(f"/api/gateways/{gid}/impact", headers=login(client, "viewer")).status_code == 403

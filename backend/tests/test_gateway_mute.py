"""Gateway LINE alerts can be switched off: for all gateways (Settings) or for one known-broken gateway, which switches
itself back on when the gateway returns. A broken gateway must also not turn into one "button silent" LINE per patient
device behind it — including after someone switched the gateway's monitoring off."""
from datetime import timedelta
from app import settings_store
from app.db import SessionLocal
from app.models import AuditLog, Device, Gateway
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
        assert _gw(db).alerts_muted is False and len(notif.system) == 2 and "กลับมาออนไลน์" in notif.system[1][1]
        assert [a for a, _ in _acts(db)] == ["gateway_offline", "gateway_alerts_muted", "gateway_update", "gateway_online"]


def test_muted_before_it_breaks_logs_but_does_not_send(env_):
    eng, downs, notif, client, ids = env_
    _start(eng, ids)
    with SessionLocal() as db:
        _gw(db).alerts_muted = True; db.commit()
        eng.gw.tick(db, T0 + timedelta(minutes=7))
        assert notif.system == [] and _gw(db).state == "offline"         # dashboard still shows it offline
        assert _acts(db) == [("gateway_offline", "muted")]


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

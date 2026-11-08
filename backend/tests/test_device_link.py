"""Device link badge: ok / late / offline / never, judged against the device's own report interval, and a live
device_seen event on every uplink (published after commit)."""
from datetime import timedelta
from app import codec, settings_store
from app.db import SessionLocal
from app.models import Device
from tests.test_all import NOON_TUE, login, up

EUI = "70b3d57ed0000001"


def test_link_states_and_stats(env_):
    eng, downs, notif, client, ids = env_
    H = login(client)
    with SessionLocal() as db:
        d = db.query(Device).filter_by(dev_eui=EUI).one()
        assert settings_store.device_link(db, d, NOON_TUE)["link"] == "never"
        d.last_seen = NOON_TUE; db.commit()
        L = lambda m: settings_store.device_link(db, d, NOON_TUE + timedelta(minutes=m))
        assert [L(m)["link"] for m in (5, 39, 41, 89, 91)] == ["ok", "ok", "late", "late", "offline"]   # hb 30 + 10 slack; offline 90
        assert L(0)["hb_min"] == 30 and L(0)["late_at"].startswith("2026-10-13T05:40")
        d.config_override = {"heartbeat_min": 60}; db.commit()                                   # its own interval counts
        assert L(65)["link"] == "ok" and L(75)["link"] == "late"
    r = client.get("/api/devices", headers=H).json()[0]
    assert r["link"] in ("ok", "late", "offline") and "late_at" in r and "off_at" in r
    s = client.get("/api/stats", headers=H).json()["devices"]
    assert s["active"] == 1 and sum(s[k] for k in ("ok", "late", "offline", "never")) == 1


def test_uplink_publishes_device_seen_after_commit(env_):
    eng, downs, notif, client, ids = env_
    seen = []

    def publish(e):
        if e["type"] == "device_seen":
            with SessionLocal() as other:
                seen.append((e, other.query(Device).filter_by(dev_eui=EUI).one().last_seen is not None))
    eng.publish = publish
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_HEARTBEAT, batt=77), {"rssi": -70, "snr": 9.5})
    assert len(seen) == 1 and seen[0][1]
    e = seen[0][0]
    assert e["link"] == "ok" and e["battery"] == 77 and e["rssi"] == -70 and e["last_seen"].endswith("+00:00")

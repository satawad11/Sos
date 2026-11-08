"""Battery / mains / silent-device alerts must be calm: one message per episode, stored across separate DB sessions
(the real tick loop opens a new session every few seconds), quiet at night for non-urgent issues, flapping power
counted as one episode."""
from datetime import timedelta
from app import settings_store
from app.db import SessionLocal
from app.models import AuditLog
from tests.test_all import NOON_TUE, _dev

def _setup(ids, **da):
    with SessionLocal() as db:
        settings_store.put(db, "system_alert", {"caregiver_ids": [ids["a"]], "repeat_min": 60}, "t")
        if da: settings_store.put(db, "device_alert", {**settings_store.get(db, "device_alert"), **da}, "t")


def _tick(eng, when, **dev):
    with SessionLocal() as db:                                # a fresh session per tick, like Engine.tick in production
        if dev:
            d = _dev(db)
            for k, v in dev.items(): setattr(d, k, v)
            db.commit()
        eng.dev_watch.tick(db, when)


def _actions():
    with SessionLocal() as db:
        return [a.action for a in db.query(AuditLog).filter(AuditLog.action.like("device_%")).order_by(AuditLog.id)]


def test_mains_alert_sent_once_across_sessions(env_):
    eng, downs, notif, client, ids = env_
    _setup(ids)
    t0 = NOON_TUE
    _tick(eng, t0, last_seen=t0, battery=90, mains_ok=False)
    for s in range(11 * 60, 20 * 60, 5):                       # every 5 s for 9 minutes after the 10-min grace
        _tick(eng, t0 + timedelta(seconds=s))
    assert _actions() == ["device_mains"], _actions()
    _tick(eng, t0 + timedelta(hours=3), last_seen=t0 + timedelta(hours=3))   # no hourly nagging for mains by default
    assert _actions() == ["device_mains"]


def test_offline_repeat_is_stored_across_sessions(env_):
    eng, downs, notif, client, ids = env_
    _setup(ids)
    t0 = NOON_TUE
    _tick(eng, t0, last_seen=t0, battery=90, mains_ok=True)
    for m in range(95, 200, 1):
        _tick(eng, t0 + timedelta(minutes=m))
    assert _actions() == ["device_offline", "device_offline_repeat"], _actions()   # 95 min, then 155 min — not every tick


def test_battery_once_then_critical_once(env_):
    eng, downs, notif, client, ids = env_
    _setup(ids)
    t0 = NOON_TUE
    _tick(eng, t0, last_seen=t0, battery=18, mains_ok=True)
    for m in range(1, 300, 7):
        _tick(eng, t0 + timedelta(minutes=m), last_seen=t0 + timedelta(minutes=m))
    _tick(eng, t0 + timedelta(minutes=301), last_seen=t0 + timedelta(minutes=301), battery=8)
    _tick(eng, t0 + timedelta(minutes=320), last_seen=t0 + timedelta(minutes=320), battery=6)
    assert _actions() == ["device_battery", "device_battery_critical"], _actions()
    assert "วิกฤต" in notif.system[-1][1]


def test_night_is_quiet_for_battery_and_mains_but_not_offline(env_):
    eng, downs, notif, client, ids = env_
    _setup(ids)
    night = NOON_TUE.replace(hour=15)                          # 22:00 Bangkok (UTC+7)
    _tick(eng, night, last_seen=night, battery=12, mains_ok=False)
    _tick(eng, night + timedelta(minutes=30), last_seen=night + timedelta(minutes=30))
    assert _actions() == []                                     # held until morning
    _tick(eng, night + timedelta(hours=9, minutes=1), last_seen=night + timedelta(hours=9))   # 07:01
    assert sorted(_actions()) == ["device_battery", "device_mains"]
    # a problem that ends before morning never produces a message
    night2 = night + timedelta(days=1)
    _tick(eng, night2, last_seen=night2, mains_ok=False, battery=90)
    _tick(eng, night2 + timedelta(hours=2), last_seen=night2 + timedelta(hours=2), mains_ok=True)
    assert _actions().count("device_mains") == 1 and "device_mains_ok" in _actions()     # ok only for the morning one
    # silent device is urgent: no night hold
    n0 = len(_actions())
    _tick(eng, night2 + timedelta(hours=4), last_seen=night2 + timedelta(minutes=30))
    assert _actions()[n0:] == ["device_offline"]


def test_flapping_mains_is_one_episode(env_):
    eng, downs, notif, client, ids = env_
    _setup(ids)
    t0 = NOON_TUE
    _tick(eng, t0, last_seen=t0, battery=90, mains_ok=False)
    _tick(eng, t0 + timedelta(minutes=11))                                       # alerted
    _tick(eng, t0 + timedelta(minutes=12), mains_ok=True)                        # back
    for k in range(6):                                                            # flickers for 30 min
        _tick(eng, t0 + timedelta(minutes=13 + k * 5), mains_ok=False)
        _tick(eng, t0 + timedelta(minutes=13 + k * 5 + 1), mains_ok=False)
        _tick(eng, t0 + timedelta(minutes=13 + k * 5 + 3), mains_ok=True)
    assert _actions() == ["device_mains", "device_mains_ok"], _actions()


def test_settings_validation(env_):
    eng, downs, notif, client, ids = env_
    from tests.test_all import login
    H = login(client)
    base = {"enabled": True, "battery_low_pct": 20, "mains_grace_min": 10}
    assert client.put("/api/settings/device_alert", headers=H, json={**base, "issue_repeat_h": 12, "quiet_night": False}).status_code == 200
    assert client.put("/api/settings/device_alert", headers=H, json={**base, "issue_repeat_h": 99}).status_code == 422
    assert client.put("/api/settings/device_alert", headers=H, json=base).status_code == 200      # old clients keep working
    with SessionLocal() as db:
        v = settings_store.get(db, "device_alert")
    assert v["issue_repeat_h"] == 12 and v["quiet_night"] is False

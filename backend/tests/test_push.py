"""Web Push: key, subscribe (push-service allow-list), fan-out on a new case, expiry cleanup, privacy level."""
import json
from app import codec, push, settings_store
from app.api import _engine
from app.db import SessionLocal
from app.models import PushSub
from tests.test_all import login, up

EUI = "70b3d57ed0000001"
FCM = "https://fcm.googleapis.com/fcm/send/abc123"


class Resp:
    def __init__(self, c): self.status_code = c


class Boom(Exception):
    def __init__(self, c): super().__init__(f"http {c}"); self.response = Resp(c)


def _sub(client, H, ep=FCM):
    return client.post("/api/push/subscribe", headers=H, json={"endpoint": ep, "label": "Android · Chrome",
                       "keys": {"p256dh": "B" * 87, "auth": "a" * 22}})


def test_endpoint_allow_list():
    assert push.endpoint_ok(FCM) and push.endpoint_ok("https://web.push.apple.com/QGx")
    assert push.endpoint_ok("https://updates.push.services.mozilla.com/wpush/v2/x")
    assert push.endpoint_ok("https://wns2-db5p.notify.windows.com/w/?token=x")
    for bad in ("http://fcm.googleapis.com/x", "https://evil.com/fcm.googleapis.com", "https://fcm.googleapis.com.evil.com/x",
                "https://169.254.169.254/", "https://localhost:8000/api", "file:///etc/passwd"):
        assert not push.endpoint_ok(bad), bad


def test_subscribe_and_fanout(env_):
    eng, downs, notif, client, ids = env_
    H, V = login(client), login(client, "viewer")
    k = client.get("/api/push/key", headers=H).json()
    assert len(k["public_key"]) == 87 and k["subscriptions"] == 0 and k["notify"] is True
    assert client.get("/api/push/key", headers=H).json()["public_key"] == k["public_key"]      # stable, stored once
    assert "vapid" not in json.dumps(client.get("/api/settings", headers=H).json())            # never exposed via settings
    assert _sub(client, H, "https://evil.example/hook").status_code == 422
    assert _sub(client, H).status_code == 200 and _sub(client, H).status_code == 200            # upsert, no duplicate
    assert _sub(client, V, "https://web.push.apple.com/viewer").status_code == 200
    with SessionLocal() as db:
        assert db.query(PushSub).count() == 2
    sent = []
    sender = push.PushSender(SessionLocal, send_fn=lambda **kw: sent.append(kw))
    with SessionLocal() as db:
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=4)) == "created"
        case_id = db.query(push.Case).order_by(push.Case.id.desc()).first().id
        n = sender.handle(db, {"type": "case_new", "case_id": case_id})
    assert n == 1 and len(sent) == 1                                     # admin only — viewer is not notified
    body = json.loads(sent[0]["data"])
    assert body["url"] == f"./?case={case_id}" and "นางสมศรี" in body["body"] and sent[0]["headers"]["Urgency"] == "high"
    # privacy "minimal": no name in the notification
    with SessionLocal() as db:
        settings_store.put(db, "privacy", {**settings_store.get(db, "privacy"), "line_detail": "minimal"}, "t")
        sent.clear(); sender.handle(db, {"type": "case_new", "case_id": case_id})
    assert "นางสมศรี" not in json.loads(sent[0]["data"])["body"]
    # overdue push only once per case; test presses never push
    sender.on_event({"type": "case_update", "case_id": case_id, "state": "new", "overdue": True})
    sender.on_event({"type": "case_update", "case_id": case_id, "state": "new", "overdue": True})
    sender.on_event({"type": "case_update", "case_id": case_id, "state": "accepted", "overdue": True})
    sender.on_event({"type": "case_test", "case_id": case_id})
    assert sender.q.qsize() == 1
    # expired subscription is removed
    gone = push.PushSender(SessionLocal, send_fn=lambda **kw: (_ for _ in ()).throw(Boom(410)))
    with SessionLocal() as db:
        gone.handle(db, {"type": "case_new", "case_id": case_id})
        assert db.query(PushSub).count() == 1


def test_push_test_endpoint_and_unsubscribe(env_):
    *_, client, ids = env_
    H = login(client)
    assert client.post("/api/push/test", headers=H).status_code == 409
    _sub(client, H)
    sent = []
    _engine["push"] = push.PushSender(SessionLocal, send_fn=lambda **kw: sent.append(kw))
    try:
        assert client.post("/api/push/test", headers=H).json() == {"sent": 1, "devices": 1}
    finally:
        _engine.pop("push", None)
    assert client.post("/api/push/unsubscribe", headers=H, json={"endpoint": FCM}).json() == {"removed": 1}

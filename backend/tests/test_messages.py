"""Preset messages to the patient's display (MSG, FPort 12) + the firmware 0.2 receipt fields in the uplink flags."""
from datetime import datetime, timedelta, timezone
import pytest
from app import codec
from app.db import SessionLocal
from app.models import Case, CaseEvent, Device
from tests.test_all import login

EUI = "70b3d57ed0000001"
NOON_TUE = datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc)   # 12:00 Bangkok, Tuesday


def up(t, alert=5, flags=1, poll=0, cfg=1):
    return codec.encode_uplink(codec.Uplink(t, alert, flags, 87, 0, 2, cfg, poll))


def fw02_poll(alert=5, ack=0, seen=0):
    """A POLL as firmware 0.2 sends it: ACK result it shows in bits 4-6, last shown MSG seq in byte 7."""
    return up(codec.T_POLL, alert, flags=1 | (ack << codec.F_ACK_SHIFT) | codec.F_MSG_SEQ, poll=seen)


@pytest.fixture()
def now(monkeypatch):
    t = {"v": NOON_TUE}
    monkeypatch.setattr("app.engine.utcnow", lambda: t["v"])
    return t


def test_codec_msg_roundtrip_and_flags():
    assert codec.encode_msg(5, 1, 1, 10) == bytes([0x19, 5, 1, 1, 10])
    assert codec.msg_preview(1, 10) == ["HELP COMING", "ETA 10 MIN"]
    for m in codec.MESSAGES.values():               # OLED: 16 characters per line at the 8x13 font
        assert all(len(line.replace("{v}", "255")) <= 16 for line in m["en"])
    with pytest.raises(codec.CodecError):
        codec.encode_msg(5, 1, 99)
    u = codec.decode_uplink(fw02_poll(ack=1, seen=3))
    assert u.ack_known == 1 and u.msg_seen == 3 and u.mains_ok
    old = codec.decode_uplink(up(codec.T_POLL, poll=3))          # firmware 0.1: byte 7 is just a poll counter
    assert old.ack_known == 0 and old.msg_seen is None


def test_message_queued_resent_and_delivered(env_, now):
    eng, downs, *_ = env_
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        eng.accept(db, 1, "staff1")
        eng.send_message(db, 1, "staff1", 1, 10)
        assert downs[-1] == (EUI, 12, bytes([0x19, 5, 1, 1, 10]))
        n = len(downs)
        # device already shows ACCEPTED -> no ACK resend; MSG just queued -> not resent yet
        assert eng.handle_uplink(db, EUI, fw02_poll(ack=1)) == "msg_in_flight"
        assert len(downs) == n
        now["v"] += timedelta(seconds=30)            # still not shown -> queue again
        assert eng.handle_uplink(db, EUI, fw02_poll(ack=1)) == "msg_sent"
        assert downs[-1][1] == 12 and len(downs) == n + 1
        assert eng.handle_uplink(db, EUI, fw02_poll(ack=1, seen=1)) == "msg_delivered"
        c = db.get(Case, 1)
        assert c.msg_pending is None
        kinds = [e.kind for e in db.query(CaseEvent).filter_by(case_id=1)]
        assert "message_sent" in kinds and "message_delivered" in kinds
        assert eng.handle_uplink(db, EUI, fw02_poll(ack=1, seen=1)) == "ack_known"


def test_newer_message_replaces_undelivered_one(env_, now):
    eng, downs, *_ = env_
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        eng.send_message(db, 1, "s", 3)
        eng.send_message(db, 1, "s", 2, 15)
        assert downs[-1][2] == bytes([0x19, 5, 2, 2, 15])
        # the device reports it showed seq 1 (the older one): seq 2 is still owed
        now["v"] += timedelta(seconds=30)
        assert eng.handle_uplink(db, EUI, fw02_poll(seen=1)) == "msg_sent"
        assert eng.handle_uplink(db, EUI, fw02_poll(seen=2)) == "msg_delivered"


def test_ack_still_sent_when_device_does_not_show_it(env_, now):
    eng, downs, *_ = env_
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        eng.accept(db, 1, "s")
        now["v"] += timedelta(seconds=40)
        assert eng.handle_uplink(db, EUI, fw02_poll(ack=0)) == "ack_sent"
        assert downs[-1] == (EUI, 10, bytes([0x16, 5, 1]))


def test_message_rules(env_, now):
    eng, downs, *_ = env_
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        with pytest.raises(ValueError):
            eng.send_message(db, 1, "s", 1, 0)        # ETA needs minutes
        with pytest.raises(ValueError):
            eng.send_message(db, 1, "s", 42)
        c = eng.send_message(db, 1, "s", 4, 99)        # value ignored for a message without a number
        assert c.msg_pending["value"] == 0
        eng.close(db, 1, "s")
        assert db.get(Case, 1).msg_pending is None
        with pytest.raises(ValueError):
            eng.send_message(db, 1, "s", 3)           # closed case
        old_fw = codec.encode_uplink(codec.Uplink(codec.T_ALERT, 6, 1, 87, 0, 1, 1, 0))   # firmware 0.1
        eng.handle_uplink(db, EUI, old_fw)
        assert db.query(Device).one().fw_version == "0.1"
        with pytest.raises(ValueError, match="too old"):
            eng.send_message(db, 2, "s", 3)


def test_api_message(env_, now):
    eng, downs, notif, client, ids = env_
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
    h = login(client)
    msgs = client.get("/api/device-messages", headers=h).json()
    assert msgs[0]["code"] == 1 and msgs[0]["value"] == "minutes"
    r = client.post("/api/cases/1/message", json={"code": 1, "value": 5}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["msg_pending"]["lines"] == ["HELP COMING", "ETA 5 MIN"]
    assert client.post("/api/cases/1/message", json={"code": 77}, headers=h).status_code == 422
    assert client.post("/api/cases/1/message", json={"code": 3}, headers=login(client, "viewer")).status_code == 403


def test_flush_nonces_endpoint(env_, monkeypatch):
    from app import provision as prov
    calls = []
    class CS:
        configured = True
        def flush_dev_nonces(self, eui): calls.append(eui)
    monkeypatch.setattr(prov, "get_cs", lambda: CS())
    *_, client, ids = env_
    assert client.post("/api/devices/1/flush-nonces", headers=login(client)).status_code == 200
    assert calls == [EUI]
    assert client.post("/api/devices/1/flush-nonces", headers=login(client, "viewer")).status_code == 403


def test_press_again_after_close_opens_new_case(env_, now):
    eng, downs, notif, *_ = env_
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=5))
        eng.close(db, 1, "s")
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=6)) == "created"
        assert db.query(Case).count() == 2 and len(notif.sent) == 2


def test_new_alert_while_old_case_still_open_is_a_new_case(env_, now):
    eng, downs, notif, *_ = env_
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=5))
        eng.accept(db, 1, "s")
        now["v"] += timedelta(minutes=40)            # device stopped polling, patient presses a fresh alert
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=6)) == "created"
        assert db.query(Case).count() == 2 and len(notif.sent) == 2
        assert any(e.kind == "new_alert_from_device" for e in db.get(Case, 1).events)


def test_repeat_press_on_accepted_case_reminds_people(env_, now):
    eng, downs, notif, *_ = env_
    pub = []; eng.publish = pub.append
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=5))
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=5)) == "duplicate"   # retransmission: quiet
        eng.accept(db, 1, "s")
        now["v"] += timedelta(minutes=3)
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=5)) == "repeat"
        assert notif.sent[-1] == (1, "อสม.หนึ่ง", True)
        assert any(p["type"] == "case_repeat" for p in pub)
        n = len(notif.sent)
        now["v"] += timedelta(seconds=20)            # pressing again within a minute: logged, not re-sent
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=5)) == "repeat"
        assert len(notif.sent) == n
        assert db.query(Case).count() == 1


def test_state_change_flushes_stale_downlinks_then_acks(env_, now):
    eng, downs, *_ = env_
    flushed = []
    eng.flush_queue = lambda eui: (flushed.append(eui), downs.clear())
    with SessionLocal() as db:
        eng.handle_uplink(db, EUI, up(codec.T_ALERT))
        eng.send_message(db, 1, "s", 3)
        eng.accept(db, 1, "s")
        assert flushed == [EUI]
        assert [d[1] for d in downs] == [10, 12]          # ACK first, the pending message queued again after it
        eng.close(db, 1, "s")
        assert flushed == [EUI, EUI] and downs[-1] == (EUI, 10, bytes([0x16, 5, 2]))
    eng.flush_queue = lambda eui: (_ for _ in ()).throw(RuntimeError("chirpstack down"))
    with SessionLocal() as db:                        # a failing flush never blocks the ACK
        eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=7))
        eng.close(db, 2, "s")
        assert downs[-1][2] == bytes([0x16, 7, 2])

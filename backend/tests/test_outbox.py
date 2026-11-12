"""LINE outbox: queued in the caller's transaction, sent by the worker, retried, and escalated when undeliverable."""
from datetime import timedelta
from app import codec
from app.db import SessionLocal
from app.engine import Engine
from app.models import Case, CaseEvent, Notification, Outbox, utcnow
from app.outbox import BACKOFF_S, NotifyWorker, QueuedNotifier

EUI = "70b3d57ed0000001"


def up(t, alert, batt):
    return codec.encode_uplink(codec.Uplink(t, alert, codec.F_MAINS_OK, batt, 0, 2, 1, 0))


class Flaky:
    """LINE stand-in: fails the first `fail` calls with `error`, then succeeds."""
    def __init__(self, fail=0, error="network: timeout"):
        self.fail, self.error, self.calls, self.system = fail, error, [], []
    def notify_case(self, db, case, cg, reminder):
        self.calls.append((case.id, cg.full_name, reminder))
        if self.fail > 0:
            self.fail -= 1; return False, self.error
        return True, "HTTP 200"
    def notify_system(self, db, cg, text):
        self.system.append((cg.full_name, text)); return True, "HTTP 200"
    def reply_text(self, tok, text): pass


def _setup(line):
    worker = NotifyWorker(SessionLocal, line)
    eng = Engine(lambda *a: None, QueuedNotifier(line, worker.wake))
    return eng, worker


def _alert(eng):
    with SessionLocal() as db:
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, 7, 90)) == "created"
        return db.query(Case).order_by(Case.id.desc()).first().id


def test_alert_is_queued_not_sent_inline_then_delivered(env_):
    line = Flaky(); eng, worker = _setup(line)
    cid = _alert(eng)
    assert line.calls == []                                   # the uplink handler did not wait for LINE
    with SessionLocal() as db:
        assert db.query(Outbox).filter_by(status="pending", kind="case", case_id=cid).count() == 1
        assert db.query(Notification).filter_by(case_id=cid).one().status == "queued"
    assert worker.run_once() == 1
    assert line.calls == [(cid, "อสม.หนึ่ง", False)]
    with SessionLocal() as db:
        assert db.query(Outbox).one().status == "sent"
        assert db.query(Notification).filter_by(case_id=cid).one().status == "sent"
    assert worker.run_once() == 0


def test_failed_send_is_retried_with_backoff(env_):
    line = Flaky(fail=2); eng, worker = _setup(line)
    cid = _alert(eng); t = utcnow()
    worker.run_once(t)
    with SessionLocal() as db:
        row = db.query(Outbox).one()
        assert (row.status, row.attempts) == ("pending", 1) and "timeout" in row.last_error
    assert worker.run_once(t + timedelta(seconds=BACKOFF_S[0] - 1)) == 0          # not yet
    worker.run_once(t + timedelta(seconds=BACKOFF_S[0] + 1))                      # 2nd attempt fails
    worker.run_once(t + timedelta(seconds=BACKOFF_S[0] + BACKOFF_S[1] + 2))       # 3rd succeeds
    assert len(line.calls) == 3
    with SessionLocal() as db:
        assert db.query(Outbox).one().status == "sent"
        n = db.query(Notification).filter_by(case_id=cid).one()
        assert n.status == "sent" and "attempt 3" in n.detail


def test_undeliverable_alert_escalates_at_once(env_):
    line = Flaky(fail=99); eng, worker = _setup(line)
    cid = _alert(eng); t = utcnow()
    for i in range(len(BACKOFF_S) + 1):
        worker.run_once(t + timedelta(seconds=200 * i))
    with SessionLocal() as db:
        row = db.query(Outbox).filter_by(case_id=cid).order_by(Outbox.id).first()
        assert (row.status, row.attempts) == ("failed", len(BACKOFF_S) + 1)
        assert db.query(Notification).filter_by(case_id=cid).first().status == "failed"
        assert db.query(CaseEvent).filter_by(case_id=cid, kind="notify_failed").count() == 1
        c = db.get(Case, cid)
        assert c.next_escalation_at is not None and c.esc_step == 0
        due = c.next_escalation_at
    line.fail = 0
    with SessionLocal() as db:
        eng.tick(db, t + timedelta(seconds=1000))             # next_escalation_at was pulled forward: the 2nd caregiver is next
        assert db.get(Case, cid).esc_step == 1
    worker.run_once(t + timedelta(seconds=1001))
    assert line.calls[-1][1] == "อสม.สอง"


def test_permanent_error_is_not_retried(env_):
    line = Flaky(fail=99, error="HTTP 401 invalid token"); eng, worker = _setup(line)
    _alert(eng); worker.run_once()
    with SessionLocal() as db:
        assert db.query(Outbox).first().status == "failed" and len(line.calls) == 1
    line2 = Flaky(fail=1, error="HTTP 429 rate limit"); eng2, w2 = _setup(line2)       # rate limit: do retry
    with SessionLocal() as db:
        db.query(Outbox).delete(); db.commit()
    with SessionLocal() as db:
        c = db.query(Case).first(); cg = eng2._chain(db, c)[0]
        eng2.notifier.notify_case(db, c, cg, True); db.commit()
    w2.run_once()
    with SessionLocal() as db:
        assert db.query(Outbox).one().status == "pending"


def test_retry_is_dropped_once_the_case_is_handled(env_):
    line = Flaky(fail=1); eng, worker = _setup(line)
    cid = _alert(eng); t = utcnow()
    worker.run_once(t)
    with SessionLocal() as db:
        eng.accept(db, cid, "staff")
    worker.run_once(t + timedelta(seconds=60))
    with SessionLocal() as db:
        assert db.query(Outbox).filter_by(kind="case").first().status == "dropped"
    assert len(line.calls) == 1


def test_system_messages_and_unbound_caregiver(env_):
    line = Flaky(); eng, worker = _setup(line); ids = env_[4]
    from app.models import Caregiver
    with SessionLocal() as db:
        a = db.get(Caregiver, ids["a"]); c = db.get(Caregiver, ids["c"]); c.line_user_id = ""
        assert eng.notifier.notify_system(db, a, "hello") == (True, "queued")
        assert eng.notifier.notify_system(db, c, "hello") == (False, "no line_user_id")
        db.commit()
    worker.run_once()
    assert line.system == [("อสม.หนึ่ง", "hello")]
    with SessionLocal() as db:
        st = worker.stats(db)
        assert st["pending"] == 0 and st["sent_24h"] == 1
        db.query(Outbox).update({Outbox.done_at: utcnow() - timedelta(days=30)}); db.commit()
        assert NotifyWorker.purge(db) == 1

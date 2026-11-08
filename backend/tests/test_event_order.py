"""A new case reaches the dashboards (a) only after it is committed — they re-read it straight away — and
(b) before the LINE calls, which can take seconds; slow LINE must not delay the screen."""
from app import codec
from app.db import SessionLocal
from app.models import Case
from tests.test_all import up

EUI = "70b3d57ed0000001"


def test_case_new_published_after_commit_and_before_line(env_):
    eng, downs, notif, client, ids = env_
    order = []
    real_notify = notif.notify_case

    def slow_line(db, case, cg, reminder):
        order.append("line")
        return real_notify(db, case, cg, reminder)

    def publish(e):
        if e["type"] != "case_new":
            return
        with SessionLocal() as other:          # a separate connection, like the dashboard's next API call
            order.append("event" if other.get(Case, e["case_id"]) is not None else "event-before-commit")

    notif.notify_case = slow_line
    eng.publish = publish
    with SessionLocal() as db:
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT), {}) == "created"
    assert order[:2] == ["event", "line"], order


def test_test_press_published_after_commit(env_):
    eng, downs, notif, client, ids = env_
    seen = []

    def publish(e):
        if e["type"] == "case_test":
            with SessionLocal() as other:
                seen.append(other.get(Case, e["case_id"]) is not None)

    eng.publish = publish
    with SessionLocal() as db:
        assert eng.handle_uplink(db, EUI, up(codec.T_TEST), {}) == "test"
    assert seen == [True]

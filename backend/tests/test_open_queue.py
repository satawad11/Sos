from app.db import SessionLocal
from app.models import Case, Device


def test_old_pending_case_stays_in_queue_and_alarm(env_):
    _, _, _, client, ids = env_
    with SessionLocal() as db:
        device = db.query(Device).one()
        for index, state in enumerate(["new"] + ["accepted"] * 101 + ["closed", "new"]):
            db.add(Case(device_id=device.id, patient_id=ids["p"], alert_id=index,
                        state=state, is_test=index == 103))
        db.commit()
    login = client.post("/api/login", json={"username": "admin", "password": "password12345"})
    headers = {"Authorization": "Bearer " + login.json()["token"]}
    queue = client.get("/api/cases/open", headers=headers).json()
    assert len(queue) == 102
    assert queue[-1]["state"] == "new"
    pending = client.get("/api/cases/open?pending_only=true", headers=headers).json()
    assert pending == [queue[-1]]

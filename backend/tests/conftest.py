import os, tempfile
_d = tempfile.mkdtemp()
os.environ["SOS_DATABASE_URL"] = f"sqlite:///{_d}/t.db"
os.environ["SOS_JWT_SECRET"] = "test-secret-test-secret-test-secret"
os.environ["SOS_LINE_CHANNEL_SECRET"] = "linesecret"
import pytest
from fastapi.testclient import TestClient
from app import api
from app.db import Base, SessionLocal, engine as dbengine
from app.engine import Engine
from app.auth import hash_pw
from app.models import User, Device, Patient, Caregiver, Assignment


class FakeNotifier:
    def __init__(self): self.sent = []; self.replies = []; self.system = []; self.loc = []; self.loc_skip = False
    def notify_case(self, db, case, cg, reminder):
        self.sent.append((case.id, cg.full_name, reminder)); return True, "ok"
    def notify_system(self, db, cg, text): self.system.append((cg.full_name, text)); return True, "ok"
    def notify_location(self, db, case, cg, lat, lon, sats, home_m):
        self.loc.append((case.id, cg.full_name, lat, lon, sats, home_m))
        return (False, "skipped: minimal privacy mode") if self.loc_skip else (True, "ok")
    def reply_text(self, tok, text): self.replies.append(text)


@pytest.fixture()
def env_():
    Base.metadata.drop_all(dbengine); Base.metadata.create_all(dbengine)
    downs = []
    notifier = FakeNotifier()
    eng = Engine(lambda eui, port, data: downs.append((eui, port, data)), notifier)
    api._engine["engine"] = eng
    with SessionLocal() as db:
        db.add(User(username="admin", password_hash=hash_pw("password12345"), role="admin"))
        db.add(User(username="viewer", password_hash=hash_pw("password12345"), role="viewer"))
        p = Patient(full_name="นางสมศรี ใจดี", address="12 ม.3", phone="0800000000")
        db.add(p); db.flush()
        db.add(Device(dev_eui="70b3d57ed0000001", lifecycle="active", patient_id=p.id, label="A1"))
        a = Caregiver(full_name="อสม.หนึ่ง", line_user_id="U1"); b = Caregiver(full_name="อสม.สอง", line_user_id="U2")
        c = Caregiver(full_name="ผอ.รพ.สต.", line_user_id="U3")
        db.add_all([a, b, c]); db.flush()
        db.add_all([Assignment(patient_id=p.id, caregiver_id=a.id, priority=1),
                    Assignment(patient_id=p.id, caregiver_id=b.id, priority=2)])
        db.commit()
        ids = {"a": a.id, "b": b.id, "c": c.id, "p": p.id}
    from app.main import create_app
    client = TestClient(create_app(with_lifespan=False))
    yield eng, downs, notifier, client, ids

"""Web Push subscriptions, the monthly button self-test and the in-app system update.
Split out of api.py; shared helpers and the engine handle still live there."""
from __future__ import annotations
from fastapi import APIRouter
from . import reports
from .auth import admin_only, audit, current_user, staff_or_admin
from .db import get_db
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from .api import _engine, get_engine

router = APIRouter(prefix="/api")

# ---------------- Web Push / installed app (app/push.py) ----------------
from . import push as pushmod
from .models import PushSub


class PushSubIn(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: dict
    label: str = Field(default="", max_length=120)


@router.get("/push/key")
def push_key(db: Session = Depends(get_db), u=Depends(current_user)):
    mine = db.query(PushSub).filter_by(user_id=u.id).count()
    return {"public_key": pushmod.public_key(db), "subscriptions": mine, "notify": u.role in pushmod.NOTIFY_ROLES}


@router.post("/push/subscribe")
def push_subscribe(b: PushSubIn, db: Session = Depends(get_db), u=Depends(current_user)):
    if not pushmod.endpoint_ok(b.endpoint): raise HTTPException(422, "push service not supported")
    p256, auth = str(b.keys.get("p256dh", "")), str(b.keys.get("auth", ""))
    if not (20 <= len(p256) <= 200 and 8 <= len(auth) <= 100): raise HTTPException(422, "bad keys")
    s = db.query(PushSub).filter_by(endpoint=b.endpoint).first()
    if s: s.user_id, s.p256dh, s.auth, s.label, s.fail_count = u.id, p256, auth, b.label, 0
    else: db.add(PushSub(user_id=u.id, endpoint=b.endpoint, p256dh=p256, auth=auth, label=b.label))
    audit(db, u.username, "push_subscribe", b.label); db.commit()
    return {"ok": True}


class EndpointIn(BaseModel):
    endpoint: str = Field(max_length=1000)


@router.post("/push/unsubscribe")
def push_unsubscribe(b: EndpointIn, db: Session = Depends(get_db), u=Depends(current_user)):
    n = db.query(PushSub).filter_by(endpoint=b.endpoint, user_id=u.id).delete(); db.commit()
    return {"removed": n}


@router.post("/push/test")
def push_test(db: Session = Depends(get_db), u=Depends(current_user)):
    subs = db.query(PushSub).filter_by(user_id=u.id).all()
    if not subs: raise HTTPException(409, "อุปกรณ์นี้ยังไม่ได้เปิดการแจ้งเตือน")
    sender = _engine.get("push") or pushmod.PushSender(None)
    ok = sender.send_many(db, subs, {"title": "🔔 ทดสอบการแจ้งเตือน", "body": "ถ้าเห็นข้อความนี้ แปลว่าอุปกรณ์นี้จะได้รับการแจ้งเหตุฉุกเฉิน",
                                     "tag": "test", "url": "./", "urgent": False})
    return {"sent": ok, "devices": len(subs)}


# ---------------- monthly button self-test (app/selftest.py) ----------------
from . import selftest as stest


@router.get("/selftest")
def selftest_status(month: str | None = None, db: Session = Depends(get_db), u=Depends(current_user)):
    try:
        return stest.status(db, month, names=u.role in ("admin", "staff"))
    except (ValueError, reports.RangeError) as e:
        raise HTTPException(422, str(e))


@router.post("/selftest/remind")
def selftest_remind(db: Session = Depends(get_db), u=Depends(staff_or_admin), eng=Depends(get_engine)):
    """Send the 'please test your button' LINE reminder now (outside the monthly schedule)."""
    return stest.send_reminders(db, eng.notifier, "manual", actor=u.username)


# ---------------- system update (GitHub → host updater; see app/updates.py, docs/UPDATE.md) ----------------
from . import updates as upd


class UpdateIn(BaseModel):
    action: str = Field(pattern="^(check|update|firmware)$")


@router.get("/system/update")
def update_state(u=Depends(admin_only)):
    return upd.state()


@router.post("/system/update")
def update_request(b: UpdateIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    try:
        req = upd.request(b.action, u.username)
    except upd.UpdateError as e:
        raise HTTPException(e.code, str(e))
    if b.action == "update":
        st = upd.state()["status"]
        audit(db, u.username, "system_update_requested", req["id"],
              current=(st.get("current") or {}).get("commit", "")[:12], latest=(st.get("latest") or {}).get("commit", "")[:12])
        db.commit()
    return req

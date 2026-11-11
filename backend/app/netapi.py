"""Dashboard API for radio/network monitoring (app/netmon.py): per-button signal history + diagnosis, ping tests,
the live event list. Admin and staff; the read-only role sees signal quality but cannot send anything."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from . import netmon, settings_store
from . import provision as prov
from .api import get_engine, iso
from .auth import audit, current_user, staff_or_admin
from .db import get_db
from .models import Case, Device, NetEvent, PingTest, utcnow

router = APIRouter(prefix="/api")
PING_ST = {"queued": "รอปุ่มส่งสัญญาณขึ้นมา", "sent": "เกตเวย์ส่งถึงปุ่มแล้ว รอปุ่มยืนยัน", "acked": "สำเร็จ — ปุ่มยืนยันรับแล้ว",
           "nack": "ปุ่มไม่ยืนยันรับ", "expired": "หมดเวลา", "cancelled": "ยกเลิก", "failed": "ส่งไม่สำเร็จ"}


def _dev(db: Session, did: int) -> Device:
    d = db.get(Device, did)
    if not d: raise HTTPException(404)
    return d


def _ping_dict(p: PingTest) -> dict:
    return {"id": p.id, "status": p.status, "status_th": PING_ST.get(p.status, p.status), "created_at": iso(p.created_at),
            "sent_at": iso(p.sent_at), "done_at": iso(p.done_at), "actor": p.actor, "gateway_id": p.gateway_id, "note": p.note}


def _ev_dict(e: NetEvent) -> dict:
    return {"id": e.id, "at": iso(e.at), "dev_eui": e.dev_eui, "kind": e.kind, "level": e.level, "code": e.code, "text": e.text}


@router.get("/devices/{did}/network")
def device_network(did: int, days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db), u=Depends(current_user),
                   eng=Depends(get_engine)):
    d = _dev(db, did)
    hb = settings_store.get(db, "device_defaults")["heartbeat_min"]
    cs = prov.get_cs()
    known = cs.device_known(d.dev_eui) if cs.configured else None
    out = {"dev_eui": d.dev_eui, "sig": d.sig, "stats": eng.netmon.stats(db, d.dev_eui, days),
           "diagnosis": netmon.diagnose(db, d, known, hb), "heartbeat_min": hb, "chirpstack": known,
           "quality_th": netmon.QUALITY_TH}
    if u.role != "viewer":
        out["events"] = [_ev_dict(e) for e in db.query(NetEvent).filter_by(dev_eui=d.dev_eui)
                         .order_by(NetEvent.at.desc(), NetEvent.id.desc()).limit(60).all()]
        out["pings"] = [_ping_dict(p) for p in db.query(PingTest).filter_by(dev_eui=d.dev_eui)
                        .order_by(PingTest.id.desc()).limit(10).all()]
    return out


@router.post("/devices/{did}/ping")
def device_ping(did: int, db: Session = Depends(get_db), u=Depends(staff_or_admin), eng=Depends(get_engine)):
    """Confirmed test downlink. Never while the button has an open case (its real ACK/messages go first)."""
    d = _dev(db, did)
    if d.lifecycle in ("retired", "lost"): raise HTTPException(409, "อุปกรณ์นี้ปลดระวาง/สูญหาย")
    if db.query(Case).filter(Case.device_id == d.id, Case.state.in_(("new", "accepted"))).first():
        raise HTTPException(409, "ปุ่มนี้มีเหตุฉุกเฉินที่ยังไม่ปิด — ทดสอบหลังปิดเคส")
    if db.query(PingTest).filter(PingTest.dev_eui == d.dev_eui, PingTest.status.in_(("queued", "sent"))).first():
        raise HTTPException(409, "มีการทดสอบที่รอผลอยู่แล้ว")
    cs = prov.get_cs()
    if not cs.configured: raise HTTPException(409, "ChirpStack API ยังไม่ได้ตั้งค่า")
    try:
        qid = cs.enqueue(d.dev_eui, netmon.PING_FPORT, netmon.PING_PAYLOAD, confirmed=True)
    except prov.ProvisionError as e:
        raise HTTPException(502, str(e))
    p = PingTest(dev_eui=d.dev_eui, actor=u.username, queue_id=qid, status="queued")
    db.add(p); audit(db, u.username, "device_ping", d.dev_eui); db.commit()
    eng.netmon.on_downlink(db, d.dev_eui, netmon.PING_FPORT, len(netmon.PING_PAYLOAD))
    hb = settings_store.get(db, "device_defaults")["heartbeat_min"]
    return {**_ping_dict(p), "eta_min": [hb, 2 * hb]}


@router.get("/net/events")
def net_events(limit: int = Query(200, ge=1, le=1000), dev_eui: str = "", kind: str = "", level: str = "",
               db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    q = db.query(NetEvent)
    if dev_eui: q = q.filter(NetEvent.dev_eui == dev_eui.lower())
    if kind: q = q.filter(NetEvent.kind == kind)
    if level: q = q.filter(NetEvent.level == level)
    labels = {d.dev_eui: d.label for d in db.query(Device).all()}
    return [{**_ev_dict(e), "label": labels.get(e.dev_eui, "")}
            for e in q.order_by(NetEvent.at.desc(), NetEvent.id.desc()).limit(limit).all()]

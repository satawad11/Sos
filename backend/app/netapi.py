"""Dashboard API for radio/network monitoring (app/netmon.py): per-button signal history + diagnosis, ping tests,
the live event list. Admin and staff; the read-only role sees signal quality but cannot send anything."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from . import netmon, netbackup, settings_store
from . import provision as prov
from .api import get_engine, iso
from .auth import admin_only, audit, current_user, staff_or_admin
from .db import get_db
from .models import Case, Device, Gateway, NetEvent, PingTest, UplinkLog, UplinkReception, utcnow

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


@router.get("/net/frames")
def net_frames(limit: int = Query(100, ge=1, le=300), db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    """Compact, recent uplink frames for field debugging. Excludes payloads and patient information."""
    labels = {d.dev_eui: d.label for d in db.query(Device).all()}
    rows = (db.query(UplinkLog).order_by(UplinkLog.at.desc(), UplinkLog.id.desc()).limit(limit).all())
    return [{"at": iso(r.at), "dev_eui": r.dev_eui, "label": labels.get(r.dev_eui, ""), "f_cnt": r.f_cnt,
             "f_port": r.f_port, "rssi": r.rssi, "snr": r.snr, "sf": r.sf, "freq": r.freq,
             "gateway_id": r.gateway_id, "gw_count": r.gw_count, "confirmed": r.confirmed} for r in rows]


@router.get("/net/gateways")
def net_gateway_stats(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db), u=Depends(current_user)):
    """Gateway reception history. A frame counts for every gateway in ChirpStack rxInfo."""
    since = utcnow() - timedelta(days=days)
    totals = dict(db.query(UplinkReception.gateway_id, func.count(UplinkReception.id))
                  .filter(UplinkReception.at >= since).group_by(UplinkReception.gateway_id).all())
    devices = dict(db.query(UplinkReception.gateway_id, func.count(distinct(UplinkReception.dev_eui)))
                   .filter(UplinkReception.at >= since).group_by(UplinkReception.gateway_id).all())
    signal = {gid: (rssi, snr) for gid, rssi, snr in db.query(
        UplinkReception.gateway_id, func.avg(UplinkReception.rssi), func.avg(UplinkReception.snr))
        .filter(UplinkReception.at >= since).group_by(UplinkReception.gateway_id).all()}
    daily = db.query(func.date(UplinkReception.at), UplinkReception.gateway_id, func.count(UplinkReception.id))\
        .filter(UplinkReception.at >= since).group_by(func.date(UplinkReception.at), UplinkReception.gateway_id)\
        .order_by(func.date(UplinkReception.at), UplinkReception.gateway_id).all()
    daily_by: dict[str, dict[str, int]] = {}
    for day, gid, count in daily:
        daily_by.setdefault(gid, {})[str(day)] = int(count)
    total_frames = db.query(func.count(UplinkLog.id)).filter(UplinkLog.at >= since).scalar() or 0
    gw_rows = db.query(Gateway).order_by(Gateway.label, Gateway.gateway_id).all()
    ids = set(totals) | {g.gateway_id for g in gw_rows}
    by_id = {g.gateway_id: g for g in gw_rows}
    output = []
    for gid in sorted(ids, key=lambda x: (by_id.get(x).label if by_id.get(x) else "", x)):
        g = by_id.get(gid)
        rssi, snr = signal.get(gid, (None, None))
        n = int(totals.get(gid, 0))
        output.append({"gateway_id": gid, "label": g.label if g else "", "enabled": g.enabled if g else None,
                       "uplinks": n, "devices": int(devices.get(gid, 0)), "uplinks_per_day": round(n / days, 1),
                       "heard_pct": round(min(100.0, n * 100 / total_frames), 1) if total_frames else None,
                       "rssi": round(rssi, 1) if rssi is not None else None,
                       "snr": round(snr, 1) if snr is not None else None, "daily": daily_by.get(gid, {})})
    return {"days": days, "network_uplinks": int(total_frames), "gateways": output}


@router.get("/chirpstack/profile-settings")
def chirpstack_profile_settings(u=Depends(admin_only)):
    cs = prov.get_cs()
    if not cs.configured: raise HTTPException(409, "ChirpStack API ยังไม่ได้ตั้งค่า")
    try: return cs.profile_settings()
    except prov.ProvisionError as e: raise HTTPException(502, str(e))


class AdrAlgorithmIn(BaseModel):
    adr_algorithm_id: str = Field(min_length=1, max_length=128)


@router.put("/chirpstack/profile-settings")
def chirpstack_profile_settings_update(b: AdrAlgorithmIn, u=Depends(admin_only), db: Session = Depends(get_db)):
    cs = prov.get_cs()
    if not cs.configured: raise HTTPException(409, "ChirpStack API ยังไม่ได้ตั้งค่า")
    try: cs.update_profile_adr(b.adr_algorithm_id)
    except prov.ProvisionError as e: raise HTTPException(502 if not e.status else e.status, str(e))
    audit(db, u.username, "chirpstack_profile_adr", "device-profile", adr_algorithm_id=b.adr_algorithm_id)
    db.commit()
    return {"ok": True}


class BackupExportIn(BaseModel):
    passphrase: str = Field(min_length=16, max_length=128)


class BackupImportIn(BaseModel):
    passphrase: str = Field(min_length=16, max_length=128)
    envelope: dict


@router.post("/chirpstack/backup/export")
def chirpstack_backup_export(b: BackupExportIn, u=Depends(admin_only), db: Session = Depends(get_db)):
    cs = prov.get_cs()
    if not cs.configured: raise HTTPException(409, "ChirpStack API ยังไม่ได้ตั้งค่า")
    try:
        payload = netbackup.validate_payload(cs.export_network_backup())
        envelope = netbackup.encrypt(payload, b.passphrase)
    except (prov.ProvisionError, ValueError) as e:
        raise HTTPException(502, str(e))
    audit(db, u.username, "chirpstack_backup_export", "network", devices=len(payload["devices"]), gateways=len(payload["gateways"]))
    db.commit()
    return envelope


@router.post("/chirpstack/backup/import")
def chirpstack_backup_import(b: BackupImportIn, u=Depends(admin_only), db: Session = Depends(get_db)):
    import json
    if len(json.dumps(b.envelope, separators=(",", ":"))) > 17 * 1024 * 1024:
        raise HTTPException(413, "ไฟล์สำรองมีขนาดใหญ่เกินไป")
    try:
        payload = netbackup.validate_payload(netbackup.decrypt(b.envelope, b.passphrase))
    except ValueError as e:
        raise HTTPException(422, str(e))
    cs = prov.get_cs()
    if not cs.configured: raise HTTPException(409, "ChirpStack API ยังไม่ได้ตั้งค่า")
    try: result = cs.restore_network_backup(payload)
    except prov.ProvisionError:
        raise HTTPException(502, "ติดต่อ ChirpStack เพื่อกู้คืนไม่ได้ ตรวจการตั้งค่าแล้วลองใหม่")
    audit(db, u.username, "chirpstack_backup_restore", "network",
          devices_created=result["devices_created"], devices_updated=result["devices_updated"],
          gateways_created=result["gateways_created"], gateways_updated=result["gateways_updated"],
          failed_devices=len(result["failed_devices"]), failed_gateways=len(result["failed_gateways"]))
    db.commit()
    return result

from __future__ import annotations
import re, secrets
from datetime import timedelta, datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload
from . import settings_store
from .auth import (admin_only, audit, check_pw, current_user, make_token, staff_or_admin)
from .db import get_db
from .engine import aware
from .models import (Assignment, AuditLog, BindCode, Case, Caregiver, Device, Gateway, Holiday, Notification,
                     Patient, User, utcnow)
from .config import env
from . import provision as prov

router = APIRouter(prefix="/api")
_engine = {"engine": None}          # set in main.py (dependency-injected so tests can swap)
EUI_RE = re.compile(r"^[0-9a-f]{16}$")


def get_engine():
    return _engine["engine"]


def iso(d):
    d = aware(d)
    return d.isoformat() if d else None


# ---------------- auth ----------------
class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.query(User).filter_by(username=body.username).first()
    now = utcnow()
    if u and u.locked_until and aware(u.locked_until) > now:
        raise HTTPException(429, "account locked, try later")
    if not u or not u.active or not check_pw(body.password, u.password_hash):
        if u:
            u.failed_logins += 1
            if u.failed_logins >= 5:
                u.locked_until, u.failed_logins = now + timedelta(minutes=15), 0
            db.commit()
        audit(db, body.username, "login_failed"); db.commit()
        raise HTTPException(401, "bad credentials")
    u.failed_logins = 0
    audit(db, u.username, "login"); db.commit()
    return {"token": make_token(u), "role": u.role, "name": u.display_name or u.username}


@router.get("/me")
def me(u: User = Depends(current_user)):
    return {"username": u.username, "role": u.role, "name": u.display_name}


# ---------------- cases ----------------
def case_dict(c: Case, events=False):
    d = {"id": c.id, "state": c.state, "overdue": c.overdue, "after_hours": c.after_hours, "is_test": c.is_test,
         "created_at": iso(c.created_at), "accepted_at": iso(c.accepted_at), "accepted_by": c.accepted_by,
         "closed_at": iso(c.closed_at), "close_reason": c.close_reason, "note": c.note,
         "device": {"id": c.device.id, "dev_eui": c.device.dev_eui, "label": c.device.label,
                    "battery": c.device.battery, "mains_ok": c.device.mains_ok},
         "patient": None}
    if c.patient:
        p = c.patient
        d["patient"] = {"id": p.id, "full_name": p.full_name, "address": p.address, "village": p.village,
                        "phone": p.phone, "lat": p.lat, "lon": p.lon, "notes": p.notes}
    if events:
        d["events"] = [{"at": iso(e.at), "kind": e.kind, "actor": e.actor, "detail": e.detail} for e in c.events]
    return d


@router.get("/cases")
def list_cases(state: str | None = None, include_test: bool = False, limit: int = 100,
               db: Session = Depends(get_db), u=Depends(current_user)):
    q = db.query(Case).options(joinedload(Case.device), joinedload(Case.patient))
    if state:
        q = q.filter(Case.state.in_(state.split(",")))
    if not include_test:
        q = q.filter(Case.is_test.is_(False))
    return [case_dict(c) for c in q.order_by(Case.id.desc()).limit(min(limit, 500)).all()]


@router.get("/cases/{cid}")
def get_case(cid: int, db: Session = Depends(get_db), u=Depends(current_user)):
    c = db.get(Case, cid)
    if not c: raise HTTPException(404)
    return case_dict(c, events=True)


@router.post("/cases/{cid}/accept")
def accept(cid: int, db: Session = Depends(get_db), u=Depends(staff_or_admin), eng=Depends(get_engine)):
    try: c = eng.accept(db, cid, u.display_name or u.username)
    except LookupError: raise HTTPException(404)
    audit(db, u.username, "case_accept", str(cid)); db.commit()
    return case_dict(c, events=True)


class CloseIn(BaseModel):
    reason: str = "resolved"
    note: str = Field(default="", max_length=1000)


@router.post("/cases/{cid}/close")
def close(cid: int, body: CloseIn, db: Session = Depends(get_db), u=Depends(staff_or_admin), eng=Depends(get_engine)):
    try: c = eng.close(db, cid, u.display_name or u.username, body.reason, body.note)
    except LookupError: raise HTTPException(404)
    except ValueError as e: raise HTTPException(422, str(e))
    audit(db, u.username, "case_close", str(cid), reason=body.reason); db.commit()
    return case_dict(c, events=True)


# ---------------- patients ----------------
class PatientIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=128)
    age: int | None = None
    address: str = ""
    village: str = ""
    lat: float | None = None
    lon: float | None = None
    phone: str = ""
    notes: str = ""
    active: bool = True


def pat_dict(p: Patient, db: Session):
    asg = db.query(Assignment).filter_by(patient_id=p.id).order_by(Assignment.priority).all()
    dev = db.query(Device).filter_by(patient_id=p.id).first()
    return {"id": p.id, "full_name": p.full_name, "age": p.age, "address": p.address, "village": p.village,
            "lat": p.lat, "lon": p.lon, "phone": p.phone, "notes": p.notes, "active": p.active,
            "device": dev.dev_eui if dev else None,
            "assignments": [{"priority": a.priority, "caregiver_id": a.caregiver_id,
                             "name": a.caregiver.full_name} for a in asg]}


@router.get("/patients")
def patients(db: Session = Depends(get_db), u=Depends(current_user)):
    return [pat_dict(p, db) for p in db.query(Patient).order_by(Patient.id).all()]


@router.post("/patients")
def add_patient(b: PatientIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    p = Patient(**b.model_dump()); db.add(p); db.flush()
    audit(db, u.username, "patient_create", str(p.id)); db.commit()
    return pat_dict(p, db)


@router.put("/patients/{pid}")
def edit_patient(pid: int, b: PatientIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    p = db.get(Patient, pid)
    if not p: raise HTTPException(404)
    for k, v in b.model_dump().items(): setattr(p, k, v)
    audit(db, u.username, "patient_update", str(pid)); db.commit()
    return pat_dict(p, db)


class AssignIn(BaseModel):
    caregiver_ids: list[int]   # ordered: first = priority 1


@router.put("/patients/{pid}/assignments")
def set_assignments(pid: int, b: AssignIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    if not db.get(Patient, pid): raise HTTPException(404)
    if len(set(b.caregiver_ids)) != len(b.caregiver_ids): raise HTTPException(422, "duplicate caregiver")
    for cid in b.caregiver_ids:
        if not db.get(Caregiver, cid): raise HTTPException(422, f"caregiver {cid} not found")
    db.query(Assignment).filter_by(patient_id=pid).delete()
    db.flush()
    for i, cid in enumerate(b.caregiver_ids, 1):
        db.add(Assignment(patient_id=pid, caregiver_id=cid, priority=i))
    audit(db, u.username, "assignments_set", str(pid), ids=b.caregiver_ids); db.commit()
    return pat_dict(db.get(Patient, pid), db)


# ---------------- caregivers + LINE binding ----------------
class CaregiverIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=128)
    role: str = "อสม."
    phone: str = ""
    active: bool = True


def cg_dict(c: Caregiver):
    return {"id": c.id, "full_name": c.full_name, "role": c.role, "phone": c.phone, "active": c.active,
            "line_bound": bool(c.line_user_id)}


@router.get("/caregivers")
def caregivers(db: Session = Depends(get_db), u=Depends(current_user)):
    return [cg_dict(c) for c in db.query(Caregiver).order_by(Caregiver.id).all()]


@router.post("/caregivers")
def add_cg(b: CaregiverIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    c = Caregiver(**b.model_dump()); db.add(c); db.flush()
    audit(db, u.username, "caregiver_create", str(c.id)); db.commit()
    return cg_dict(c)


@router.put("/caregivers/{cid}")
def edit_cg(cid: int, b: CaregiverIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    c = db.get(Caregiver, cid)
    if not c: raise HTTPException(404)
    for k, v in b.model_dump().items(): setattr(c, k, v)
    audit(db, u.username, "caregiver_update", str(cid)); db.commit()
    return cg_dict(c)


@router.post("/caregivers/{cid}/bind-code")
def bind_code(cid: int, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    if not db.get(Caregiver, cid): raise HTTPException(404)
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    db.add(BindCode(code=code, caregiver_id=cid, expires_at=utcnow() + timedelta(minutes=30)))
    audit(db, u.username, "bindcode_create", str(cid)); db.commit()
    return {"code": code, "expires_minutes": 30}


@router.post("/caregivers/{cid}/unbind")
def unbind(cid: int, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    c = db.get(Caregiver, cid)
    if not c: raise HTTPException(404)
    c.line_user_id = ""; audit(db, u.username, "line_unbind", str(cid)); db.commit()
    return cg_dict(c)


# ---------------- devices ----------------
class DeviceIn(BaseModel):
    dev_eui: str
    label: str = ""
    lifecycle: str = "ready"
    patient_id: int | None = None


@router.get("/devices")
def devices(db: Session = Depends(get_db), u=Depends(current_user)):
    off = settings_store.get(db, "offline")["device_offline_min"]
    now = utcnow(); out = []
    for d in db.query(Device).order_by(Device.id).all():
        ls = aware(d.last_seen)
        offline = d.lifecycle == "active" and (ls is None or (now - ls).total_seconds() > off * 60)
        pending = (d.reported_config_ver is not None and d.reported_config_ver != (d.config_ver & 0xFF)) \
            or (d.reported_config_ver is None and bool(d.config_override))
        out.append({"config": settings_store.effective_device_cfg(db, d), "config_override": d.config_override or {},
                    "config_pending": pending, "config_ver": d.config_ver, "config_reported_ver": d.reported_config_ver,
                    "config_applied_at": iso(d.config_applied_at), "config_changed_at": iso(d.config_changed_at),
                    "id": d.id, "dev_eui": d.dev_eui, "label": d.label, "lifecycle": d.lifecycle,
                    "patient_id": d.patient_id, "patient": d.patient.full_name if d.patient else None,
                    "fw": d.fw_version, "battery": d.battery, "mains_ok": d.mains_ok, "rssi": d.last_rssi,
                    "snr": d.last_snr, "last_seen": iso(d.last_seen), "offline": offline})
    return out


@router.post("/devices")
def add_device(b: DeviceIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    eui = b.dev_eui.lower().replace(":", "")
    if not EUI_RE.match(eui): raise HTTPException(422, "DevEUI must be 16 hex chars")
    if db.query(Device).filter_by(dev_eui=eui).first(): raise HTTPException(409, "already exists")
    d = Device(dev_eui=eui, label=b.label, lifecycle=b.lifecycle, patient_id=b.patient_id)
    db.add(d); audit(db, u.username, "device_create", eui); db.commit()
    return {"id": d.id, "dev_eui": eui}


@router.put("/devices/{did}")
def edit_device(did: int, b: DeviceIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    d = db.get(Device, did)
    if not d: raise HTTPException(404)
    if b.lifecycle not in ("ready", "active", "repair", "retired", "lost"): raise HTTPException(422, "bad lifecycle")
    if b.patient_id:
        other = db.query(Device).filter(Device.patient_id == b.patient_id, Device.id != did,
                                        Device.lifecycle == "active").first()
        if other and b.lifecycle == "active": raise HTTPException(409, "patient already has an active device")
    d.label, d.lifecycle, d.patient_id = b.label, b.lifecycle, b.patient_id
    audit(db, u.username, "device_update", d.dev_eui, lifecycle=b.lifecycle, patient=b.patient_id); db.commit()
    return {"ok": True}


class DeviceConfigIn(BaseModel):
    override: dict | None = None      # {} or None = back to the shared defaults


def bump_ver(d: Device) -> None:
    d.config_ver = (d.config_ver % 255) + 1
    d.config_changed_at = utcnow()


@router.put("/devices/{did}/config")
def set_device_config(did: int, b: DeviceConfigIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    d = db.get(Device, did)
    if not d: raise HTTPException(404)
    off = settings_store.get(db, "offline")["device_offline_min"]
    try:
        ov = settings_store.validate_device_cfg(b.override or {}, partial=True, offline_min=off)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if (d.config_override or {}) == ov:
        return {"ok": True, "changed": False}
    d.config_override = ov or None
    bump_ver(d)
    audit(db, u.username, "device_config", d.dev_eui, override=ov, config_ver=d.config_ver); db.commit()
    return {"ok": True, "changed": True, "config_ver": d.config_ver,
            "effective": settings_store.effective_device_cfg(db, d)}


@router.get("/gateways")
def gateways(db: Session = Depends(get_db), u=Depends(current_user)):
    off = settings_store.get(db, "offline")["gateway_offline_min"]
    now = utcnow()
    return [{"gateway_id": g.gateway_id, "label": g.label, "last_seen": iso(g.last_seen),
             "offline": g.last_seen is None or (now - aware(g.last_seen)).total_seconds() > off * 60}
            for g in db.query(Gateway).all()]


# ---------------- settings / holidays / users / audit ----------------
@router.get("/settings")
def get_settings(db: Session = Depends(get_db), u=Depends(current_user)):
    return {k: settings_store.get(db, k) for k in settings_store.DEFAULTS}


@router.put("/settings/{key}")
def put_setting(key: str, value: dict, db: Session = Depends(get_db), u=Depends(admin_only)):
    if key not in settings_store.DEFAULTS: raise HTTPException(404)
    if key == "office_hours":
        if not re.match(r"^\d\d:\d\d$", str(value.get("start", ""))) or not re.match(r"^\d\d:\d\d$", str(value.get("end", ""))):
            raise HTTPException(422, "start/end must be HH:MM")
    if key == "device_defaults":
        try:
            value = settings_store.validate_device_cfg(
                value, partial=False, offline_min=settings_store.get(db, "offline")["device_offline_min"])
        except ValueError as e:
            raise HTTPException(422, str(e))
        # bump desired config version on all devices so they pick it up on next heartbeat
        for d in db.query(Device).all(): bump_ver(d)
    res = settings_store.put(db, key, value, u.username)
    audit(db, u.username, "setting_update", key, value=value); db.commit()
    return res


class HolidayIn(BaseModel):
    day: str = Field(pattern=r"^\d{4}-\d\d-\d\d$")
    name: str = ""


@router.get("/holidays")
def holidays(db: Session = Depends(get_db), u=Depends(current_user)):
    return [{"id": h.id, "day": h.day, "name": h.name} for h in db.query(Holiday).order_by(Holiday.day)]


@router.post("/holidays")
def add_holiday(b: HolidayIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    if db.query(Holiday).filter_by(day=b.day).first(): raise HTTPException(409)
    db.add(Holiday(day=b.day, name=b.name)); audit(db, u.username, "holiday_add", b.day); db.commit()
    return {"ok": True}


@router.delete("/holidays/{hid}")
def del_holiday(hid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    h = db.get(Holiday, hid)
    if h: db.delete(h); audit(db, u.username, "holiday_del", str(hid)); db.commit()
    return {"ok": True}


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=10)
    display_name: str = ""
    role: str = "staff"


@router.get("/users")
def users(db: Session = Depends(get_db), u=Depends(admin_only)):
    return [{"id": x.id, "username": x.username, "role": x.role, "display_name": x.display_name, "active": x.active}
            for x in db.query(User).all()]


@router.post("/users")
def add_user(b: UserIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    from .auth import hash_pw
    if b.role not in ("admin", "staff", "viewer"): raise HTTPException(422, "bad role")
    if db.query(User).filter_by(username=b.username).first(): raise HTTPException(409)
    db.add(User(username=b.username, password_hash=hash_pw(b.password), display_name=b.display_name, role=b.role))
    audit(db, u.username, "user_create", b.username, role=b.role); db.commit()
    return {"ok": True}


@router.get("/audit")
def audit_log(limit: int = 200, db: Session = Depends(get_db), u=Depends(admin_only)):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000)).all()
    return [{"at": iso(r.at), "actor": r.actor, "action": r.action, "target": r.target, "detail": r.detail} for r in rows]


@router.get("/stats")
def stats(db: Session = Depends(get_db), u=Depends(current_user)):
    since = utcnow() - timedelta(days=30)
    cs = db.query(Case).filter(Case.created_at >= since, Case.is_test.is_(False)).all()
    acc = [(aware(c.accepted_at) - aware(c.created_at)).total_seconds() for c in cs if c.accepted_at]
    return {"cases_30d": len(cs), "open": sum(c.state == "new" for c in cs),
            "false_alarm": sum(c.close_reason == "false_alarm" for c in cs),
            "after_hours": sum(c.after_hours for c in cs),
            "avg_accept_s": round(sum(acc) / len(acc), 1) if acc else None}


# ---------------- LINE webhook ----------------
@router.post("/line/webhook")
async def line_webhook(request: Request, db: Session = Depends(get_db), eng=Depends(get_engine)):
    from .line import verify_signature
    body = await request.body()
    if not verify_signature(body, request.headers.get("x-line-signature", "")):
        raise HTTPException(401, "bad signature")
    notifier = eng.notifier
    for ev in (await request.json()).get("events", []):
        uid = (ev.get("source") or {}).get("userId", "")
        tok = ev.get("replyToken", "")
        reply = (lambda t: notifier.reply_text(tok, t)) if notifier and tok else (lambda t: None)
        if ev.get("type") == "message" and (ev.get("message") or {}).get("type") == "text":
            code = ev["message"]["text"].strip().upper()
            bc = db.query(BindCode).filter_by(code=code, used=False).first()
            if bc and aware(bc.expires_at) > utcnow():
                cg = db.get(Caregiver, bc.caregiver_id)
                cg.line_user_id, bc.used = uid, True
                audit(db, "line", "line_bind", str(cg.id)); db.commit()
                reply(f"ผูกบัญชีสำเร็จ: {cg.full_name}")
            else:
                reply("รหัสไม่ถูกต้องหรือหมดอายุ")
        elif ev.get("type") == "postback":
            data = (ev.get("postback") or {}).get("data", "")
            if data.startswith("accept:"):
                cg = db.query(Caregiver).filter_by(line_user_id=uid, active=True).first() if uid else None
                if not cg:
                    reply("ไม่พบบัญชีของคุณในระบบ"); continue
                cid = int(data.split(":")[1])
                case = db.get(Case, cid)
                if case and case.state == "new":
                    eng.accept(db, cid, f"{cg.role} {cg.full_name}")
                    audit(db, f"line:{cg.id}", "case_accept", str(cid)); db.commit()
                    reply("รับเรื่องแล้ว กรุณาเดินทางไปดูผู้ป่วย")
                elif case:
                    reply(f"เคสนี้ถูกรับโดย {case.accepted_by or 'ผู้อื่น'} แล้ว" if case.state == "accepted" else "เคสนี้ปิดแล้ว")
    return {"ok": True}


# ---------------- provisioning (web page, Web Serial) ----------------
class ProvStart(BaseModel):
    label: str = Field(min_length=1, max_length=64)


class ProvConfirm(BaseModel):
    info: str = Field(max_length=400)


@router.get("/provision/status")
def prov_status(u=Depends(admin_only)):
    return {"configured": prov.get_cs().configured}


@router.post("/provision/start")
def prov_start(b: ProvStart, db: Session = Depends(get_db), u=Depends(admin_only)):
    cs = prov.get_cs()
    if not cs.configured: raise HTTPException(503, "ChirpStack provisioning is not configured (CS API key / profile id)")
    label = b.label.strip()
    if not label: raise HTTPException(422, "label required")
    if db.query(Device).filter_by(label=label).first(): raise HTTPException(409, "label already used")
    eui, key = prov.gen_identity()
    try:
        cs.register(eui, key, label, "provisioned by " + u.username)
    except prov.ProvisionError as e:
        raise HTTPException(502, str(e))
    try:
        db.add(Device(dev_eui=eui, label=label, lifecycle="ready")); audit(db, u.username, "provision_start", eui, label=label)
        db.commit()
    except Exception:
        db.rollback()
        try: cs.delete(eui)
        except Exception: pass
        raise HTTPException(500, "could not save device")
    return {"dev_eui": eui, "app_key": key, "join_eui": "0000000000000000", "label": label}


def _fresh_device(db, eui):
    d = db.query(Device).filter_by(dev_eui=eui.lower()).first()
    if not d: raise HTTPException(404)
    return d


@router.post("/provision/{eui}/confirm")
def prov_confirm(eui: str, b: ProvConfirm, db: Session = Depends(get_db), u=Depends(admin_only)):
    d = _fresh_device(db, eui)
    if f"deveui={d.dev_eui}" not in b.info.lower() or "provisioned=1" not in b.info:
        raise HTTPException(422, "device readback does not match")
    audit(db, u.username, "provision_confirmed", d.dev_eui, info=b.info[:200]); db.commit()
    return {"ok": True, "dev_eui": d.dev_eui, "label": d.label}


@router.post("/provision/{eui}/abort")
def prov_abort(eui: str, db: Session = Depends(get_db), u=Depends(admin_only)):
    d = _fresh_device(db, eui)
    if d.lifecycle != "ready" or d.last_seen is not None or d.patient_id:
        raise HTTPException(409, "device already in use; retire it instead")
    try: prov.get_cs().delete(d.dev_eui)
    except Exception as e: audit(db, u.username, "provision_abort_cs_failed", d.dev_eui, err=str(e)[:100])
    audit(db, u.username, "provision_abort", d.dev_eui); db.delete(d); db.commit()
    return {"ok": True}


@router.get("/provision/{eui}/label.svg")
def prov_label(eui: str, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    """QR (DevEUI) as SVG for the sticker on the box/device. Contains no secrets."""
    import io
    import qrcode
    import qrcode.image.svg
    from fastapi.responses import Response
    d = _fresh_device(db, eui)
    img = qrcode.make(d.dev_eui, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO(); img.save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml")

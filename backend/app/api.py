from __future__ import annotations
import re, secrets
import httpx
from datetime import timedelta, datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload
from . import settings_store
from .auth import (admin_only, audit, check_pw, current_user, hash_pw, make_token, password_problem, staff_or_admin,
                   make_mfa_ticket, user_from_mfa_ticket)
from . import mfa
from .db import get_db
from .engine import aware
from .models import (Assignment, AuditLog, BindCode, Case, Caregiver, Device, Gateway, GatewayCommand, Holiday, Notification,
                     Patient, User, utcnow)
from .config import env
from . import gateways as gwmod
from . import gwcmd
from . import linecfg
from . import geo
from . import privacy, reports
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
    if u.totp_secret:
        # password OK, second step pending — failed_logins is NOT reset here, so the code step shares the lockout
        db.commit()
        return {"mfa_required": True, "ticket": make_mfa_ticket(u), "name": u.display_name or u.username}
    u.failed_logins, u.locked_until, u.last_login_at = 0, None, now
    audit(db, u.username, "login"); db.commit()
    return {"token": make_token(u), "role": u.role, "name": u.display_name or u.username,
            "must_change": bool(u.must_change), "mfa_setup": mfa.required(db, u)}


class MfaLoginIn(BaseModel):
    ticket: str = Field(max_length=2048)
    code: str = Field(max_length=32)


@router.post("/login/2fa")
def login_2fa(body: MfaLoginIn, db: Session = Depends(get_db)):
    u = user_from_mfa_ticket(db, body.ticket)
    now = utcnow()
    if u.locked_until and aware(u.locked_until) > now:
        raise HTTPException(429, "account locked, try later")
    step = mfa.verify_totp(u.totp_secret, body.code, u.totp_last_step)
    used_backup = step is None and mfa.use_backup(u, body.code)
    if step is None and not used_backup:
        u.failed_logins += 1
        if u.failed_logins >= 5:
            u.locked_until, u.failed_logins = now + timedelta(minutes=15), 0
        audit(db, u.username, "login_failed", step="2fa"); db.commit()
        raise HTTPException(401, "รหัสยืนยันไม่ถูกต้อง")
    if step is not None:
        u.totp_last_step = step
    u.failed_logins, u.locked_until, u.last_login_at = 0, None, now
    audit(db, u.username, "login", method="backup_code" if used_backup else "totp",
          **({"backup_codes_left": len(u.backup_codes or [])} if used_backup else {}))
    db.commit()
    return {"token": make_token(u, mfa=True), "role": u.role, "name": u.display_name or u.username,
            "must_change": bool(u.must_change), "backup_codes_left": len(u.backup_codes or [])}


@router.get("/me")
def me(db: Session = Depends(get_db), u: User = Depends(current_user)):
    return {"username": u.username, "role": u.role, "name": u.display_name, "must_change": bool(u.must_change),
            "mfa": {"enabled": mfa.enabled(u), "required": mfa.required(db, u), "backup_left": len(u.backup_codes or []),
                    "enabled_at": iso(u.totp_enabled_at)}}


@router.post("/me/2fa/setup")
def mfa_setup(db: Session = Depends(get_db), u: User = Depends(current_user)):
    if u.totp_secret: raise HTTPException(409, "เปิดใช้งานอยู่แล้ว")
    u.totp_pending = mfa.new_secret(); db.commit()
    link = mfa.uri(u, u.totp_pending)
    return {"secret": u.totp_pending, "uri": link, "qr_svg": mfa.qr_svg(link)}


class MfaCodeIn(BaseModel):
    code: str = Field(max_length=32)
    password: str | None = Field(default=None, max_length=128)


@router.post("/me/2fa/enable")
def mfa_enable(b: MfaCodeIn, db: Session = Depends(get_db), u: User = Depends(current_user)):
    if u.totp_secret: raise HTTPException(409, "เปิดใช้งานอยู่แล้ว")
    if not u.totp_pending: raise HTTPException(409, "เริ่มตั้งค่าใหม่อีกครั้ง")
    step = mfa.verify_totp(u.totp_pending, b.code, None)
    if step is None: raise HTTPException(422, "รหัสไม่ถูกต้อง — ตรวจว่าเวลาในมือถือตรง แล้วลองรหัสล่าสุด")
    plain, hashed = mfa.new_backup_codes()
    u.totp_secret, u.totp_pending, u.totp_last_step, u.backup_codes, u.totp_enabled_at = u.totp_pending, None, step, hashed, utcnow()
    u.token_ver = (u.token_ver or 0) + 1                 # other sessions (without 2FA) are logged out
    audit(db, u.username, "2fa_enable"); db.commit()
    return {"ok": True, "backup_codes": plain, "token": make_token(u, mfa=True)}


def _check_code(db: Session, u: User, code: str) -> None:
    step = mfa.verify_totp(u.totp_secret, code, u.totp_last_step)
    if step is None and not mfa.use_backup(u, code):
        audit(db, u.username, "2fa_check_failed"); db.commit()
        raise HTTPException(403, "รหัสยืนยันไม่ถูกต้อง")
    if step is not None: u.totp_last_step = step


@router.post("/me/2fa/disable")
def mfa_disable(b: MfaCodeIn, db: Session = Depends(get_db), u: User = Depends(current_user)):
    if not u.totp_secret: raise HTTPException(409, "ยังไม่ได้เปิดใช้งาน")
    if mfa.required(db, u): raise HTTPException(409, "นโยบายของระบบบังคับใช้การยืนยัน 2 ขั้นสำหรับบัญชีนี้ — ปิดเองไม่ได้")
    if not b.password or not check_pw(b.password, u.password_hash): raise HTTPException(403, "รหัสผ่านไม่ถูกต้อง")
    _check_code(db, u, b.code)
    mfa.clear(u); u.token_ver = (u.token_ver or 0) + 1
    audit(db, u.username, "2fa_disable"); db.commit()
    return {"ok": True, "token": make_token(u)}


@router.post("/me/2fa/backup-codes")
def mfa_new_backup(b: MfaCodeIn, db: Session = Depends(get_db), u: User = Depends(current_user)):
    if not u.totp_secret: raise HTTPException(409, "ยังไม่ได้เปิดใช้งาน")
    _check_code(db, u, b.code)
    plain, u.backup_codes = mfa.new_backup_codes()
    audit(db, u.username, "2fa_backup_codes_regenerated"); db.commit()
    return {"backup_codes": plain}


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(max_length=128)


@router.post("/me/password")
def change_my_password(b: PasswordChangeIn, db: Session = Depends(get_db), u: User = Depends(current_user)):
    if not check_pw(b.current_password, u.password_hash):
        audit(db, u.username, "password_change_failed"); db.commit()
        raise HTTPException(403, "รหัสผ่านปัจจุบันไม่ถูกต้อง")
    bad = password_problem(b.new_password, u.username)
    if bad: raise HTTPException(422, bad)
    if b.new_password == b.current_password: raise HTTPException(422, "รหัสผ่านใหม่ต้องไม่ซ้ำรหัสเดิม")
    u.password_hash, u.must_change, u.password_changed_at = hash_pw(b.new_password), False, utcnow()
    u.token_ver = (u.token_ver or 0) + 1                 # every other session of this user is logged out
    audit(db, u.username, "password_change"); db.commit()
    return {"ok": True, "token": make_token(u)}


# ---------------- cases ----------------
def case_dict(c: Case, events=False, role: str = "staff"):
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
        if role == "viewer": d["patient"] = privacy.mask_patient(d["patient"])
    d["loc"] = None
    if c.loc_lat is not None and role != "viewer":
        home = round(geo.distance_m(c.patient.lat, c.patient.lon, c.loc_lat, c.loc_lon)) if c.patient and c.patient.lat is not None else None
        d["loc"] = {"lat": c.loc_lat, "lon": c.loc_lon, "at": iso(c.loc_at), "sats": c.loc_sats, "hdop": c.loc_hdop,
                    "home_distance_m": home, "url": geo.maps_url(c.loc_lat, c.loc_lon)}
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
    return [case_dict(c, role=u.role) for c in q.order_by(Case.id.desc()).limit(min(limit, 500)).all()]


@router.get("/cases/{cid}")
def get_case(cid: int, db: Session = Depends(get_db), u=Depends(current_user)):
    c = db.get(Case, cid)
    if not c: raise HTTPException(404)
    return case_dict(c, events=True, role=u.role)


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


def pat_dict(p: Patient, db: Session, role: str = "staff"):
    asg = db.query(Assignment).filter_by(patient_id=p.id).order_by(Assignment.priority).all()
    dev = db.query(Device).filter_by(patient_id=p.id).first()
    out = {"id": p.id, "full_name": p.full_name, "age": p.age, "address": p.address, "village": p.village,
            "lat": p.lat, "lon": p.lon, "phone": p.phone, "notes": p.notes, "active": p.active,
            "device": dev.dev_eui if dev else None,
            "consent_ok": privacy.consent_ok(p), "consent_at": iso(p.consent_at), "consent_by": p.consent_by,
            "consent_note": p.consent_note, "consent_withdrawn_at": iso(p.consent_withdrawn_at),
            "anonymized_at": iso(p.anonymized_at),
            "assignments": [{"priority": a.priority, "caregiver_id": a.caregiver_id,
                             "name": a.caregiver.full_name} for a in asg]}
    return privacy.mask_patient(out) if role == "viewer" else out


@router.get("/patients")
def patients(db: Session = Depends(get_db), u=Depends(current_user)):
    return [pat_dict(p, db, u.role) for p in db.query(Patient).order_by(Patient.id).all()]


@router.post("/patients")
def add_patient(b: PatientIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    p = Patient(**b.model_dump()); db.add(p); db.flush()
    audit(db, u.username, "patient_create", str(p.id)); db.commit()
    return pat_dict(p, db)


@router.put("/patients/{pid}")
def edit_patient(pid: int, b: PatientIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    p = db.get(Patient, pid)
    if not p: raise HTTPException(404)
    if p.anonymized_at: raise HTTPException(409, "ข้อมูลผู้ป่วยรายนี้ถูกลบ (ทำให้ไม่ระบุตัวตน) แล้ว แก้ไขไม่ได้")
    if b.active and p.consent_withdrawn_at: raise HTTPException(409, "ผู้ป่วยถอนความยินยอมแล้ว — ต้องบันทึกความยินยอมใหม่ก่อนเปิดใช้งาน")
    for k, v in b.model_dump().items(): setattr(p, k, v)
    audit(db, u.username, "patient_update", str(pid)); db.commit()
    return pat_dict(p, db)


@router.delete("/patients/{pid}")
def delete_patient(pid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    p = db.get(Patient, pid)
    if not p: raise HTTPException(404)
    if db.query(Device).filter_by(patient_id=pid).first():
        raise HTTPException(409, "ผู้ป่วยรายนี้ยังมีอุปกรณ์ผูกอยู่ — ถอดอุปกรณ์ออกก่อน")
    if db.query(Case).filter_by(patient_id=pid).first():
        raise HTTPException(409, "ผู้ป่วยรายนี้มีประวัติเคสแล้ว ลบทิ้งตรง ๆ ไม่ได้ — ใช้ 'ข้อมูล/PDPA → ลบข้อมูลที่ระบุตัวตน' หรือปิดใช้งานแทน")
    db.query(Assignment).filter_by(patient_id=pid).delete()
    db.delete(p); audit(db, u.username, "patient_delete", str(pid)); db.commit()
    return {"ok": True}


class ConsentIn(BaseModel):
    given: bool
    note: str = Field(default="", max_length=128)      # e.g. consent form no., or "ผู้ดูแล: นางสมหญิง (บุตร)" when a guardian consents


@router.put("/patients/{pid}/consent")
def set_consent(pid: int, b: ConsentIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    p = db.get(Patient, pid)
    if not p: raise HTTPException(404)
    if p.anonymized_at: raise HTTPException(409, "ข้อมูลผู้ป่วยรายนี้ถูกลบแล้ว")
    if b.given:
        p.consent_at, p.consent_by, p.consent_note, p.consent_withdrawn_at = utcnow(), u.username, b.note.strip(), None
        audit(db, u.username, "consent_given", str(pid), note=b.note.strip())
        res = {}
    else:
        res = privacy.withdraw(db, p, u.username)
        audit(db, u.username, "consent_withdrawn", str(pid), **res)
    db.commit()
    return {"patient": pat_dict(p, db), **res}


@router.get("/patients/{pid}/export")
def export_patient(pid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    from fastapi.responses import JSONResponse
    p = db.get(Patient, pid)
    if not p: raise HTTPException(404)
    audit(db, u.username, "patient_export", str(pid)); db.commit()
    return JSONResponse(privacy.export_patient(db, p),
                        headers={"Content-Disposition": f'attachment; filename="patient-{pid}-export.json"'})


class AnonymizeIn(BaseModel):
    confirm_id: int                  # must equal the patient id — guards against clicking the wrong row
    keep_village: bool = True


@router.post("/patients/{pid}/anonymize")
def anonymize_patient(pid: int, b: AnonymizeIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    p = db.get(Patient, pid)
    if not p: raise HTTPException(404)
    if b.confirm_id != pid: raise HTTPException(422, "confirm_id must equal the patient id")
    if p.anonymized_at: raise HTTPException(409, "already anonymised")
    open_case = db.query(Case).filter(Case.patient_id == pid, Case.state.in_(("new", "accepted"))).first()
    if open_case: raise HTTPException(409, f"มีเคส #{open_case.id} ที่ยังเปิดอยู่ — ปิดเคสก่อน")
    res = privacy.anonymize(db, p, b.keep_village)
    audit(db, u.username, "patient_anonymize", str(pid), **res); db.commit()
    return {"ok": True, **res}


@router.get("/privacy/overview")
def privacy_overview(db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    return privacy.overview(db)


class RetentionIn(BaseModel):
    dry_run: bool = True


@router.post("/privacy/retention")
def privacy_retention(b: RetentionIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    res = privacy.retention(db, dry_run=b.dry_run)
    audit(db, u.username, "retention_preview" if b.dry_run else "retention_run", "", **{k: v for k, v in res.items() if isinstance(v, int)})
    db.commit()
    return res


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


@router.delete("/caregivers/{cid}")
def delete_cg(cid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    c = db.get(Caregiver, cid)
    if not c: raise HTTPException(404)
    if db.query(Notification).filter_by(caregiver_id=cid).first():
        raise HTTPException(409, "ผู้ดูแลคนนี้มีประวัติการแจ้งเตือนในเคสแล้ว ลบไม่ได้ (เพื่อรักษาประวัติ) — ใช้ปุ่ม 'ปิดใช้งาน' แทน")
    db.query(Assignment).filter_by(caregiver_id=cid).delete()
    db.query(BindCode).filter_by(caregiver_id=cid).delete()
    for g in db.query(Gateway).all():                     # drop him from per-gateway recipient lists too
        if g.alert_ids and cid in g.alert_ids: g.alert_ids = [x for x in g.alert_ids if x != cid] or None
    name = c.full_name; db.delete(c)
    audit(db, u.username, "caregiver_delete", str(cid), name=name); db.commit()
    return {"ok": True}


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
    gcfg = settings_store.get(db, "geo")
    now = utcnow(); out = []
    for d in db.query(Device).order_by(Device.id).all():
        ls = aware(d.last_seen)
        offline = d.lifecycle == "active" and (ls is None or (now - ls).total_seconds() > off * 60)
        pending = (d.reported_config_ver is not None and d.reported_config_ver != (d.config_ver & 0xFF)) \
            or (d.reported_config_ver is None and bool(d.config_override))
        loc = None
        if d.loc_lat is not None and u.role != "viewer":
            home = (round(geo.distance_m(d.patient.lat, d.patient.lon, d.loc_lat, d.loc_lon))
                    if d.patient and d.patient.lat is not None and d.patient.lon is not None else None)
            fresh = (now - aware(d.loc_at)).total_seconds() <= gcfg["gps_fresh_min"] * 60
            loc = {"lat": d.loc_lat, "lon": d.loc_lon, "at": iso(d.loc_at), "sats": d.loc_sats, "hdop": d.loc_hdop, "fresh": fresh,
                   "home_distance_m": home, "away": home is not None and home > gcfg["home_radius_m"],
                   "url": geo.maps_url(d.loc_lat, d.loc_lon)}
        out.append({"loc": loc, "config": settings_store.effective_device_cfg(db, d), "config_override": d.config_override or {},
                    "config_pending": pending, "config_ver": d.config_ver, "config_reported_ver": d.reported_config_ver,
                    "config_applied_at": iso(d.config_applied_at), "config_changed_at": iso(d.config_changed_at),
                    "id": d.id, "dev_eui": d.dev_eui, "label": d.label, "lifecycle": d.lifecycle,
                    "patient_id": d.patient_id, "patient": d.patient.full_name if d.patient else None,
                    "fw": d.fw_version, "battery": d.battery, "mains_ok": d.mains_ok, "rssi": d.last_rssi,
                    "snr": d.last_snr, "last_seen": iso(d.last_seen), "offline": offline})
    return out


def _require_consent(db: Session, lifecycle: str, patient_id: int | None):
    """PDPA: do not put a button into service for someone whose consent is not on record."""
    if lifecycle != "active" or not patient_id or not settings_store.get(db, "privacy")["require_consent"]:
        return
    p = db.get(Patient, patient_id)
    if p is None: raise HTTPException(422, "patient not found")
    if p.anonymized_at: raise HTTPException(409, "ผู้ป่วยรายนี้ถูกลบข้อมูลแล้ว")
    if not privacy.consent_ok(p):
        raise HTTPException(409, "ผู้ป่วยยังไม่มีบันทึกความยินยอม (PDPA) — บันทึกความยินยอมที่หน้าผู้ป่วยก่อนตั้งเป็น active")


@router.post("/devices")
def add_device(b: DeviceIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    eui = b.dev_eui.lower().replace(":", "")
    if not EUI_RE.match(eui): raise HTTPException(422, "DevEUI must be 16 hex chars")
    if db.query(Device).filter_by(dev_eui=eui).first(): raise HTTPException(409, "already exists")
    _require_consent(db, b.lifecycle, b.patient_id)
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
    _require_consent(db, b.lifecycle, b.patient_id)
    if d.patient_id != b.patient_id:                      # a fix from the old patient's home says nothing about the new one
        d.loc_lat = d.loc_lon = d.loc_at = d.loc_sats = d.loc_hdop = None
    was_retired = d.lifecycle == "retired"
    d.label, d.lifecycle, d.patient_id = b.label, b.lifecycle, b.patient_id
    audit(db, u.username, "device_update", d.dev_eui, lifecycle=b.lifecycle, patient=b.patient_id); db.commit()
    cs_res = "unchanged"
    if was_retired != (b.lifecycle == "retired"):
        cs_res = _cs_device_disabled(db, u.username, d.dev_eui, b.lifecycle == "retired")
    return {"ok": True, "chirpstack": cs_res}


def _cs_device_disabled(db: Session, who: str, eui: str, disabled: bool) -> str:
    """A retired button is disabled in ChirpStack (its uplinks are dropped there); un-retiring enables it again."""
    cs = prov.get_cs()
    if not cs.configured: return "not_configured"
    try:
        changed = cs.set_disabled(eui, disabled)
    except Exception as e:
        audit(db, who, "chirpstack_device_failed", eui, err=str(e)[:150]); db.commit()
        return "error: " + str(e)[:150]
    if changed:
        audit(db, who, "chirpstack_device_disabled" if disabled else "chirpstack_device_enabled", eui); db.commit()
    return "disabled" if disabled else "enabled"


@router.delete("/devices/{did}")
def delete_device(did: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    d = db.get(Device, did)
    if not d: raise HTTPException(404)
    if db.query(Case).filter_by(device_id=did).first():
        raise HTTPException(409, "อุปกรณ์นี้มีประวัติเคสแล้ว ลบไม่ได้ — เปลี่ยนสถานะเป็น 'ปลดระวาง/สูญหาย' แทน")
    eui = d.dev_eui; db.delete(d); audit(db, u.username, "device_delete", eui); db.commit()
    return {"ok": True, "chirpstack": _cs_remove(db, u.username, "device", eui)}


def _cs_remove(db: Session, who: str, kind: str, ident: str) -> str:
    """Best effort: also remove the device/gateway from ChirpStack. Never fails the delete here (the row is already gone)."""
    cs = prov.get_cs()
    if not cs.configured: return "not_configured"
    try:
        gone = cs.remove_device(ident) if kind == "device" else cs.remove_gateway(ident)
    except Exception as e:
        audit(db, who, f"chirpstack_{kind}_delete_failed", ident, err=str(e)[:150]); db.commit()
        return "error: " + str(e)[:150]
    if gone: audit(db, who, f"chirpstack_{kind}_delete", ident); db.commit()
    return "deleted" if gone else "absent"


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


def gw_dict(g: Gateway, now, off: int, n_dev: dict, move_m: int = 200, role: str = "staff") -> dict:
    st, reason = gwmod.classify(g, now, g.offline_min or off, move_m)
    crit, warn = gwmod.critical(g.report), gwmod.warnings(g.report)
    mv = gwmod.moved_m(g, now, move_m)
    if mv is not None: warn = warn + [f"ตำแหน่ง GPS ห่างจากที่ลงทะเบียน {mv} เมตร (เกตเวย์อาจถูกย้ายหรือถูกนำออกไป)"]
    gps_fresh = bool(g.gps_at and (now - aware(g.gps_at)).total_seconds() <= gwmod.GPS_FRESH_S)
    rep = dict(g.report or {})
    loc = {"registered": {"lat": g.lat, "lon": g.lon} if g.lat is not None else None,
           "gps": {"lat": g.gps_lat, "lon": g.gps_lon, "at": iso(g.gps_at), "sats": g.gps_sats, "fresh": gps_fresh} if g.gps_lat is not None else None,
           "gps_attached": "gps" in rep, "moved_m": mv,
           "url": geo.maps_url(g.lat, g.lon) if g.lat is not None else None}
    return {"id": g.id, "gateway_id": g.gateway_id, "label": g.label, "enabled": g.enabled is not False,
            "status": st, "offline": st == "offline", "reason": reason or (g.offline_reason if st == "offline" else ""),
            "last_seen": iso(g.last_seen), "last_report_at": iso(g.last_report_at),
            "state_changed_at": iso(g.state_changed_at), "uplinks_total": g.uplinks_total or 0,
            "devices": n_dev.get(g.gateway_id, 0), "report": g.report or {}, "critical": crit, "warnings": warn, "loc": loc,
            "notes": g.notes or "", "offline_min": g.offline_min, "offline_min_effective": g.offline_min or off,
            "alert_ids": g.alert_ids, "agent": rep.get("agent") is True}


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


@router.put("/patients/{pid}/location")
def set_patient_location(pid: int, b: LocationIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    """Pin a patient's home on the map (only the coordinates change)."""
    p = db.get(Patient, pid)
    if not p: raise HTTPException(404)
    if p.anonymized_at: raise HTTPException(409, "ข้อมูลผู้ป่วยรายนี้ถูกลบ (ทำให้ไม่ระบุตัวตน) แล้ว แก้ไขไม่ได้")
    p.lat, p.lon = round(b.lat, 6), round(b.lon, 6)
    audit(db, u.username, "patient_location", str(pid)); db.commit()
    return {"id": p.id, "lat": p.lat, "lon": p.lon}


@router.get("/gateways")
def gateways(db: Session = Depends(get_db), u=Depends(current_user)):
    off = settings_store.get(db, "offline")["gateway_offline_min"]
    now = utcnow()
    n_dev: dict[str, int] = {}
    for d in db.query(Device).filter(Device.lifecycle == "active").all():
        if d.last_gateway:
            n_dev[d.last_gateway] = n_dev.get(d.last_gateway, 0) + 1
    mv = settings_store.get(db, "geo")["gateway_move_m"]
    return [gw_dict(g, now, off, n_dev, mv, u.role) for g in db.query(Gateway).order_by(Gateway.id).all()]


def _quality(rssi, snr):
    """Rough LoRa link grade from the last uplink (SF9-ish): good / fair / weak / None when unknown."""
    if rssi is None: return None
    if rssi >= -105 and (snr is None or snr >= 0): return "good"
    if rssi >= -115 and (snr is None or snr >= -7): return "fair"
    return "weak"


@router.get("/map")
def map_data(db: Session = Depends(get_db), u=Depends(current_user)):
    """Gateways, devices and which gateway heard each device last — for the dashboard map.
    Patient/home coordinates and GPS fixes are never sent to the read-only role (PDPA); the connectivity table still is."""
    now = utcnow(); viewer = u.role == "viewer"
    goff = settings_store.get(db, "offline")["gateway_offline_min"]
    doff = settings_store.get(db, "offline")["device_offline_min"]
    gcfg = settings_store.get(db, "geo")
    n_dev: dict[str, int] = {}
    devs = db.query(Device).filter(Device.lifecycle != "retired").order_by(Device.id).all()
    for d in devs:
        if d.lifecycle == "active" and d.last_gateway: n_dev[d.last_gateway] = n_dev.get(d.last_gateway, 0) + 1
    gws, gpos = [], {}
    for g in db.query(Gateway).filter(Gateway.enabled.isnot(False)).order_by(Gateway.id).all():
        x = gw_dict(g, now, goff, n_dev, gcfg["gateway_move_m"], u.role)
        loc = x["loc"]; pos = loc["registered"] or (loc["gps"] if loc["gps"] and loc["gps"]["fresh"] else None)
        src = "registered" if loc["registered"] else ("gps" if pos else None)
        if pos: gpos[g.gateway_id] = (pos["lat"], pos["lon"])
        gws.append({"gateway_id": g.gateway_id, "label": g.label, "status": x["status"], "offline": x["offline"], "reason": x["reason"],
                    "last_seen": x["last_seen"], "devices": x["devices"], "lat": pos["lat"] if pos else None,
                    "lon": pos["lon"] if pos else None, "pos_src": src})
    dvs, links = [], []
    open_ = {c.patient_id: c for c in db.query(Case).filter(Case.state.in_(("new", "accepted")), Case.is_test.is_(False))
             .order_by(Case.id).all() if c.patient_id}
    for d in devs:
        ls = aware(d.last_seen)
        offline = d.lifecycle == "active" and (ls is None or (now - ls).total_seconds() > doff * 60)
        lat = lon = src = None; home = None
        if not viewer:
            fresh = d.loc_lat is not None and d.loc_at is not None and (now - aware(d.loc_at)).total_seconds() <= gcfg["gps_fresh_min"] * 60
            if fresh: lat, lon, src = d.loc_lat, d.loc_lon, "gps"
            elif d.patient and d.patient.lat is not None and d.patient.lon is not None: lat, lon, src = d.patient.lat, d.patient.lon, "home"
            elif d.loc_lat is not None: lat, lon, src = d.loc_lat, d.loc_lon, "gps_old"
        q = _quality(d.last_rssi, d.last_snr)
        cs = open_.get(d.patient_id) if d.patient_id else None
        dvs.append({"id": d.id, "dev_eui": d.dev_eui, "label": d.label, "lifecycle": d.lifecycle,
                    "patient": d.patient.full_name if d.patient else None, "patient_id": d.patient_id,
                    "phone": d.patient.phone if d.patient and not viewer else "", "address": (f"{d.patient.address} {d.patient.village}".strip() if d.patient and not viewer else ""),
                    "case": {"id": cs.id, "state": cs.state, "overdue": cs.overdue, "created_at": iso(cs.created_at)} if cs else None, "battery": d.battery, "mains_ok": d.mains_ok,
                    "offline": offline, "last_seen": iso(d.last_seen), "rssi": d.last_rssi, "snr": d.last_snr, "quality": q,
                    "gateway_id": d.last_gateway or None, "lat": lat, "lon": lon, "pos_src": src})
        if d.last_gateway:
            gp = gpos.get(d.last_gateway)
            dist = round(geo.distance_m(lat, lon, gp[0], gp[1])) if lat is not None and gp else None
            links.append({"device_id": d.id, "gateway_id": d.last_gateway, "rssi": d.last_rssi, "snr": d.last_snr, "quality": q,
                          "offline": offline, "distance_m": dist})
    # patients for the "pin a home" tool and homes that have no button yet (never for the read-only role)
    homes, no_loc = [], []
    if not viewer:
        with_dev = {d.patient_id for d in devs if d.patient_id}
        for p in db.query(Patient).filter(Patient.active.is_(True), Patient.anonymized_at.is_(None)).order_by(Patient.id).all():
            row = {"id": p.id, "name": p.full_name, "village": p.village, "address": p.address, "phone": p.phone, "lat": p.lat, "lon": p.lon}
            if p.lat is None: no_loc.append(row)
            elif p.id not in with_dev: homes.append(row)
    return {"gateways": gws, "devices": dvs, "links": links, "restricted": viewer, "coverage_m": gcfg["coverage_m"],
            "homes": homes, "no_location": no_loc}


class GatewayIn(BaseModel):
    gateway_id: str
    label: str = Field(default="", max_length=64)


class GatewayPatch(BaseModel):
    label: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    lat: float | None = None
    lon: float | None = None
    clear_location: bool = False        # forget the registered position
    use_gps: bool = False               # copy the gateway's own latest GPS fix into the registered position
    notes: str | None = Field(default=None, max_length=500)
    offline_min: int | None = None      # per-gateway "silent for N minutes = offline" (3-240); admin only
    reset_offline: bool = False         # back to the global value
    alert_ids: list[int] | None = None  # per-gateway LINE recipients for this gateway's alerts; admin only
    reset_alerts: bool = False          # back to the global list


@router.post("/gateways")
def add_gateway(b: GatewayIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    gid = b.gateway_id.lower().replace(":", "").strip()
    if not gwmod.GW_RE.match(gid): raise HTTPException(422, "Gateway ID must be 16 hex chars")
    if db.query(Gateway).filter_by(gateway_id=gid).first(): raise HTTPException(409, "already registered")
    g = Gateway(gateway_id=gid, label=b.label.strip(), enabled=True, state="unknown", uplinks_total=0)
    db.add(g); audit(db, u.username, "gateway_add", gid); db.commit()
    cs_res = _cs_gateway(db, u.username, g)
    return {"ok": True, "id": g.id, "chirpstack": cs_res}


def _cs_gateway(db: Session, who: str, g: Gateway) -> str:
    """Best effort: also register the gateway in ChirpStack so it shows up there (stats, live frames)."""
    cs = prov.get_cs()
    if not cs.configured: return "not_configured"
    try:
        res = cs.ensure_gateway(g.gateway_id, g.label, g.lat, g.lon)
    except Exception as e:
        audit(db, who, "chirpstack_gateway_failed", g.gateway_id, err=str(e)[:150]); db.commit()
        return "error: " + str(e)[:150]
    if res == "created":
        audit(db, who, "chirpstack_gateway_add", g.gateway_id); db.commit()
    return res


@router.delete("/gateways/{gid}")
def delete_gateway(gid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    g = db.get(Gateway, gid)
    if not g: raise HTTPException(404)
    gw = g.gateway_id
    db.query(GatewayCommand).filter_by(gateway_id=gw).delete()
    db.delete(g); audit(db, u.username, "gateway_delete", gw); db.commit()
    return {"ok": True, "chirpstack": _cs_remove(db, u.username, "gateway", gw), "note": "ถ้าเกตเวย์ยังเปิดอยู่ มันจะถูกเพิ่มกลับเองเมื่อส่งข้อมูลครั้งถัดไป — ปิดการเฝ้าระวังแทนถ้าแค่ไม่ต้องการแจ้งเตือน"}


@router.put("/gateways/{gid}")
def patch_gateway(gid: int, b: GatewayPatch, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    g = db.get(Gateway, gid)
    if not g: raise HTTPException(404)
    if b.label is not None:
        g.label = b.label.strip()
    if b.enabled is not None and b.enabled != (g.enabled is not False):
        if u.role != "admin": raise HTTPException(403, "only admin can switch monitoring on/off")
        g.enabled = b.enabled
        if not b.enabled:
            g.state, g.offline_notified_at = "unknown", None     # retired: stop alerting and forget the offline episode
    if b.use_gps:
        if g.gps_lat is None: raise HTTPException(409, "เกตเวย์นี้ยังไม่เคยส่งพิกัด GPS")
        g.lat, g.lon = g.gps_lat, g.gps_lon
    elif b.clear_location:
        g.lat = g.lon = None
    elif b.lat is not None or b.lon is not None:
        if not geo.valid_latlon(b.lat, b.lon): raise HTTPException(422, "lat/lon ไม่ถูกต้อง (ต้องระบุทั้งคู่ และไม่ใช่ 0,0)")
        g.lat, g.lon = round(b.lat, 6), round(b.lon, 6)
    if b.notes is not None:
        g.notes = b.notes.strip()
    if b.offline_min is not None or b.reset_offline or b.alert_ids is not None or b.reset_alerts:
        if u.role != "admin": raise HTTPException(403, "only admin can change alert thresholds/recipients")
        if b.reset_offline: g.offline_min = None
        elif b.offline_min is not None:
            if isinstance(b.offline_min, bool) or not 3 <= b.offline_min <= 240:
                raise HTTPException(422, "offline_min must be 3-240 (gateways report every minute)")
            g.offline_min = b.offline_min
        if b.reset_alerts: g.alert_ids = None
        elif b.alert_ids is not None:
            ids = sorted(set(b.alert_ids))
            if len(ids) > 20: raise HTTPException(422, "too many recipients")
            if ids and {c.id for c in db.query(Caregiver).filter(Caregiver.id.in_(ids)).all()} != set(ids):
                raise HTTPException(422, "unknown caregiver id")
            g.alert_ids = ids or None
    audit(db, u.username, "gateway_update", g.gateway_id, label=g.label, enabled=g.enabled is not False,
          located=g.lat is not None, offline_min=g.offline_min, own_recipients=g.alert_ids is not None)
    db.commit()
    return {"ok": True}


# ---- remote actions (signed MQTT commands to the gateway agent) ----
def _cmd_dict(c) -> dict:
    return {"id": c.id, "cmd_id": c.cmd_id, "action": c.action, "label": gwcmd.ACTIONS.get(c.action, c.action), "status": c.status,
            "detail": c.detail, "output": c.output, "by": c.requested_by, "created_at": iso(c.created_at), "done_at": iso(c.done_at)}


def _expire_commands(db: Session, gateway_id: str | None = None):
    q = db.query(GatewayCommand).filter(GatewayCommand.status == "pending")
    if gateway_id: q = q.filter(GatewayCommand.gateway_id == gateway_id)
    now = utcnow(); n = 0
    for c in q.all():
        if (now - aware(c.created_at)).total_seconds() > gwcmd.EXPIRE_S:
            c.status, c.detail, c.done_at = "expired", "ไม่มีคำตอบจากเกตเวย์ (ออฟไลน์ หรือยังไม่ได้เปิดตัวช่วยควบคุม)", now; n += 1
    if n: db.commit()


class GwCommandIn(BaseModel):
    action: str


@router.get("/gateways/{gid}/commands")
def gateway_commands(gid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    g = db.get(Gateway, gid)
    if not g: raise HTTPException(404)
    _expire_commands(db, g.gateway_id)
    rows = db.query(GatewayCommand).filter_by(gateway_id=g.gateway_id).order_by(GatewayCommand.id.desc()).limit(10).all()
    return {"actions": gwcmd.ACTIONS, "agent": (g.report or {}).get("agent") is True, "commands": [_cmd_dict(c) for c in rows]}


@router.post("/gateways/{gid}/commands")
def gateway_command(gid: int, b: GwCommandIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    g = db.get(Gateway, gid)
    if not g: raise HTTPException(404)
    if b.action not in gwcmd.ACTIONS: raise HTTPException(422, "unknown action")
    if (g.report or {}).get("agent") is not True:
        raise HTTPException(409, "เกตเวย์นี้ยังไม่ได้ติดตั้ง/เปิด sos-gateway-agent (ดูคู่มือ docs/GATEWAY-REMOTE.md)")
    _expire_commands(db, g.gateway_id)
    if db.query(GatewayCommand).filter_by(gateway_id=g.gateway_id, status="pending").first():
        raise HTTPException(409, "มีคำสั่งที่ยังรอคำตอบอยู่ รอสักครู่")
    eng = get_engine()
    if eng.gw_cmd_publish is None or not eng.mqtt_ok(): raise HTTPException(503, "ระบบยังเชื่อมต่อ MQTT ไม่ได้")
    cid, payload = gwcmd.make_command(g.gateway_id, b.action)
    c = GatewayCommand(cmd_id=cid, gateway_id=g.gateway_id, action=b.action, requested_by=u.username)
    db.add(c); db.flush()
    if not eng.gw_cmd_publish(g.gateway_id, payload):
        db.rollback(); raise HTTPException(503, "ส่งคำสั่งไม่สำเร็จ (MQTT)")
    audit(db, u.username, "gateway_command", g.gateway_id, cmd=b.action, cmd_id=cid)
    db.commit()
    return _cmd_dict(c)


@router.get("/gateways/{gid}/agent-key")
def gateway_agent_key(gid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    """The per-gateway signing key to put in /etc/sos-gateway.env on the Pi. Shown to admins only and audited."""
    g = db.get(Gateway, gid)
    if not g: raise HTTPException(404)
    audit(db, u.username, "gateway_agent_key_view", g.gateway_id); db.commit()
    return {"gateway_id": g.gateway_id, "key": gwcmd.agent_key(g.gateway_id)}


@router.post("/system-alert/test")
def system_alert_test(db: Session = Depends(get_db), u=Depends(admin_only)):
    """Send a test LINE message to the people who receive 'gateway offline' alerts."""
    mon = get_engine().gw
    if not mon.recipients(db):
        raise HTTPException(422, "ยังไม่ได้เลือกผู้รับการแจ้งเตือนระบบ (หรือผู้รับสุดท้ายใน escalation)")
    res = mon.send_system(db, "✅ ข้อความทดสอบจากระบบแจ้งเหตุฉุกเฉิน: ถ้าคุณเห็นข้อความนี้ แสดงว่าจะได้รับการแจ้งเตือนเมื่อเกตเวย์ขาดการเชื่อมต่อ")
    audit(db, u.username, "system_alert_test", "", results=res); db.commit()
    return {"results": res}


# ---------------- settings / holidays / users / audit ----------------
@router.get("/settings")
def get_settings(db: Session = Depends(get_db), u=Depends(current_user)):
    return {k: settings_store.get(db, k) for k in settings_store.DEFAULTS}


@router.put("/settings/{key}")
def put_setting(key: str, value: dict, db: Session = Depends(get_db), u=Depends(admin_only)):
    if key not in settings_store.DEFAULTS: raise HTTPException(404)
    if key == "org":
        out = {}
        for k in settings_store.DEFAULTS["org"]:
            v = value.get(k, "")
            if not isinstance(v, str) or len(v) > 160: raise HTTPException(422, f"{k} must be text (max 160)")
            out[k] = v.strip()
        value = out
    if key == "self_test":
        def _day(k, allow0):
            v = value.get(k)
            if isinstance(v, bool) or not isinstance(v, int) or not ((allow0 and v == 0) or 1 <= v <= 28):
                raise HTTPException(422, f"{k} must be 1-28" + (" or 0 (off)" if allow0 else ""))
            return v
        if not isinstance(value.get("enabled"), bool) or not isinstance(value.get("confirm_line"), bool):
            raise HTTPException(422, "enabled and confirm_line must be true/false")
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(value.get("remind_time", ""))): raise HTTPException(422, "remind_time must be HH:MM")
        d1, d2 = _day("remind_day", False), _day("second_day", True)
        if d2 and d2 <= d1: raise HTTPException(422, "second_day must be after remind_day (or 0)")
        value = {"enabled": value["enabled"], "remind_day": d1, "second_day": d2, "remind_time": value["remind_time"],
                 "confirm_line": value["confirm_line"]}
    if key == "security":
        on, roles = value.get("mfa_enabled"), value.get("mfa_roles", ["admin"])
        if not isinstance(on, bool): raise HTTPException(422, "mfa_enabled must be true/false")
        if not isinstance(roles, list) or not set(roles) <= set(ROLES) or "admin" not in roles:
            raise HTTPException(422, "mfa_roles must be a list of roles and always include admin")
        value = {"mfa_enabled": on, "mfa_roles": sorted(set(roles))}
        if on: audit(db, u.username, "2fa_policy_on", ",".join(value["mfa_roles"]))
        elif settings_store.get(db, "security").get("mfa_enabled"): audit(db, u.username, "2fa_policy_off")
    if key == "office_hours":
        if not re.match(r"^\d\d:\d\d$", str(value.get("start", ""))) or not re.match(r"^\d\d:\d\d$", str(value.get("end", ""))):
            raise HTTPException(422, "start/end must be HH:MM")
    if key == "offline":
        try:
            d_, g_ = value.get("device_offline_min"), value.get("gateway_offline_min")
            if isinstance(d_, bool) or isinstance(g_, bool) or not isinstance(d_, int) or not isinstance(g_, int): raise ValueError
        except (AttributeError, ValueError):
            raise HTTPException(422, "device_offline_min and gateway_offline_min must be integers")
        if not 3 <= g_ <= 240: raise HTTPException(422, "gateway_offline_min must be 3-240 (gateways report every minute)")
        if not 10 <= d_ <= 10080: raise HTTPException(422, "device_offline_min must be 10-10080")
        value = {"device_offline_min": d_, "gateway_offline_min": g_}
    if key == "system_alert":
        ids, rep = value.get("caregiver_ids"), value.get("repeat_min", 60)
        if not isinstance(ids, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in ids) or len(ids) > 20:
            raise HTTPException(422, "caregiver_ids must be a list of caregiver ids")
        if isinstance(rep, bool) or not isinstance(rep, int) or not (rep == 0 or 10 <= rep <= 1440):
            raise HTTPException(422, "repeat_min must be 0 (off) or 10-1440")
        found = {c.id for c in db.query(Caregiver).filter(Caregiver.id.in_(ids)).all()} if ids else set()
        if set(ids) - found: raise HTTPException(422, "unknown caregiver id")
        value = {"caregiver_ids": sorted(set(ids)), "repeat_min": rep}
    if key == "privacy":
        def _int(k, lo, hi):
            v = value.get(k)
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi: raise HTTPException(422, f"{k} must be {lo}-{hi}")
            return v
        if not isinstance(value.get("require_consent"), bool) or not isinstance(value.get("auto_purge"), bool):
            raise HTTPException(422, "require_consent and auto_purge must be true/false")
        if value.get("line_detail") not in ("full", "minimal"): raise HTTPException(422, "line_detail must be full or minimal")
        value = {"require_consent": value["require_consent"], "auto_purge": value["auto_purge"], "line_detail": value["line_detail"],
                 "case_retention_days": _int("case_retention_days", 30, 3650), "audit_retention_days": _int("audit_retention_days", 90, 3650)}
    if key == "device_alert":
        def _i(k, lo, hi):
            v = value.get(k)
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi: raise HTTPException(422, f"{k} must be {lo}-{hi}")
            return v
        if not isinstance(value.get("enabled"), bool): raise HTTPException(422, "enabled must be true/false")
        value = {"enabled": value["enabled"], "battery_low_pct": _i("battery_low_pct", 5, 50), "mains_grace_min": _i("mains_grace_min", 1, 240)}
    if key == "geo":
        out = {}
        cur = settings_store.get(db, "geo")
        for k, lo, hi in (("home_radius_m", 20, 5000), ("gps_fresh_min", 1, 1440), ("gateway_move_m", 20, 100000), ("coverage_m", 200, 30000)):
            v = value.get(k, cur[k])
            if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi: raise HTTPException(422, f"{k} must be {lo}-{hi}")
            out[k] = v
        value = out
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
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(default="", max_length=128)
    role: str = "staff"


ROLES = ("admin", "staff", "viewer")
_PW_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"    # no look-alikes (0/O, 1/l/I)


def temp_password() -> str:
    return "".join(secrets.choice(_PW_ALPHABET) for _ in range(14))


def user_dict(x: User) -> dict:
    return {"id": x.id, "username": x.username, "role": x.role, "display_name": x.display_name, "active": x.active,
            "must_change": bool(x.must_change), "locked": bool(x.locked_until and aware(x.locked_until) > utcnow()),
            "last_login_at": iso(x.last_login_at), "password_changed_at": iso(x.password_changed_at),
            "mfa": bool(x.totp_secret)}


def _other_active_admin(db: Session, uid: int) -> bool:
    return db.query(User).filter(User.role == "admin", User.active.is_(True), User.id != uid).count() > 0


@router.get("/users")
def users(db: Session = Depends(get_db), u=Depends(admin_only)):
    return [user_dict(x) for x in db.query(User).order_by(User.id).all()]


@router.post("/users")
def add_user(b: UserIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    if b.role not in ROLES: raise HTTPException(422, "bad role")
    bad = password_problem(b.password, b.username)
    if bad: raise HTTPException(422, bad)
    if db.query(User).filter_by(username=b.username).first(): raise HTTPException(409, "username already exists")
    # the admin chose this password, so the new user must replace it at first login
    db.add(User(username=b.username, password_hash=hash_pw(b.password), display_name=b.display_name, role=b.role,
                must_change=True, password_changed_at=utcnow()))
    audit(db, u.username, "user_create", b.username, role=b.role); db.commit()
    return {"ok": True}


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    role: str | None = None
    active: bool | None = None


@router.put("/users/{uid}")
def patch_user(uid: int, b: UserPatch, db: Session = Depends(get_db), u=Depends(admin_only)):
    t = db.get(User, uid)
    if not t: raise HTTPException(404)
    if b.role is not None and b.role not in ROLES: raise HTTPException(422, "bad role")
    losing_admin = t.role == "admin" and t.active and ((b.role is not None and b.role != "admin") or b.active is False)
    if losing_admin and (t.id == u.id or not _other_active_admin(db, t.id)):
        raise HTTPException(409, "ต้องมีผู้ดูแลระบบที่ใช้งานอยู่อย่างน้อย 1 คน (และแก้สิทธิ์/ปิดบัญชีตัวเองไม่ได้)")
    if t.id == u.id and b.active is False: raise HTTPException(409, "ปิดบัญชีตัวเองไม่ได้")
    changed = {}
    if b.display_name is not None and b.display_name != t.display_name: t.display_name = b.display_name; changed["display_name"] = b.display_name
    if b.role is not None and b.role != t.role: t.role = b.role; changed["role"] = b.role; t.token_ver = (t.token_ver or 0) + 1
    if b.active is not None and b.active != t.active:
        t.active = b.active; changed["active"] = b.active; t.token_ver = (t.token_ver or 0) + 1
    if changed: audit(db, u.username, "user_update", t.username, **changed)
    db.commit()
    return user_dict(t)


@router.delete("/users/{uid}")
def delete_user(uid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    t = db.get(User, uid)
    if not t: raise HTTPException(404)
    if t.id == u.id: raise HTTPException(409, "ลบบัญชีตัวเองไม่ได้")
    if t.active: raise HTTPException(409, "ต้องปิดบัญชีก่อนจึงจะลบได้")
    db.delete(t); audit(db, u.username, "user_delete", t.username); db.commit()
    return {"ok": True}


@router.post("/users/{uid}/reset-password")
def reset_password(uid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    """Admin issues a one-time temporary password (shown ONCE). The user must choose a new one at next login."""
    t = db.get(User, uid)
    if not t: raise HTTPException(404)
    if t.id == u.id: raise HTTPException(422, "ใช้เมนู 'เปลี่ยนรหัสผ่าน' สำหรับบัญชีของตัวเอง")
    pw = temp_password()
    t.password_hash, t.must_change, t.password_changed_at = hash_pw(pw), True, utcnow()
    t.failed_logins, t.locked_until = 0, None              # also unlocks the account
    t.token_ver = (t.token_ver or 0) + 1                   # kills every session of that user
    audit(db, u.username, "password_reset", t.username); db.commit()    # the password itself is never logged
    return {"username": t.username, "temp_password": pw}


@router.post("/users/{uid}/reset-2fa")
def reset_user_2fa(uid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    """Lost phone: clears the user's 2FA. If the policy requires it they enrol again at the next sign-in."""
    t = db.get(User, uid)
    if not t: raise HTTPException(404)
    if t.id == u.id: raise HTTPException(422, "ใช้เมนู 'ยืนยัน 2 ขั้น' ของบัญชีตัวเอง")
    mfa.clear(t); t.token_ver = (t.token_ver or 0) + 1
    audit(db, u.username, "2fa_reset", t.username); db.commit()
    return user_dict(t)


@router.post("/users/{uid}/unlock")
def unlock_user(uid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    t = db.get(User, uid)
    if not t: raise HTTPException(404)
    t.failed_logins, t.locked_until = 0, None
    audit(db, u.username, "user_unlock", t.username); db.commit()
    return user_dict(t)


@router.get("/audit")
def audit_log(limit: int = 200, db: Session = Depends(get_db), u=Depends(admin_only)):
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000)).all()
    return [{"at": iso(r.at), "actor": r.actor, "action": r.action, "target": r.target, "detail": r.detail} for r in rows]


def _range(month, frm, to):
    try:
        return reports.parse_range(month, frm, to)
    except reports.RangeError as e:
        raise HTTPException(422, str(e))


@router.get("/reports/summary")
def report_summary(month: str | None = None, frm: str | None = Query(default=None, alias="from"), to: str | None = None,
                   db: Session = Depends(get_db), u=Depends(current_user)):
    start, end, d1, d2 = _range(month, frm, to)
    return reports.summary(db, start, end, d1, d2, names=u.role in ("admin", "staff"))


@router.get("/reports/cases.csv")
def report_cases_csv(month: str | None = None, frm: str | None = Query(default=None, alias="from"), to: str | None = None,
                     db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    from fastapi.responses import Response
    start, end, d1, d2 = _range(month, frm, to)
    text, n = reports.cases_csv(db, start, end)
    audit(db, u.username, "report_export", f"{d1}..{d2}", rows=n); db.commit()
    fn = f"sos-cases-{d1.year + 543}-{d1.month:02d}" + ("" if month else f"-to-{d2.year + 543}-{d2.month:02d}-{d2.day:02d}") + ".csv"
    return Response(text, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@router.get("/reports/monthly.pdf")
def report_pdf(month: str, db: Session = Depends(get_db), u=Depends(current_user)):
    from fastapi.responses import Response
    from . import pdf_report
    start, end, d1, d2 = _range(month, None, None)
    data = pdf_report.build(db, month, names=u.role in ("admin", "staff"), actor=u.display_name or u.username)
    audit(db, u.username, "report_export", month, format="pdf"); db.commit()
    fn = f"sos-report-{d1.year + 543}-{d1.month:02d}.pdf"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fn}"'})


LOGO_SLOTS = ("left", "right")
LOGO_MAX = 400_000


class LogoIn(BaseModel):
    data_url: str = Field(max_length=600_000)


@router.put("/org/logo/{slot}")
def put_logo(slot: str, b: LogoIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    import base64
    from .models import Asset
    if slot not in LOGO_SLOTS: raise HTTPException(404)
    m = re.match(r"^data:(image/(?:png|jpeg));base64,([A-Za-z0-9+/=]+)$", b.data_url)
    if not m: raise HTTPException(422, "ต้องเป็นไฟล์ PNG หรือ JPG")
    raw = base64.b64decode(m.group(2))
    if len(raw) > LOGO_MAX: raise HTTPException(422, "ไฟล์ใหญ่เกิน 400 KB")
    sig_ok = raw.startswith(b"\x89PNG\r\n\x1a\n") if m.group(1) == "image/png" else raw.startswith(b"\xff\xd8\xff")
    if not sig_ok: raise HTTPException(422, "ไฟล์ไม่ใช่รูปภาพที่ถูกต้อง")
    a = db.get(Asset, f"logo_{slot}")
    if a: a.mime, a.data, a.updated_at, a.updated_by = m.group(1), raw, utcnow(), u.username
    else: db.add(Asset(key=f"logo_{slot}", mime=m.group(1), data=raw, updated_by=u.username))
    audit(db, u.username, "org_logo_set", slot, bytes=len(raw)); db.commit()
    return {"ok": True}


@router.delete("/org/logo/{slot}")
def del_logo(slot: str, db: Session = Depends(get_db), u=Depends(admin_only)):
    from .models import Asset
    if slot not in LOGO_SLOTS: raise HTTPException(404)
    a = db.get(Asset, f"logo_{slot}")
    if a: db.delete(a); audit(db, u.username, "org_logo_delete", slot); db.commit()
    return {"ok": True}


@router.get("/org/logo/{slot}")
def get_logo(slot: str, db: Session = Depends(get_db), u=Depends(current_user)):
    from fastapi.responses import Response
    from .models import Asset
    a = db.get(Asset, f"logo_{slot}") if slot in LOGO_SLOTS else None
    if not a: raise HTTPException(404)
    return Response(a.data, media_type=a.mime, headers={"Cache-Control": "no-store"})


@router.get("/stats")
def stats(db: Session = Depends(get_db), u=Depends(current_user)):
    since = utcnow() - timedelta(days=30)
    cs = db.query(Case).filter(Case.created_at >= since, Case.is_test.is_(False)).all()
    acc = [(aware(c.accepted_at) - aware(c.created_at)).total_seconds() for c in cs if c.accepted_at]
    return {"cases_30d": len(cs), "open": sum(c.state == "new" for c in cs),
            "false_alarm": sum(c.close_reason == "false_alarm" for c in cs),
            "after_hours": sum(c.after_hours for c in cs),
            "avg_accept_s": round(sum(acc) / len(acc), 1) if acc else None}


# ---------------- LINE credentials (dashboard) ----------------
class LineCfgIn(BaseModel):
    token: str | None = Field(default=None, max_length=600)       # None = leave as is, "" = remove the dashboard copy
    secret: str | None = Field(default=None, max_length=200)


def _line_state() -> dict:
    t, sc = linecfg.token(), linecfg.secret()
    return {"token_set": bool(t), "token_hint": linecfg.hint(t) if t else "", "token_source": linecfg.source("token"),
            "secret_set": bool(sc), "secret_hint": linecfg.hint(sc) if sc else "", "secret_source": linecfg.source("secret"),
            "webhook_url": env.public_url.rstrip("/") + "/api/line/webhook", "ready": bool(t and sc)}


@router.get("/integrations/line")
def line_cfg(u=Depends(admin_only)):
    return _line_state()


@router.put("/integrations/line")
def line_cfg_put(b: LineCfgIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    changed = []
    for name, v in (("token", b.token), ("secret", b.secret)):
        if v is None: continue
        v = v.strip()
        if v and (len(v) < 8 or re.search(r"\s", v)): raise HTTPException(422, f"ค่า {name} ไม่ถูกต้อง (สั้นเกินไปหรือมีช่องว่าง)")
        linecfg.save(db, name, v); changed.append(name + ("_removed" if not v else "_set"))
    if changed: audit(db, u.username, "line_config_update", "", changed=",".join(changed)); db.commit()     # never the values
    return _line_state()


@router.post("/integrations/line/test")
def line_test(db: Session = Depends(get_db), u=Depends(admin_only)):
    """Checks the token against LINE (bot info). Optionally nothing is sent to anyone."""
    t = linecfg.token()
    if not t: raise HTTPException(409, "ยังไม่ได้ตั้งค่า Channel access token")
    try:
        r = httpx.get("https://api.line.me/v2/bot/info", headers={"Authorization": f"Bearer {t}"}, timeout=10)
    except httpx.HTTPError as e:
        return {"ok": False, "detail": "เชื่อมต่อ LINE ไม่ได้: " + e.__class__.__name__}
    if r.status_code != 200:
        return {"ok": False, "detail": "LINE ปฏิเสธโทเคน (HTTP %d) — ตรวจว่าคัดลอกครบและเป็น Channel access token (long-lived)" % r.status_code}
    j = r.json()
    audit(db, u.username, "line_config_test", "", ok=True); db.commit()
    return {"ok": True, "detail": "เชื่อมต่อสำเร็จ", "bot": j.get("displayName", ""), "basic_id": j.get("basicId", ""),
            "secret_set": bool(linecfg.secret())}


class LineTestPushIn(BaseModel):
    caregiver_id: int


@router.post("/integrations/line/test-push")
def line_test_push(b: LineTestPushIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    cg = db.get(Caregiver, b.caregiver_id)
    if not cg: raise HTTPException(404)
    if not cg.line_user_id: raise HTTPException(409, "ผู้ดูแลคนนี้ยังไม่ได้ผูก LINE")
    from .line import LineNotifier
    ok, detail = LineNotifier().notify_system(db, cg, "✅ ทดสอบการแจ้งเตือนจากระบบแจ้งเหตุฉุกเฉิน — ถ้าเห็นข้อความนี้ แปลว่าการตั้งค่า LINE ถูกต้อง")
    audit(db, u.username, "line_test_push", str(cg.id), ok=ok); db.commit()
    return {"ok": ok, "detail": "ส่งแล้ว" if ok else detail}


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


# ---------------- ChirpStack (network server) — status, compare and sync from the dashboard ----------------
def _cs_compare(db: Session, cs) -> dict:
    cs_devs = {d["devEui"].lower(): d for d in cs.list_devices()}
    sos_devs = {d.dev_eui: d for d in db.query(Device).all()}
    cs_gws = {g["gatewayId"].lower() for g in cs.list_gateways()}
    sos_gws = db.query(Gateway).filter(Gateway.enabled.isnot(False)).all()
    return {
        "devices_cs": len(cs_devs), "devices_sos": len(sos_devs),
        # registered in SOS but ChirpStack does not know it: the button cannot join -> provision it again
        "missing_in_cs": [{"dev_eui": e, "label": d.label, "lifecycle": d.lifecycle}
                          for e, d in sos_devs.items() if e not in cs_devs and d.lifecycle != "retired"],
        # in ChirpStack but not in SOS: its alarms reach nobody
        "unknown_in_sos": [{"dev_eui": e, "name": d.get("name", ""), "last_seen": d.get("lastSeenAt")}
                           for e, d in cs_devs.items() if e not in sos_devs],
        "disabled_mismatch": [e for e, d in sos_devs.items()
                              if e in cs_devs and bool(cs_devs[e].get("isDisabled")) != (d.lifecycle == "retired")],
        "gateways_cs": len(cs_gws),
        "gateways_missing_in_cs": [{"gateway_id": g.gateway_id, "label": g.label} for g in sos_gws if g.gateway_id not in cs_gws],
    }


@router.get("/chirpstack")
def chirpstack_status(db: Session = Depends(get_db), u=Depends(admin_only)):
    cs = prov.get_cs()
    st = cs.status()
    st["ui_url"] = f"https://{env.cs_domain}" if env.cs_domain else ""
    if st.get("reachable"):
        try: st["compare"] = _cs_compare(db, cs)
        except Exception as e: st["compare_error"] = str(e)[:200]
    return st


@router.post("/chirpstack/sync")
def chirpstack_sync(db: Session = Depends(get_db), u=Depends(admin_only)):
    """Make ChirpStack match the dashboard: register missing gateways, disable retired buttons / enable the rest.
    Never creates or deletes devices (keys only exist on the button + ChirpStack; use the install page for that)."""
    cs = prov.get_cs()
    if not cs.configured: raise HTTPException(503, "ChirpStack is not configured (CS API key / app id / profile id)")
    try:
        cmp_ = _cs_compare(db, cs)
    except prov.ProvisionError as e:
        raise HTTPException(502, str(e))
    done = {"gateways_added": [], "devices_disabled": [], "devices_enabled": [], "errors": []}
    for g in cmp_["gateways_missing_in_cs"]:
        gw = db.query(Gateway).filter_by(gateway_id=g["gateway_id"]).one()
        try:
            if cs.ensure_gateway(gw.gateway_id, gw.label, gw.lat, gw.lon) == "created":
                done["gateways_added"].append(gw.gateway_id)
        except Exception as e:
            done["errors"].append(f"gateway {gw.gateway_id}: {str(e)[:120]}")
    for eui in cmp_["disabled_mismatch"]:
        d = db.query(Device).filter_by(dev_eui=eui).one()
        try:
            cs.set_disabled(eui, d.lifecycle == "retired")
            done["devices_disabled" if d.lifecycle == "retired" else "devices_enabled"].append(eui)
        except Exception as e:
            done["errors"].append(f"device {eui}: {str(e)[:120]}")
    audit(db, u.username, "chirpstack_sync", "", gateways=len(done["gateways_added"]),
          disabled=len(done["devices_disabled"]), enabled=len(done["devices_enabled"]), errors=len(done["errors"]))
    db.commit()
    return done
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
    action: str = Field(pattern="^(check|update)$")


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

"""Dashboard API — the hub: cases, patients, caregivers (+ LINE binding) and the helpers the other routers share.

The rest of the API lives next to this file, one router per area (all mounted in main.py):
  api_auth     sign-in, 2-step sign-in, own password        api_devices  buttons, gateways, remote commands, map
  api_admin    settings, holidays, users, audit, reports     api_line     LINE credentials + webhook
  api_ai       AI assistant                                  api_setup    provisioning, ChirpStack, firmware
  api_app      Web Push, monthly self-test, system update    dataapi      system health, history, import, readiness
  netapi       radio history, diagnosis, live events
"""
from __future__ import annotations
import re, secrets
import httpx  # noqa: F401  (tests patch app.api.httpx.get)
from datetime import timedelta, datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload
from . import settings_store
from .auth import admin_only, audit, current_user, staff_or_admin
from .db import get_db
from .engine import aware, msg_capable
from .models import Assignment, BindCode, Case, Caregiver, Device, Gateway, Notification, Patient, utcnow
from . import geo
from . import privacy, reports
from . import codec

router = APIRouter(prefix="/api")
_engine = {"engine": None}          # set in main.py (dependency-injected so tests can swap)
EUI_RE = re.compile(r"^[0-9a-f]{16}$")


def get_engine():
    return _engine["engine"]


def iso(d):
    d = aware(d)
    return d.isoformat() if d else None


# ---------------- cases ----------------
def case_dict(c: Case, events=False, role: str = "staff"):
    d = {"id": c.id, "state": c.state, "overdue": c.overdue, "after_hours": c.after_hours, "is_test": c.is_test,
         "created_at": iso(c.created_at), "accepted_at": iso(c.accepted_at), "accepted_by": c.accepted_by,
         "closed_at": iso(c.closed_at), "close_reason": c.close_reason, "note": c.note,
         "device": {"id": c.device.id, "dev_eui": c.device.dev_eui, "label": c.device.label,
                    "battery": c.device.battery, "mains_ok": c.device.mains_ok,
                    "fw": c.device.fw_version},
         "patient": None}
    if c.patient:
        p = c.patient
        d["patient"] = {"id": p.id, "full_name": p.full_name, "address": p.address, "village": p.village,
                        "phone": p.phone, "lat": p.lat, "lon": p.lon, "notes": p.notes, "priority": p.priority or "normal"}
        if role == "viewer": d["patient"] = privacy.mask_patient(d["patient"])
    d["loc"] = None
    if c.loc_lat is not None and role != "viewer":
        home = round(geo.distance_m(c.patient.lat, c.patient.lon, c.loc_lat, c.loc_lon)) if c.patient and c.patient.lat is not None else None
        d["loc"] = {"lat": c.loc_lat, "lon": c.loc_lon, "at": iso(c.loc_at), "sats": c.loc_sats, "hdop": c.loc_hdop,
                    "home_distance_m": home, "url": geo.maps_url(c.loc_lat, c.loc_lon)}
    d["msg_pending"] = ({**c.msg_pending, "lines": codec.msg_preview(c.msg_pending["code"], c.msg_pending["value"])}
                        if c.msg_pending else None)
    d["msg_capable"] = msg_capable(c.device)
    if events:
        d["events"] = [{"at": iso(e.at), "kind": e.kind, "actor": e.actor, "detail": e.detail} for e in c.events]
    return d


@router.get("/cases")
def list_cases(response: Response, state: str | None = None, include_test: bool = False, limit: int = 100, offset: int = Query(default=0, ge=0),
               frm: str | None = Query(default=None, alias="from"), to: str | None = None, patient_id: int | None = None,
               village: str | None = None, reason: str | None = None, q: str | None = Query(default=None, max_length=80),
               db: Session = Depends(get_db), u=Depends(current_user)):
    """Newest first. Filters: state (comma list), from/to (YYYY-MM-DD, local days, inclusive), patient_id, village, reason
    (resolved|false_alarm), q (patient name, device label / DevEUI, or a case number). The number of matching cases is in
    the X-Total-Count header; page through them with limit + offset."""
    qy = db.query(Case)
    if state:
        qy = qy.filter(Case.state.in_(state.split(",")))
    if not include_test:
        qy = qy.filter(Case.is_test.is_(False))
    if frm or to:
        try:
            start, end, *_ = reports.parse_range(None, frm or "2000-01-01", to or "2999-12-31", max_days=None)
        except reports.RangeError as e:
            raise HTTPException(422, str(e))
        qy = qy.filter(Case.created_at >= start, Case.created_at < end)
    if patient_id is not None:
        qy = qy.filter(Case.patient_id == patient_id)
    if reason:
        qy = qy.filter(Case.close_reason == reason)
    if village or q:
        qy = qy.outerjoin(Patient, Patient.id == Case.patient_id).outerjoin(Device, Device.id == Case.device_id)
        if village:
            qy = qy.filter(Patient.village == village)
        if q and q.strip():
            t = q.strip(); like = f"%{t}%"
            conds = [Patient.full_name.ilike(like), Device.label.ilike(like), Device.dev_eui.ilike(like)]
            if t.lstrip("#").isdigit(): conds.append(Case.id == int(t.lstrip("#")))
            qy = qy.filter(or_(*conds))
    response.headers["X-Total-Count"] = str(qy.count())
    rows = (qy.options(joinedload(Case.device), joinedload(Case.patient)).order_by(Case.id.desc())
            .offset(offset).limit(max(1, min(limit, 500))).all())
    return [case_dict(c, role=u.role) for c in rows]


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


@router.get("/device-messages")
def device_messages(u=Depends(current_user)):
    """Preset messages that can be shown on the patient's device (text is fixed in the firmware)."""
    return [{"code": k, "th": m["th"], "lines": list(m["en"]), "value": m["value"]} for k, m in codec.MESSAGES.items()]


class MessageIn(BaseModel):
    code: int
    value: int = 0


@router.post("/cases/{cid}/message")
def send_message(cid: int, body: MessageIn, db: Session = Depends(get_db), u=Depends(staff_or_admin),
                 eng=Depends(get_engine)):
    try: c = eng.send_message(db, cid, u.display_name or u.username, body.code, body.value)
    except LookupError: raise HTTPException(404)
    except ValueError as e: raise HTTPException(422, str(e))
    audit(db, u.username, "case_message", str(cid), code=body.code, value=body.value); db.commit()
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


PRIORITIES = ("normal", "watch", "critical")


def case_stats(db: Session) -> dict[int, dict]:
    """Per patient: cases in the last 7 / 30 days and the last case ever (real devices only, never self-tests)."""
    cfg = settings_store.get(db, "frequent")
    now = utcnow(); d7, d30 = now - timedelta(days=7), now - timedelta(days=30)
    out: dict[int, dict] = {}
    for pid, created, reason in db.query(Case.patient_id, Case.created_at, Case.close_reason).filter(
            Case.patient_id.isnot(None), Case.is_test.is_(False), Case.created_at >= d30).all():
        if reason == "false_alarm" and not cfg["count_false_alarm"]: continue
        st = out.setdefault(pid, {"cases_7d": 0, "cases_30d": 0})
        st["cases_30d"] += 1
        if aware(created) >= d7: st["cases_7d"] += 1
    return out


def last_cases(db: Session) -> dict[int, datetime]:
    return {pid: t for pid, t in db.query(Case.patient_id, func.max(Case.created_at)).filter(
        Case.patient_id.isnot(None), Case.is_test.is_(False)).group_by(Case.patient_id).all()}


def pat_dict(p: Patient, db: Session, role: str = "staff", stats: dict | None = None, last: dict | None = None, cfg: dict | None = None,
             pre: dict | None = None):
    """pre = {"asg": {patient_id: [Assignment]}, "dev": {patient_id: Device}} loaded once by the list endpoint (no query per row)."""
    if pre is not None:
        asg, dev = pre["asg"].get(p.id, []), pre["dev"].get(p.id)
    else:
        asg = db.query(Assignment).filter_by(patient_id=p.id).order_by(Assignment.priority).all()
        dev = db.query(Device).filter_by(patient_id=p.id).order_by(Device.id).first()
    out = {"id": p.id, "full_name": p.full_name, "age": p.age, "address": p.address, "village": p.village,
            "lat": p.lat, "lon": p.lon, "phone": p.phone, "notes": p.notes, "active": p.active,
            "device": dev.dev_eui if dev else None,
            "consent_ok": privacy.consent_ok(p), "consent_at": iso(p.consent_at), "consent_by": p.consent_by,
            "consent_note": p.consent_note, "consent_withdrawn_at": iso(p.consent_withdrawn_at),
            "anonymized_at": iso(p.anonymized_at),
            "priority": p.priority or "normal", "priority_at": iso(p.priority_at), "priority_by": p.priority_by,
            "assignments": [{"priority": a.priority, "caregiver_id": a.caregiver_id,
                             "name": a.caregiver.full_name} for a in asg]}
    st = (stats or {}).get(p.id, {})
    cfg = cfg or settings_store.get(db, "frequent")
    out["cases_7d"], out["cases_30d"] = st.get("cases_7d", 0), st.get("cases_30d", 0)
    out["last_case_at"] = iso((last or {}).get(p.id))
    out["frequent_week"] = out["cases_7d"] >= cfg["week_threshold"] > 0
    out["frequent_month"] = out["cases_30d"] >= cfg["month_threshold"] > 0
    return privacy.mask_patient(out) if role == "viewer" else out


@router.get("/patients")
def patients(db: Session = Depends(get_db), u=Depends(current_user)):
    stats, last, cfg = case_stats(db), last_cases(db), settings_store.get(db, "frequent")
    pre = {"asg": {}, "dev": {}}
    for a in db.query(Assignment).options(joinedload(Assignment.caregiver)).order_by(Assignment.patient_id, Assignment.priority).all():
        pre["asg"].setdefault(a.patient_id, []).append(a)
    for d in db.query(Device).filter(Device.patient_id.isnot(None)).order_by(Device.id.desc()).all():
        pre["dev"][d.patient_id] = d                      # lowest id wins, like the per-row query
    return [pat_dict(p, db, u.role, stats, last, cfg, pre) for p in db.query(Patient).order_by(Patient.id).all()]


class PriorityIn(BaseModel):
    priority: str


@router.put("/patients/{pid}/priority")
def set_priority(pid: int, b: PriorityIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    p = db.get(Patient, pid)
    if not p: raise HTTPException(404)
    if b.priority not in PRIORITIES: raise HTTPException(422, "priority must be normal, watch or critical")
    if p.anonymized_at: raise HTTPException(409, "ข้อมูลผู้ป่วยรายนี้ถูกลบ (ทำให้ไม่ระบุตัวตน) แล้ว")
    old = p.priority or "normal"
    if old != b.priority:
        p.priority, p.priority_at, p.priority_by = b.priority, utcnow(), u.username
        audit(db, u.username, "patient_priority", str(pid), frm=old, to=b.priority); db.commit()
    return {"id": p.id, "priority": p.priority}


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
    from fastapi.responses import FileResponse, JSONResponse
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
    is_central: bool | None = None       # เจ้าหน้าที่ส่วนกลาง: every patient alert + may close any case from LINE
    system_alert: bool | None = None     # ผู้ดูแลระบบ: gets the system alerts (stored in the system_alert setting)
    # None = leave as it is. Both decide who is told what, so only an admin may change them.


def system_alert_ids(db: Session) -> list[int]:
    return list(settings_store.get(db, "system_alert").get("caregiver_ids") or [])


def cg_dict(c: Caregiver, sys_ids: list[int] | None = None, db: Session | None = None):
    if sys_ids is None:
        sys_ids = system_alert_ids(db) if db is not None else []
    return {"id": c.id, "full_name": c.full_name, "role": c.role, "phone": c.phone, "active": c.active,
            "line_bound": bool(c.line_user_id), "is_central": bool(c.is_central), "system_alert": c.id in sys_ids}


def _cg_routing(db: Session, u, c: Caregiver, b: CaregiverIn) -> None:
    """Apply the two "who is told what" switches. Admin only; a non-admin sending a change gets 403, sending None is fine."""
    sys_ids = system_alert_ids(db)
    want_c = bool(c.is_central) if b.is_central is None else b.is_central
    want_s = (c.id in sys_ids) if b.system_alert is None else b.system_alert
    if want_c == bool(c.is_central) and want_s == (c.id in sys_ids):
        return
    if u.role != "admin":
        raise HTTPException(403, "เฉพาะผู้ดูแลระบบที่กำหนดผู้รับแจ้งเตือนระบบ/เจ้าหน้าที่ส่วนกลางได้")
    if want_c != bool(c.is_central):
        c.is_central = want_c
        audit(db, u.username, "caregiver_central_on" if want_c else "caregiver_central_off", str(c.id))
    if want_s != (c.id in sys_ids):
        cur = settings_store.get(db, "system_alert")
        ids = sorted(set(sys_ids) | {c.id}) if want_s else [i for i in sys_ids if i != c.id]
        settings_store.put(db, "system_alert", {**cur, "caregiver_ids": ids}, u.username)
        audit(db, u.username, "system_alert_recipient_on" if want_s else "system_alert_recipient_off", str(c.id))


@router.get("/caregivers")
def caregivers(db: Session = Depends(get_db), u=Depends(current_user)):
    from . import stats as st
    load = st.caregiver_load(db, u.role)["by_id"]
    sys_ids = system_alert_ids(db)
    return [{**cg_dict(c, sys_ids), **load.get(c.id, {})} for c in db.query(Caregiver).order_by(Caregiver.id).all()]


@router.get("/caregivers/summary")
def caregivers_summary(db: Session = Depends(get_db), u=Depends(current_user)):
    """Workload overview: how many patients each caregiver looks after, and which patients have nobody yet."""
    from . import stats as st
    return st.caregiver_load(db, u.role)["summary"]


@router.get("/patients/{pid}/stats")
def patient_statistics(pid: int, db: Session = Depends(get_db), u=Depends(current_user)):
    from . import stats as st
    p = db.get(Patient, pid)
    if not p: raise HTTPException(404)
    return st.patient_stats(db, p, u.role)


@router.post("/caregivers")
def add_cg(b: CaregiverIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    c = Caregiver(**b.model_dump(exclude={"is_central", "system_alert"})); db.add(c); db.flush()
    _cg_routing(db, u, c, b)
    audit(db, u.username, "caregiver_create", str(c.id)); db.commit()
    return cg_dict(c, db=db)


@router.put("/caregivers/{cid}")
def edit_cg(cid: int, b: CaregiverIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    c = db.get(Caregiver, cid)
    if not c: raise HTTPException(404)
    _cg_routing(db, u, c, b)
    for k, v in b.model_dump(exclude={"is_central", "system_alert"}).items(): setattr(c, k, v)
    audit(db, u.username, "caregiver_update", str(cid)); db.commit()
    return cg_dict(c, db=db)


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
    if cid in system_alert_ids(db):
        cur = settings_store.get(db, "system_alert")
        settings_store.put(db, "system_alert", {**cur, "caregiver_ids": [i for i in cur["caregiver_ids"] if i != cid]}, u.username)
    name = c.full_name; db.delete(c)
    audit(db, u.username, "caregiver_delete", str(cid), name=name); db.commit()
    return {"ok": True}


BIND_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@router.post("/caregivers/{cid}/bind-code")
def bind_code(cid: int, db: Session = Depends(get_db), u=Depends(staff_or_admin), eng=Depends(get_engine)):
    cg = db.get(Caregiver, cid)
    if not cg: raise HTTPException(404)
    code = "".join(secrets.choice(BIND_ALPHABET) for _ in range(8))
    db.add(BindCode(code=code, caregiver_id=cid, expires_at=utcnow() + timedelta(minutes=30)))
    audit(db, u.username, "bindcode_create", str(cid)); db.commit()
    # One QR for the caregiver's phone: LINE opens a chat with our Official Account with the code already typed
    # (https://line.me/R/oaMessage/<basic id>/?<text>), so binding is "scan, then tap send".
    info = getattr(getattr(eng, "notifier", None), "bot_info", lambda: None)()
    oa = None
    if info:
        from urllib.parse import quote
        from . import mfa
        bid = quote(info["basicId"], safe="")
        msg = f"https://line.me/R/oaMessage/{bid}/?{code}"
        oa = {"name": info.get("displayName") or "", "basic_id": info["basicId"], "add_url": f"https://line.me/R/ti/p/{bid}",
              "message_url": msg, "qr_svg": mfa.qr_svg(msg)}
    return {"code": code, "expires_minutes": 30, "name": cg.full_name, "oa": oa}


@router.post("/caregivers/{cid}/unbind")
def unbind(cid: int, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    c = db.get(Caregiver, cid)
    if not c: raise HTTPException(404)
    c.line_user_id = ""; audit(db, u.username, "line_unbind", str(cid)); db.commit()
    return cg_dict(c, db=db)

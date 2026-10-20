"""PDPA tooling: consent record, data-subject export, anonymisation, retention purge, viewer masking.

Design notes (see docs/PDPA.md):
- Patient health/location data is sensitive personal data (PDPA section 26): explicit consent is recorded per patient and,
  by default, a device cannot be set to `active` for a patient without it.
- "Erasure" is implemented as ANONYMISATION of the patient record (the row stays so that closed cases keep their
  statistics, but nothing identifies the person any more).  Old cases are removed by the retention purge.
- Nothing here is legal advice; the data controller must confirm periods and wording with their own DPO/legal.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from . import settings_store
from .gateways import _aware
from .models import (Assignment, AuditLog, Case, CaseEvent, Device, Notification, Patient, utcnow)

# audit records about privacy actions are kept even when old audit rows are purged (accountability)
KEEP_AUDIT = ("patient_export", "patient_anonymize", "consent_given", "consent_withdrawn", "retention_run", "retention_preview")
CLOSED = ("closed", "cancelled")
TEST_CASE_DAYS = 30


def consent_ok(p: Patient | None) -> bool:
    return bool(p and p.consent_at and not p.consent_withdrawn_at and not p.anonymized_at)


def mask_phone(phone: str) -> str:
    d = "".join(ch for ch in (phone or "") if ch.isdigit())
    return ("xxx-xxx-" + d[-4:]) if len(d) >= 4 else ("xxx" if d else "")


def mask_patient(d: dict) -> dict:
    """Least privilege for the read-only role: who it is (for context) but not where, how to reach them, or free text."""
    d = dict(d)
    for k in ("address", "notes"): d[k] = ""
    d["phone"] = mask_phone(d.get("phone", ""))
    d["lat"] = d["lon"] = None
    return d


def withdraw(db: Session, p: Patient, actor: str) -> dict:
    """Consent withdrawn: stop processing — deactivate the patient, free the device, drop the LINE notification chain."""
    p.consent_withdrawn_at, p.active = utcnow(), False
    freed = 0
    for d in db.query(Device).filter_by(patient_id=p.id).all():
        d.patient_id, d.lifecycle, d.loc_lat, d.loc_lon, d.loc_at, d.loc_sats, d.loc_hdop = None, "ready", None, None, None, None, None
        freed += 1
    removed = db.query(Assignment).filter_by(patient_id=p.id).delete()
    return {"devices_freed": freed, "assignments_removed": removed}


def anonymize(db: Session, p: Patient, keep_village: bool = True) -> dict:
    """Irreversibly remove everything that identifies the patient. Cases are kept (statistics) but scrubbed."""
    now = utcnow()
    res = withdraw(db, p, "") if not p.consent_withdrawn_at else {"devices_freed": 0, "assignments_removed": 0}
    if p.consent_withdrawn_at is None: p.consent_withdrawn_at = now
    p.full_name = f"(ลบข้อมูลแล้ว #{p.id})"
    p.age = None; p.address = ""; p.phone = ""; p.notes = ""; p.lat = p.lon = None
    p.consent_by = ""; p.consent_note = ""
    if not keep_village: p.village = ""
    p.active, p.anonymized_at = False, now
    n_cases = 0
    for c in db.query(Case).filter_by(patient_id=p.id).all():
        n_cases += 1
        c.note = ""
        c.loc_lat = c.loc_lon = c.loc_at = c.loc_sats = c.loc_hdop = None
        for e in c.events:
            if "note" in (e.detail or {}):
                e.detail = {k: v for k, v in e.detail.items() if k != "note"}
    return {**res, "cases_scrubbed": n_cases, "kept_village": keep_village}


def export_patient(db: Session, p: Patient) -> dict:
    """Everything stored about one patient (data-subject access request / data portability)."""
    from .api import iso
    asg = db.query(Assignment).filter_by(patient_id=p.id).order_by(Assignment.priority).all()
    devs = db.query(Device).filter_by(patient_id=p.id).all()
    cases = db.query(Case).filter_by(patient_id=p.id).order_by(Case.id).all()
    return {
        "exported_at": iso(utcnow()),
        "patient": {"id": p.id, "full_name": p.full_name, "age": p.age, "address": p.address, "village": p.village,
                    "lat": p.lat, "lon": p.lon, "phone": p.phone, "notes": p.notes, "active": p.active,
                    "created_at": iso(p.created_at)},
        "consent": {"given_at": iso(p.consent_at), "recorded_by": p.consent_by, "note": p.consent_note,
                    "withdrawn_at": iso(p.consent_withdrawn_at)},
        "caregivers_assigned": [{"priority": a.priority, "name": a.caregiver.full_name, "role": a.caregiver.role} for a in asg],
        "devices": [{"dev_eui": d.dev_eui, "label": d.label, "lifecycle": d.lifecycle, "last_seen": iso(d.last_seen),
                     "last_location": {"lat": d.loc_lat, "lon": d.loc_lon, "at": iso(d.loc_at)} if d.loc_lat is not None else None}
                    for d in devs],
        "cases": [{"id": c.id, "created_at": iso(c.created_at), "state": c.state, "is_test": c.is_test,
                   "after_hours": c.after_hours, "accepted_by": c.accepted_by, "accepted_at": iso(c.accepted_at),
                   "closed_at": iso(c.closed_at), "close_reason": c.close_reason, "note": c.note,
                   "live_location": {"lat": c.loc_lat, "lon": c.loc_lon, "at": iso(c.loc_at)} if c.loc_lat is not None else None,
                   "events": [{"at": iso(e.at), "kind": e.kind, "actor": e.actor, "detail": e.detail} for e in c.events],
                   "notifications": [{"at": iso(n.at), "caregiver_id": n.caregiver_id, "channel": n.channel, "status": n.status}
                                     for n in db.query(Notification).filter_by(case_id=c.id)]}
                  for c in cases],
        "note": "ข้อมูลนี้ออกจากระบบแจ้งเหตุฉุกเฉินตามคำขอของเจ้าของข้อมูล; ข้อความที่ส่งทาง LINE อยู่กับผู้ให้บริการ LINE ตามนโยบายของเขาด้วย",
    }


def retention_cutoffs(db: Session, now: datetime) -> dict:
    cfg = settings_store.get(db, "privacy")
    return {"cases_before": now - timedelta(days=cfg["case_retention_days"]),
            "tests_before": now - timedelta(days=TEST_CASE_DAYS),
            "audit_before": now - timedelta(days=cfg["audit_retention_days"])}


def _old_cases(db: Session, cut: dict) -> list[Case]:
    real = [c for c in db.query(Case).filter(Case.is_test.is_(False), Case.state.in_(CLOSED)).all()
            if _aware(c.created_at) < cut["cases_before"]]
    tests = db.query(Case).filter(Case.is_test.is_(True), Case.created_at < cut["tests_before"]).all()
    return real + tests


def retention(db: Session, now: datetime | None = None, dry_run: bool = True) -> dict:
    """Delete closed cases older than `case_retention_days` (open cases are never touched), test cases older than 30 days
    and audit rows older than `audit_retention_days` (privacy-action records are kept). Returns the counts."""
    now = now or utcnow()
    cut = retention_cutoffs(db, now)
    cases = _old_cases(db, cut)
    ids = [c.id for c in cases]
    audit_q = db.query(AuditLog).filter(AuditLog.at < cut["audit_before"], AuditLog.action.not_in(KEEP_AUDIT))
    out = {"dry_run": dry_run, "cases": len(ids), "case_events": 0, "notifications": 0, "audit_rows": audit_q.count(),
           "cases_before": cut["cases_before"].isoformat(), "audit_before": cut["audit_before"].isoformat()}
    if ids:
        out["case_events"] = db.query(CaseEvent).filter(CaseEvent.case_id.in_(ids)).count()
        out["notifications"] = db.query(Notification).filter(Notification.case_id.in_(ids)).count()
    if not dry_run:
        if ids:
            db.query(CaseEvent).filter(CaseEvent.case_id.in_(ids)).delete(synchronize_session=False)
            db.query(Notification).filter(Notification.case_id.in_(ids)).delete(synchronize_session=False)
            db.query(Case).filter(Case.id.in_(ids)).delete(synchronize_session=False)
        audit_q.delete(synchronize_session=False)
    return out


def overview(db: Session) -> dict:
    pats = db.query(Patient).all()
    live = [p for p in pats if p.active and not p.anonymized_at]
    with_dev = {d.patient_id for d in db.query(Device).filter(Device.patient_id.is_not(None), Device.lifecycle == "active")}
    return {"patients": len(pats), "active": len(live),
            "without_consent": [{"id": p.id, "full_name": p.full_name, "has_active_device": p.id in with_dev}
                                for p in live if not consent_ok(p)],
            "withdrawn": sum(1 for p in pats if p.consent_withdrawn_at and not p.anonymized_at),
            "anonymized": sum(1 for p in pats if p.anonymized_at),
            "settings": settings_store.get(db, "privacy"),
            "retention_preview": retention(db, dry_run=True)}

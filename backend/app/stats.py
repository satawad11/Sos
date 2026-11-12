"""Per-patient statistics and caregiver workload (who looks after how many patients) for the dashboard.
Real alerts only: monthly self-tests (Case.is_test) never count, they are reported apart as "last button test"."""
from __future__ import annotations
from datetime import datetime, timedelta
from statistics import median
from sqlalchemy.orm import Session, joinedload
from . import privacy, settings_store
from .gateways import _aware
from .models import Assignment, Caregiver, Case, Device, Notification, Patient, utcnow
from .reports import tz

DAYPARTS = (("morning", "เช้า 06–12 น.", 6, 12), ("afternoon", "บ่าย 12–18 น.", 12, 18),
            ("evening", "ค่ำ 18–22 น.", 18, 22), ("night", "กลางคืน 22–06 น.", 22, 6))


def _iso(d: datetime | None) -> str | None:
    return _aware(d).isoformat() if d else None


def _months_back(now_local: datetime, n: int) -> list[str]:
    y, m, out = now_local.year, now_local.month, []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0: y, m = y - 1, 12
    return out[::-1]


def patient_stats(db: Session, p: Patient, role: str = "staff", now: datetime | None = None) -> dict:
    now = now or utcnow(); z = tz()
    rows = db.query(Case).filter(Case.patient_id == p.id).order_by(Case.created_at, Case.id).all()
    real, tests = [c for c in rows if not c.is_test], [c for c in rows if c.is_test]
    d7, d30, d365 = (now - timedelta(days=n) for n in (7, 30, 365))
    acc = sorted((_aware(c.accepted_at) - _aware(c.created_at)).total_seconds() for c in real if c.accepted_at)
    close = [(_aware(c.closed_at) - _aware(c.created_at)).total_seconds() / 60 for c in real if c.closed_at and c.state == "closed"]
    months = {k: {"month": k, "n": 0, "false_alarm": 0} for k in _months_back(now.astimezone(z), 12)}
    parts = {k: 0 for k, *_ in DAYPARTS}; weekdays = [0] * 7
    for c in real:
        loc = _aware(c.created_at).astimezone(z)
        m = months.get(f"{loc.year:04d}-{loc.month:02d}")
        if m:
            m["n"] += 1; m["false_alarm"] += c.close_reason == "false_alarm"
        weekdays[loc.weekday()] += 1
        for k, _, lo, hi in DAYPARTS:
            if (lo <= loc.hour < hi) if lo < hi else (loc.hour >= lo or loc.hour < hi):
                parts[k] += 1; break
    dev = db.query(Device).filter_by(patient_id=p.id).order_by(Device.id).first()
    asg = (db.query(Assignment).options(joinedload(Assignment.caregiver)).filter_by(patient_id=p.id).order_by(Assignment.priority).all())
    pat = {"id": p.id, "full_name": p.full_name, "age": p.age, "village": p.village, "address": p.address, "phone": p.phone,
           "notes": p.notes, "lat": p.lat, "lon": p.lon, "priority": p.priority or "normal", "active": p.active,
           "anonymized_at": _iso(p.anonymized_at), "consent_ok": privacy.consent_ok(p)}
    if role == "viewer":
        pat = privacy.mask_patient(pat)
    n = len(real)
    return {
        "patient": pat,
        "device": None if dev is None else {
            "id": dev.id, "label": dev.label, "dev_eui": dev.dev_eui, "battery": dev.battery, "mains_ok": dev.mains_ok,
            "last_seen": _iso(dev.last_seen), "lifecycle": dev.lifecycle, **settings_store.device_link(db, dev, now)},
        "caregivers": [{"priority": a.priority, "name": a.caregiver.full_name, "active": a.caregiver.active,
                        "line_bound": bool(a.caregiver.line_user_id)} for a in asg],
        "totals": {"all": n, "d7": sum(_aware(c.created_at) >= d7 for c in real), "d30": sum(_aware(c.created_at) >= d30 for c in real),
                   "d365": sum(_aware(c.created_at) >= d365 for c in real),
                   "resolved": sum(c.state == "closed" and c.close_reason != "false_alarm" for c in real),
                   "false_alarm": sum(c.close_reason == "false_alarm" for c in real),
                   "cancelled": sum(c.state == "cancelled" for c in real),
                   "open": sum(c.state in ("new", "accepted") for c in real),
                   "after_hours": sum(bool(c.after_hours) for c in real), "overdue": sum(bool(c.overdue) for c in real)},
        "response": {"n_accepted": len(acc), "avg_accept_s": round(sum(acc) / len(acc), 1) if acc else None,
                     "median_accept_s": round(median(acc), 1) if acc else None, "max_accept_s": round(acc[-1], 1) if acc else None,
                     "avg_close_min": round(sum(close) / len(close), 1) if close else None},
        "months": list(months.values()),
        "dayparts": [{"key": k, "label": lbl, "n": parts[k]} for k, lbl, *_ in DAYPARTS],
        "weekdays": weekdays,                                   # Monday first
        "first_case_at": _iso(real[0].created_at) if real else None,
        "last_case_at": _iso(real[-1].created_at) if real else None,
        "last_test_at": _iso(tests[-1].created_at) if tests else None,
        "tests": len(tests),
        "recent": [{"id": c.id, "created_at": _iso(c.created_at), "state": c.state, "close_reason": c.close_reason,
                    "after_hours": bool(c.after_hours), "overdue": bool(c.overdue), "accepted_by": c.accepted_by,
                    "accept_s": round((_aware(c.accepted_at) - _aware(c.created_at)).total_seconds()) if c.accepted_at else None}
                   for c in real[::-1][:10]],
    }


def caregiver_load(db: Session, role: str = "staff", now: datetime | None = None) -> dict:
    """{"by_id": {caregiver_id: workload}, "summary": {...}} — only patients that are active and not anonymised count."""
    now = now or utcnow()
    live = {p.id: p for p in db.query(Patient).filter(Patient.active.is_(True), Patient.anonymized_at.is_(None)).all()}
    by: dict[int, dict] = {}
    covered: set[int] = set()
    for a in db.query(Assignment).order_by(Assignment.patient_id, Assignment.priority).all():
        p = live.get(a.patient_id)
        if p is None:
            continue
        covered.add(p.id)
        w = by.setdefault(a.caregiver_id, {"n_patients": 0, "n_primary": 0, "n_backup": 0, "n_critical": 0, "n_watch": 0, "patients": []})
        w["n_patients"] += 1
        w["n_primary" if a.priority == 1 else "n_backup"] += 1
        w["n_critical"] += p.priority == "critical"; w["n_watch"] += p.priority == "watch"
        w["patients"].append({"id": p.id, "full_name": p.full_name, "village": p.village, "order": a.priority, "priority": p.priority or "normal"})
    since = now - timedelta(days=30)
    noti: dict[int, set] = {}
    for cid, case_id in (db.query(Notification.caregiver_id, Notification.case_id)
                         .join(Case, Case.id == Notification.case_id)
                         .filter(Notification.at >= since, Notification.caregiver_id.isnot(None), Case.is_test.is_(False)).all()):
        noti.setdefault(cid, set()).add(case_id)
    cgs = db.query(Caregiver).all()
    for c in cgs:
        w = by.setdefault(c.id, {"n_patients": 0, "n_primary": 0, "n_backup": 0, "n_critical": 0, "n_watch": 0, "patients": []})
        w["cases_notified_30d"] = len(noti.get(c.id, ()))
    active = [c for c in cgs if c.active]
    with_pat = [c for c in active if by[c.id]["n_patients"]]
    loads = [by[c.id]["n_patients"] for c in with_pat]
    summary = {"caregivers": len(cgs), "active": len(active), "line_bound": sum(bool(c.line_user_id) for c in active),
               "with_patients": len(with_pat), "without_patients": len(active) - len(with_pat),
               "patients": len(live), "patients_covered": len(covered & set(live)), "patients_uncovered": len(set(live) - covered),
               "avg_per_caregiver": round(sum(loads) / len(loads), 1) if loads else 0, "max_per_caregiver": max(loads, default=0),
               "uncovered": [{"id": p.id, "full_name": p.full_name, "village": p.village} for p in live.values() if p.id not in covered]}
    sys_ids = set(settings_store.get(db, "system_alert").get("caregiver_ids") or [])
    central, admins = [c for c in active if c.is_central], [c for c in active if c.id in sys_ids]
    summary["device_to_caregiver"] = bool(settings_store.get(db, "device_alert").get("to_caregiver", True))
    summary.update({"central": len(central), "central_line": sum(bool(c.line_user_id) for c in central),
                    "system_admins": len(admins), "system_admins_line": sum(bool(c.line_user_id) for c in admins)})
    return {"by_id": by, "summary": summary}

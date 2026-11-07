"""Monthly self-test of patient buttons.

A test is 3 quick presses on the button: the device sends a TEST frame, the server records a closed is_test case
(never an emergency, never escalated) and the button shows green.  A button that silently broke is the worst
failure of this system, so once a month the responsible อสม. (first caregiver of each patient) gets one LINE
message listing the buttons in their care that have not been tested yet this month; a second reminder goes out
later in the month for the ones still missing.  Settings "self_test" (all editable in the dashboard).
"""
from __future__ import annotations
import logging, re
from datetime import datetime, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from . import settings_store
from .auth import audit
from .gateways import _aware
from .models import Assignment, AuditLog, Case, Caregiver, Device, utcnow
from .reports import parse_range, tz

log = logging.getLogger("sos.selftest")
GRACE_DAYS = 3
MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน",
          "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]


def month_key(now: datetime | None = None) -> str:
    l = (now or utcnow()).astimezone(tz())
    return f"{l.year}-{l.month:02d}"


def month_th(month: str) -> str:
    y, m = int(month[:4]), int(month[5:])
    return f"{MONTHS[m - 1]} {y + 543}"


def first_caregivers(db: Session) -> dict[int, Caregiver]:
    """patient_id -> the active caregiver with the lowest priority number."""
    out: dict[int, Caregiver] = {}
    for a in db.query(Assignment).options(joinedload(Assignment.caregiver)).order_by(Assignment.priority).all():
        if a.patient_id not in out and a.caregiver and a.caregiver.active:
            out[a.patient_id] = a.caregiver
    return out


def status(db: Session, month: str | None = None, names: bool = True) -> dict:
    month = month or month_key()
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("month must be YYYY-MM")
    start, end, *_ = parse_range(month, None, None)
    devs = (db.query(Device).options(joinedload(Device.patient)).filter(Device.lifecycle == "active")
            .order_by(Device.label, Device.id).all())
    in_month = dict(db.query(Case.device_id, func.count(Case.id)).filter(Case.is_test.is_(True), Case.created_at >= start,
                                                                          Case.created_at < end).group_by(Case.device_id).all())
    last_any = dict(db.query(Case.device_id, func.max(Case.created_at)).filter(Case.is_test.is_(True)).group_by(Case.device_id).all())
    cgs = first_caregivers(db)
    rows = []
    for d in devs:
        p = d.patient; cg = cgs.get(d.patient_id) if d.patient_id else None
        last = _aware(last_any.get(d.id))
        rows.append({"device_id": d.id, "label": d.label or d.dev_eui, "dev_eui": d.dev_eui,
                     "patient": (p.full_name if names else "") if p else "", "village": p.village if p else "",
                     "caregiver": cg.full_name if cg else "", "caregiver_has_line": bool(cg and cg.line_user_id),
                     "tests_this_month": in_month.get(d.id, 0), "tested": in_month.get(d.id, 0) > 0,
                     "last_test_at": last.isoformat() if last else None, "battery": d.battery, "rssi": d.last_rssi,
                     "last_seen": _aware(d.last_seen).isoformat() if d.last_seen else None})
    rows.sort(key=lambda r: (r["tested"], r["label"]))
    n_ok = sum(r["tested"] for r in rows)
    sent = (db.query(AuditLog).filter(AuditLog.action == "selftest_reminder", AuditLog.target.like(f"{month}%"))
            .order_by(AuditLog.id).all())
    return {"month": month, "month_th": month_th(month), "active": len(rows), "tested": n_ok,
            "pct": round(100 * n_ok / len(rows), 1) if rows else None, "devices": rows,
            "reminders": [{"at": _aware(a.at).isoformat(), "round": a.target.split("#")[-1], "by": a.actor,
                           "caregivers": a.detail.get("caregivers", 0), "devices": a.detail.get("devices", 0)} for a in sent]}


def _line_name(db: Session, d: Device) -> str:
    p = d.patient
    if not p:
        return d.label or d.dev_eui
    if settings_store.get(db, "privacy")["line_detail"] == "minimal":
        return f"{d.label or d.dev_eui} ({p.village or 'ไม่ระบุหมู่'})"
    return f"{d.label or d.dev_eui} {p.full_name}" + (f" ({p.village})" if p.village else "")


def send_reminders(db: Session, notifier, round_: str, actor: str = "system", now: datetime | None = None) -> dict:
    """LINE each first caregiver the list of their untested buttons for the current month."""
    month = month_key(now)
    st = status(db, month)
    pending = {r["device_id"] for r in st["devices"] if not r["tested"]}
    cgs = first_caregivers(db)
    per: dict[int, tuple[Caregiver, list[Device]]] = {}
    no_cg = 0
    for d in db.query(Device).options(joinedload(Device.patient)).filter(Device.id.in_(pending)).order_by(Device.label).all() if pending else []:
        cg = cgs.get(d.patient_id) if d.patient_id else None
        if not cg:
            no_cg += 1; continue
        per.setdefault(cg.id, (cg, []))[1].append(d)
    sent = failed = 0
    for cg, ds in per.values():
        if not cg.line_user_id or notifier is None:
            failed += 1; continue
        lines = "\n".join("• " + _line_name(db, d) for d in ds[:30]) + (f"\n… และอีก {len(ds) - 30} เครื่อง" if len(ds) > 30 else "")
        text = (f"🧪 ทดสอบปุ่มแจ้งเหตุประจำเดือน{month_th(month)}\n"
                f"ปุ่มที่ยังไม่ได้ทดสอบ ({len(ds)} เครื่อง):\n{lines}\n\n"
                "วิธีทดสอบ: กดปุ่มสั้น ๆ 3 ครั้งติดกัน ไฟเขียว = ผ่าน (ไม่ใช่การแจ้งเหตุจริง ไม่มีใครถูกเรียก)\n"
                "ไฟแดง = ส่งไม่ถึง กรุณาแจ้ง รพ.สต.")
        ok, _ = notifier.notify_system(db, cg, text)
        sent += ok; failed += not ok
    res = {"caregivers": sent, "failed": failed, "devices": len(pending), "no_caregiver": no_cg}
    audit(db, actor, "selftest_reminder", f"{month}#{round_}", **res); db.commit()
    return res


class SelfTestScheduler:
    """Called from Engine.tick (every ~5 s). Sends at most one reminder round per due date per month."""
    def tick(self, db: Session, now: datetime, notifier) -> None:
        s = settings_store.get(db, "self_test")
        if not s.get("enabled") or notifier is None:
            return
        l = now.astimezone(tz())
        hh, mm = (int(x) for x in s.get("remind_time", "10:00").split(":"))
        if (l.hour, l.minute) < (hh, mm):
            return
        month = month_key(now)
        # late sending is allowed for GRACE_DAYS (server was down on the date) — never weeks later, e.g. right after installing
        due = [r for r, day in (("1", s.get("remind_day", 1)), ("2", s.get("second_day", 0))) if day and day <= l.day <= day + GRACE_DAYS]
        if not due:
            return
        last = due[-1]          # if the server was down on day 1 and it is already past the 2nd date, send just one message
        if db.query(AuditLog).filter(AuditLog.action == "selftest_reminder", AuditLog.target.like(f"{month}#%")).filter(
                AuditLog.target >= f"{month}#{last}").first():
            return
        st = status(db, month)
        if st["active"] and st["tested"] == st["active"]:
            audit(db, "system", "selftest_reminder", f"{month}#{last}", caregivers=0, failed=0, devices=0, no_caregiver=0, skipped="all tested")
            db.commit(); return
        log.info("self-test reminder %s round %s", month, last)
        send_reminders(db, notifier, last, now=now)


def confirm_text(db: Session, d: Device, battery: int | None) -> str:
    b = f" · แบต {battery}%" if battery not in (None, 255) else ""
    return f"✅ ทดสอบปุ่มผ่าน: {_line_name(db, d)}{b}\nสัญญาณถึงเซิร์ฟเวอร์เรียบร้อย ({month_th(month_key())})"

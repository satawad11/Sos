"""Operational reports for the sub-district health office (รพ.สต.) / municipality (อบต.): monthly summary + CSV.

Dates in the API are Gregorian (YYYY-MM-DD / YYYY-MM); the CSV and the dashboard show Buddhist Era (พ.ศ.).
All day boundaries are Asia/Bangkok.  Test presses are reported separately and never counted as emergencies.
"""
from __future__ import annotations
import csv, io, re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session, joinedload
from . import settings_store
from .config import env
from .gateways import _aware
from .models import AuditLog, Case, Device, Gateway, Notification, utcnow

MAX_DAYS = 400
WEEKDAYS = ["จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์"]
STATE_TH = {"new": "รอรับเรื่อง", "accepted": "รับเรื่องแล้ว", "closed": "ปิดเคส", "cancelled": "ยกเลิกโดยผู้ป่วย"}
REASON_TH = {"resolved": "แก้ไขแล้ว", "false_alarm": "เหตุผิดพลาด", "": ""}


class RangeError(ValueError):
    pass


def tz() -> ZoneInfo:
    return ZoneInfo(env.tz)


def parse_range(month: str | None, frm: str | None, to: str | None) -> tuple[datetime, datetime, date, date]:
    """Return (start_utc, end_utc_exclusive, first_day, last_day) for ?month=YYYY-MM or ?from=&to= (inclusive)."""
    try:
        if month:
            if not re.fullmatch(r"\d{4}-\d{2}", month): raise ValueError
            y, m = int(month[:4]), int(month[5:])
            d1 = date(y, m, 1)
            d2 = (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
        elif frm and to:
            d1, d2 = date.fromisoformat(frm), date.fromisoformat(to)
        else:
            raise RangeError("ระบุ month=YYYY-MM หรือ from และ to (YYYY-MM-DD)")
    except ValueError as e:
        if isinstance(e, RangeError): raise
        raise RangeError("รูปแบบวันที่ไม่ถูกต้อง")
    if d2 < d1: raise RangeError("วันที่สิ้นสุดต้องไม่ก่อนวันที่เริ่ม")
    if (d2 - d1).days + 1 > MAX_DAYS: raise RangeError(f"ช่วงเวลายาวเกิน {MAX_DAYS} วัน")
    z = tz()
    start = datetime(d1.year, d1.month, d1.day, tzinfo=z).astimezone(timezone.utc)
    end = (datetime(d2.year, d2.month, d2.day, tzinfo=z) + timedelta(days=1)).astimezone(timezone.utc)
    return start, end, d1, d2


def be_date(dt: datetime | None) -> str:
    if dt is None: return ""
    l = _aware(dt).astimezone(tz())
    return f"{l.day:02d}/{l.month:02d}/{l.year + 543}"


def be_dt(dt: datetime | None) -> str:
    if dt is None: return ""
    l = _aware(dt).astimezone(tz())
    return f"{be_date(dt)} {l:%H:%M:%S}"


def _pct(sorted_vals: list[float], q: float):
    if not sorted_vals: return None
    i = min(len(sorted_vals) - 1, max(0, round(q * (len(sorted_vals) - 1))))
    return round(sorted_vals[i], 1)


def _cases(db: Session, start: datetime, end: datetime, tests=False) -> list[Case]:
    return (db.query(Case).options(joinedload(Case.patient), joinedload(Case.device))
            .filter(Case.created_at >= start, Case.created_at < end, Case.is_test.is_(tests)).order_by(Case.id).all())


def _gateway_outages(db: Session, start: datetime, end: datetime) -> list[dict]:
    """Pair gateway_offline / gateway_online audit rows into outage episodes, clipped to [start, end)."""
    rows = (db.query(AuditLog).filter(AuditLog.action.in_(("gateway_offline", "gateway_online")), AuditLog.at < end)
            .order_by(AuditLog.at, AuditLog.id).all())
    labels = {g.gateway_id: g.label for g in db.query(Gateway).all()}
    open_: dict[str, datetime] = {}
    eps: list[tuple[str, datetime, datetime | None]] = []
    for r in rows:
        at = _aware(r.at)
        if r.action == "gateway_offline":
            open_.setdefault(r.target, at)
        elif r.target in open_:
            eps.append((r.target, open_.pop(r.target), at))
    eps += [(t, a, None) for t, a in open_.items()]
    now = min(utcnow(), end)
    per: dict[str, dict] = {}
    for gid, a, b in eps:
        b2 = b or now
        lo, hi = max(a, start), min(b2, end)
        if hi <= lo: continue
        g = per.setdefault(gid, {"gateway_id": gid, "label": labels.get(gid, ""), "outages": 0, "downtime_min": 0.0, "ongoing": False})
        g["outages"] += 1; g["downtime_min"] += (hi - lo).total_seconds() / 60
        g["ongoing"] = g["ongoing"] or b is None
    return [{**g, "downtime_min": round(g["downtime_min"], 1)} for g in per.values()]


def summary(db: Session, start: datetime, end: datetime, d1: date, d2: date, names: bool) -> dict:
    cs = _cases(db, start, end)
    esc = settings_store.get(db, "escalation")
    z = tz()
    acc = sorted((_aware(c.accepted_at) - _aware(c.created_at)).total_seconds() for c in cs if c.accepted_at)
    clo = sorted((_aware(c.closed_at) - _aware(c.created_at)).total_seconds() / 60 for c in cs if c.closed_at and c.state == "closed")
    by_village: dict[str, dict] = {}
    by_patient: dict[int, dict] = {}
    hours = [0] * 24; wdays = [0] * 7
    for c in cs:
        v = (c.patient.village if c.patient and c.patient.village else "") or "ไม่ระบุ"
        b = by_village.setdefault(v, {"village": v, "cases": 0, "false_alarm": 0, "accept_s": []})
        b["cases"] += 1; b["false_alarm"] += c.close_reason == "false_alarm"
        if c.accepted_at: b["accept_s"].append((_aware(c.accepted_at) - _aware(c.created_at)).total_seconds())
        l = _aware(c.created_at).astimezone(z); hours[l.hour] += 1; wdays[l.weekday()] += 1
        if names and c.patient:
            bp = by_patient.setdefault(c.patient.id, {"patient_id": c.patient.id, "name": c.patient.full_name, "village": c.patient.village, "cases": 0, "false_alarm": 0})
            bp["cases"] += 1; bp["false_alarm"] += c.close_reason == "false_alarm"
    ids = [c.id for c in cs]
    notes = db.query(Notification).filter(Notification.case_id.in_(ids), Notification.channel == "line").all() if ids else []
    off = settings_store.get(db, "offline")["device_offline_min"]; now = utcnow()
    devs = db.query(Device).filter(Device.lifecycle == "active").all()
    return {
        "period": {"from": d1.isoformat(), "to": d2.isoformat(), "from_be": be_date(start), "to_be": be_date(end - timedelta(seconds=1))},
        "cases": {"total": len(cs), "resolved": sum(c.close_reason == "resolved" for c in cs),
                  "false_alarm": sum(c.close_reason == "false_alarm" for c in cs),
                  "cancelled_by_device": sum(c.state == "cancelled" for c in cs),
                  "still_open": sum(c.state in ("new", "accepted") for c in cs),
                  "after_hours": sum(c.after_hours for c in cs), "overdue": sum(c.overdue for c in cs),
                  "escalated": sum(c.esc_step >= 1 for c in cs), "tests": len(_cases(db, start, end, tests=True))},
        "response": {"accepted": len(acc), "accept_avg_s": round(sum(acc) / len(acc), 1) if acc else None,
                     "accept_median_s": _pct(acc, .5), "accept_p90_s": _pct(acc, .9),
                     "within_target_pct": round(100 * sum(a <= esc["escalate_after_s"] for a in acc) / len(acc), 1) if acc else None,
                     "target_s": esc["escalate_after_s"], "close_avg_min": round(sum(clo) / len(clo), 1) if clo else None},
        "by_village": sorted(({"village": b["village"], "cases": b["cases"], "false_alarm": b["false_alarm"],
                               "accept_avg_s": round(sum(b["accept_s"]) / len(b["accept_s"]), 1) if b["accept_s"] else None}
                              for b in by_village.values()), key=lambda x: -x["cases"]),
        "by_hour": hours, "by_weekday": [{"day": WEEKDAYS[i], "cases": n} for i, n in enumerate(wdays)],
        "top_patients": sorted(by_patient.values(), key=lambda x: -x["cases"])[:10] if names else [],
        "line": {"sent": sum(n.status == "sent" for n in notes), "failed": sum(n.status == "failed" for n in notes)},
        "devices": {"active": len(devs),
                    "offline_now": sum(1 for d in devs if d.last_seen is None or (now - _aware(d.last_seen)).total_seconds() > off * 60),
                    "low_battery": sum(1 for d in devs if d.battery is not None and d.battery != 255 and d.battery < 20),
                    "mains_lost": sum(1 for d in devs if d.mains_ok is False), "as_of": now.isoformat()},
        "gateways": _gateway_outages(db, start, end),
    }


def _safe(v) -> str:
    """Neutralise spreadsheet formula injection (a patient or note starting with = + - @ would run in Excel)."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def cases_csv(db: Session, start: datetime, end: datetime) -> tuple[str, int]:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["เลขเคส", "วันที่ (พ.ศ.)", "เวลาเกิดเหตุ", "ผู้ป่วย", "หมู่บ้าน", "อุปกรณ์", "สถานะ", "นอกเวลาราชการ", "เกินเวลา",
                "แจ้งต่อคนถัดไป (ครั้ง)", "รับเรื่องโดย", "รับเรื่องภายใน (วินาที)", "ปิดเคสเมื่อ", "ผลการปิด", "หมายเหตุ"])
    cs = _cases(db, start, end)
    for c in cs:
        acc = round((_aware(c.accepted_at) - _aware(c.created_at)).total_seconds()) if c.accepted_at else ""
        w.writerow([_safe(x) for x in (c.id, be_date(c.created_at), _aware(c.created_at).astimezone(tz()).strftime("%H:%M:%S"),
                    c.patient.full_name if c.patient else "(ไม่ทราบผู้ป่วย)", c.patient.village if c.patient else "",
                    c.device.label or c.device.dev_eui, STATE_TH.get(c.state, c.state), "ใช่" if c.after_hours else "ไม่",
                    "ใช่" if c.overdue else "ไม่", c.esc_step, c.accepted_by, acc, be_dt(c.closed_at),
                    REASON_TH.get(c.close_reason, c.close_reason), c.note)])
    return "﻿" + out.getvalue(), len(cs)          # BOM so Excel opens Thai text correctly

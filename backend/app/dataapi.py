"""Dashboard API for data management and system status: system health + backup state, device history (battery/signal),
bulk import of patients / caregivers from CSV, and the data-readiness overview ("which patient is still missing what")."""
from __future__ import annotations
import csv, io
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload
from . import backupinfo, health, privacy, reports, settings_store, telemetry
from . import selftest as stest
from .api import _engine, PRIORITIES
from .auth import admin_only, audit, current_user, staff_or_admin
from .db import SessionLocal, get_db
from .models import Assignment, Caregiver, Device, Patient, utcnow

router = APIRouter(prefix="/api")


# ---------------- system health (admin) ----------------
@router.get("/system/health")
def system_health(db: Session = Depends(get_db), u=Depends(admin_only)):
    mon = settings_store.get(db, "monitoring")
    snap = health.snapshot(SessionLocal, _engine.get("engine"), _engine.get("notify"))
    return {**snap, "monitoring": mon, "backup": backupinfo.state(int(mon.get("backup_max_age_h", 36))),
            "heartbeat": {"configured": bool((mon.get("heartbeat_url") or "").strip()), "at": health.state["hb_at"],
                          "ok": health.state["hb_ok"], "detail": health.state["hb_detail"]}}


# ---------------- device history ----------------
@router.get("/devices/{did}/telemetry")
def device_telemetry(did: int, days: int = Query(default=30, ge=1, le=400), db: Session = Depends(get_db), u=Depends(current_user)):
    d = db.get(Device, did)
    if not d: raise HTTPException(404)
    return telemetry.history(db, d, days)


@router.get("/reports/telemetry.csv")
def telemetry_csv(month: str | None = None, frm: str | None = Query(default=None, alias="from"), to: str | None = None,
                  db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    try:
        start, end, d1, d2 = reports.parse_range(month, frm, to)
    except reports.RangeError as e:
        raise HTTPException(422, str(e))
    text, n = telemetry.export_csv(db, start, end)
    audit(db, u.username, "telemetry_export", f"{d1}..{d2}", rows=n); db.commit()
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="sos-telemetry-{d1}-to-{d2}.csv"'})


# ---------------- bulk import (CSV saved from Excel / Google Sheets) ----------------
MAX_ROWS, MAX_BYTES = 1000, 1_000_000
PRI_TH = {"": "normal", "ปกติ": "normal", "normal": "normal", "เฝ้าระวัง": "watch", "watch": "watch", "วิกฤต": "critical", "critical": "critical"}
COLS = {
    "patients": [("full_name", "ชื่อ-สกุล", ("ชื่อ", "ชื่อสกุล", "ชื่อ-นามสกุล", "ชื่อ นามสกุล", "name")), ("age", "อายุ", ()),
                 ("address", "ที่อยู่", ()), ("village", "หมู่บ้าน", ("หมู่",)), ("phone", "โทร", ("เบอร์โทร", "โทรศัพท์", "เบอร์")),
                 ("lat", "ละติจูด", ("latitude",)), ("lon", "ลองจิจูด", ("longitude", "lng")), ("notes", "หมายเหตุ", ()),
                 ("priority", "ระดับ", ("ระดับความสำคัญ",))],
    "caregivers": [("full_name", "ชื่อ-สกุล", ("ชื่อ", "ชื่อสกุล", "ชื่อ-นามสกุล", "ชื่อ นามสกุล", "name")),
                   ("role", "บทบาท", ("ตำแหน่ง",)), ("phone", "โทร", ("เบอร์โทร", "โทรศัพท์", "เบอร์"))],
}
EXAMPLE = {"patients": ["นางตัวอย่าง ใจดี", "78", "12 ม.3 ต.ตัวอย่าง", "หมู่ 3", "0812345678", "13.912345", "100.612345", "", "ปกติ"],
           "caregivers": ["นายตัวอย่าง อาสา", "อสม.", "0898765432"]}
LIMITS = {"full_name": 128, "village": 64, "phone": 32, "role": 32, "address": 500, "notes": 500}


class ImportIn(BaseModel):
    csv: str = Field(max_length=MAX_BYTES)
    dry_run: bool = True


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).lower()


def _header_map(kind: str, header: list[str]) -> dict[int, str]:
    names = {}
    for field, th, alt in COLS[kind]:
        for n in (field, th, *alt):
            names[_norm(n)] = field
    return {i: names[_norm(h)] for i, h in enumerate(header) if _norm(h) in names}


def _parse(kind: str, text: str) -> tuple[list[dict], list[str]]:
    text = text.lstrip("﻿")
    first = text.splitlines()[0] if text.strip() else ""
    delim = max(",\t;", key=first.count)
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if not rows: raise HTTPException(422, "ไฟล์ว่าง")
    cols = _header_map(kind, rows[0])
    if "full_name" not in cols.values():
        raise HTTPException(422, 'ไม่พบคอลัมน์ "ชื่อ-สกุล" ในบรรทัดแรก — ดาวน์โหลดไฟล์ตัวอย่างแล้วกรอกตามนั้น')
    if len(rows) - 1 > MAX_ROWS: raise HTTPException(422, f"นำเข้าได้ครั้งละไม่เกิน {MAX_ROWS} แถว")
    unknown = [h for i, h in enumerate(rows[0]) if i not in cols and h.strip()]
    return [{"line": n + 2, **{cols[i]: v.strip() for i, v in enumerate(r) if i in cols}} for n, r in enumerate(rows[1:])], unknown


def _check(kind: str, raw: dict) -> tuple[dict, list[str]]:
    errs, d = [], {k: raw.get(k, "") for k, *_ in COLS[kind]}
    if not d["full_name"]: errs.append("ไม่มีชื่อ-สกุล")
    for k, mx in LIMITS.items():
        if k in d and len(d[k]) > mx: errs.append(f"{k} ยาวเกิน {mx} ตัวอักษร")
    if kind == "patients":
        blank = (d["lat"] == "", d["lon"] == "")
        def num(k, lo, hi, cast, label):
            if d[k] == "": d[k] = None; return
            try:
                v = cast(d[k].replace(",", "."))
                if not lo <= v <= hi: raise ValueError
                d[k] = v
            except ValueError:
                errs.append(f"{label} ไม่ถูกต้อง ({d[k]})"); d[k] = None
        num("age", 0, 130, lambda x: int(float(x)), "อายุ"); num("lat", -90, 90, float, "ละติจูด"); num("lon", -180, 180, float, "ลองจิจูด")
        if blank[0] != blank[1]: errs.append("ต้องใส่ทั้งละติจูดและลองจิจูด หรือเว้นว่างทั้งคู่")
        pr = PRI_TH.get(_norm(d["priority"]))
        if pr is None: errs.append(f'ระดับต้องเป็น ปกติ / เฝ้าระวัง / วิกฤต ({d["priority"]})')
        d["priority"] = pr or "normal"
    else:
        d["role"] = d["role"] or "อสม."
    return d, errs


@router.get("/import/{kind}/template.csv")
def import_template(kind: str, u=Depends(staff_or_admin)):
    if kind not in COLS: raise HTTPException(404)
    out = io.StringIO(); w = csv.writer(out)
    w.writerow([th for _, th, _ in COLS[kind]]); w.writerow(EXAMPLE[kind])
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="sos-import-{kind}.csv"'})


@router.post("/import/{kind}")
def import_rows(kind: str, b: ImportIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    """dry_run=true: check every row and report what would happen. dry_run=false: create the rows marked "new".
    A row whose name already exists (or appears twice in the file) is skipped, never overwritten."""
    if kind not in COLS: raise HTTPException(404)
    rows, unknown = _parse(kind, b.csv)
    if kind == "patients":
        have = {_norm(p.full_name) for p in db.query(Patient).filter(Patient.anonymized_at.is_(None)).all()}
    else:
        have = {_norm(c.full_name) for c in db.query(Caregiver).all()}
    out, seen, created = [], set(), 0
    for raw in rows:
        d, errs = _check(kind, raw)
        key = _norm(d["full_name"])
        status = "error" if errs else "duplicate" if key in have else "duplicate_in_file" if key in seen else "new"
        seen.add(key)
        if status == "new" and not b.dry_run:
            if kind == "patients":
                db.add(Patient(full_name=d["full_name"], age=d["age"], address=d["address"], village=d["village"], phone=d["phone"],
                               lat=d["lat"], lon=d["lon"], notes=d["notes"], priority=d["priority"],
                               priority_at=utcnow() if d["priority"] != "normal" else None,
                               priority_by=u.username if d["priority"] != "normal" else ""))
            else:
                db.add(Caregiver(full_name=d["full_name"], role=d["role"], phone=d["phone"]))
            created += 1
        out.append({"line": raw["line"], "status": status, "errors": errs, "data": d})
    counts = {s: sum(r["status"] == s for r in out) for s in ("new", "duplicate", "duplicate_in_file", "error")}
    if not b.dry_run:
        audit(db, u.username, f"{kind}_import", "", created=created, skipped=len(out) - created); db.commit()
    return {"kind": kind, "dry_run": b.dry_run, "rows": out, "counts": counts, "created": created, "ignored_columns": unknown}


# ---------------- data readiness ----------------
CHECKS = [("consent", "บันทึกความยินยอม (PDPA)"), ("location", "ตำแหน่งบ้าน"), ("phone", "เบอร์โทร"), ("caregiver", "อสม./ผู้ดูแล"),
          ("caregiver_line", "ผู้ดูแลลำดับ 1 ผูก LINE"), ("backup_caregiver", "ผู้ดูแลสำรอง"), ("device", "ปุ่มแจ้งเหตุ"),
          ("device_link", "ปุ่มส่งสัญญาณปกติ"), ("selftest", "ทดสอบปุ่มเดือนนี้")]
CRITICAL = ("caregiver", "caregiver_line", "device", "device_link")      # without these an alert may reach nobody


@router.get("/readiness")
def readiness(db: Session = Depends(get_db), u=Depends(current_user)):
    """Per active patient: what is in place and what is still missing for an alert to reach someone."""
    now = utcnow()
    pats = db.query(Patient).filter(Patient.active.is_(True), Patient.anonymized_at.is_(None)).order_by(Patient.id).all()
    asg: dict[int, list] = {}
    for a in db.query(Assignment).options(joinedload(Assignment.caregiver)).order_by(Assignment.patient_id, Assignment.priority).all():
        if a.caregiver.active: asg.setdefault(a.patient_id, []).append(a.caregiver)
    devs: dict[int, Device] = {}
    for d in db.query(Device).filter(Device.patient_id.isnot(None), Device.lifecycle == "active").order_by(Device.id.desc()).all():
        devs[d.patient_id] = d
    tested = {r["device_id"] for r in stest.status(db, names=False)["devices"] if r["tested"]}
    off = settings_store.get(db, "offline")["device_offline_min"]
    rows = []
    for p in pats:
        cg, d = asg.get(p.id, []), devs.get(p.id)
        link = settings_store.device_link(db, d, now, None, off)["link"] if d else None
        c = {"consent": privacy.consent_ok(p), "location": p.lat is not None and p.lon is not None, "phone": bool(p.phone.strip()),
             "caregiver": bool(cg), "caregiver_line": bool(cg and cg[0].line_user_id), "backup_caregiver": len(cg) >= 2,
             "device": d is not None, "device_link": link in ("ok", "late"), "selftest": bool(d and d.id in tested)}
        missing = [k for k, _ in CHECKS if not c[k]]
        rows.append({"id": p.id, "full_name": p.full_name, "village": p.village, "priority": p.priority or "normal",
                     "checks": c, "missing": missing, "done": len(CHECKS) - len(missing),
                     "level": "red" if any(k in CRITICAL for k in missing) else "amb" if missing else "grn",
                     "device": (d.label or d.dev_eui) if d else None, "caregiver": cg[0].full_name if cg else None})
    rows.sort(key=lambda r: ({"red": 0, "amb": 1, "grn": 2}[r["level"]], r["done"], r["id"]))
    total = len(rows)
    cgs = db.query(Caregiver).filter(Caregiver.active.is_(True)).all()
    used = {c.id for lst in asg.values() for c in lst}
    spare = db.query(Device).filter(Device.patient_id.is_(None), Device.lifecycle.in_(("ready", "active"))).count()
    return {"checks": [{"key": k, "label": lbl, "critical": k in CRITICAL, "ok": sum(r["checks"][k] for r in rows), "total": total}
                       for k, lbl in CHECKS],
            "patients": rows, "total": total, "ready": sum(r["level"] == "grn" for r in rows),
            "at_risk": sum(r["level"] == "red" for r in rows),
            "caregivers_no_line": [{"id": c.id, "full_name": c.full_name, "in_use": c.id in used} for c in cgs if not c.line_user_id],
            "spare_devices": spare}

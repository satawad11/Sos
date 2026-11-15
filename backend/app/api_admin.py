"""Settings, holidays, users, audit log, reports and organisation logos.
Split out of api.py; shared helpers and the engine handle still live there."""
from __future__ import annotations
from fastapi import APIRouter
import re
import secrets
from . import engine as engine_mod, mfa, reports, settings_store
from . import routing
from .auth import admin_only, audit, current_user, hash_pw, password_problem, staff_or_admin
from .db import get_db
from .engine import aware
from .models import AuditLog, Caregiver, Case, Device, Holiday, User, utcnow
from datetime import timedelta
from fastapi import Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from .api import iso
from .api_devices import bump_ver

router = APIRouter(prefix="/api")

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
    if key == "monitoring":
        url = str(value.get("heartbeat_url", "") or "").strip()
        if url and not (url.startswith("https://") and len(url) <= 300 and " " not in url):
            raise HTTPException(422, "heartbeat_url ต้องขึ้นต้นด้วย https:// (หรือเว้นว่างเพื่อปิด)")
        iv, age = value.get("interval_min", 5), value.get("backup_max_age_h", 36)
        if isinstance(iv, bool) or not isinstance(iv, int) or not 1 <= iv <= 60: raise HTTPException(422, "interval_min must be 1-60")
        if isinstance(age, bool) or not isinstance(age, int) or not 12 <= age <= 240: raise HTTPException(422, "backup_max_age_h must be 12-240")
        value = {"heartbeat_url": url, "interval_min": iv, "backup_max_age_h": age}
    if key == "telemetry":
        kd = value.get("keep_days")
        if isinstance(kd, bool) or not isinstance(kd, int) or not 30 <= kd <= 1825: raise HTTPException(422, "keep_days must be 30-1825")
        value = {"keep_days": kd}
    if key == "frequent":
        w, m, fa = value.get("week_threshold"), value.get("month_threshold"), value.get("count_false_alarm")
        if any(isinstance(x, bool) or not isinstance(x, int) for x in (w, m)) or not (0 <= w <= 50 and 0 <= m <= 200):
            raise HTTPException(422, "week_threshold 0-50 and month_threshold 0-200 (0 = off)")
        if not isinstance(fa, bool): raise HTTPException(422, "count_false_alarm must be true/false")
        value = {"week_threshold": w, "month_threshold": m, "count_false_alarm": fa}
        audit(db, u.username, "settings_frequent", "", week=w, month=m, false_alarm=fa)
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
        gl = value.get("gateway_line", settings_store.get(db, "system_alert").get("gateway_line", True))
        if not isinstance(gl, bool): raise HTTPException(422, "gateway_line must be true/false")
        value = {"caregiver_ids": sorted(set(ids)), "repeat_min": rep, "gateway_line": gl}
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
        cur = settings_store.get(db, "device_alert")
        value = {**{k: cur[k] for k in ("issue_repeat_h", "quiet_night", "signal_alert", "to_caregiver")}, **value}   # older clients send fewer fields
        for k in ("quiet_night", "signal_alert", "to_caregiver"):
            if not isinstance(value.get(k), bool): raise HTTPException(422, f"{k} must be true/false")
        value = {"enabled": value["enabled"], "battery_low_pct": _i("battery_low_pct", 5, 50), "mains_grace_min": _i("mains_grace_min", 1, 240),
                 "issue_repeat_h": _i("issue_repeat_h", 0, 72), "quiet_night": value["quiet_night"], "signal_alert": value["signal_alert"],
                 "to_caregiver": value["to_caregiver"]}
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


class SettingRestore(BaseModel):
    value: dict
    source_at: str = ""


@router.get("/settings/history")
def setting_history(limit: int = 50, db: Session = Depends(get_db), u=Depends(admin_only)):
    """Recent complete setting snapshots.  Values are admin-only and are safe to restore through the endpoint below."""
    rows = (db.query(AuditLog)
            .filter(AuditLog.action == "setting_update", AuditLog.target.in_(settings_store.DEFAULTS.keys()))
            .order_by(AuditLog.id.desc()).limit(min(max(limit, 1), 200)).all())
    out = []
    for row in rows:
        value = (row.detail or {}).get("value")
        if isinstance(value, dict):
            out.append({"at": iso(row.at), "actor": row.actor, "key": row.target, "value": value})
    return out


@router.post("/settings/{key}/restore")
def restore_setting(key: str, body: SettingRestore, db: Session = Depends(get_db), u=Depends(admin_only)):
    # Reuse the normal write path so restored values receive exactly the same validation and device-config side effects.
    result = put_setting(key, body.value, db, u)
    audit(db, u.username, "setting_restore", key, source_at=body.source_at)
    db.commit()
    return result


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


def user_line(db: Session, x: User) -> dict:
    return {**user_dict(x), "line": routing.state(db, x)}


class UserLineIn(BaseModel):
    system: bool | None = None
    device: bool | None = None
    cases: str | None = None


@router.put("/users/{uid}/line")
def set_user_line(uid: int, b: UserLineIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    """An admin decides what another user is told on LINE (the user can still narrow it in "my account")."""
    t = db.get(User, uid)
    if not t: raise HTTPException(404)
    try:
        routing.set_prefs(db, t, b.model_dump(), u.username)
    except routing.RoutingError as e:
        raise HTTPException(422, str(e))
    audit(db, u.username, "line_alerts_set", t.username, **{k: v for k, v in b.model_dump().items() if v is not None}); db.commit()
    return user_line(db, t)


def _other_active_admin(db: Session, uid: int) -> bool:
    return db.query(User).filter(User.role == "admin", User.active.is_(True), User.id != uid).count() > 0


@router.get("/users")
def users(db: Session = Depends(get_db), u=Depends(admin_only)):
    return [user_line(db, x) for x in db.query(User).order_by(User.id).all()]


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
    if changed:
        audit(db, u.username, "user_update", t.username, **changed)
        routing.sync_user(db, t, u.username)             # their LINE alerts follow the account (closed / lost a right / renamed)
    db.commit()
    return user_dict(t)


@router.delete("/users/{uid}")
def delete_user(uid: int, db: Session = Depends(get_db), u=Depends(admin_only)):
    t = db.get(User, uid)
    if not t: raise HTTPException(404)
    if t.id == u.id: raise HTTPException(409, "ลบบัญชีตัวเองไม่ได้")
    if t.active: raise HTTPException(409, "ต้องปิดบัญชีก่อนจึงจะลบได้")
    routing.sync_user(db, t, u.username, deleted=True)
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
    since = engine_mod.utcnow() - timedelta(days=30)
    cs = db.query(Case.state, Case.close_reason, Case.after_hours, Case.created_at, Case.accepted_at).filter(
        Case.created_at >= since, Case.is_test.is_(False)).all()
    acc = [(aware(c.accepted_at) - aware(c.created_at)).total_seconds() for c in cs if c.accepted_at]
    return {"cases_30d": len(cs), "open": sum(c.state == "new" for c in cs),
            "false_alarm": sum(c.close_reason == "false_alarm" for c in cs),
            "after_hours": sum(c.after_hours for c in cs),
            "avg_accept_s": round(sum(acc) / len(acc), 1) if acc else None, "devices": _device_health(db)}


def _device_health(db: Session) -> dict:
    """Active buttons by link state — the home page's "ปุ่มปกติ x/y" tile."""
    now, off = engine_mod.utcnow(), settings_store.get(db, "offline")["device_offline_min"]
    n = {"active": 0, "ok": 0, "late": 0, "offline": 0, "never": 0}
    for d in db.query(Device).filter(Device.lifecycle == "active").all():
        n["active"] += 1
        n[settings_store.device_link(db, d, now, None, off)["link"]] += 1
    return n

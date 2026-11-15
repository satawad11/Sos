"""Sign-in, 2-step sign-in and the signed-in user's own account.
Split out of api.py; shared helpers and the engine handle still live there."""
from __future__ import annotations
from fastapi import APIRouter
from . import mfa
from . import routing
from .auth import audit, check_pw, current_user, hash_pw, make_mfa_ticket, make_token, password_problem, user_from_mfa_ticket
from .db import get_db
from .engine import aware
from .models import User, utcnow
from datetime import timedelta
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from .api import get_engine, iso

router = APIRouter(prefix="/api")

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


# ---------------- my own LINE alerts (app/routing.py) ----------------
class MyLineIn(BaseModel):
    system: bool | None = None
    device: bool | None = None
    cases: str | None = None          # off | overdue | all


@router.get("/me/line")
def my_line(db: Session = Depends(get_db), u: User = Depends(current_user)):
    return routing.state(db, u)


@router.put("/me/line")
def my_line_put(b: MyLineIn, db: Session = Depends(get_db), u: User = Depends(current_user)):
    try:
        routing.set_prefs(db, u, b.model_dump(), u.username)
    except routing.RoutingError as e:
        raise HTTPException(403 if "สิทธิ์" in str(e) else 422, str(e))
    audit(db, u.username, "line_alerts_set", u.username, **{k: v for k, v in b.model_dump().items() if v is not None}); db.commit()
    return routing.state(db, u)


@router.post("/me/line/bind-code")
def my_line_bind(db: Session = Depends(get_db), u: User = Depends(current_user), eng=Depends(get_engine)):
    if not routing.ROLE_ALLOW.get(u.role):
        raise HTTPException(403, "บทบาทนี้ไม่รับการแจ้งเตือนทาง LINE")
    from .api import make_bind_code
    return make_bind_code(db, eng, routing.contact(db, u, create=True), u.username)


@router.post("/me/line/unbind")
def my_line_unbind(db: Session = Depends(get_db), u: User = Depends(current_user)):
    cg = routing.contact(db, u)
    if cg is not None:
        cg.line_user_id = ""; audit(db, u.username, "line_unbind", str(cg.id)); db.commit()
    return routing.state(db, u)


@router.post("/me/line/test")
def my_line_test(db: Session = Depends(get_db), u: User = Depends(current_user)):
    """Let a dashboard user verify their own LINE binding without needing an admin."""
    cg = routing.contact(db, u)
    if cg is None or not cg.line_user_id:
        raise HTTPException(409, "ยังไม่ได้ผูก LINE ของบัญชีนี้")
    from .line import LineNotifier
    ok, detail = LineNotifier().notify_system(db, cg, "✅ ทดสอบการแจ้งเตือน LINE ของคุณ — ถ้าเห็นข้อความนี้ การผูกบัญชีและการส่งแจ้งเตือนพร้อมใช้งาน")
    audit(db, u.username, "my_line_test", str(cg.id), ok=ok); db.commit()
    return {"ok": ok, "detail": "ส่งแล้ว" if ok else detail}

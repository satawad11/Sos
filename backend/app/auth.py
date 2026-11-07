from datetime import datetime, timedelta, timezone
import bcrypt, jwt
from fastapi import Depends, HTTPException, Header, Query, Request
from sqlalchemy.orm import Session
from .config import env
from .db import get_db
from .models import User, AuditLog


def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def check_pw(pw: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), h.encode())
    except ValueError:
        return False


COMMON_WEAK = {"password", "password1", "password12", "password123", "password1234", "password12345", "1234567890",
               "12345678910", "qwertyuiop", "0123456789", "admin12345", "letmein123", "iloveyou12", "11111111111"}


def password_problem(pw: str, username: str = "") -> str | None:
    """Return a human-readable reason if the password is too weak, else None."""
    if len(pw) < 10: return "รหัสผ่านต้องยาวอย่างน้อย 10 ตัวอักษร"
    if len(pw) > 128: return "รหัสผ่านยาวเกิน 128 ตัวอักษร"
    if len(set(pw)) < 5: return "รหัสผ่านซ้ำตัวอักษรมากเกินไป"
    low = pw.lower()
    if username and username.lower() in low: return "รหัสผ่านต้องไม่มีชื่อผู้ใช้อยู่ในนั้น"
    if low in COMMON_WEAK: return "รหัสผ่านนี้เดาง่ายเกินไป"
    return None


def make_token(u: User, mfa: bool = False) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=env.jwt_hours)
    return jwt.encode({"sub": u.username, "role": u.role, "tv": u.token_ver or 0, "mfa": bool(mfa), "exp": exp},
                      env.jwt_secret, algorithm="HS256")


def make_mfa_ticket(u: User) -> str:
    """Short-lived proof that the password step passed; only /api/login/2fa accepts it."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=5)
    return jwt.encode({"sub": u.username, "tv": u.token_ver or 0, "purpose": "mfa", "exp": exp}, env.jwt_secret, algorithm="HS256")


def user_from_mfa_ticket(db: Session, ticket: str) -> User:
    try:
        data = jwt.decode(ticket, env.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "ขั้นตอนเข้าสู่ระบบหมดเวลา กรุณาเข้าสู่ระบบใหม่")
    u = db.query(User).filter_by(username=data.get("sub"), active=True).first()
    if data.get("purpose") != "mfa" or not u or data.get("tv", 0) != (u.token_ver or 0):
        raise HTTPException(401, "ขั้นตอนเข้าสู่ระบบหมดเวลา กรุณาเข้าสู่ระบบใหม่")
    return u


def user_from_token(db: Session, token: str) -> User:
    try:
        data = jwt.decode(token, env.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token")
    u = db.query(User).filter_by(username=data["sub"], active=True).first()
    if not u:
        raise HTTPException(401, "user disabled")
    if data.get("purpose"):
        raise HTTPException(401, "invalid token")                           # a 2FA ticket is not a session
    if data.get("tv", 0) != (u.token_ver or 0):
        raise HTTPException(401, "session revoked, please log in again")   # password changed/reset, role changed, user disabled
    u._mfa_ok = bool(data.get("mfa"))
    return u


MUST_CHANGE_OK = ("/api/me", "/api/me/password")
MFA_SETUP_OK = ("/api/me", "/api/me/password", "/api/me/2fa/setup", "/api/me/2fa/enable")


def current_user(request: Request, authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing token")
    u = user_from_token(db, authorization[7:])
    if u.must_change and request.url.path not in MUST_CHANGE_OK:
        raise HTTPException(403, "password change required")
    from . import mfa
    if u.totp_secret and not getattr(u, "_mfa_ok", False) and mfa.required(db, u):
        raise HTTPException(401, "2fa required")                 # session from before the policy was switched on
    if not u.totp_secret and request.url.path not in MFA_SETUP_OK and mfa.required(db, u):
        raise HTTPException(403, "2fa setup required")
    return u


def require_role(*roles: str):
    def dep(u: User = Depends(current_user)) -> User:
        if u.role not in roles:
            raise HTTPException(403, "forbidden")
        return u
    return dep


staff_or_admin = require_role("staff", "admin")
admin_only = require_role("admin")


def audit(db: Session, actor: str, action: str, target: str = "", **detail):
    db.add(AuditLog(actor=actor, action=action, target=target, detail=detail))

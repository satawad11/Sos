from datetime import datetime, timedelta, timezone
import bcrypt, jwt
from fastapi import Depends, HTTPException, Header, Query
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


def make_token(u: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=env.jwt_hours)
    return jwt.encode({"sub": u.username, "role": u.role, "exp": exp}, env.jwt_secret, algorithm="HS256")


def user_from_token(db: Session, token: str) -> User:
    try:
        data = jwt.decode(token, env.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid token")
    u = db.query(User).filter_by(username=data["sub"], active=True).first()
    if not u:
        raise HTTPException(401, "user disabled")
    return u


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing token")
    return user_from_token(db, authorization[7:])


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

"""Sign-in gate for the ChirpStack web UI on its own domain (CS_DOMAIN, e.g. lns.dmtc.ac.th).

Replaces "allow only these IPs": staff IPs change (home/mobile internet), so access is tied to an SOS admin account instead.

  1. Dashboard (admin, signed in, 2FA if required) -> POST /api/chirpstack/ui-ticket -> one-time ticket, valid 60 s
  2. Browser opens https://<CS_DOMAIN>/__sos/login?t=<ticket>
     Caddy maps /__sos/* -> backend /api/cs-gate/*; the backend checks the ticket and sets the cookie `sos_cs`
     (HttpOnly, Secure, SameSite=Lax, only for <CS_DOMAIN>, 8 h) then redirects to "/"
  3. Every other request on <CS_DOMAIN> passes Caddy `forward_auth` -> GET /api/cs-gate/check: 200 = let through,
     otherwise the browser is sent back to the dashboard's ChirpStack page.

The cookie is re-checked on every request against the database: disabling the user, changing the role or the password
(token_ver) ends the ChirpStack session at once. ChirpStack's own login still applies after this gate.
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from .auth import admin_only, audit
from .config import env
from .db import get_db
from .models import User

COOKIE = "sos_cs"
TICKET_SECONDS = 60
SESSION_HOURS = 8

router = APIRouter(prefix="/api")

_used: dict[str, float] = {}        # ticket jti -> expiry (one-time use; one backend process)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dashboard_url() -> str:
    return env.public_url.rstrip("/") + "/#chirpstack"


def _sign(claims: dict, seconds: int) -> str:
    return jwt.encode({**claims, "exp": _now() + timedelta(seconds=seconds)}, env.jwt_secret, algorithm="HS256")


def _user(db: Session, token: str, purpose: str) -> tuple[User | None, dict]:
    try:
        data = jwt.decode(token, env.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None, {}
    if data.get("purpose") != purpose:
        return None, data
    u = db.query(User).filter_by(username=data.get("sub"), active=True).first()
    if not u or u.role != "admin" or data.get("tv", 0) != (u.token_ver or 0):
        return None, data
    return u, data


@router.post("/chirpstack/ui-ticket")
def ui_ticket(db: Session = Depends(get_db), u: User = Depends(admin_only)):
    if not env.cs_domain:
        raise HTTPException(409, "ยังไม่ได้เปิดโดเมนของ ChirpStack (scripts/enable-chirpstack-domain.sh)")
    t = _sign({"sub": u.username, "tv": u.token_ver or 0, "purpose": "cs-ticket", "jti": secrets.token_urlsafe(16)},
              TICKET_SECONDS)
    return {"url": f"https://{env.cs_domain}/__sos/login?t={t}", "expires_in": TICKET_SECONDS}


def _deny_page(msg: str, status: int = 403) -> HTMLResponse:
    url = _dashboard_url()
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChirpStack — ต้องเข้าผ่านระบบ SOS</title>
<body style="font-family:system-ui,sans-serif;max-width:34rem;margin:15vh auto;padding:0 1rem;line-height:1.6">
<h2>เปิดหน้า ChirpStack ผ่านระบบ SOS</h2><p>{msg}</p>
<p><a href="{url}" style="display:inline-block;padding:.6rem 1rem;background:#c62828;color:#fff;border-radius:.4rem;text-decoration:none">
ไปที่แดชบอร์ด SOS → ChirpStack → เปิดหน้า ChirpStack</a></p>
<p style="color:#666;font-size:.9em">เฉพาะผู้ดูแลระบบ (admin) ของระบบ SOS</p></body>""", status_code=status)


@router.get("/cs-gate/login")
def gate_login(t: str = "", request: Request = None, db: Session = Depends(get_db)):
    u, data = _user(db, t, "cs-ticket")
    jti = data.get("jti", "")
    now = time.time()
    for k in [k for k, exp in _used.items() if exp < now]:
        _used.pop(k, None)
    if not u or not jti or jti in _used:
        return _deny_page("ลิงก์นี้หมดอายุหรือถูกใช้ไปแล้ว — กดปุ่ม \"เปิดหน้า ChirpStack\" ในแดชบอร์ดอีกครั้ง")
    _used[jti] = float(data.get("exp", now + TICKET_SECONDS))
    session = _sign({"sub": u.username, "tv": u.token_ver or 0, "purpose": "cs-session"}, SESSION_HOURS * 3600)
    audit(db, u.username, "chirpstack_ui_login", env.cs_domain,
          ip=(request.headers.get("x-forwarded-for", "").split(",")[0].strip() if request else ""))
    db.commit()
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(COOKIE, session, max_age=SESSION_HOURS * 3600, httponly=True, secure=True, samesite="lax", path="/")
    r.headers["Cache-Control"] = "no-store"
    r.headers["Referrer-Policy"] = "no-referrer"     # the ticket is in this URL
    return r


@router.get("/cs-gate/check")
def gate_check(request: Request, db: Session = Depends(get_db)):
    u, _ = _user(db, request.cookies.get(COOKIE, ""), "cs-session")
    if u:
        return Response(status_code=200, headers={"X-SOS-User": u.username})
    # forward_auth hands this response to the browser as is
    if "text/html" in request.headers.get("accept", ""):
        return _deny_page("หน้านี้ต้องเข้าผ่านบัญชีผู้ดูแลระบบ SOS (เข้าได้จากทุกเครือข่าย ไม่ต้องเพิ่ม IP)", 401)
    return Response("sign in through the SOS dashboard", status_code=401, media_type="text/plain")


@router.get("/cs-gate/logout")
def gate_logout():
    r = RedirectResponse(_dashboard_url(), status_code=303)
    r.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return r

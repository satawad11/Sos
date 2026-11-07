"""Two-step sign-in (TOTP, RFC 6238 — Google/Microsoft Authenticator, etc.) + one-time backup codes.

Policy (settings "security"): mfa_enabled (default False) and mfa_roles (default ["admin"]).
  * policy OFF: nobody is forced; a user who switched 2FA on for themselves is still asked for a code.
  * policy ON:  users in mfa_roles must enrol before they can use the system, and sessions issued without the
                second step are refused (claim "mfa" in the JWT).
Codes are checked with ±1 step tolerance (clock drift) and a used step can never be reused (replay).
Backup codes are stored as SHA-256 hashes and are single-use.  Recovery when every admin lost their phone:
  docker compose exec backend python -m app.cli reset-2fa <username>
"""
from __future__ import annotations
import hashlib, io, secrets, time
import pyotp
from sqlalchemy.orm import Session
from . import settings_store
from .models import User

ISSUER = "SOS-Alert"
BACKUP_N = 10
_ALPH = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def required(db: Session, u: User) -> bool:
    s = settings_store.get(db, "security")
    return bool(s.get("mfa_enabled")) and u.role in (s.get("mfa_roles") or [])


def enabled(u: User) -> bool:
    return bool(u.totp_secret)


def new_secret() -> str:
    return pyotp.random_base32()


def uri(u: User, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=u.username, issuer_name=ISSUER)


def qr_svg(text: str) -> str:
    import qrcode, qrcode.image.svg
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO(); img.save(buf)
    return buf.getvalue().decode()


def _norm(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def verify_totp(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """Return the matched time-step (store it as last_step) or None. Rejects a step <= last_step (replay)."""
    code = _norm(code)
    if not (code.isdigit() and len(code) == 6) or not secret:
        return None
    t = pyotp.TOTP(secret); now = now if now is not None else time.time()
    step = int(now // t.interval)
    for s in (step, step - 1, step + 1):
        if last_step is not None and s <= last_step:
            continue
        if secrets.compare_digest(t.at(s * t.interval), code):
            return s
    return None


def _h(code: str) -> str:
    return hashlib.sha256(_norm(code).encode()).hexdigest()


def new_backup_codes() -> tuple[list[str], list[str]]:
    plain = ["".join(secrets.choice(_ALPH) for _ in range(4)) + "-" + "".join(secrets.choice(_ALPH) for _ in range(4))
             for _ in range(BACKUP_N)]
    return plain, [_h(c) for c in plain]


def use_backup(u: User, code: str) -> bool:
    if len(_norm(code)) != 8:
        return False
    h = _h(code); left = list(u.backup_codes or [])
    for i, x in enumerate(left):
        if secrets.compare_digest(x, h):
            left.pop(i); u.backup_codes = left
            return True
    return False


def clear(u: User):
    u.totp_secret = None; u.totp_pending = None; u.totp_last_step = None; u.backup_codes = None; u.totp_enabled_at = None

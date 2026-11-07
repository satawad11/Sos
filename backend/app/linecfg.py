"""LINE credentials editable from the dashboard (admin only).

Values live in sos_secrets, encrypted with a key derived from SOS_JWT_SECRET (a leaked DB dump alone is not enough), and are
never returned by the API - only "is it set" and the last 4 characters. The .env values (LINE_CHANNEL_TOKEN/SECRET) stay as
the fallback, so an existing install keeps working. Changes apply immediately, no restart.
"""
from __future__ import annotations
import base64, hashlib
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session
from .config import env
from .models import Secret

_K = {"token": "line_channel_token", "secret": "line_channel_secret", "gemini": "gemini_api_key"}
_cache: dict[str, str] = {}


def _fernet() -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"sos-secrets-v1|" + env.jwt_secret.encode()).digest()))


def load(db: Session) -> None:
    """Refresh the in-memory copy from the DB (called at startup and after every change)."""
    _cache.clear()
    for name, key in _K.items():
        row = db.get(Secret, key)
        if not row: continue
        try: _cache[name] = _fernet().decrypt(row.value.encode()).decode()
        except InvalidToken: pass            # JWT secret changed since it was saved -> treat as not set, admin re-enters it


def token() -> str:
    return _cache.get("token") or env.line_channel_token


def secret() -> str:
    return _cache.get("secret") or env.line_channel_secret


def source(name: str) -> str:
    return "dashboard" if _cache.get(name) else "env" if (env.line_channel_token if name == "token" else env.line_channel_secret) else "none"


def save(db: Session, name: str, value: str | None) -> None:
    """value None/"" removes the dashboard copy (falls back to .env)."""
    key = _K[name]; row = db.get(Secret, key)
    if not value:
        if row: db.delete(row)
    else:
        enc = _fernet().encrypt(value.encode()).decode()
        if row: row.value = enc
        else: db.add(Secret(key=key, value=enc))
    db.commit(); load(db)


def hint(v: str) -> str:
    return ("…" + v[-4:]) if len(v) >= 8 else ("•" * len(v))


def gemini_key() -> str:
    return _cache.get("gemini", "")


def flag(db: Session, key: str, default: str = "") -> str:
    """Plain (non-secret) integration flags kept in sos_secrets next to the keys, e.g. the Gemini model name."""
    row = db.get(Secret, key)
    return row.value if row else default


def set_flag(db: Session, key: str, value: str) -> None:
    row = db.get(Secret, key)
    if row: row.value = value
    else: db.add(Secret(key=key, value=value))
    db.commit()

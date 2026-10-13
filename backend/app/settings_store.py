"""Configurable settings (versioned, editable in the web UI)."""
from sqlalchemy.orm import Session
from .models import Setting, utcnow

DEFAULTS: dict[str, dict] = {
    "office_hours": {"days": [0, 1, 2, 3, 4], "start": "08:30", "end": "16:30"},  # Mon=0
    "escalation": {"escalate_after_s": 120, "overdue_after_s": 300, "final_caregiver_id": None,
                   "max_rounds": 1},
    "device_defaults": {"heartbeat_min": 30, "poll_s": 10, "buzzer": 2, "hold_ms": 1500},
    "offline": {"device_offline_min": 90, "gateway_offline_min": 15},
    "message": {"afterhours_text": "นอกเวลาราชการ กรุณาโทร 1669"},
}


def get(db: Session, key: str) -> dict:
    row = db.get(Setting, key)
    merged = dict(DEFAULTS[key])
    if row:
        merged.update(row.value)
    return merged


def put(db: Session, key: str, value: dict, actor: str) -> dict:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = db.get(Setting, key)
    if row:
        row.value, row.version, row.updated_at, row.updated_by = value, row.version + 1, utcnow(), actor
    else:
        db.add(Setting(key=key, value=value, updated_by=actor))
    db.commit()
    return get(db, key)

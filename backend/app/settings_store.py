"""Configurable settings (versioned, editable in the web UI)."""
from sqlalchemy.orm import Session
from .models import Setting, utcnow

CFG_FIELDS = ("heartbeat_min", "poll_s", "buzzer", "hold_ms")
# limits match what the firmware accepts (CONFIG downlink is clamped there too)
CFG_LIMITS = {"heartbeat_min": (5, 1275), "poll_s": (5, 120), "buzzer": (0, 3), "hold_ms": (500, 5000)}

DEFAULTS: dict[str, dict] = {
    "office_hours": {"days": [0, 1, 2, 3, 4], "start": "08:30", "end": "16:30"},  # Mon=0
    "escalation": {"escalate_after_s": 120, "overdue_after_s": 300, "final_caregiver_id": None,
                   "max_rounds": 1},
    "device_defaults": {"heartbeat_min": 30, "poll_s": 10, "buzzer": 2, "hold_ms": 1500},
    "offline": {"device_offline_min": 90, "gateway_offline_min": 5},
    "system_alert": {"caregiver_ids": [], "repeat_min": 60, "gateway_line": True},   # who gets "gateway offline" LINE alerts; 0 = no reminders
    "device_alert": {"enabled": True, "to_caregiver": True, "battery_low_pct": 20, "mains_grace_min": 10,   # LINE alert when a button goes quiet / low battery / mains lost
                     "issue_repeat_h": 0, "quiet_night": True, "signal_alert": True},   # signal: weak radio link (app/netmon.py)       # battery/mains: 0 = no reminders; hold LINE 21:00–07:00
    "privacy": {"require_consent": True, "case_retention_days": 730, "audit_retention_days": 365,
                "auto_purge": False, "line_detail": "full"},          # line_detail: full | minimal (name + village only)
    "geo": {"home_radius_m": 150, "gps_fresh_min": 15, "gateway_move_m": 200, "coverage_m": 3000},   # coverage_m: map circle per gateway
    "message": {"afterhours_text": "นอกเวลาราชการ กรุณาโทร 1669"},
    "org": {"name": "", "partner": "", "system_name": "ระบบปุ่มแจ้งเหตุฉุกเฉินผู้ป่วยติดบ้านด้วย LoRaWAN",
            "reporter_name": "", "reporter_title": "ผู้รายงาน", "approver_name": "", "approver_title": "ผู้อำนวยการ รพ.สต."},   # PDF report header/signers
    "frequent": {"week_threshold": 3, "month_threshold": 6, "count_false_alarm": True},   # "presses often": cases in the last 7 / 30 days (patients page)
    "security": {"mfa_enabled": False, "mfa_roles": ["admin"]},       # 2-step sign-in policy (off until an admin turns it on)
    "monitoring": {"heartbeat_url": "", "interval_min": 5, "backup_max_age_h": 36},   # outgoing dead-man's-switch ping + backup age alarm (app/health.py)
    "telemetry": {"keep_days": 400},                                    # device battery/signal history (sos_device_telemetry)
    "self_test": {"enabled": True, "remind_day": 1, "second_day": 15, "remind_time": "10:00", "confirm_line": True},   # monthly button test (app/selftest.py)
}


def get(db: Session, key: str) -> dict:
    # A saved setting stays in the session's identity map after the first read, so repeated calls cost nothing. A setting
    # that was never saved has no row to cache, so remember "not there" for the life of this (short) session instead of
    # asking the database again on every call — list endpoints call this once per device.
    miss = db.info.setdefault("settings_miss", set())
    row = None if key in miss else db.get(Setting, key)
    merged = dict(DEFAULTS[key])
    if row:
        merged.update(row.value)
    else:
        miss.add(key)
    return merged


def put(db: Session, key: str, value: dict, actor: str) -> dict:
    if key not in DEFAULTS:
        raise KeyError(key)
    db.info.get("settings_miss", set()).discard(key)
    row = db.get(Setting, key)
    if row:
        row.value, row.version, row.updated_at, row.updated_by = value, row.version + 1, utcnow(), actor
    else:
        db.add(Setting(key=key, value=value, updated_by=actor))
    db.commit()
    return get(db, key)


def validate_device_cfg(value: dict, partial: bool, offline_min: int | None = None) -> dict:
    """Validate device config values. partial=True allows a subset (per-device override). Raises ValueError."""
    if not isinstance(value, dict):
        raise ValueError("config must be an object")
    out = {}
    for k, v in value.items():
        if k not in CFG_LIMITS:
            raise ValueError(f"unknown field {k}")
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"{k} must be an integer")
        lo, hi = CFG_LIMITS[k]
        if not lo <= v <= hi:
            raise ValueError(f"{k} must be {lo}-{hi}")
        out[k] = v
    if not partial and set(out) != set(CFG_FIELDS):
        raise ValueError("all fields required: " + ", ".join(CFG_FIELDS))
    if offline_min is not None and "heartbeat_min" in out and out["heartbeat_min"] * 1.5 > offline_min:
        raise ValueError(f"heartbeat_min too long for offline threshold ({offline_min} min): device would show offline")
    return out


LINK_SLACK_MIN = 10          # a heartbeat may arrive this late and still count as on time


def device_link(db: Session, dev, now, cfg: dict | None = None, off_min: int | None = None) -> dict:
    """How a button is doing, judged from its last uplink against its own report interval (LoRaWAN class A cannot be
    asked "are you there?"): ok = report on time, late = missed at least one report, offline = silent past the
    device-offline threshold (the same one that sends the LINE alert), never = no uplink yet.
    late_at / off_at let a screen move the badge on as time passes without asking the server again."""
    from datetime import timedelta
    hb = (cfg or effective_device_cfg(db, dev))["heartbeat_min"]
    off = off_min if off_min is not None else get(db, "offline")["device_offline_min"]
    ls = dev.last_seen
    if ls is None:
        return {"link": "never", "hb_min": hb, "late_at": None, "off_at": None}
    if ls.tzinfo is None:
        from datetime import timezone
        ls = ls.replace(tzinfo=timezone.utc)
    late_at, off_at = ls + timedelta(minutes=hb + LINK_SLACK_MIN), ls + timedelta(minutes=max(off, hb + LINK_SLACK_MIN))
    link = "ok" if now < late_at else "late" if now < off_at else "offline"
    return {"link": link, "hb_min": hb, "late_at": late_at.isoformat(), "off_at": off_at.isoformat()}


def effective_device_cfg(db: Session, dev) -> dict:
    dflt = get(db, "device_defaults")
    cfg = {k: dflt[k] for k in CFG_FIELDS}
    cfg.update(dev.config_override or {})
    return cfg

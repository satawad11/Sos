"""Device history: one row per uplink (battery, mains, signal). Device.battery / last_rssi only hold the latest value;
this keeps the trend — when will the battery need changing, is the radio link getting worse — and feeds the research report."""
from __future__ import annotations
import csv, io
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from . import settings_store
from .gateways import _aware
from .models import Device, Telemetry, utcnow

MAX_POINTS = 400


def record(db: Session, dev: Device, up, meta: dict, now: datetime) -> None:
    gws = meta.get("gateways") or ([meta["gateway_id"]] if meta.get("gateway_id") else [])
    db.add(Telemetry(device_id=dev.id, at=now, battery=up.battery, mains_ok=up.mains_ok, rssi=meta.get("rssi"), snr=meta.get("snr"),
                     gateway_id=str(meta.get("gateway_id") or "")[:16], n_gateways=len(gws) or None, msg_type=up.msg_type))


def purge(db: Session, now: datetime | None = None) -> int:
    keep = max(30, int(settings_store.get(db, "telemetry")["keep_days"]))
    return db.query(Telemetry).filter(Telemetry.at < (now or utcnow()) - timedelta(days=keep)).delete(synchronize_session=False)


def _battery_trend(pts: list[Telemetry], low_pct: int) -> dict:
    """Straight line through the battery readings taken while NOT on mains (least squares). Needs 3 days of readings."""
    xs = [(_aware(p.at), p.battery) for p in pts if p.battery is not None and p.mains_ok is False]
    if len(xs) < 6 or (xs[-1][0] - xs[0][0]) < timedelta(days=3):
        return {"per_day": None, "days_to_low": None, "basis": len(xs)}
    t0 = xs[0][0]
    x = [(t - t0).total_seconds() / 86400 for t, _ in xs]; y = [b for _, b in xs]
    n = len(x); mx, my = sum(x) / n, sum(y) / n
    den = sum((a - mx) ** 2 for a in x)
    slope = sum((a - mx) * (b - my) for a, b in zip(x, y)) / den if den else 0.0
    left = None
    if slope < -0.01 and y[-1] > low_pct:
        left = round((y[-1] - low_pct) / -slope)
    return {"per_day": round(slope, 2), "days_to_low": left, "basis": n}


def history(db: Session, dev: Device, days: int = 30, now: datetime | None = None) -> dict:
    now = now or utcnow(); days = max(1, min(int(days), 400))
    pts = (db.query(Telemetry).filter(Telemetry.device_id == dev.id, Telemetry.at >= now - timedelta(days=days))
           .order_by(Telemetry.at, Telemetry.id).all())
    low = settings_store.get(db, "device_alert")["battery_low_pct"]
    step = max(1, -(-len(pts) // MAX_POINTS))                 # thin evenly so a chart never gets more than MAX_POINTS
    shown = pts[::step] + ([pts[-1]] if pts and (len(pts) - 1) % step else [])
    rssi = [p.rssi for p in pts if p.rssi is not None]; snr = [p.snr for p in pts if p.snr is not None]
    bat = [p.battery for p in pts if p.battery is not None]
    return {"device_id": dev.id, "days": days, "count": len(pts), "battery_low_pct": low,
            "points": [{"at": _aware(p.at).isoformat(), "battery": p.battery, "mains_ok": p.mains_ok, "rssi": p.rssi, "snr": p.snr} for p in shown],
            "summary": {"battery_now": bat[-1] if bat else None, "battery_min": min(bat) if bat else None,
                        "rssi_avg": round(sum(rssi) / len(rssi), 1) if rssi else None, "rssi_min": min(rssi) if rssi else None,
                        "snr_avg": round(sum(snr) / len(snr), 1) if snr else None,
                        "mains_off_reports": sum(p.mains_ok is False for p in pts),
                        "first_at": _aware(pts[0].at).isoformat() if pts else None, "last_at": _aware(pts[-1].at).isoformat() if pts else None,
                        "trend": _battery_trend(pts, low)}}


def export_csv(db: Session, start: datetime, end: datetime) -> tuple[str, int]:
    labels = {d.id: (d.dev_eui, d.label) for d in db.query(Device).all()}
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["at_utc", "dev_eui", "label", "battery_pct", "mains_ok", "rssi_dbm", "snr_db", "gateway_id", "n_gateways", "msg_type"])
    n = 0
    for p in db.query(Telemetry).filter(Telemetry.at >= start, Telemetry.at < end).order_by(Telemetry.at, Telemetry.id).yield_per(1000):
        eui, label = labels.get(p.device_id, ("", ""))
        w.writerow([_aware(p.at).isoformat(timespec="seconds"), eui, label, p.battery if p.battery is not None else "",
                    "" if p.mains_ok is None else int(p.mains_ok), p.rssi if p.rssi is not None else "", p.snr if p.snr is not None else "",
                    p.gateway_id, p.n_gateways or "", p.msg_type if p.msg_type is not None else ""])
        n += 1
    return "﻿" + out.getvalue(), n

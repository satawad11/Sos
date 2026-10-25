"""LINE alerts for patient devices that go quiet, run low on battery or lose mains power.

A button that has silently died is the worst failure of this system, so the people on the system-alert list
(Settings -> "ผู้รับแจ้งเตือนระบบ", same list as the gateway alerts) hear about it instead of someone noticing on a dashboard.

State lives in Device.watch = {"offline"|"battery"|"mains": {"since": iso, "notified_at": iso|None}}.
Nothing is judged while our own MQTT link is down / just reconnected, or while NO gateway is online (the gateway alert already
explains that; every device would look dead for the same reason).
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from . import settings_store
from .gateways import GatewayMonitor, _aware, _mins, _hhmm, classify
from .models import AuditLog, Device, Gateway, utcnow

log = logging.getLogger("sos.devwatch")
BATT_HYSTERESIS = 10          # battery alert clears only when it is back at (threshold + 10)%


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(s: str | None) -> datetime | None:
    return _aware(datetime.fromisoformat(s)) if s else None


class DeviceMonitor:
    def __init__(self, gw: GatewayMonitor):
        self.gw = gw

    # ---- helpers ----
    @staticmethod
    def _name(db: Session, d: Device) -> str:
        base = d.label or d.dev_eui
        if d.patient and not d.patient.anonymized_at and settings_store.get(db, "privacy")["line_detail"] == "full":
            return f"{base} ({d.patient.full_name})"
        return base

    def _any_gateway_up(self, db: Session, now: datetime) -> bool:
        gws = db.query(Gateway).filter(Gateway.enabled.is_not(False)).all()
        if not gws:
            return True                                  # nothing registered: cannot tell, do not suppress
        off = settings_store.get(db, "offline")["gateway_offline_min"]
        return any(classify(g, now, off)[0] != "offline" for g in gws)

    def _send(self, db: Session, d: Device, action: str, text: str):
        results = self.gw.send_system(db, text)
        if not results:
            log.warning("%s for %s but nobody is configured to receive system alerts", action, d.dev_eui)
        db.add(AuditLog(actor="system", action=action, target=d.dev_eui,
                        detail={"recipients": results, "no_recipients": not results}))
        self.gw.publish({"type": "device_state", "dev_eui": d.dev_eui, "issue": action})

    # ---- the check (called every few seconds by Engine.tick) ----
    def tick(self, db: Session, now: datetime | None = None, mqtt_ok: bool = True) -> int:
        now = now or utcnow()
        cfg = settings_store.get(db, "device_alert")
        if not cfg.get("enabled", True):
            return 0
        if not mqtt_ok or (self.gw.grace_until and now < _aware(self.gw.grace_until)):
            return 0
        off_min = settings_store.get(db, "offline")["device_offline_min"]
        repeat = settings_store.get(db, "system_alert").get("repeat_min", 60)
        low, grace = cfg["battery_low_pct"], cfg["mains_grace_min"]
        gw_up = self._any_gateway_up(db, now)
        n = 0
        for d in db.query(Device).filter(Device.lifecycle != "active", Device.watch.is_not(None)).all():
            d.watch = None                               # retired/ready/lost devices carry no stale alert state
        for d in db.query(Device).filter(Device.lifecycle == "active").all():
            w = dict(d.watch or {})
            before = dict(w)
            # condition per issue: True = problem, False = fine, None = unknown/keep state
            ls = _aware(d.last_seen)
            conds = {
                "offline": None if not gw_up or ls is None else (now - ls) > timedelta(minutes=off_min),
                "battery": None if d.battery is None or d.battery == 255 else
                           (True if d.battery < low else (False if d.battery >= low + BATT_HYSTERESIS else None)),
                "mains": None if d.mains_ok is None else d.mains_ok is False,
            }
            for key, bad in conds.items():
                st = w.get(key)
                if bad is None:
                    continue
                if bad:
                    if st is None:
                        st = w[key] = {"since": _iso(now), "notified_at": None}
                    since = _parse(st["since"]); last = _parse(st.get("notified_at"))
                    wait = timedelta(minutes=grace) if key == "mains" else timedelta(0)
                    if last is None and now - since >= wait:
                        st["notified_at"] = _iso(now); n += 1
                        self._send(db, d, f"device_{key}", self._text(db, d, key, now, since, off_min))
                    elif last is not None and repeat and now - last >= timedelta(minutes=repeat):
                        st["notified_at"] = _iso(now)
                        self._send(db, d, f"device_{key}_repeat", "⏰ เตือนซ้ำ: " + self._text(db, d, key, now, since, off_min, short=True))
                elif st is not None:
                    del w[key]
                    if st.get("notified_at"):
                        n += 1
                        self._send(db, d, f"device_{key}_ok", self._ok_text(db, d, key, now, _parse(st["since"])))
            if w != before:
                d.watch = w or None
        db.commit()
        return n

    # ---- texts ----
    def _text(self, db, d, key, now, since, off_min, short=False) -> str:
        nm = self._name(db, d)
        if key == "offline":
            ls = _aware(d.last_seen)
            why = f"ไม่ได้รับสัญญาณมา {_mins(now - ls)} (เห็นครั้งสุดท้าย {_hhmm(ls)} น.)"
            return (f"อุปกรณ์ {nm} ยังเงียบอยู่: {why}" if short else
                    f"⚠️ อุปกรณ์ {nm} เงียบหาย\n{why}\nอาจดับ แบตหมด หรืออยู่นอกระยะเกตเวย์ — ปุ่มอาจแจ้งเหตุไม่ได้ "
                    "โปรดโทรตรวจสอบกับผู้ป่วย/ญาติ")
        if key == "battery":
            return f"🔋 อุปกรณ์ {nm} แบตเตอรี่ต่ำ ({d.battery}%) — โปรดเปลี่ยน/ชาร์จหรือตรวจไฟบ้าน"
        return (f"⚡ อุปกรณ์ {nm} ไฟบ้านดับ นาน {_mins(now - since)}"
                + (f" (ใช้แบตสำรอง {d.battery}%)" if d.battery not in (None, 255) else "") + " — โปรดตรวจไฟที่บ้านผู้ป่วย")

    def _ok_text(self, db, d, key, now, since) -> str:
        nm = self._name(db, d)
        return {"offline": f"✅ อุปกรณ์ {nm} กลับมาส่งสัญญาณแล้ว",
                "battery": f"✅ อุปกรณ์ {nm} แบตเตอรี่กลับมาปกติ ({d.battery}%)",
                "mains": f"✅ อุปกรณ์ {nm} ไฟบ้านกลับมาแล้ว"}[key]

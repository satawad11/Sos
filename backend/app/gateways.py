"""Gateway monitoring: health reports from the gateway itself, liveness state machine, LINE alerts.

Why reports and not just device uplinks: a healthy gateway can be silent for as long as the devices'
heartbeat interval (default 30 min), so "no uplink" does not mean "gateway down". Each gateway therefore
publishes a small JSON health report every minute to  <prefix>/gateway/<id>/sos/health  (MQTT ACL: a gateway can
only write under its own id, so the id taken from the topic is trusted). Device uplinks that name the gateway
also count as proof of life.

A gateway is considered DOWN when
  - nothing was heard from it for `gateway_offline_min` minutes ("silent"), or
  - it still reports, but its radio path (concentratord / mqtt-forwarder / SPI) has been reported dead for
    > DEGRADED_GRACE_S seconds ("radio") — the Pi is up but no device frame can get through.
"""
from __future__ import annotations
import logging, re
from datetime import datetime, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from . import settings_store
from .config import env
from . import geo
from .models import AuditLog, Caregiver, Gateway, utcnow

log = logging.getLogger("sos.gateways")
GW_RE = re.compile(r"^[0-9a-f]{16}$")
DEGRADED_GRACE_S = 180          # radio path reported dead this long => counts as DOWN
STARTUP_GRACE_S = 120           # after (re)connecting to MQTT wait for fresh reports before judging anyone
MAX_REPORT_BYTES = 2048


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def _num(v, lo, hi, cast):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if not (lo <= v <= hi):
        return None
    return cast(v)


def _flag(v):
    return v if isinstance(v, bool) else None


def sanitize_report(d) -> dict | None:
    """Whitelist + range-check a health report. Anything unknown or malformed is dropped (never stored verbatim)."""
    if not isinstance(d, dict):
        return None
    out: dict = {}
    for key, lo, hi, cast in (("uptime_s", 0, 10 ** 9, int), ("cpu_temp_c", -40, 150, float), ("disk_pct", 0, 100, int),
                              ("mem_pct", 0, 100, int), ("load1", 0, 1000, float), ("radio_fail", 0, 10 ** 6, int)):
        v = _num(d.get(key), lo, hi, cast)
        if v is not None:
            out[key] = round(v, 1) if cast is float else v
    for key in ("spi", "ntp", "agent"):
        v = _flag(d.get(key))
        if v is not None:
            out[key] = v
    sv = d.get("services")
    if isinstance(sv, dict):
        out["services"] = {k: sv[k] for k in ("concentratord", "forwarder") if isinstance(sv.get(k), bool)}
    th = d.get("throttled")
    if isinstance(th, str) and re.fullmatch(r"0x[0-9a-fA-F]{1,8}", th):
        out["throttled"] = th.lower()
    for key in ("ip", "sw"):
        v = d.get(key)
        if isinstance(v, str) and re.fullmatch(r"[0-9A-Za-z .:_/+-]{1,40}", v):
            out[key] = v
    gp = d.get("gps")
    if isinstance(gp, dict):
        lat, lon = gp.get("lat"), gp.get("lon")
        if geo.valid_latlon(lat, lon) and gp.get("fix") is True:
            g = {"fix": True, "lat": round(float(lat), 6), "lon": round(float(lon), 6)}
            sats = _num(gp.get("sats"), 0, 99, int)
            if sats is not None: g["sats"] = sats
            alt = _num(gp.get("alt"), -500, 10000, float)
            if alt is not None: g["alt"] = round(alt, 1)
            out["gps"] = g
        elif gp.get("fix") is False:
            out["gps"] = {"fix": False}                 # a GPS is attached but has no fix right now
    return out or None


def critical(rep: dict | None) -> list[str]:
    """Radio path broken: the Pi may be up but no device frame can reach the server."""
    if not rep:
        return []
    out = []
    sv = rep.get("services") or {}
    if sv.get("concentratord") is False: out.append("ตัวรับสัญญาณ LoRa (concentratord) หยุดทำงาน")
    if sv.get("forwarder") is False: out.append("ตัวส่งต่อ MQTT (forwarder) หยุดทำงาน")
    if rep.get("spi") is False: out.append("ไม่พบ SPI ของ WM1302")
    return out


def warnings(rep: dict | None) -> list[str]:
    if not rep:
        return []
    out = []
    th = rep.get("throttled")
    if th:
        v = int(th, 16)
        if v & 0x1: out.append("ไฟเลี้ยงต่ำ (undervoltage) ตอนนี้")
        elif v & 0x4: out.append("CPU ถูกลดความเร็ว")
        elif v & 0x10000: out.append("เคยมีไฟเลี้ยงต่ำตั้งแต่บูต")
    if rep.get("cpu_temp_c", 0) >= 80: out.append(f"CPU ร้อน {rep['cpu_temp_c']} °C")
    if rep.get("disk_pct", 0) >= 90: out.append(f"การ์ดเต็ม {rep['disk_pct']}%")
    if rep.get("mem_pct", 0) >= 90: out.append(f"หน่วยความจำเต็ม {rep['mem_pct']}%")
    if rep.get("ntp") is False: out.append("เวลายังไม่ซิงก์ (NTP)")
    return out


GPS_FRESH_S = 30 * 60


def moved_m(g: Gateway, now: datetime, limit_m: int | None = None) -> float | None:
    """Metres between the REGISTERED position and a fresh GPS fix of the gateway itself (None if either is missing).
    Returns a value only when it exceeds limit_m (when given): used to flag a gateway that was moved or stolen."""
    if g.lat is None or g.lon is None or g.gps_lat is None or g.gps_lon is None or not g.gps_at:
        return None
    if (now - _aware(g.gps_at)).total_seconds() > GPS_FRESH_S:
        return None
    d = geo.distance_m(g.lat, g.lon, g.gps_lat, g.gps_lon)
    return round(d) if (limit_m is None or d > limit_m) else None


def classify(g: Gateway, now: datetime, off_min: int, move_m: int | None = None) -> tuple[str, str]:
    """Return (status, reason). status: disabled|unknown|online|warning|offline ; reason only for offline."""
    if g.enabled is False:
        return "disabled", ""
    ls = _aware(g.last_seen)
    if ls is None:
        return "unknown", ""
    if (now - ls).total_seconds() > off_min * 60:
        return "offline", "silent"
    ds = _aware(g.degraded_since)
    if ds and (now - ds).total_seconds() >= DEGRADED_GRACE_S:
        return "offline", "radio"
    if ds or warnings(g.report) or (move_m is not None and moved_m(g, now, move_m) is not None):
        return "warning", ""
    return "online", ""


def _hhmm(dt: datetime) -> str:
    return _aware(dt).astimezone(ZoneInfo(env.tz)).strftime("%H:%M")


def _mins(td: timedelta) -> str:
    m = int(td.total_seconds() // 60)
    return f"{m} นาที" if m < 120 else f"{m // 60} ชม. {m % 60} นาที"


class GatewayMonitor:
    def __init__(self, notifier=None, publish: Callable[[dict], None] | None = None):
        self.notifier = notifier
        self.publish = publish or (lambda e: None)
        self.grace_until: datetime | None = None

    # ---- inputs ----
    def mqtt_connected(self, now: datetime | None = None):
        """Call when the backend (re)connects to MQTT: do not judge gateways until fresh reports have had time to arrive."""
        self.grace_until = (now or utcnow()) + timedelta(seconds=STARTUP_GRACE_S)

    def _get(self, db: Session, gid: str, create=True) -> Gateway | None:
        g = db.query(Gateway).filter_by(gateway_id=gid).first()
        if g is None and create:
            g = Gateway(gateway_id=gid, enabled=True, state="unknown", uplinks_total=0)
            db.add(g); db.flush()
        return g

    def on_uplink(self, db: Session, gid: str, now: datetime):
        g = self._get(db, gid)
        g.last_seen = now
        g.uplinks_total = (g.uplinks_total or 0) + 1
        g.degraded_since = None          # a frame just came through it, so the radio path works

    def on_report(self, db: Session, gid: str, raw, now: datetime) -> str:
        if not GW_RE.match(gid or ""):
            return "bad_id"
        rep = sanitize_report(raw)
        if rep is None:
            return "bad_report"
        g = self._get(db, gid)
        g.last_seen = g.last_report_at = now
        g.report = rep
        gp = rep.get("gps")
        if gp and gp.get("fix") and gp.get("sats", 4) >= 4:        # ignore 2-3 satellite "fixes": they can be hundreds of metres off
            g.gps_lat, g.gps_lon, g.gps_at, g.gps_sats = gp["lat"], gp["lon"], now, gp.get("sats")
        if critical(rep):
            g.degraded_since = g.degraded_since or now
        else:
            g.degraded_since = None
        db.commit()
        return "ok"

    # ---- state machine (called every few seconds) ----
    def tick(self, db: Session, now: datetime | None = None, mqtt_ok: bool = True) -> int:
        now = now or utcnow()
        if not mqtt_ok or (self.grace_until and now < _aware(self.grace_until)):
            return 0                       # our own link is down/just came back: reports are missing because of US
        off = settings_store.get(db, "offline")["gateway_offline_min"]
        sa = settings_store.get(db, "system_alert")
        repeat, line_on = sa.get("repeat_min", 60), sa.get("gateway_line", True) is not False
        changed = 0
        for g in db.query(Gateway).filter(Gateway.enabled.is_not(False)).all():
            goff = g.offline_min or off                 # per-gateway threshold overrides the global one
            st, reason = classify(g, now, goff)
            if g.alerts_muted and g.maint_end == "time" and g.maint_until and now >= _aware(g.maint_until):
                # still down -> one LINE now (a gateway that only now goes offline gets the normal alert just below)
                self.end_maint(db, g, "system", "expired", now, line_on, st == "offline" and g.state == "offline")
            line = line_on and not g.alerts_muted        # LINE off for all gateways (settings) or this one (maintenance)
            if st == "offline":
                if g.state != "offline":
                    g.state, g.state_changed_at, g.offline_reason, g.offline_notified_at = "offline", now, reason, now
                    self._alert(db, g, self._down_text(g, reason, now, goff), "gateway_offline", line)
                    changed += 1
                else:
                    last = _aware(g.offline_notified_at)
                    if line and repeat and last and (now - last) >= timedelta(minutes=repeat):
                        g.offline_notified_at = now
                        self._alert(db, g, "⏰ เตือนซ้ำ: " + self._down_text(g, g.offline_reason, now, goff, short=True),
                                    "gateway_offline_repeat")
            elif st in ("online", "warning"):
                if g.state == "offline":
                    was = _aware(g.state_changed_at)
                    g.state, g.state_changed_at, g.offline_reason = "online", now, ""
                    if g.alerts_muted and g.maint_end in (None, "online"):
                        g.offline_notified_at = None             # repaired: maintenance ends by itself, with its own message
                        self.end_maint(db, g, "system", "online", now, line_on, False)
                    elif g.offline_notified_at:
                        g.offline_notified_at = None
                        self._alert(db, g, f"✅ เกตเวย์ {self._name(g)} กลับมาออนไลน์แล้ว"
                                           + (f" (ขาดไปประมาณ {_mins(now - was)})" if was else ""), "gateway_online", line)
                    changed += 1
                elif g.state != "online":
                    g.state, g.state_changed_at = "online", now
        db.commit()
        return changed

    # ---- alerts ----
    @staticmethod
    def _name(g: Gateway) -> str:
        return f"{g.label} ({g.gateway_id})" if g.label else g.gateway_id

    def _down_text(self, g: Gateway, reason: str, now: datetime, off: int, short=False) -> str:
        if reason == "radio":
            why = "ยังต่อเน็ตได้ แต่ตัวรับสัญญาณ LoRa ใช้งานไม่ได้: " + "; ".join(critical(g.report) or ["ไม่ทราบสาเหตุ"])
        else:
            ls = _aware(g.last_seen)
            why = f"ไม่ได้รับสัญญาณมา {_mins(now - ls)} (เห็นครั้งสุดท้าย {_hhmm(ls)} น.)" if ls else "ไม่ได้รับสัญญาณ"
        if short:
            return f"เกตเวย์ {self._name(g)} ยังออฟไลน์: {why}"
        return (f"⚠️ เกตเวย์ {self._name(g)} ขาดการเชื่อมต่อ\n{why}\n"
                "ปุ่มแจ้งเหตุของผู้ป่วยที่อยู่ในระยะเกตเวย์นี้อาจส่งสัญญาณมาไม่ถึง "
                "โปรดตรวจไฟ/อินเทอร์เน็ตที่เกตเวย์ (ตรวจเบื้องต้นก่อน แล้วแจ้งผู้ดูแลระบบ)")

    def recipients(self, db: Session, override: list | None = None) -> list[Caregiver]:
        """override = a gateway's own list (None / empty -> the global system-alert list)."""
        ids = list(override) if override else list(settings_store.get(db, "system_alert").get("caregiver_ids") or [])
        fin = settings_store.get(db, "escalation").get("final_caregiver_id")
        if not ids and fin:
            ids = [fin]
        out = []
        for i in ids:
            c = db.get(Caregiver, i)
            if c and c.active and c not in out:
                out.append(c)
        return out

    def send_system(self, db: Session, text: str, override: list | None = None) -> list[dict]:
        """Push a plain-text LINE message to the system-alert recipients. Returns per-recipient results."""
        res = []
        fn = getattr(self.notifier, "notify_system", None)
        for c in self.recipients(db, override):
            if fn is None:
                ok, detail = False, "no notifier"
            elif not c.line_user_id:
                ok, detail = False, "caregiver has no LINE binding"
            else:
                try:
                    ok, detail = fn(db, c, text)
                except Exception as e:                   # one bad recipient must not stop the others
                    log.exception("system alert failed"); ok, detail = False, f"error: {e}"
            res.append({"caregiver": c.full_name, "ok": ok, "detail": detail})
        return res

    # ---- maintenance (no LINE for this gateway; the dashboard still shows its real state) ----
    def start_maint(self, db: Session, g: Gateway, actor: str, end: str, until: datetime | None, reason: str, now: datetime):
        fresh = not g.alerts_muted
        g.alerts_muted, g.maint_end, g.maint_reason, g.maint_by = True, end, reason, actor
        g.maint_until = until if end == "time" else None
        if fresh or g.maint_since is None:
            g.maint_since = now
        db.add(AuditLog(actor=actor, action="gateway_maint_on" if fresh else "gateway_maint_change", target=g.gateway_id,
                        detail={"end": end, "until": until.isoformat() if until and end == "time" else None, "reason": reason}))
        self.publish({"type": "gateway_state", "gateway_id": g.gateway_id, "state": g.state, "maint": True})

    def end_maint(self, db: Session, g: Gateway, actor: str, why: str, now: datetime, line_on: bool = True,
                  still_down: bool | None = None):
        """why: manual | expired | online. Ending while the gateway is still offline sends one LINE (its alerts are back on);
        ending because it came back sends a 'repair done' LINE instead of the usual recovery message."""
        if not g.alerts_muted:
            return
        since, reason = _aware(g.maint_since), g.maint_reason or ""
        g.alerts_muted, g.maint_since, g.maint_until, g.maint_end, g.maint_reason, g.maint_by = False, None, None, None, None, None
        db.add(AuditLog(actor=actor, action="gateway_maint_off", target=g.gateway_id,
                        detail={"why": why, "minutes": round((now - since).total_seconds() / 60) if since else None}))
        if still_down is None:
            still_down = g.state == "offline"
        if why == "online":                              # logged as gateway_online too: closes the outage in the reports
            self._alert(db, g, f"✅ ซ่อมบำรุงเสร็จ: เกตเวย์ {self._name(g)} กลับมาออนไลน์แล้ว"
                               + (f" (ใช้เวลาประมาณ {_mins(now - since)})" if since else ""), "gateway_online", line_on)
        elif still_down:
            g.offline_notified_at = now                  # reminders continue from here
            head = "⏰ หมดเวลาซ่อมบำรุงแล้ว" if why == "expired" else "🔧 ปิดโหมดซ่อมบำรุงแล้ว"
            self._alert(db, g, f"{head} แต่เกตเวย์ {self._name(g)} ยังออฟไลน์อยู่"
                               + (f"\nงานซ่อม: {reason}" if reason else "")
                               + "\nปุ่มแจ้งเหตุของผู้ป่วยที่อยู่ในระยะเกตเวย์นี้อาจส่งสัญญาณมาไม่ถึง", "gateway_maint_overdue", line_on)
        else:
            self.publish({"type": "gateway_state", "gateway_id": g.gateway_id, "state": g.state, "maint": False})

    def _alert(self, db: Session, g: Gateway, text: str, action: str, line: bool = True):
        """line=False: LINE is switched off (all gateways, or this one is in maintenance). The state change is
        still logged and pushed to the dashboard."""
        results = self.send_system(db, text, g.alert_ids) if line else []
        if line and not results:
            log.warning("%s for %s but nobody is configured to receive system alerts", action, g.gateway_id)
        extra = {} if line else {"line": "maintenance" if g.alerts_muted else "off"}
        db.add(AuditLog(actor="system", action=action, target=g.gateway_id,
                        detail={"recipients": results, "no_recipients": line and not results, **extra}))
        self.publish({"type": "gateway_state", "gateway_id": g.gateway_id, "state": g.state})

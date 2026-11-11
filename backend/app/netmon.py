"""Network monitoring of the LoRaWAN link, from ChirpStack's MQTT application events (JSON).

- every uplink -> UplinkLog (radio history) + Device.sig (rolling link quality, used by the "weak signal" alert)
- join / ack / txack / log / status -> NetEvent (live view, diagnosis) ; our downlinks are logged as kind "down"
- PingTest: a confirmed test downlink. Buttons are LoRaWAN class A: a downlink waits for the button's next uplink
  (heartbeat, default every 30 min) and the button confirms it in the uplink after that. ChirpStack reports
  txack (gateway sent it) and ack (button confirmed / did not confirm).
- diagnose(): reads recent events + state and explains in Thai why a button cannot join / talk, with a fix action.

Link margin = SNR above the LoRa demodulation floor for the spreading factor used. It is the honest measure of
"how close to losing the button" a link is (RSSI alone depends on noise). < 3 dB = about to drop out.
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy.orm import Session

from .models import Device, NetEvent, PingTest, UplinkLog, utcnow

log = logging.getLogger("sos.netmon")

KEEP_UPLINK_DAYS = 90
KEEP_EVENT_DAYS = 14
SIG_WINDOW = 20                 # Device.sig = last N uplinks
PING_FPORT = 20                 # ignored by the button firmware (it acts on 10/11/12 only); the LoRaWAN stack still ACKs
PING_PAYLOAD = b"P"
# LoRa demodulation floor (dB SNR) per spreading factor (Semtech SX126x/SX130x datasheets)
SNR_FLOOR = {7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}
EVENT_KINDS = ("join", "ack", "txack", "log", "status")


def _aw(dt: datetime | None) -> datetime | None:
    from .engine import aware
    return aware(dt)


def margin(snr: float | None, sf: int | None) -> float | None:
    if snr is None:
        return None
    return round(snr - SNR_FLOOR.get(sf or 0, -12.5), 1)


def quality(m: float | None, loss: float | None = None) -> str:
    """good | fair | weak | bad | unknown — same words on the device page, the map and the alert."""
    if m is None:
        return "unknown"
    if m < 3 or (loss is not None and loss >= 30): return "bad"
    if m < 6 or (loss is not None and loss >= 15): return "weak"
    if m < 10: return "fair"
    return "good"


QUALITY_TH = {"good": "ดีมาก", "fair": "ดี", "weak": "อ่อน", "bad": "แย่ — ใกล้หลุด", "unknown": "ยังไม่มีข้อมูล"}


def loss_pct(fcnts: list[int]) -> float | None:
    """Missing frames between consecutive frame counters (oldest -> newest). A counter going back = button re-joined:
    that gap is not counted. Duplicates are ignored."""
    prev = None; got = 0; expected = 0
    for f in fcnts:
        if f is None: continue
        if prev is not None:
            if f == prev: continue
            if f > prev:
                expected += f - prev; got += 1
        prev = f
    if expected < 5:
        return None
    return round(max(0.0, (expected - got) * 100.0 / expected), 1)


def _parse_up(j: dict) -> dict:
    rx = j.get("rxInfo") or []
    best = max(rx, key=lambda r: (r.get("snr") is not None, r.get("snr") or -99, r.get("rssi") or -999)) if rx else {}
    lora = ((j.get("txInfo") or {}).get("modulation") or {}).get("lora") or {}
    return {"f_cnt": j.get("fCnt"), "f_port": int(j.get("fPort") or 0), "rssi": best.get("rssi"), "snr": best.get("snr"),
            "sf": lora.get("spreadingFactor"), "freq": (j.get("txInfo") or {}).get("frequency"),
            "gateway_id": str(best.get("gatewayId", "")).lower(), "gw_count": len(rx) or 1,
            "confirmed": bool(j.get("confirmed")), "dr": j.get("dr")}


# ---- Thai explanations of ChirpStack log codes ----
def explain_log(code: str, desc: str) -> tuple[str, str]:
    """(level, Thai text) for a ChirpStack LogEvent."""
    d = (desc or "").lower()
    if code == "UPLINK_MIC" or "mic" in d:
        return "error", "ChirpStack ปฏิเสธข้อความ: รหัสยืนยัน (MIC) ไม่ตรง — AppKey บนปุ่มไม่ตรงกับใน ChirpStack"
    if code == "OTAA" and "devnonce" in d:
        return "error", "ปุ่มขอเข้าเครือข่ายด้วย DevNonce ที่เคยใช้แล้ว — มักเกิดหลังแฟลช/รีเซ็ตปุ่ม (แก้ได้ด้วยปุ่ม \"ล้าง DevNonce\")"
    if code == "OTAA":
        return "error", "ขอเข้าเครือข่าย (OTAA) ไม่สำเร็จ: " + (desc or "")[:150]
    if code in ("UPLINK_F_CNT_RESET", "UPLINK_F_CNT_RETRANSMISSION"):
        return "warning", "ตัวนับข้อความของปุ่มย้อนกลับ/ซ้ำ — ปุ่มอาจรีสตาร์ตโดยไม่ได้เข้าเครือข่ายใหม่"
    if code == "DOWNLINK_GATEWAY":
        return "warning", "เกตเวย์ส่งข้อความกลับไปที่ปุ่มไม่สำเร็จ (" + (desc or "")[:80] + ")"
    if code == "EXPIRED":
        return "warning", "ข้อความที่รอส่งไปปุ่มหมดอายุในคิว (ปุ่มไม่ได้ส่งสัญญาณขึ้นมาทันเวลา)"
    if code == "F_CNT_DOWN":
        return "warning", "ตัวนับข้อความขาลงไม่ตรง — ข้อความไปปุ่มถูกข้าม"
    if code in ("UPLINK_CODEC", "DOWNLINK_CODEC"):
        return "warning", "codec ใน Device profile แปลงข้อมูลไม่ได้ (ไม่กระทบการแจ้งเหตุ ระบบ SOS ถอดรหัสเอง)"
    return ("error" if "error" in d else "info"), (desc or code or "log")[:200]


class NetMon:
    def __init__(self, publish: Callable[[dict], None] | None = None):
        self.publish = publish or (lambda e: None)
        self._last_prune: datetime | None = None

    # ---------------- inbound from MQTT ----------------
    def on_uplink(self, db: Session, dev_eui: str, j: dict) -> None:
        u = _parse_up(j)
        db.add(UplinkLog(dev_eui=dev_eui, at=utcnow(), f_cnt=u["f_cnt"], f_port=u["f_port"], rssi=u["rssi"], snr=u["snr"],
                         sf=u["sf"], freq=u["freq"], gateway_id=u["gateway_id"], gw_count=u["gw_count"],
                         confirmed=u["confirmed"]))
        db.flush()
        self._update_sig(db, dev_eui)
        db.commit()
        self.publish({"type": "net_event", "dev_eui": dev_eui, "kind": "up", "level": "info",
                      "text": self._up_text(u), "at": utcnow().isoformat()})

    @staticmethod
    def _up_text(u: dict) -> str:
        m = margin(u["snr"], u["sf"])
        return (f"ข้อความขึ้น fPort {u['f_port']} · FCnt {u['f_cnt']} · RSSI {u['rssi']} dBm · SNR {u['snr']} dB"
                + (f" · SF{u['sf']}" if u["sf"] else "") + (f" · margin {m} dB" if m is not None else "")
                + (f" · ผ่าน {u['gw_count']} เกตเวย์" if u["gw_count"] > 1 else ""))

    def on_event(self, db: Session, kind: str, dev_eui: str, j: dict) -> None:
        if kind not in EVENT_KINDS:
            return
        level, code, text, detail = "info", "", "", {}
        if kind == "join":
            text = f"ปุ่มเข้าเครือข่ายสำเร็จ (DevAddr {j.get('devAddr', '')})"
            detail = {"dev_addr": j.get("devAddr")}
        elif kind == "log":
            code = str(j.get("code", ""))
            level, text = explain_log(code, str(j.get("description", "")))
            lv = str(j.get("level", "")).lower()
            if lv in ("error", "warning") and level == "info": level = lv
            detail = {"description": str(j.get("description", ""))[:300], "context": j.get("context") or {}}
        elif kind == "txack":
            text = f"เกตเวย์ส่งข้อความลงไปที่ปุ่มแล้ว (FCntDown {j.get('fCntDown', '')})"
            detail = {"queue_id": j.get("queueItemId", ""), "gateway_id": str(j.get("gatewayId", "")).lower()}
            self._ping_update(db, detail["queue_id"], "sent", detail["gateway_id"])
        elif kind == "ack":
            ok = bool(j.get("acknowledged"))
            level = "info" if ok else "warning"
            text = "ปุ่มยืนยันรับข้อความแล้ว" if ok else "ปุ่มไม่ยืนยันรับข้อความ (อาจไม่ได้รับ)"
            detail = {"queue_id": j.get("queueItemId", ""), "acknowledged": ok}
            self._ping_update(db, detail["queue_id"], "acked" if ok else "nack")
        elif kind == "status":
            mg = j.get("margin")
            text = f"สถานะจากปุ่ม: link margin {mg} dB" + ("" if j.get("batteryLevelUnavailable") else
                                                           f" · แบต {j.get('batteryLevel')}%")
            detail = {"margin": mg, "battery": j.get("batteryLevel"), "external_power": j.get("externalPowerSource")}
        self.add_event(db, dev_eui, kind, level, code, text, detail)
        db.commit()

    def add_event(self, db: Session, dev_eui: str, kind: str, level: str, code: str, text: str,
                  detail: dict | None = None) -> NetEvent:
        e = NetEvent(dev_eui=dev_eui, at=utcnow(), kind=kind, level=level, code=code[:40], text=text[:300], detail=detail)
        db.add(e)
        self.publish({"type": "net_event", "dev_eui": dev_eui, "kind": kind, "level": level, "code": code,
                      "text": text[:300], "at": e.at.isoformat()})
        return e

    def on_downlink(self, db: Session, dev_eui: str, fport: int, nbytes: int, why: str = "") -> None:
        """Our own downlinks (case ACK, message, config) — so the live view shows both directions."""
        names = {10: "ACK การรับเรื่อง", 11: "ตั้งค่าปุ่ม", 12: "ข้อความถึงผู้ป่วย", PING_FPORT: "ทดสอบส่งกลับ (ping)"}
        self.add_event(db, dev_eui, "down", "info", str(fport),
                       f"ส่งข้อความลง fPort {fport} ({names.get(fport, why or 'อื่น ๆ')}, {nbytes} ไบต์) — รอปุ่มส่งสัญญาณขึ้นมาเพื่อรับ")
        db.commit()

    # ---------------- link quality ----------------
    def _update_sig(self, db: Session, dev_eui: str) -> None:
        d = db.query(Device).filter_by(dev_eui=dev_eui).first()
        if not d:
            return
        rows = (db.query(UplinkLog).filter_by(dev_eui=dev_eui).order_by(UplinkLog.at.desc(), UplinkLog.id.desc())
                .limit(SIG_WINDOW).all())[::-1]
        ms = [m for m in (margin(r.snr, r.sf) for r in rows) if m is not None]
        rs = [r.rssi for r in rows if r.rssi is not None]
        sn = [r.snr for r in rows if r.snr is not None]
        lp = loss_pct([r.f_cnt for r in rows])
        med = round(statistics.median(ms), 1) if ms else None
        d.sig = {"n": len(rows), "margin": med, "rssi": round(statistics.median(rs)) if rs else None,
                 "snr": round(statistics.median(sn), 1) if sn else None, "loss": lp, "q": quality(med, lp),
                 "sf": rows[-1].sf if rows else None, "at": utcnow().isoformat()}

    def stats(self, db: Session, dev_eui: str, days: int = 7) -> dict:
        """History for the device page: daily medians, per-gateway breakdown, loss, recent points."""
        since = utcnow() - timedelta(days=days)
        rows = (db.query(UplinkLog).filter(UplinkLog.dev_eui == dev_eui, UplinkLog.at >= since)
                .order_by(UplinkLog.at, UplinkLog.id).all())
        by_day: dict[str, list[UplinkLog]] = {}
        from zoneinfo import ZoneInfo
        from .config import env
        tz = ZoneInfo(env.tz)
        for r in rows:
            by_day.setdefault(_aw(r.at).astimezone(tz).strftime("%Y-%m-%d"), []).append(r)
        daily = []
        for day in sorted(by_day):
            rs = by_day[day]
            ms = [m for m in (margin(r.snr, r.sf) for r in rs) if m is not None]
            daily.append({"day": day, "n": len(rs), "margin": round(statistics.median(ms), 1) if ms else None,
                          "rssi": round(statistics.median([r.rssi for r in rs if r.rssi is not None])) if any(r.rssi is not None for r in rs) else None,
                          "snr": round(statistics.median([r.snr for r in rs if r.snr is not None]), 1) if any(r.snr is not None for r in rs) else None,
                          "loss": loss_pct([r.f_cnt for r in rs])})
        per_gw: dict[str, list[UplinkLog]] = {}
        for r in rows:
            per_gw.setdefault(r.gateway_id or "?", []).append(r)
        gws = []
        for g, rs in sorted(per_gw.items(), key=lambda x: -len(x[1])):
            ms = [m for m in (margin(r.snr, r.sf) for r in rs) if m is not None]
            mm = round(statistics.median(ms), 1) if ms else None
            gws.append({"gateway_id": g, "n": len(rs), "margin": mm, "q": quality(mm),
                        "rssi": round(statistics.median([r.rssi for r in rs if r.rssi is not None])) if any(r.rssi is not None for r in rs) else None})
        ms = [m for m in (margin(r.snr, r.sf) for r in rows) if m is not None]
        mm = round(statistics.median(ms), 1) if ms else None
        lp = loss_pct([r.f_cnt for r in rows])
        pts = [{"at": _aw(r.at).isoformat(), "rssi": r.rssi, "snr": r.snr, "margin": margin(r.snr, r.sf), "sf": r.sf,
                "gw": r.gateway_id, "fport": r.f_port} for r in rows[-300:]]
        return {"days": days, "n": len(rows), "margin": mm, "loss": lp, "q": quality(mm, lp), "q_th": QUALITY_TH[quality(mm, lp)],
                "daily": daily, "gateways": gws, "points": pts}

    # ---------------- ping ----------------
    def _ping_update(self, db: Session, queue_id: str, status: str, gateway_id: str = "") -> None:
        if not queue_id:
            return
        p = db.query(PingTest).filter_by(queue_id=queue_id).first()
        if not p or p.status in ("acked", "nack", "expired", "cancelled", "failed"):
            return
        now = utcnow()
        if status == "sent":
            p.status, p.sent_at, p.gateway_id = "sent", now, gateway_id or p.gateway_id
        else:
            p.status, p.done_at = status, now
            if not p.sent_at: p.sent_at = now
        self.publish({"type": "ping", "dev_eui": p.dev_eui, "id": p.id, "status": p.status})

    def cancel_pings(self, db: Session, dev_eui: str, why: str) -> int:
        n = 0
        for p in db.query(PingTest).filter(PingTest.dev_eui == dev_eui, PingTest.status.in_(("queued", "sent"))).all():
            p.status, p.done_at, p.note = "cancelled", utcnow(), why[:200]; n += 1
        if n: db.commit()
        return n

    def expire_pings(self, db: Session, now: datetime, hb_min: int) -> int:
        """queued: the button never sent an uplink to carry it; sent: it never confirmed. 2 heartbeats + 15 min."""
        lim = timedelta(minutes=2 * max(hb_min, 5) + 15)
        n = 0
        for p in db.query(PingTest).filter(PingTest.status.in_(("queued", "sent"))).all():
            base = _aw(p.sent_at or p.created_at)
            if now - base > lim:
                p.status, p.done_at = "expired", now
                p.note = ("ปุ่มไม่ได้ส่งสัญญาณขึ้นมารับข้อความ" if not p.sent_at else "ส่งแล้วแต่ปุ่มไม่ยืนยันรับ")
                n += 1
                self.publish({"type": "ping", "dev_eui": p.dev_eui, "id": p.id, "status": p.status})
        if n: db.commit()
        return n

    # ---------------- housekeeping ----------------
    def prune(self, db: Session, now: datetime | None = None, force: bool = False) -> None:
        now = now or utcnow()
        if not force and self._last_prune and now - self._last_prune < timedelta(hours=6):
            return
        self._last_prune = now
        db.query(UplinkLog).filter(UplinkLog.at < now - timedelta(days=KEEP_UPLINK_DAYS)).delete(synchronize_session=False)
        db.query(NetEvent).filter(NetEvent.at < now - timedelta(days=KEEP_EVENT_DAYS)).delete(synchronize_session=False)
        db.query(PingTest).filter(PingTest.created_at < now - timedelta(days=KEEP_UPLINK_DAYS)).delete(synchronize_session=False)
        db.commit()

    def tick(self, db: Session, now: datetime, hb_min: int) -> None:
        self.expire_pings(db, now, hb_min)
        self.prune(db, now)


# ---------------- diagnosis ----------------
def diagnose(db: Session, d: Device, cs_known: bool | None, hb_min: int, now: datetime | None = None) -> list[dict]:
    """Findings, most serious first: {level, title, detail, fix} — fix is an action the dashboard offers
    (flush_nonces | reprovision | ping | None)."""
    now = now or utcnow()
    out: list[dict] = []
    if cs_known is False:
        out.append({"level": "error", "title": "ไม่มีปุ่มนี้ใน ChirpStack",
                    "detail": "ปุ่มจะเข้าเครือข่ายไม่ได้เลย — ลงทะเบียนใหม่ที่หน้า \"ติดตั้งอุปกรณ์\" (เสียบ USB)", "fix": "reprovision"})
    since = now - timedelta(days=3)
    evs = (db.query(NetEvent).filter(NetEvent.dev_eui == d.dev_eui, NetEvent.at >= since)
           .order_by(NetEvent.at.desc(), NetEvent.id.desc()).limit(200).all())
    last_join = next((e for e in evs if e.kind == "join"), None)
    last_up = _aw(d.last_seen)

    def recent(code_pred) -> NetEvent | None:
        return next((e for e in evs if e.kind == "log" and code_pred(e)), None)

    mic = recent(lambda e: e.code == "UPLINK_MIC" or "MIC" in (e.text or ""))
    if mic and (not last_join or _aw(mic.at) > _aw(last_join.at)):
        out.append({"level": "error", "title": "AppKey บนปุ่มไม่ตรงกับ ChirpStack",
                    "detail": f"ตั้งแต่ {_aw(mic.at):%d/%m %H:%M} ปุ่มขอเข้าเครือข่ายแต่ถูกปฏิเสธ (MIC ไม่ตรง) — "
                              "ติดตั้งปุ่มใหม่ผ่านหน้า \"ติดตั้งอุปกรณ์\" เพื่อเขียน key ชุดเดียวกันทั้งสองฝั่ง", "fix": "reprovision"})
    dn = recent(lambda e: e.code == "OTAA" and "DevNonce" in (e.text or ""))
    if dn and (not last_join or _aw(dn.at) > _aw(last_join.at)):
        out.append({"level": "error", "title": "DevNonce ซ้ำ — ปุ่มเข้าเครือข่ายไม่ได้",
                    "detail": f"ตั้งแต่ {_aw(dn.at):%d/%m %H:%M} — มักเกิดหลังแฟลชหรือรีเซ็ตปุ่ม กด \"ล้าง DevNonce\" แล้วรีสตาร์ตปุ่ม",
                    "fix": "flush_nonces"})
    joins = [e for e in evs if e.kind == "join" and _aw(e.at) >= now - timedelta(hours=24)]
    if len(joins) >= 3:
        out.append({"level": "warning", "title": f"เข้าเครือข่ายใหม่บ่อย ({len(joins)} ครั้งใน 24 ชม.)",
                    "detail": "ปุ่มรีสตาร์ตบ่อย — ตรวจไฟเลี้ยง/แบตเตอรี่ หรือสัญญาณที่หลุดจนต้องเข้าใหม่", "fix": None})
    fc = recent(lambda e: e.code in ("UPLINK_F_CNT_RESET", "UPLINK_F_CNT_RETRANSMISSION"))
    if fc and _aw(fc.at) >= now - timedelta(hours=24):
        out.append({"level": "warning", "title": "ตัวนับข้อความของปุ่มย้อนกลับ",
                    "detail": "ปุ่มอาจรีสตาร์ตโดยไม่เข้าเครือข่ายใหม่ — ChirpStack อาจทิ้งข้อความบางส่วน", "fix": None})
    dg = [e for e in evs if e.kind == "log" and e.code == "DOWNLINK_GATEWAY" and _aw(e.at) >= now - timedelta(hours=24)]
    if dg:
        out.append({"level": "warning", "title": f"เกตเวย์ส่งข้อความลงไม่สำเร็จ {len(dg)} ครั้งใน 24 ชม.",
                    "detail": "ACK/ข้อความถึงผู้ป่วยอาจไม่ถึงปุ่ม — ตรวจเกตเวย์ (เวลา/GPS, เสาอากาศ) แล้วลอง \"ทดสอบส่งกลับ\"", "fix": "ping"})
    s = d.sig or {}
    if s.get("q") in ("bad", "weak") and (s.get("n") or 0) >= 5:
        out.append({"level": "error" if s["q"] == "bad" else "warning",
                    "title": f"สัญญาณ{QUALITY_TH[s['q']]} (margin {s.get('margin')} dB" + (f", หาย {s['loss']}%" if s.get("loss") is not None else "") + ")",
                    "detail": "ย้ายปุ่ม/เสาให้สูงขึ้น ห่างผนังโลหะ หรือเพิ่มเกตเวย์ใกล้บ้านนี้ — ดูแผนที่ความครอบคลุม", "fix": None})
    if d.lifecycle == "active" and last_up and now - last_up > timedelta(minutes=2 * max(hb_min, 5) + 15) \
            and not any(f["level"] == "error" for f in out):
        out.append({"level": "warning", "title": "ปุ่มเงียบเกิน 2 รอบ heartbeat",
                    "detail": f"เห็นครั้งสุดท้าย {last_up:%d/%m %H:%M} — ถ้าไม่มีข้อความ error ข้างบน มักเป็นไฟ/แบตหมด หรืออยู่นอกระยะ", "fix": None})
    if not out and last_up:
        out.append({"level": "ok", "title": "ไม่พบปัญหา", "detail": "ปุ่มเข้าเครือข่ายและส่งสัญญาณตามปกติ", "fix": None})
    order = {"error": 0, "warning": 1, "ok": 2}
    return sorted(out, key=lambda f: order.get(f["level"], 3))


def parse_topic(topic: str) -> tuple[str, str] | None:
    """application/<app>/device/<eui>/event/<kind> -> (eui, kind)"""
    p = topic.split("/")
    if len(p) == 6 and p[0] == "application" and p[2] == "device" and p[4] == "event":
        return p[3].lower(), p[5]
    return None


def loads(raw: bytes) -> dict | None:
    try:
        j = json.loads(raw)
        return j if isinstance(j, dict) else None
    except ValueError:
        return None

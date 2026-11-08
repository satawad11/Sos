"""Case engine: uplink handling, ACK_L2 downlink, escalation."""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol
from sqlalchemy.orm import Session
from . import codec, geo, settings_store
from .hours import is_office_hours
from .gateways import GatewayMonitor
from .device_monitor import DeviceMonitor
from .selftest import SelfTestScheduler, confirm_text, first_caregivers
from .models import (Case, CaseEvent, Device, Assignment, Caregiver, Notification, utcnow)

log = logging.getLogger("sos.engine")
OPEN_STATES = ("new", "accepted")


def aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Notifier(Protocol):
    def notify_case(self, db: Session, case: Case, caregiver: Caregiver, reminder: bool) -> tuple[bool, str]: ...


Downlink = Callable[[str, int, bytes], None]   # (dev_eui, fport, payload)
Publish = Callable[[dict], None]


class Engine:
    def __init__(self, downlink: Downlink, notifier: Notifier | None = None, publish: Publish | None = None):
        self.downlink = downlink
        self.notifier = notifier
        self.publish = publish or (lambda e: None)
        self.gw = GatewayMonitor(notifier, self.publish)
        self.dev_watch = DeviceMonitor(self.gw)
        self.selftest = SelfTestScheduler()
        self.mqtt_ok: Callable[[], bool] = lambda: True      # main.py plugs in the MQTT worker's connection state
        self.gw_cmd_publish: Callable[[str, dict], bool] | None = None   # (gateway_id, signed command) -> sent?  set by the MQTT worker

    # ---------- helpers ----------
    def _event(self, db: Session, case: Case, kind: str, actor="system", **detail):
        db.add(CaseEvent(case_id=case.id, kind=kind, actor=actor, detail=detail))

    def _pub(self, case: Case, what: str):
        self.publish({"type": what, "case_id": case.id, "state": case.state, "overdue": case.overdue})

    # ---------- uplink ----------
    def handle_uplink(self, db: Session, dev_eui: str, payload: bytes, meta: dict | None = None) -> str:
        meta = meta or {}
        dev_eui = dev_eui.lower()
        try:
            up = codec.decode_uplink(payload)
        except codec.CodecError as e:
            log.warning("bad payload from %s: %s", dev_eui, e)
            return "bad_payload"
        dev = db.query(Device).filter_by(dev_eui=dev_eui.lower()).first()
        if dev is None:
            log.warning("unknown device %s", dev_eui)
            return "unknown_device"
        now = utcnow()
        dev.last_seen, dev.battery, dev.mains_ok = now, up.battery, up.mains_ok
        dev.fw_version = up.fw
        if dev.reported_config_ver != up.config_ver:
            dev.reported_config_ver = up.config_ver
            if up.config_ver == (dev.config_ver & 0xFF):
                dev.config_applied_at = now
        if "rssi" in meta: dev.last_rssi = meta["rssi"]
        if "snr" in meta: dev.last_snr = meta["snr"]
        if meta.get("gateway_id"):
            dev.last_gateway = meta["gateway_id"]
            self.gw.on_uplink(db, meta["gateway_id"], now)
        result = "ok"
        if dev.lifecycle not in ("active", "ready"):
            result = "inactive_device"          # repair/retired/lost: record health only
        elif up.msg_type == codec.T_ALERT:
            result = self._alert(db, dev, up, now)
        elif up.msg_type == codec.T_TEST:
            result = self._test(db, dev, up, now)
        elif up.msg_type == codec.T_CANCEL:
            result = self._cancel(db, dev, up)
        elif up.msg_type == codec.T_POLL:
            result = self._poll(db, dev, up, now)
        elif up.msg_type == codec.T_HEARTBEAT:
            pass
        # CONFIG sync (any uplink reports config_ver)
        if up.config_ver != (dev.config_ver & 0xFF) and up.msg_type in (codec.T_HEARTBEAT, codec.T_POLL):
            self._send_config(db, dev)
        db.commit()
        return result

    def handle_gateway_report(self, db: Session, gateway_id: str, raw) -> str:
        return self.gw.on_report(db, gateway_id.lower(), raw, utcnow())

    def handle_gateway_result(self, db: Session, gateway_id: str, res: dict) -> str:
        """The gateway agent's answer to a remote command. Only a still-pending command of THAT gateway can be completed."""
        from .models import GatewayCommand
        c = db.query(GatewayCommand).filter_by(cmd_id=res["id"], gateway_id=gateway_id).first()
        if c is None or c.status != "pending":
            return "ignored"
        c.status = "ok" if res["ok"] else "failed"
        c.detail, c.output, c.done_at = res["detail"], res["output"], utcnow()
        db.commit()
        self.publish({"type": "gateway_cmd", "gateway_id": gateway_id, "action": c.action, "status": c.status})
        return c.status

    def handle_location(self, db: Session, dev_eui: str, payload: bytes, meta: dict | None = None) -> str:
        """LOC uplink (FPort 4) from a device with optional GPS: remember the fix, attach it to the alert's case and,
        if the case is still open, send the people already notified a follow-up with the live position."""
        meta = meta or {}
        try:
            loc = codec.decode_loc(payload)
        except codec.CodecError as e:
            log.warning("bad LOC from %s: %s", dev_eui, e)
            return "bad_payload"
        dev = db.query(Device).filter_by(dev_eui=dev_eui.lower()).first()
        if dev is None:
            return "unknown_device"
        now = utcnow()
        dev.last_seen = now
        if "rssi" in meta: dev.last_rssi = meta["rssi"]
        if "snr" in meta: dev.last_snr = meta["snr"]
        if meta.get("gateway_id"):
            dev.last_gateway = meta["gateway_id"]; self.gw.on_uplink(db, meta["gateway_id"], now)
        dev.loc_lat, dev.loc_lon, dev.loc_at, dev.loc_sats, dev.loc_hdop = loc.lat, loc.lon, now, loc.sats, loc.hdop
        case = (db.query(Case).filter(Case.device_id == dev.id, Case.alert_id == loc.alert_id, Case.is_test.is_(False),
                                      Case.created_at > now - timedelta(minutes=30)).order_by(Case.id.desc()).first())
        if case is None:
            db.commit(); return "stored_no_case"
        first = case.loc_lat is None
        moved = (not first) and geo.distance_m(case.loc_lat, case.loc_lon, loc.lat, loc.lon) > 50
        case.loc_lat, case.loc_lon, case.loc_at, case.loc_sats, case.loc_hdop = loc.lat, loc.lon, now, loc.sats, loc.hdop
        home_m = None
        if case.patient and case.patient.lat is not None and case.patient.lon is not None:
            home_m = round(geo.distance_m(case.patient.lat, case.patient.lon, loc.lat, loc.lon))
        self._event(db, case, "location", sats=loc.sats, hdop=loc.hdop, away_from_home_m=home_m)     # coordinates stay in the case columns only
        if (first or moved) and case.state in OPEN_STATES:
            self._push_location(db, case, loc, home_m)
        db.commit(); self._pub(case, "case_update")
        return "ok"

    def _push_location(self, db: Session, case: Case, loc, home_m):
        fn = getattr(self.notifier, "notify_location", None)
        if fn is None:
            return
        chain = self._chain(db, case)[: case.esc_step + 1]
        for cg in chain:
            try:
                ok, detail = fn(db, case, cg, loc.lat, loc.lon, loc.sats, home_m)
            except Exception as e:
                log.exception("location push failed"); ok, detail = False, f"error: {e}"
            status = "skipped" if detail.startswith("skipped") else ("sent" if ok else "failed")
            db.add(Notification(case_id=case.id, caregiver_id=cg.id, channel="line_loc", status=status, detail=detail))

    def _open_case(self, db: Session, dev: Device) -> Case | None:
        return (db.query(Case).filter(Case.device_id == dev.id, Case.state.in_(OPEN_STATES),
                                      Case.is_test.is_(False)).order_by(Case.id.desc()).first())

    def _alert(self, db, dev, up, now) -> str:
        existing = self._open_case(db, dev)
        if existing:
            self._event(db, existing, "repeat_press", alert_id=up.alert_id)
            return "duplicate"
        # same alert_id resent after case closed within 2 minutes = retransmission
        dup = (db.query(Case).filter(Case.device_id == dev.id, Case.alert_id == up.alert_id,
                                     Case.created_at > now - timedelta(minutes=2)).first())
        if dup:
            return "duplicate"
        ah = not is_office_hours(db, now)
        case = Case(device_id=dev.id, patient_id=dev.patient_id, alert_id=up.alert_id,
                    after_hours=ah, created_at=now)
        db.add(case); db.flush()
        self._event(db, case, "created", battery=up.battery, mains_ok=up.mains_ok, after_hours=ah)
        esc = settings_store.get(db, "escalation")
        case.esc_step = 0
        self._notify_step(db, case, 0)
        case.next_escalation_at = now + timedelta(seconds=esc["escalate_after_s"])
        self._pub(case, "case_new")
        return "created"

    def _test(self, db, dev, up, now) -> str:
        case = Case(device_id=dev.id, patient_id=dev.patient_id, alert_id=up.alert_id,
                    is_test=True, state="closed", closed_at=now, close_reason="test", created_at=now)
        db.add(case); db.flush()
        self._event(db, case, "test_received", battery=up.battery)
        self._pub(case, "case_test")
        if self.notifier is not None and settings_store.get(db, "self_test").get("confirm_line"):
            try:   # tell the responsible อสม. the monthly test went through (never blocks the device reply)
                cg = first_caregivers(db).get(dev.patient_id) if dev.patient_id else None
                if cg and cg.line_user_id:
                    self.notifier.notify_system(db, cg, confirm_text(db, dev, up.battery))
            except Exception:
                log.exception("self-test confirmation failed")
        return "test"

    def _cancel(self, db, dev, up) -> str:
        case = self._open_case(db, dev)
        if case and case.state == "new" and case.alert_id == up.alert_id:
            case.state = "cancelled"; case.next_escalation_at = None
            self._event(db, case, "cancelled_by_device")
            self._pub(case, "case_update")
            return "cancelled"
        return "ignored"

    def _result_for(self, case: Case) -> int:
        if case.state == "closed":
            return codec.R_FALSE_ALARM if case.close_reason == "false_alarm" else codec.R_CLOSED
        if case.state == "accepted":
            return codec.R_ACCEPTED
        if case.state == "new" and case.after_hours:
            return codec.R_AFTER_HOURS
        return 0

    def _poll(self, db, dev, up, now) -> str:
        case = (db.query(Case).filter(Case.device_id == dev.id, Case.alert_id == up.alert_id,
                                      Case.is_test.is_(False)).order_by(Case.id.desc()).first())
        if not case:
            return "no_case"
        res = self._result_for(case)
        if res == 0:
            return "pending"
        last = aware(case.ack_sent_at)
        if case.ack_result_sent == res and last and (now - last).total_seconds() < 25:
            return "ack_in_flight"
        self._send_ack(db, case, dev, res)
        return "ack_sent"

    def _send_ack(self, db, case, dev, res):
        self.downlink(dev.dev_eui, codec.FPORT_ACK_L2, codec.encode_ack_l2(case.alert_id, res))
        case.ack_result_sent, case.ack_sent_at = res, utcnow()
        self._event(db, case, "ack_l2_queued", result=res)

    def _send_config(self, db, dev):
        d = settings_store.effective_device_cfg(db, dev)
        self.downlink(dev.dev_eui, codec.FPORT_CONFIG,
                      codec.encode_config(dev.config_ver, d["heartbeat_min"], d["poll_s"], d["buzzer"], d["hold_ms"]))

    # ---------- human actions ----------
    def accept(self, db: Session, case_id: int, actor: str) -> Case:
        case = db.get(Case, case_id)
        if case is None: raise LookupError("case not found")
        if case.state != "new":
            return case                      # idempotent: first acceptance wins
        case.state, case.accepted_at, case.accepted_by = "accepted", utcnow(), actor
        case.next_escalation_at = None
        self._event(db, case, "accepted", actor=actor)
        self._send_ack(db, case, case.device, self._result_for(case))
        db.commit(); self._pub(case, "case_update")
        return case

    def close(self, db: Session, case_id: int, actor: str, reason: str = "resolved", note: str = "") -> Case:
        if reason not in ("resolved", "false_alarm"): raise ValueError("bad reason")
        case = db.get(Case, case_id)
        if case is None: raise LookupError("case not found")
        if case.state in ("closed", "cancelled"): return case
        was_new = case.state == "new"
        case.state, case.closed_at, case.close_reason = "closed", utcnow(), reason
        case.next_escalation_at = None
        if note: case.note = note
        self._event(db, case, "closed", actor=actor, reason=reason, note=note, skipped_accept=was_new)
        self._send_ack(db, case, case.device, self._result_for(case))
        db.commit(); self._pub(case, "case_update")
        return case

    # ---------- escalation ----------
    def _chain(self, db: Session, case: Case) -> list[Caregiver]:
        chain: list[Caregiver] = []
        if case.patient_id:
            rows = (db.query(Assignment).filter_by(patient_id=case.patient_id)
                    .order_by(Assignment.priority).all())
            chain = [r.caregiver for r in rows if r.caregiver.active]
        fin = settings_store.get(db, "escalation").get("final_caregiver_id")
        if fin:
            c = db.get(Caregiver, fin)
            if c and c.active and c not in chain:
                chain.append(c)
        return chain

    def _notify_step(self, db: Session, case: Case, step: int, reminder=False) -> bool:
        chain = self._chain(db, case)
        if step >= len(chain):
            return False
        cg = chain[step]
        ok, detail = (False, "no notifier") if self.notifier is None else self.notifier.notify_case(db, case, cg, reminder)
        if not cg.line_user_id:
            ok, detail = False, "caregiver has no LINE binding"
        db.add(Notification(case_id=case.id, caregiver_id=cg.id, status="sent" if ok else "failed", detail=detail))
        self._event(db, case, "notified", caregiver=cg.full_name, step=step, ok=ok, detail=detail)
        return True

    def tick(self, db: Session, now: datetime | None = None) -> int:
        """Run periodically (every ~5 s). Returns number of cases touched."""
        now = now or utcnow()
        esc = settings_store.get(db, "escalation")
        n = 0
        for case in db.query(Case).filter(Case.state == "new", Case.is_test.is_(False)).all():
            age = (now - aware(case.created_at)).total_seconds()
            if not case.overdue and age >= esc["overdue_after_s"]:
                case.overdue = True; n += 1
                self._event(db, case, "overdue"); self._pub(case, "case_update")
            nxt = aware(case.next_escalation_at)
            if nxt and now >= nxt:
                n += 1
                chain_len = len(self._chain(db, case))
                case.esc_step += 1
                if case.esc_step < chain_len:
                    self._notify_step(db, case, case.esc_step)
                    case.next_escalation_at = now + timedelta(seconds=esc["escalate_after_s"])
                else:
                    # chain exhausted: re-notify the last recipient, then keep as overdue (dashboard alarm)
                    case.esc_step = max(chain_len - 1, 0)
                    if chain_len:
                        self._notify_step(db, case, case.esc_step, reminder=True)
                    case.next_escalation_at = now + timedelta(seconds=max(esc["escalate_after_s"], 60) * 2)
                    self._event(db, case, "chain_exhausted")
        db.commit()
        self.gw.tick(db, now, self.mqtt_ok())
        self.dev_watch.tick(db, now, self.mqtt_ok())
        try:
            self.selftest.tick(db, now, self.notifier)
        except Exception:
            log.exception("self-test scheduler failed")
        return n

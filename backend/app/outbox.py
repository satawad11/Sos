"""LINE outbox: messages are queued in the caller's transaction and sent by a separate worker.

Why: the LINE API can take seconds (10 s timeout per recipient) or fail outright.
  * Sent inline, a slow call held up the MQTT thread — the next button's uplink and the ACK back to a device waited behind it.
  * A failed call was only recorded; nothing tried again until the next escalation step, minutes later.
Now the engine's notifier is a QueuedNotifier (same interface as LineNotifier): it writes a row to sos_outbox and returns at
once. NotifyWorker sends the rows from its own threads, retries with a short back-off, and when a case alert cannot be
delivered at all it escalates the case immediately instead of waiting out the escalation timer."""
from __future__ import annotations
import logging, re, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from .gateways import _aware
from .models import Caregiver, Case, CaseEvent, Notification, Outbox, utcnow

log = logging.getLogger("sos.outbox")
QUEUED = "queued"
BACKOFF_S = (5, 15, 30, 60)            # wait after the 1st..4th failed attempt; the 5th failure is final (~2 min in all)
MAX_AGE = timedelta(minutes=30)        # a message older than this is not worth delivering late
KEEP_DONE = timedelta(days=7)
_PERMANENT = re.compile(r"^HTTP 4(?!29)\d\d")      # 400/401/403...: asking again will not help (429 = rate limit, retry)


class QueuedNotifier:
    """Drop-in for LineNotifier inside the engine. Anything else (reply_text, bot_info, token...) goes straight through."""

    def __init__(self, real, wake=lambda: None):
        self.real, self.wake = real, wake

    def __getattr__(self, name):
        return getattr(self.real, name)

    def _put(self, db: Session, kind: str, cg: Caregiver, case: Case | None, payload: dict) -> tuple[bool, str]:
        if not cg.line_user_id:
            return False, "no line_user_id"
        db.add(Outbox(kind=kind, case_id=case.id if case is not None else None, caregiver_id=cg.id, payload=payload))
        self.wake()
        return True, QUEUED

    def notify_case(self, db, case, cg, reminder):
        return self._put(db, "case", cg, case, {"reminder": bool(reminder)})

    def notify_system(self, db, cg, text):
        return self._put(db, "system", cg, None, {"text": text})

    def notify_location(self, db, case, cg, lat, lon, sats, home_m):
        from . import settings_store
        if cg.line_user_id and settings_store.get(db, "privacy")["line_detail"] == "minimal":
            return False, "skipped: minimal privacy mode"
        return self._put(db, "location", cg, case, {"lat": lat, "lon": lon, "sats": sats, "home_m": home_m})


class NotifyWorker:
    def __init__(self, session_factory, notifier, publish=lambda e: None, threads: int = 4):
        self.sf, self.notifier, self.publish = session_factory, notifier, publish
        self._wake, self._stop = threading.Event(), threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="sos-line")
        self._thread: threading.Thread | None = None
        self.last_ok_at: datetime | None = None
        self.last_error: str = ""
        self.last_error_at: datetime | None = None

    # ---- lifecycle ----
    def wake(self):
        self._wake.set()

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="sos-outbox", daemon=True); self._thread.start()

    def stop(self):
        self._stop.set(); self._wake.set(); self._pool.shutdown(wait=False)

    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self):
        while not self._stop.is_set():
            self._wake.wait(1.0); self._wake.clear()       # the row may not be committed yet when wake() fires: poll as well
            try:
                self.run_once()
            except Exception:
                log.exception("outbox run failed")

    # ---- work ----
    def run_once(self, now: datetime | None = None, parallel: bool = True) -> int:
        """Send every message that is due. Returns how many were attempted."""
        now = now or utcnow()
        with self.sf() as db:
            ids = [i for (i,) in db.query(Outbox.id).filter(Outbox.status == "pending", Outbox.next_try_at <= now).order_by(Outbox.id).all()]
        if not ids:
            return 0
        if parallel and len(ids) > 1:
            list(self._pool.map(lambda i: self._safe(i, now), ids))
        else:
            for i in ids: self._safe(i, now)
        return len(ids)

    def _safe(self, row_id: int, now: datetime):
        try:
            self._one(row_id, now)
        except Exception:
            log.exception("outbox row %s failed", row_id)

    def _deliver(self, db: Session, row: Outbox, cg: Caregiver, case: Case | None) -> tuple[bool, str]:
        p = row.payload or {}
        if row.kind == "case":
            return self.notifier.notify_case(db, case, cg, bool(p.get("reminder")))
        if row.kind == "location":
            return self.notifier.notify_location(db, case, cg, p["lat"], p["lon"], p["sats"], p.get("home_m"))
        return self.notifier.notify_system(db, cg, p.get("text", ""))

    def _one(self, row_id: int, now: datetime):
        with self.sf() as db:
            row = db.get(Outbox, row_id)
            if row is None or row.status != "pending":
                return
            cg = db.get(Caregiver, row.caregiver_id) if row.caregiver_id else None
            case = db.get(Case, row.case_id) if row.case_id else None
            drop = ""
            if cg is None or not cg.line_user_id: drop = "caregiver removed or LINE unbound"
            elif row.kind != "system" and case is None: drop = "case deleted"
            elif now - _aware(row.created_at) > MAX_AGE: drop = "too old to deliver"
            elif row.kind == "case" and row.attempts and case.state != "new" and not (row.payload or {}).get("reminder"):
                drop = "case already handled"
            if drop:
                self._finish(db, row, "dropped", drop, now, case); db.commit(); return
            try:
                ok, detail = self._deliver(db, row, cg, case)
            except Exception as e:                          # a bug in a notifier must not wedge the queue
                ok, detail = False, f"error: {e.__class__.__name__}: {e}"
            row.attempts += 1
            if ok:
                self.last_ok_at = now
                self._finish(db, row, "sent", detail, now, case)
            else:
                self.last_error, self.last_error_at = detail[:240], now
                final = row.attempts > len(BACKOFF_S) or bool(_PERMANENT.match(detail)) or detail.startswith("skipped")
                if final:
                    self._finish(db, row, "failed", detail, now, case)
                else:
                    row.last_error = detail[:240]
                    row.next_try_at = now + timedelta(seconds=BACKOFF_S[row.attempts - 1])
                    log.warning("LINE %s to caregiver %s failed (attempt %s): %s", row.kind, row.caregiver_id, row.attempts, detail)
            db.commit()
            if row.status == "failed" and case is not None and row.kind == "case":
                self.publish({"type": "case_update", "case_id": case.id, "state": case.state, "overdue": case.overdue})

    def _finish(self, db: Session, row: Outbox, status: str, detail: str, now: datetime, case: Case | None):
        row.status, row.done_at, row.last_error = status, now, ("" if status == "sent" else detail[:240])
        if case is None:
            return
        chan = "line_loc" if row.kind == "location" else "line"
        n = (db.query(Notification).filter_by(case_id=case.id, caregiver_id=row.caregiver_id, channel=chan, status=QUEUED)
             .order_by(Notification.id.desc()).first())
        if n is not None:
            n.status = {"sent": "sent", "failed": "failed", "dropped": "skipped"}[status]
            n.detail = detail if status != "sent" or row.attempts <= 1 else f"{detail} (attempt {row.attempts})"
        if status == "failed" and row.kind == "case":
            cg = db.get(Caregiver, row.caregiver_id)
            db.add(CaseEvent(case_id=case.id, kind="notify_failed", actor="system",
                             detail={"caregiver": cg.full_name if cg else "", "attempts": row.attempts, "detail": detail[:200]}))
            if case.state == "new" and not (row.payload or {}).get("reminder") and self._in_chain(db, case, cg):
                case.next_escalation_at = now          # nobody was reached: move on to the next person at the next tick
            log.error("LINE alert for case %s could not be delivered to caregiver %s: %s", case.id, row.caregiver_id, detail)

    @staticmethod
    def _in_chain(db: Session, case: Case, cg: Caregiver | None) -> bool:
        """Central staff are told next to the chain, not as a step of it: failing to reach one of them must not skip the
        patient's own caregiver's waiting time. (Someone who is central AND in this patient's chain still counts.)"""
        if cg is None or not cg.is_central:
            return True
        from . import settings_store
        from .models import Assignment
        if cg.id == settings_store.get(db, "escalation").get("final_caregiver_id"):
            return True
        return bool(case.patient_id) and db.query(Assignment).filter_by(patient_id=case.patient_id, caregiver_id=cg.id).first() is not None

    # ---- status / housekeeping ----
    def stats(self, db: Session, now: datetime | None = None) -> dict:
        now = now or utcnow()
        rows = db.query(Outbox.created_at).filter(Outbox.status == "pending").all()
        oldest = min((_aware(c) for (c,) in rows), default=None)
        day = now - timedelta(hours=24)
        return {"alive": self.alive(), "pending": len(rows), "oldest_s": round((now - oldest).total_seconds()) if oldest else 0,
                "failed_24h": db.query(Outbox).filter(Outbox.status == "failed", Outbox.done_at >= day).count(),
                "sent_24h": db.query(Outbox).filter(Outbox.status == "sent", Outbox.done_at >= day).count(),
                "last_ok_at": self.last_ok_at.isoformat() if self.last_ok_at else None,
                "last_error": self.last_error, "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None}

    @staticmethod
    def purge(db: Session, now: datetime | None = None) -> int:
        now = now or utcnow()
        return db.query(Outbox).filter(Outbox.status != "pending", Outbox.done_at < now - KEEP_DONE).delete(synchronize_session=False)

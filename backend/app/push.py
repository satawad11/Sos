"""Web Push notifications for the installed dashboard (PWA) — a second alarm channel next to LINE.

* VAPID key pair is generated on first use and kept in sos_secrets (never in /api/settings, which every user can read).
* Subscriptions are accepted only for the browsers' own push services (no SSRF: the server never POSTs to a URL
  an authenticated user typed in).
* Sending happens on a background thread so the MQTT / alarm path is never slowed down by a push service.
* Only admin + staff are notified (viewers do not handle cases).  Text follows the LINE privacy level
  ("minimal" = village only, no name).  Payloads are end-to-end encrypted (RFC 8291) on the way through
  Google / Apple / Mozilla push servers.
* Expired subscriptions (HTTP 404/410) are deleted.
"""
from __future__ import annotations
import base64, json, logging, queue, threading
from urllib.parse import urlparse
from sqlalchemy.orm import Session
from . import settings_store
from .config import env
from .models import Case, Gateway, PushSub, Secret, User, utcnow

log = logging.getLogger("sos.push")
ALLOWED_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com",
                 ".push.services.mozilla.com", "web.push.apple.com", ".push.apple.com", ".notify.windows.com")
NOTIFY_ROLES = ("admin", "staff")


def endpoint_ok(url: str) -> bool:
    try:
        u = urlparse(url)
    except ValueError:
        return False
    h = (u.hostname or "").lower()
    return u.scheme == "https" and len(url) <= 1000 and any(h == a.lstrip(".") or (a.startswith(".") and h.endswith(a)) for a in ALLOWED_HOSTS)


def _vapid(db: Session):
    from py_vapid import Vapid01
    row = db.get(Secret, "vapid_private_pem")
    if row is None:
        v = Vapid01(); v.generate_keys()
        row = Secret(key="vapid_private_pem", value=v.private_pem().decode()); db.add(row); db.commit()
        return v
    return Vapid01.from_pem(row.value.encode())


def public_key(db: Session) -> str:
    from cryptography.hazmat.primitives import serialization
    pub = _vapid(db).public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(pub).rstrip(b"=").decode()


def _case_payload(db: Session, case: Case, overdue: bool) -> dict:
    p = case.patient
    minimal = settings_store.get(db, "privacy")["line_detail"] == "minimal"
    who = (f"หมู่ {p.village}" if p and p.village else "ผู้ป่วย") if minimal or not p else f"{p.full_name}" + (f" · {p.village}" if p.village else "")
    title = ("⏰ ยังไม่มีผู้รับเรื่อง " if overdue else "🚨 แจ้งเหตุฉุกเฉิน ") + f"#{case.id}"
    return {"title": title, "body": f"{who} — แตะเพื่อเปิดเคส", "tag": f"case-{case.id}", "url": f"./?case={case.id}",
            "urgent": True}


class PushSender:
    def __init__(self, session_factory, send_fn=None):
        self.sf = session_factory
        self.send_fn = send_fn            # tests inject a fake; default = pywebpush.webpush
        self.q: queue.Queue = queue.Queue(maxsize=1000)
        self._overdue_sent: set[int] = set()
        self._t: threading.Thread | None = None

    def start(self):
        self._t = threading.Thread(target=self._run, daemon=True, name="push"); self._t.start()

    # called from Engine.publish (any thread) — must be fast
    def on_event(self, e: dict):
        t = e.get("type")
        if t == "case_new" or (t == "case_update" and e.get("state") == "new" and e.get("overdue")
                               and e.get("case_id") not in self._overdue_sent) or (t == "gateway_state" and e.get("state") == "offline"):
            if t == "case_update": self._overdue_sent.add(e.get("case_id"))
            try: self.q.put_nowait(e)
            except queue.Full: log.warning("push queue full, dropping %s", t)

    def _run(self):
        while True:
            e = self.q.get()
            try:
                with self.sf() as db:
                    self.handle(db, e)
            except Exception:
                log.exception("push send failed")

    def handle(self, db: Session, e: dict) -> int:
        t = e.get("type")
        if t in ("case_new", "case_update"):
            case = db.get(Case, e.get("case_id"))
            if not case or case.is_test: return 0
            payload = _case_payload(db, case, overdue=t == "case_update")
        elif t == "gateway_state":
            g = db.query(Gateway).filter_by(gateway_id=e.get("gateway_id", "")).first()
            payload = {"title": "⚠️ เกตเวย์ขาดการเชื่อมต่อ", "body": f"{(g.label if g else '') or e.get('gateway_id', '')} — ปุ่มในระยะนี้อาจส่งไม่ถึง",
                       "tag": f"gw-{e.get('gateway_id', '')}", "url": "./?page=gateways", "urgent": False}
        else:
            return 0
        subs = (db.query(PushSub).join(User, PushSub.user_id == User.id)
                .filter(User.active.is_(True), User.role.in_(NOTIFY_ROLES)).all())
        return self.send_many(db, subs, payload)

    def send_many(self, db: Session, subs: list, payload: dict) -> int:
        if not subs: return 0
        send = self.send_fn
        if send is None:
            from pywebpush import webpush
            send = webpush
        v = _vapid(db); sub_claim = env.public_url if env.public_url.startswith("https://") else "mailto:admin@localhost"
        ok = 0
        for s in subs:
            try:
                send(subscription_info={"endpoint": s.endpoint, "keys": {"p256dh": s.p256dh, "auth": s.auth}},
                     data=json.dumps(payload, ensure_ascii=False), vapid_private_key=v, vapid_claims={"sub": sub_claim},
                     ttl=600 if payload.get("urgent") else 3600, headers={"Urgency": "high" if payload.get("urgent") else "normal"},
                     timeout=10)
                s.last_ok_at, s.fail_count = utcnow(), 0; ok += 1
            except Exception as ex:          # pywebpush.WebPushException carries .response
                code = getattr(getattr(ex, "response", None), "status_code", None)
                if code in (404, 410):
                    log.info("push subscription expired (%s) — removed", code); db.delete(s)
                else:
                    s.fail_count = (s.fail_count or 0) + 1
                    log.warning("push failed (%s): %s", code, str(ex)[:200])
                    if s.fail_count >= 20: db.delete(s)
        db.commit()
        return ok

"""LINE Messaging API: push Flex message, webhook signature, bind codes."""
from __future__ import annotations
import base64, hashlib, hmac, logging
import httpx
from sqlalchemy.orm import Session
from . import linecfg
from .models import Case, Caregiver

log = logging.getLogger("sos.line")
PUSH_URL = "https://api.line.me/v2/bot/message/push"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"


def verify_signature(body: bytes, signature: str, secret: str | None = None) -> bool:
    secret = secret if secret is not None else linecfg.secret()
    if not secret:               # never accept an HMAC made with an empty key (forgeable)
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature or "")


def flex_for_case(case: Case, reminder: bool = False, detail: str = "full") -> dict:
    p = case.patient
    name = p.full_name if p else "ไม่ทราบผู้ป่วย (อุปกรณ์ยังไม่ผูก)"
    addr = (p.address if p else "") or "-"
    phone = (p.phone if p else "") or "-"
    title = ("⏰ เตือนซ้ำ: " if reminder else "🚨 ") + "แจ้งเหตุฉุกเฉิน"
    minimal = detail == "minimal"          # PDPA data minimisation: LINE servers see only name + village
    rows = [("ผู้ป่วย", name), ("หมู่บ้าน", (p.village if p else "") or "-")] if minimal else [("ผู้ป่วย", name), ("ที่อยู่", addr), ("โทร", phone)]
    body = [{"type": "text", "text": title, "weight": "bold", "size": "lg", "color": "#D32F2F"}]
    for k, v in rows:
        body.append({"type": "box", "layout": "baseline", "contents": [
            {"type": "text", "text": k, "size": "sm", "color": "#888888", "flex": 2},
            {"type": "text", "text": v, "size": "sm", "wrap": True, "flex": 5}]})
    contents = {"type": "bubble", "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
                "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                    {"type": "button", "style": "primary", "color": "#2E7D32",
                     "action": {"type": "postback", "label": "รับเรื่อง", "data": f"accept:{case.id}"}}]}}
    if minimal:
        pass
    elif p and p.phone:
        contents["footer"]["contents"].append({"type": "button", "style": "secondary",
            "action": {"type": "uri", "label": "โทรหาผู้ป่วย", "uri": f"tel:{p.phone}"}})
    if not minimal and p and p.lat is not None and p.lon is not None:
        contents["footer"]["contents"].append({"type": "button", "style": "secondary",
            "action": {"type": "uri", "label": "นำทาง", "uri": f"https://maps.google.com/?q={p.lat},{p.lon}"}})
    return {"type": "flex", "altText": f"{title}: {name}", "contents": contents}


class LineNotifier:
    def __init__(self, token: str | None = None, client: httpx.Client | None = None):
        self._token = token
        self.http = client or httpx.Client(timeout=10)

    @property
    def token(self) -> str:
        return self._token or linecfg.token()      # re-read each time: the dashboard can change it live

    def _post(self, url: str, payload: dict) -> tuple[bool, str]:
        if not self.token:
            return False, "LINE token not configured"
        try:
            r = self.http.post(url, json=payload, headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError as e:
            return False, f"network: {e}"
        return (r.status_code == 200), f"HTTP {r.status_code}" + ("" if r.status_code == 200 else f" {r.text[:120]}")

    def notify_case(self, db: Session, case: Case, cg: Caregiver, reminder: bool) -> tuple[bool, str]:
        if not cg.line_user_id:
            return False, "no line_user_id"
        from . import settings_store
        detail = settings_store.get(db, "privacy")["line_detail"]
        return self._post(PUSH_URL, {"to": cg.line_user_id, "messages": [flex_for_case(case, reminder, detail)]})

    def notify_system(self, db: Session, cg: Caregiver, text: str) -> tuple[bool, str]:
        """Plain-text system message (e.g. gateway offline)."""
        if not cg.line_user_id:
            return False, "no line_user_id"
        return self._post(PUSH_URL, {"to": cg.line_user_id, "messages": [{"type": "text", "text": text[:4900]}]})

    def notify_location(self, db: Session, case: Case, cg: Caregiver, lat: float, lon: float, sats: int,
                        home_m: int | None) -> tuple[bool, str]:
        """Follow-up to the alert: the live GPS fix of the button. Skipped in 'minimal' privacy mode (coordinates = navigation data)."""
        from . import settings_store
        from .geo import maps_url
        if not cg.line_user_id:
            return False, "no line_user_id"
        if settings_store.get(db, "privacy")["line_detail"] == "minimal":
            return False, "skipped: minimal privacy mode"
        name = case.patient.full_name if case.patient else f"เคส #{case.id}"
        far = f"\nห่างจากที่อยู่ที่ลงทะเบียน ~{home_m} ม." if home_m is not None else ""
        text = f"📍 พิกัดสดจากอุปกรณ์ ({name}) จับดาวเทียมได้ {sats} ดวง{far}\n{maps_url(lat, lon)}"
        return self._post(PUSH_URL, {"to": cg.line_user_id, "messages": [{"type": "text", "text": text}]})

    # ---- the Official Account itself (for the bind QR) and friendship (push only reaches friends) ----
    _info: tuple[float, dict | None] = (0.0, None)

    def bot_info(self) -> dict | None:
        """{"basicId": "@xxxx", "displayName": ...} of the OA, cached 10 min; None without a token / on error."""
        import time
        if time.time() - self._info[0] < 600 and self._info[1]:
            return self._info[1]
        if not self.token:
            return None
        try:
            r = self.http.get("https://api.line.me/v2/bot/info", headers={"Authorization": f"Bearer {self.token}"})
            info = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            info = None
        if info and info.get("basicId"):
            LineNotifier._info = (time.time(), {k: info.get(k) for k in ("basicId", "displayName", "pictureUrl")})
            return LineNotifier._info[1]
        return None

    def is_friend(self, user_id: str) -> bool | None:
        """True/False from the profile API (404 = not a friend / blocked); None when it cannot tell."""
        if not self.token or not user_id:
            return None
        try:
            r = self.http.get(f"https://api.line.me/v2/bot/profile/{user_id}", headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError:
            return None
        return True if r.status_code == 200 else False if r.status_code == 404 else None

    def reply_text(self, reply_token: str, text: str):
        self._post(REPLY_URL, {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]})

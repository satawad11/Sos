"""LINE Messaging API: push Flex message, webhook signature, bind codes."""
from __future__ import annotations
import base64, hashlib, hmac, logging
import httpx
from sqlalchemy.orm import Session
from .config import env
from .models import Case, Caregiver

log = logging.getLogger("sos.line")
PUSH_URL = "https://api.line.me/v2/bot/message/push"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"


def verify_signature(body: bytes, signature: str, secret: str | None = None) -> bool:
    secret = secret if secret is not None else env.line_channel_secret
    if not secret:               # never accept an HMAC made with an empty key (forgeable)
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(mac).decode(), signature or "")


def flex_for_case(case: Case, reminder: bool = False) -> dict:
    p = case.patient
    name = p.full_name if p else "ไม่ทราบผู้ป่วย (อุปกรณ์ยังไม่ผูก)"
    addr = (p.address if p else "") or "-"
    phone = (p.phone if p else "") or "-"
    title = ("⏰ เตือนซ้ำ: " if reminder else "🚨 ") + "แจ้งเหตุฉุกเฉิน"
    rows = [("ผู้ป่วย", name), ("ที่อยู่", addr), ("โทร", phone)]
    body = [{"type": "text", "text": title, "weight": "bold", "size": "lg", "color": "#D32F2F"}]
    for k, v in rows:
        body.append({"type": "box", "layout": "baseline", "contents": [
            {"type": "text", "text": k, "size": "sm", "color": "#888888", "flex": 2},
            {"type": "text", "text": v, "size": "sm", "wrap": True, "flex": 5}]})
    contents = {"type": "bubble", "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
                "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                    {"type": "button", "style": "primary", "color": "#2E7D32",
                     "action": {"type": "postback", "label": "รับเรื่อง", "data": f"accept:{case.id}"}}]}}
    if p and p.phone:
        contents["footer"]["contents"].append({"type": "button", "style": "secondary",
            "action": {"type": "uri", "label": "โทรหาผู้ป่วย", "uri": f"tel:{p.phone}"}})
    if p and p.lat is not None and p.lon is not None:
        contents["footer"]["contents"].append({"type": "button", "style": "secondary",
            "action": {"type": "uri", "label": "นำทาง", "uri": f"https://maps.google.com/?q={p.lat},{p.lon}"}})
    return {"type": "flex", "altText": f"{title}: {name}", "contents": contents}


class LineNotifier:
    def __init__(self, token: str | None = None, client: httpx.Client | None = None):
        self.token = token or env.line_channel_token
        self.http = client or httpx.Client(timeout=10)

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
        return self._post(PUSH_URL, {"to": cg.line_user_id, "messages": [flex_for_case(case, reminder)]})

    def reply_text(self, reply_token: str, text: str):
        self._post(REPLY_URL, {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]})

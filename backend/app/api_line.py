"""LINE: credentials set from the dashboard, and the webhook (bind codes, accept from LINE).
Split out of api.py; shared helpers and the engine handle still live there."""
from __future__ import annotations
from fastapi import APIRouter
import httpx
import re
from . import linecfg
from .auth import admin_only, audit
from .config import env
from .db import get_db
from .engine import aware
from .models import BindCode, Caregiver, Case, utcnow
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from .api import BIND_ALPHABET, get_engine

router = APIRouter(prefix="/api")

# ---------------- LINE credentials (dashboard) ----------------
class LineCfgIn(BaseModel):
    token: str | None = Field(default=None, max_length=600)       # None = leave as is, "" = remove the dashboard copy
    secret: str | None = Field(default=None, max_length=200)


def _line_state() -> dict:
    t, sc = linecfg.token(), linecfg.secret()
    return {"token_set": bool(t), "token_hint": linecfg.hint(t) if t else "", "token_source": linecfg.source("token"),
            "secret_set": bool(sc), "secret_hint": linecfg.hint(sc) if sc else "", "secret_source": linecfg.source("secret"),
            "webhook_url": env.public_url.rstrip("/") + "/api/line/webhook", "ready": bool(t and sc)}


@router.get("/integrations/line")
def line_cfg(u=Depends(admin_only)):
    return _line_state()


@router.put("/integrations/line")
def line_cfg_put(b: LineCfgIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    changed = []
    for name, v in (("token", b.token), ("secret", b.secret)):
        if v is None: continue
        v = v.strip()
        if v and (len(v) < 8 or re.search(r"\s", v)): raise HTTPException(422, f"ค่า {name} ไม่ถูกต้อง (สั้นเกินไปหรือมีช่องว่าง)")
        linecfg.save(db, name, v); changed.append(name + ("_removed" if not v else "_set"))
    if changed: audit(db, u.username, "line_config_update", "", changed=",".join(changed)); db.commit()     # never the values
    return _line_state()


@router.post("/integrations/line/test")
def line_test(db: Session = Depends(get_db), u=Depends(admin_only)):
    """Checks the token against LINE (bot info). Optionally nothing is sent to anyone."""
    t = linecfg.token()
    if not t: raise HTTPException(409, "ยังไม่ได้ตั้งค่า Channel access token")
    try:
        r = httpx.get("https://api.line.me/v2/bot/info", headers={"Authorization": f"Bearer {t}"}, timeout=10)
    except httpx.HTTPError as e:
        return {"ok": False, "detail": "เชื่อมต่อ LINE ไม่ได้: " + e.__class__.__name__}
    if r.status_code != 200:
        return {"ok": False, "detail": "LINE ปฏิเสธโทเคน (HTTP %d) — ตรวจว่าคัดลอกครบและเป็น Channel access token (long-lived)" % r.status_code}
    j = r.json()
    audit(db, u.username, "line_config_test", "", ok=True); db.commit()
    return {"ok": True, "detail": "เชื่อมต่อสำเร็จ", "bot": j.get("displayName", ""), "basic_id": j.get("basicId", ""),
            "secret_set": bool(linecfg.secret())}


class LineTestPushIn(BaseModel):
    caregiver_id: int


@router.post("/integrations/line/test-push")
def line_test_push(b: LineTestPushIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    cg = db.get(Caregiver, b.caregiver_id)
    if not cg: raise HTTPException(404)
    if not cg.line_user_id: raise HTTPException(409, "ผู้ดูแลคนนี้ยังไม่ได้ผูก LINE")
    from .line import LineNotifier
    ok, detail = LineNotifier().notify_system(db, cg, "✅ ทดสอบการแจ้งเตือนจากระบบแจ้งเหตุฉุกเฉิน — ถ้าเห็นข้อความนี้ แปลว่าการตั้งค่า LINE ถูกต้อง")
    audit(db, u.username, "line_test_push", str(cg.id), ok=ok); db.commit()
    return {"ok": ok, "detail": "ส่งแล้ว" if ok else detail}


# ---------------- LINE webhook ----------------
@router.post("/line/webhook")
async def line_webhook(request: Request, db: Session = Depends(get_db), eng=Depends(get_engine)):
    from .line import verify_signature
    body = await request.body()
    if not verify_signature(body, request.headers.get("x-line-signature", "")):
        raise HTTPException(401, "bad signature")
    notifier = eng.notifier
    for ev in (await request.json()).get("events", []):
        uid = (ev.get("source") or {}).get("userId", "")
        tok = ev.get("replyToken", "")
        reply = (lambda t: notifier.reply_text(tok, t)) if notifier and tok else (lambda t: None)
        bound = db.query(Caregiver).filter_by(line_user_id=uid, active=True).first() if uid else None
        if ev.get("type") == "follow":
            reply(f"ยินดีต้อนรับกลับครับ บัญชีนี้ผูกกับ {bound.full_name} อยู่แล้ว ระบบจะส่งแจ้งเหตุมาที่แชทนี้" if bound else
                  "สวัสดีครับ นี่คือบัญชีแจ้งเหตุฉุกเฉินของ รพ.สต.\n\nถ้าคุณเป็น อสม./ผู้ดูแลผู้ป่วย: ขอรหัสผูกบัญชี 8 ตัวจากเจ้าหน้าที่ "
                  "แล้วพิมพ์ส่งในแชทนี้ (หรือสแกน QR จากหน้าจอเจ้าหน้าที่) เพื่อรับแจ้งเหตุทาง LINE")
        elif ev.get("type") == "message" and (ev.get("message") or {}).get("type") == "text":
            code = "".join(ev["message"]["text"].split()).upper()          # tolerate spaces typed in the middle
            looks_like_code = len(code) == 8 and all(c in BIND_ALPHABET for c in code)
            bc = db.query(BindCode).filter_by(code=code, used=False).first() if looks_like_code else None
            if bc and aware(bc.expires_at) > utcnow():
                cg = db.get(Caregiver, bc.caregiver_id)
                cg.line_user_id, bc.used = uid, True
                audit(db, "line", "line_bind", str(cg.id)); db.commit()
                friend = getattr(notifier, "is_friend", lambda _u: None)(uid)
                reply(f"ผูกบัญชีสำเร็จ: {cg.full_name}\nเมื่อผู้ป่วยกดปุ่มแจ้งเหตุ ระบบจะส่งข้อความมาที่แชทนี้ พร้อมปุ่ม \"รับเรื่อง\""
                      + ("\n\n⚠️ กรุณากด \"เพิ่มเพื่อน\" บัญชีนี้ด้วย ไม่เช่นนั้นจะไม่ได้รับแจ้งเหตุ" if friend is False else ""))
            elif looks_like_code:
                reply("รหัสไม่ถูกต้องหรือหมดอายุแล้ว (ใช้ได้ 30 นาที ครั้งเดียว) — ขอรหัสใหม่จากเจ้าหน้าที่")
            elif bound:
                reply(f"บัญชีนี้ผูกกับ {bound.full_name} แล้ว ระบบจะส่งแจ้งเหตุมาที่แชทนี้\n"
                      "ข้อความที่พิมพ์ในแชทนี้ไม่ถึงเจ้าหน้าที่ — ถ้าเร่งด่วนโทร 1669")
            else:
                reply("ยังไม่ได้ผูกบัญชี: ขอรหัส 8 ตัวจากเจ้าหน้าที่ รพ.สต. แล้วพิมพ์ส่งในแชทนี้")
        elif ev.get("type") == "postback":
            data = (ev.get("postback") or {}).get("data", "")
            if data.startswith("accept:"):
                cg = db.query(Caregiver).filter_by(line_user_id=uid, active=True).first() if uid else None
                if not cg:
                    reply("ไม่พบบัญชีของคุณในระบบ"); continue
                cid = int(data.split(":")[1])
                case = db.get(Case, cid)
                if case and case.state == "new":
                    eng.accept(db, cid, f"{cg.role} {cg.full_name}")
                    audit(db, f"line:{cg.id}", "case_accept", str(cid)); db.commit()
                    reply("รับเรื่องแล้ว กรุณาเดินทางไปดูผู้ป่วย")
                elif case:
                    reply(f"เคสนี้ถูกรับโดย {case.accepted_by or 'ผู้อื่น'} แล้ว" if case.state == "accepted" else "เคสนี้ปิดแล้ว")
    return {"ok": True}

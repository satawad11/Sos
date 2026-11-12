"""AI assistant (Gemini): settings, diagnosis of devices/gateways and the monthly report summary.
Split out of api.py; shared helpers and the engine handle still live there."""
from __future__ import annotations
from fastapi import APIRouter
import re
from . import ai, linecfg, reports
from .auth import admin_only, audit, current_user, staff_or_admin
from .db import get_db
from .models import utcnow
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from .api_admin import _range
from .api_devices import devices, gateways

router = APIRouter(prefix="/api")

# ---------------- AI (Gemini) ----------------
class AiCfgIn(BaseModel):
    key: str | None = Field(default=None, max_length=200)       # None = unchanged, "" = remove
    model: str | None = Field(default=None, max_length=60)
    enabled: bool | None = None


def _ai_state(db: Session) -> dict:
    k = linecfg.gemini_key()
    return {"provider": "gemini", "key_set": bool(k), "key_hint": linecfg.hint(k) if k else "",
            "model": linecfg.flag(db, "gemini_model", ai.DEFAULT_MODEL), "enabled": linecfg.flag(db, "gemini_enabled", "1") == "1"}


@router.get("/integrations/ai")
def ai_cfg(db: Session = Depends(get_db), u=Depends(admin_only)):
    return _ai_state(db)


@router.put("/integrations/ai")
def ai_cfg_put(b: AiCfgIn, db: Session = Depends(get_db), u=Depends(admin_only)):
    changed = []
    if b.key is not None:
        v = b.key.strip()
        if v and (len(v) < 20 or re.search(r"\s", v)): raise HTTPException(422, "API key ไม่ถูกต้อง (สั้นเกินไปหรือมีช่องว่าง)")
        linecfg.save(db, "gemini", v); changed.append("key_removed" if not v else "key_set")
    if b.model is not None:
        if not ai.MODEL_RE.match(b.model.strip()): raise HTTPException(422, "ชื่อโมเดลไม่ถูกต้อง")
        linecfg.set_flag(db, "gemini_model", b.model.strip()); changed.append("model=" + b.model.strip())
    if b.enabled is not None:
        linecfg.set_flag(db, "gemini_enabled", "1" if b.enabled else "0"); changed.append("enabled=%s" % b.enabled)
    if changed: audit(db, u.username, "ai_config_update", "gemini", changed=",".join(changed)); db.commit()       # never the key
    return _ai_state(db)


@router.post("/integrations/ai/test")
def ai_test(db: Session = Depends(get_db), u=Depends(admin_only)):
    st = _ai_state(db)
    try:
        out = ai.generate(linecfg.gemini_key(), st["model"], "ตอบสั้น ๆ ว่า OK เป็นภาษาไทยหนึ่งประโยค", max_tokens=256)
    except ai.AIError as e:
        return {"ok": False, "detail": str(e)}
    audit(db, u.username, "ai_config_test", "gemini", ok=True); db.commit()
    return {"ok": True, "detail": "เชื่อมต่อสำเร็จ", "model": st["model"], "reply": out[:120]}


@router.get("/integrations/ai/models")
def ai_models(u=Depends(admin_only)):
    try:
        return {"models": ai.list_models(linecfg.gemini_key())}
    except ai.AIError as e:
        raise HTTPException(502, str(e))


_ai_last = {"t": 0.0}


def _ai_ready(db: Session) -> dict:
    """Shared guard for every AI feature: configured, switched on, and not hammered (free-tier quota)."""
    import time
    st = _ai_state(db)
    if not st["key_set"] or not st["enabled"]: raise HTTPException(409, "ยังไม่ได้เปิดใช้ AI — ผู้ดูแลระบบตั้งค่าได้ที่เมนู 'เชื่อมต่อ LINE/AI'")
    if time.monotonic() - _ai_last["t"] < 5: raise HTTPException(429, "กดถี่เกินไป รอสักครู่")
    _ai_last["t"] = time.monotonic()
    return st


@router.get("/ai/status")
def ai_status(db: Session = Depends(get_db), u=Depends(current_user)):
    st = _ai_state(db)
    return {"ready": st["key_set"] and st["enabled"]}


class AiDiagIn(BaseModel):
    scope: str                      # gateways | devices
    id: int | None = None           # one gateway / device, or all of them when omitted


@router.post("/ai/diagnose")
def ai_diagnose(b: AiDiagIn, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    """AI reading of gateway/device health. Sends only technical readings under anonymous names ("เกตเวย์ 1", "อุปกรณ์ 2"):
    no labels, ids, IPs, coordinates, patient names or addresses."""
    if b.scope not in ("gateways", "devices"): raise HTTPException(422, "scope must be gateways or devices")
    st = _ai_ready(db)
    now = utcnow()
    rows = gateways(db=db, u=u) if b.scope == "gateways" else [d for d in devices(db=db, u=u) if d["lifecycle"] in ("active", "repair", "ready")]
    if b.id is not None:
        rows = [r for r in rows if r["id"] == b.id]
        if not rows: raise HTTPException(404)
    if not rows: raise HTTPException(409, "ยังไม่มีข้อมูลให้วิเคราะห์")
    rows = rows[:40]                                      # keeps the prompt small / inside the free quota
    conv = ai.anon_gateway if b.scope == "gateways" else ai.anon_device
    items = [conv(r, i + 1, now) for i, r in enumerate(rows)]
    try:
        text = ai.generate(linecfg.gemini_key(), st["model"], ai.diagnose_prompt(b.scope, items), max_tokens=3000)
    except ai.AIError as e:
        raise HTTPException(502, str(e))
    audit(db, u.username, "ai_diagnose", b.scope + (f":{b.id}" if b.id is not None else ":all"), model=st["model"]); db.commit()
    return {"text": text, "model": st["model"], "count": len(items),
            "note": "วิเคราะห์โดย AI จากค่าทางเทคนิค (ไม่มีชื่อ/ที่อยู่/พิกัด) — เป็นข้อเสนอแนะ ตรวจสอบหน้างานก่อนดำเนินการ"}


@router.post("/reports/ai-summary")
def report_ai_summary(month: str, db: Session = Depends(get_db), u=Depends(staff_or_admin)):
    """AI-written Thai summary of one month. Only anonymised aggregate numbers leave the server (see ai.anonymise)."""
    st = _ai_ready(db)
    start, end, d1, d2 = _range(month, None, None)
    summary = reports.summary(db, start, end, d1, d2, names=False)
    try:
        text = ai.generate(linecfg.gemini_key(), st["model"], ai.summary_prompt(summary))
    except ai.AIError as e:
        raise HTTPException(502, str(e))
    audit(db, u.username, "ai_summary", month, model=st["model"]); db.commit()
    return {"text": text, "model": st["model"], "note": "สร้างโดย AI จากตัวเลขสรุป (ไม่มีชื่อ/ที่อยู่ผู้ป่วย) — ตรวจทานก่อนใช้งานจริง"}

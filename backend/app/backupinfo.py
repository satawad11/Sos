"""What the host's backup script last did (infra/scripts/backup.sh writes <update_dir>/backup-status.json)."""
from __future__ import annotations
import json, os
from datetime import datetime, timezone
from .config import env

FILE = "backup-status.json"


def state(max_age_h: int = 36, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    try:
        with open(os.path.join(env.update_dir, FILE), encoding="utf-8") as f:
            st = json.load(f)
        at = datetime.fromisoformat(st["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return {"known": False, "problem": "ยังไม่เคยมีรายงานการสำรองข้อมูล — ตั้ง cron ให้รัน infra/scripts/backup.sh ทุกวัน"}
    age_h = round((now - at).total_seconds() / 3600, 1)
    off = st.get("offsite") or {}
    problem = ""
    if not st.get("ok"): problem = "การสำรองข้อมูลครั้งล่าสุดล้มเหลว: " + str(st.get("error", ""))
    elif age_h > max_age_h: problem = f"ไม่มีการสำรองข้อมูลมาแล้ว {round(age_h)} ชั่วโมง"
    elif off.get("configured") and not off.get("ok"): problem = "สำรองในเครื่องสำเร็จ แต่ส่งสำเนาออกนอกเครื่องไม่สำเร็จ: " + str(off.get("detail", ""))
    return {"known": True, "at": st["at"], "age_h": age_h, "ok": bool(st.get("ok")), "encrypted": bool(st.get("encrypted")),
            "offsite": {"configured": bool(off.get("configured")), "ok": bool(off.get("ok")), "detail": str(off.get("detail", ""))[:200]},
            "files": [{"name": str(x.get("name", ""))[:80], "size": int(x.get("size", 0))} for x in (st.get("files") or [])[:10]],
            "keep_days": st.get("keep_days"), "problem": problem}

"""Optional AI helper (Google Gemini API, free tier is enough). Used ONLY with anonymised aggregate numbers.

PDPA: on the free tier Google may use prompts to improve its products, so nothing that identifies a person
(names, addresses, phone numbers, device ids, villages) is ever put in a prompt - see anonymise().
"""
from __future__ import annotations
import json, re
import httpx

BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,59}$")


class AIError(Exception):
    pass


def _headers(key: str) -> dict:
    return {"x-goog-api-key": key, "content-type": "application/json"}      # header, not URL: keys in URLs end up in logs


def _fail(r: httpx.Response) -> AIError:
    try: msg = (r.json().get("error") or {}).get("message", "")
    except Exception: msg = ""
    if r.status_code == 429: return AIError("เกินโควตาฟรีของ Gemini (ลองใหม่อีกครู่ หรือพรุ่งนี้)")
    if r.status_code in (400, 403) and "API key" in msg: return AIError("API key ไม่ถูกต้อง หรือยังไม่ได้เปิดใช้งาน")
    if r.status_code == 404: return AIError("ไม่พบโมเดลนี้ — กด 'ดูรายชื่อโมเดล' แล้วเลือกใหม่")
    return AIError(f"Gemini ตอบกลับ HTTP {r.status_code}: {msg[:140]}")


def generate(key: str, model: str, prompt: str, max_tokens: int = 2048) -> str:
    if not key: raise AIError("ยังไม่ได้ตั้งค่า Gemini API key")
    if not MODEL_RE.match(model or ""): raise AIError("ชื่อโมเดลไม่ถูกต้อง")
    try:
        r = httpx.post(f"{BASE}/models/{model}:generateContent", headers=_headers(key), timeout=45,
                       json={"contents": [{"parts": [{"text": prompt}]}],
                             "generationConfig": {"temperature": 0.3, "maxOutputTokens": max_tokens}})
    except httpx.HTTPError as e:
        raise AIError("เชื่อมต่อ Gemini ไม่ได้: " + e.__class__.__name__) from e
    if r.status_code != 200: raise _fail(r)
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, ValueError, TypeError):
        raise AIError("Gemini ไม่ส่งข้อความกลับมา (อาจถูกกรองเนื้อหา)")


def list_models(key: str) -> list[str]:
    if not key: raise AIError("ยังไม่ได้ตั้งค่า Gemini API key")
    try:
        r = httpx.get(f"{BASE}/models", headers=_headers(key), params={"pageSize": 200}, timeout=20)
    except httpx.HTTPError as e:
        raise AIError("เชื่อมต่อ Gemini ไม่ได้: " + e.__class__.__name__) from e
    if r.status_code != 200: raise _fail(r)
    out = [m["name"].split("/", 1)[-1] for m in r.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
    return sorted(m for m in out if m.startswith("gemini") and "embedding" not in m)


def anonymise(summary: dict) -> dict:
    """Aggregate numbers only. Villages/gateways become 'หมู่บ้าน 1..', 'เกตเวย์ 1..'; no names, ids, addresses."""
    return {
        "ช่วงเวลา": f'{summary["period"]["from_be"]} ถึง {summary["period"]["to_be"]}',
        "เคส": summary["cases"], "ความเร็วรับเรื่อง": summary["response"],
        "จำนวนเคสแยกตามชั่วโมง(0-23)": summary["by_hour"],
        "จำนวนเคสแยกตามวัน": {d["day"]: d["cases"] for d in summary["by_weekday"]},
        "แยกตามพื้นที่": [{"พื้นที่": f"หมู่บ้าน {i+1}", "เคส": v["cases"], "แจ้งผิดพลาด": v["false_alarm"], "รับเรื่องเฉลี่ย_วินาที": v["accept_avg_s"]}
                          for i, v in enumerate(summary["by_village"])],
        "LINE": summary["line"], "อุปกรณ์": {k: v for k, v in summary["devices"].items() if k != "as_of"},
        "เกตเวย์ขัดข้อง": [{"เกตเวย์": f"เกตเวย์ {i+1}", "ครั้ง": g["outages"], "นาทีที่ขาดการเชื่อมต่อ": g["downtime_min"]}
                           for i, g in enumerate(summary["gateways"])],
    }


def summary_prompt(summary: dict) -> str:
    data = json.dumps(anonymise(summary), ensure_ascii=False)
    return ("คุณช่วยเจ้าหน้าที่ รพ.สต. สรุปรายงานระบบปุ่มแจ้งเหตุฉุกเฉินผู้ป่วยติดบ้านประจำเดือน เป็นภาษาไทยทางการ กระชับ 5-8 ประโยค\n"
            "กติกา: ใช้เฉพาะตัวเลขในข้อมูลด้านล่าง ห้ามแต่งตัวเลขหรือข้อเท็จจริงเพิ่ม ห้ามให้คำแนะนำทางการแพทย์ "
            "ระบุจุดที่ควรติดตาม (เช่น เคสรับเรื่องช้า แจ้งผิดพลาดมาก เกตเวย์ขัดข้อง แบตต่ำ) และสรุปแนวโน้มช่วงเวลาที่เกิดเหตุบ่อย "
            "ถ้าไม่มีเคสให้บอกว่าไม่มีเหตุในช่วงนี้\n\nข้อมูล (JSON):\n" + data)


# ---------------------------------------------------------------- gateway / device diagnosis (health data only, no patient data)
def _mins(iso: str | None, now) -> int | None:
    if not iso: return None
    from datetime import datetime, timezone
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if t.tzinfo is None: t = t.replace(tzinfo=timezone.utc)
    return max(0, round((now - t).total_seconds() / 60))


def anon_gateway(g: dict, i: int, now) -> dict:
    r = g.get("report") or {}; sv = r.get("services") or {}
    out = {"ชื่อ": f"เกตเวย์ {i}", "สถานะ": g["status"], "ไม่ได้รับสัญญาณมา_นาที": _mins(g.get("last_seen"), now),
           "รายงานล่าสุดเมื่อ_นาทีที่แล้ว": _mins(g.get("last_report_at"), now), "เกณฑ์ถือว่าออฟไลน์_นาที": g.get("offline_min_effective"),
           "จำนวนอุปกรณ์ที่ใช้เกตเวย์นี้": g.get("devices"), "เฟรมที่รับสะสม": g.get("uplinks_total"),
           "CPU_องศา": r.get("cpu_temp_c"), "ทำงานต่อเนื่อง_ชั่วโมง": round(r["uptime_s"] / 3600, 1) if r.get("uptime_s") is not None else None,
           "throttled": r.get("throttled"), "การ์ดใช้_เปอร์เซ็นต์": r.get("disk_pct"), "หน่วยความจำใช้_เปอร์เซ็นต์": r.get("mem_pct"),
           "load1": r.get("load1"), "concentratord_ทำงาน": sv.get("concentratord"), "forwarder_ทำงาน": sv.get("forwarder"),
           "spi_พบ": r.get("spi"), "ntp_ซิงก์": r.get("ntp"), "รีสตาร์ตวิทยุอัตโนมัติสะสม": r.get("radio_fail"),
           "ระบบตรวจพบ": (g.get("critical") or []) + (g.get("warnings") or [])}
    if g["status"] == "offline" and g.get("reason"): out["ประเภทที่ขาด"] = "ตัวรับสัญญาณเสีย" if g["reason"] == "radio" else "เงียบเกินเวลา"
    return {k: v for k, v in out.items() if v is not None}


def anon_device(d: dict, i: int, now) -> dict:
    out = {"ชื่อ": f"อุปกรณ์ {i}", "สถานะ": d["lifecycle"], "ออฟไลน์": d["offline"], "เห็นล่าสุด_นาทีที่แล้ว": _mins(d.get("last_seen"), now),
           "แบตเตอรี่_เปอร์เซ็นต์": None if d.get("battery") in (None, 255) else d["battery"], "ไฟบ้านปกติ": d.get("mains_ok"),
           "RSSI": d.get("rssi"), "SNR": d.get("snr"), "firmware": d.get("fw"), "ค่าตั้งใหม่ยังไม่ถึงเครื่อง": d.get("config_pending")}
    return {k: v for k, v in out.items() if v is not None}


def diagnose_prompt(kind: str, items: list[dict]) -> str:
    what = "เกตเวย์ LoRaWAN (Raspberry Pi + WM1302)" if kind == "gateways" else "ปุ่มแจ้งเหตุฉุกเฉิน LoRa (ESP32)"
    data = json.dumps(items, ensure_ascii=False)
    return (f"คุณเป็นผู้ช่วยช่างเทคนิคของ รพ.สต. วิเคราะห์สถานะ{what} จากข้อมูลด้านล่าง ตอบเป็นภาษาไทย กระชับ ใช้หัวข้อสั้น ๆ\n"
            "รูปแบบคำตอบ: (1) ภาพรวม 1-2 ประโยค (2) ปัญหาที่พบ เรียงจากเร่งด่วนที่สุด พร้อมสาเหตุที่เป็นไปได้ (3) สิ่งที่ควรตรวจ/ทำตามลำดับ\n"
            "กติกา: ใช้เฉพาะข้อมูลที่ให้ ห้ามแต่งตัวเลข ถ้าข้อมูลไม่พอให้บอกว่าขาดข้อมูลอะไร "
            "ระบบนี้ใช้แจ้งเหตุฉุกเฉินของผู้ป่วยติดบ้าน — เกตเวย์ออฟไลน์ ตัวรับสัญญาณเสีย หรือปุ่มออฟไลน์/แบตต่ำ ถือว่าเร่งด่วน "
            "คำแนะนำต้องทำได้จริงโดยเจ้าหน้าที่ทั่วไป (เช่น ตรวจแหล่งจ่ายไฟ ตรวจสายเสาอากาศ สั่งรีสตาร์ท LoRa services จากแดชบอร์ด เปลี่ยนแบตเตอรี่) "
            "ถ้าทุกอย่างปกติให้บอกสั้น ๆ ว่าปกติ\n\nข้อมูล (JSON):\n" + data)

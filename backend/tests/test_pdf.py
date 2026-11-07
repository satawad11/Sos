"""Monthly PDF report: renders Thai, matches the summary numbers, honours role/names, logos + org settings."""
import base64, io
from datetime import datetime, timezone
from app import codec
from app.db import SessionLocal
from app.models import Case
from tests.test_all import login, up

EUI = "70b3d57ed0000001"
PNG = open("../dashboard/icons/icon-192.png", "rb").read()


def _text(pdf: bytes) -> str:
    import pymupdf
    import re
    t = "\n".join(p.get_text() for p in pymupdf.open(stream=pdf, filetype="pdf"))
    return re.sub(r"[\x00-\x08\x0b-\x1f]", "", t)      # shaped SARA AM (ำ) extracts with a stray control char


def test_monthly_pdf(env_):
    eng, downs, notif, client, ids = env_
    H, V = login(client), login(client, "viewer")
    with SessionLocal() as db:
        assert eng.handle_uplink(db, EUI, up(codec.T_ALERT, alert=2)) == "created"
        c = db.query(Case).first(); month = c.created_at.astimezone(timezone.utc).strftime("%Y-%m")
    org = {"name": "โรงพยาบาลส่งเสริมสุขภาพตำบลทดสอบ", "partner": "อบต.ทดสอบ", "system_name": "ระบบทดสอบ",
           "reporter_name": "นายรายงาน ดีมาก", "reporter_title": "นักวิชาการสาธารณสุข", "approver_name": "", "approver_title": "ผอ.รพ.สต."}
    assert client.put("/api/settings/org", json={**org, "name": "x" * 200}, headers=H).status_code == 422
    assert client.put("/api/settings/org", json=org, headers=H).status_code == 200
    # logos: only real PNG/JPEG, admin only
    bad = "data:image/png;base64," + base64.b64encode(b"<svg onload=alert(1)>").decode()
    assert client.put("/api/org/logo/left", json={"data_url": bad}, headers=H).status_code == 422
    good = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert client.put("/api/org/logo/left", json={"data_url": good}, headers=V).status_code == 403
    assert client.put("/api/org/logo/top", json={"data_url": good}, headers=H).status_code == 404
    assert client.put("/api/org/logo/left", json={"data_url": good}, headers=H).status_code == 200
    assert client.get("/api/org/logo/left", headers=H).content == PNG
    r = client.get(f"/api/reports/monthly.pdf?month={month}", headers=H)
    assert r.status_code == 200 and r.content[:5] == b"%PDF-" and "attachment" in r.headers["content-disposition"]
    t = _text(r.content)
    for s in ("รายงานสรุปผลการดำเนินงานประจำเดือน", "โรงพยาบาลส่งเสริมสุขภาพตำบลทดสอบ", "นางสมศรี ใจดี", "นายรายงาน ดีมาก",
              "นักวิชาการสาธารณสุข", "การทดสอบปุ่มประจำเดือน", "รายการเหตุแจ้งทั้งหมด (1 รายการ)", f"#{c.id}"):
        assert s in t, s
    # viewer: same report without names
    tv = _text(client.get(f"/api/reports/monthly.pdf?month={month}", headers=V).content)
    assert "นางสมศรี" not in tv and "รายการเหตุแจ้งทั้งหมด (1 รายการ)" in tv
    assert client.get("/api/reports/monthly.pdf?month=2026-13", headers=H).status_code == 422
    assert client.delete("/api/org/logo/left", headers=H).status_code == 200
    assert client.get("/api/org/logo/left", headers=H).status_code == 404
    # empty month still renders
    assert _text(client.get("/api/reports/monthly.pdf?month=2020-01", headers=H).content).count("ไม่มีเหตุแจ้งในเดือนนี้") == 1

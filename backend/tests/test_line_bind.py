"""LINE binding: a QR that opens the OA chat with the code typed, a welcome on "add friend", helpful replies to
anything else, and a warning when the person bound without adding the account as a friend (push would not reach them)."""
import json
from app.db import SessionLocal
from app.models import Caregiver
from tests.test_all import login, sign


def hook(client, *events):
    body = json.dumps({"events": list(events)}).encode()
    return client.post("/api/line/webhook", content=body, headers={"x-line-signature": sign(body)})


def text(uid, t): return {"type": "message", "replyToken": "r", "source": {"userId": uid}, "message": {"type": "text", "text": t}}


def test_bind_flow_qr_and_replies(env_):
    eng, downs, notif, client, ids = env_
    H = login(client)
    notif.bot_info = lambda: {"basicId": "@123abcd", "displayName": "แจ้งเหตุ รพ.สต."}
    notif.is_friend = lambda uid: uid != "Unofriend"
    r = client.post(f"/api/caregivers/{ids['a']}/bind-code", headers=H).json()
    code = r["code"]
    assert r["oa"]["message_url"] == f"https://line.me/R/oaMessage/%40123abcd/?{code}"
    assert r["oa"]["add_url"].endswith("/%40123abcd") and r["oa"]["qr_svg"].startswith("<?xml") and r["name"] == "อสม.หนึ่ง"

    hook(client, {"type": "follow", "replyToken": "r", "source": {"userId": "Unew"}})
    assert "รหัสผูกบัญชี" in notif.replies[-1]
    hook(client, text("Unew", "สวัสดีครับ"))
    assert notif.replies[-1].startswith("ยังไม่ได้ผูกบัญชี")
    hook(client, text("Unew", "ABCD2345"))                                  # code-shaped but unknown
    assert "หมดอายุ" in notif.replies[-1]
    hook(client, text("Unew", f" {code[:4]} {code[4:].lower()} "))           # spaces / lower case typed by hand
    assert notif.replies[-1].startswith("ผูกบัญชีสำเร็จ: อสม.หนึ่ง") and "เพิ่มเพื่อน" not in notif.replies[-1]
    with SessionLocal() as db:
        assert db.get(Caregiver, ids["a"]).line_user_id == "Unew"
    hook(client, text("Unew", "ขอบคุณครับ"))
    assert notif.replies[-1].startswith("บัญชีนี้ผูกกับ อสม.หนึ่ง แล้ว") and "1669" in notif.replies[-1]
    hook(client, {"type": "follow", "replyToken": "r", "source": {"userId": "Unew"}})
    assert "อยู่แล้ว" in notif.replies[-1]

    code2 = client.post(f"/api/caregivers/{ids['b']}/bind-code", headers=H).json()["code"]
    hook(client, text("Unofriend", code2))
    assert "ผูกบัญชีสำเร็จ" in notif.replies[-1] and "เพิ่มเพื่อน" in notif.replies[-1]


def test_bind_code_without_line_token_still_works(env_):
    eng, downs, notif, client, ids = env_
    r = client.post(f"/api/caregivers/{ids['a']}/bind-code", headers=login(client)).json()
    assert len(r["code"]) == 8 and r["oa"] is None


def test_line_notifier_bot_info_and_friendship():
    import httpx
    from app.line import LineNotifier
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/v2/bot/info":
            return httpx.Response(200, json={"basicId": "@999zzz", "displayName": "OA", "userId": "Ux"})
        if req.url.path.endswith("/Ufriend"): return httpx.Response(200, json={"displayName": "x"})
        if req.url.path.endswith("/Ublocked"): return httpx.Response(404, json={})
        return httpx.Response(500)
    LineNotifier._info = (0.0, None)
    n = LineNotifier("tok", httpx.Client(transport=httpx.MockTransport(handler)))
    assert n.bot_info() == {"basicId": "@999zzz", "displayName": "OA", "pictureUrl": None}
    n.bot_info(); assert calls.count("/v2/bot/info") == 1                       # cached
    assert (n.is_friend("Ufriend"), n.is_friend("Ublocked"), n.is_friend("Uerr")) == (True, False, None)
    LineNotifier._info = (0.0, None)

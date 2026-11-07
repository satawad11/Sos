"""Two-step sign-in (TOTP + backup codes) and the on/off policy."""
import time
import pyotp
from app.db import SessionLocal
from app.models import AuditLog, User
from app import mfa
from tests.test_all import login

PW = "password12345"


def _code(secret, offset=0):
    return pyotp.TOTP(secret).at(time.time() + offset * 30)


def _enrol(client, H):
    r = client.post("/api/me/2fa/setup", headers=H); assert r.status_code == 200
    s = r.json(); assert s["uri"].startswith("otpauth://totp/") and "<svg" in s["qr_svg"]
    assert client.post("/api/me/2fa/enable", json={"code": "000000"}, headers=H).status_code == 422
    r = client.post("/api/me/2fa/enable", json={"code": _code(s["secret"])}, headers=H); assert r.status_code == 200
    return s["secret"], r.json()


def test_policy_default_off_and_voluntary_enrolment(env_):
    *_, client, ids = env_
    H = login(client)
    me = client.get("/api/me", headers=H).json()
    assert me["mfa"] == {"enabled": False, "required": False, "backup_left": 0, "enabled_at": None}
    assert client.get("/api/settings", headers=H).json()["security"]["mfa_enabled"] is False
    secret, en = _enrol(client, H)
    assert len(en["backup_codes"]) == 10
    # enabling logged out the old session (it had no second step)
    assert client.get("/api/cases", headers=H).status_code == 401
    # password alone no longer gives a session
    r = client.post("/api/login", json={"username": "admin", "password": PW}).json()
    assert r["mfa_required"] and "token" not in r
    assert client.get("/api/cases", headers={"Authorization": "Bearer " + r["ticket"]}).status_code == 401  # ticket ≠ session
    bad = client.post("/api/login/2fa", json={"ticket": r["ticket"], "code": "123456"}); assert bad.status_code == 401
    # a code already used at enrolment cannot be replayed; the next step works
    assert client.post("/api/login/2fa", json={"ticket": r["ticket"], "code": _code(secret)}).status_code == 401
    ok = client.post("/api/login/2fa", json={"ticket": r["ticket"], "code": _code(secret, +1)})
    assert ok.status_code == 200
    H2 = {"Authorization": "Bearer " + ok.json()["token"]}
    assert client.get("/api/cases", headers=H2).status_code == 200
    # backup code: works once
    bc = en["backup_codes"][0]
    t = client.post("/api/login", json={"username": "admin", "password": PW}).json()["ticket"]
    r = client.post("/api/login/2fa", json={"ticket": t, "code": bc.lower().replace("-", " ")})
    assert r.status_code == 200 and r.json()["backup_codes_left"] == 9
    t = client.post("/api/login", json={"username": "admin", "password": PW}).json()["ticket"]
    assert client.post("/api/login/2fa", json={"ticket": t, "code": bc}).status_code == 401
    # disable needs password + code, and is allowed while the policy is off
    H3 = {"Authorization": "Bearer " + r.json()["token"]}
    assert client.post("/api/me/2fa/disable", json={"code": en["backup_codes"][1], "password": "wrong"}, headers=H3).status_code == 403
    d = client.post("/api/me/2fa/disable", json={"code": en["backup_codes"][1], "password": PW}, headers=H3)
    assert d.status_code == 200
    assert "token" in client.post("/api/login", json={"username": "admin", "password": PW}).json()
    with SessionLocal() as db:
        acts = [a.action for a in db.query(AuditLog).all()]
        assert {"2fa_enable", "2fa_disable"} <= set(acts) and acts.count("login_failed") >= 2


def test_code_step_shares_lockout(env_):
    *_, client, ids = env_
    secret, _ = _enrol(client, login(client))
    for i in range(5):
        t = client.post("/api/login", json={"username": "admin", "password": PW}).json()["ticket"]
        client.post("/api/login/2fa", json={"ticket": t, "code": "000000"})
    assert client.post("/api/login", json={"username": "admin", "password": PW}).status_code == 429


def test_policy_on_forces_enrolment_and_admin_reset(env_):
    *_, client, ids = env_
    H = login(client)
    assert client.put("/api/settings/security", json={"mfa_enabled": True, "mfa_roles": ["staff"]}, headers=H).status_code == 422
    assert client.put("/api/settings/security", json={"mfa_enabled": True, "mfa_roles": ["admin"]}, headers=H).status_code == 200
    # admin without 2FA: may only enrol
    assert client.get("/api/cases", headers=H).status_code == 403
    assert client.get("/api/cases", headers=H).json()["detail"] == "2fa setup required"
    r = client.post("/api/login", json={"username": "admin", "password": PW}).json()
    assert r["mfa_setup"] is True
    # viewer is not in mfa_roles → unaffected
    assert client.get("/api/cases", headers=login(client, "viewer")).status_code == 200
    secret, en = _enrol(client, H)
    H2 = {"Authorization": "Bearer " + en["token"]}
    assert client.get("/api/cases", headers=H2).status_code == 200
    assert client.get("/api/me", headers=H2).json()["mfa"]["required"] is True
    # cannot switch it off for yourself while the policy requires it
    assert client.post("/api/me/2fa/disable", json={"code": en["backup_codes"][0], "password": PW}, headers=H2).status_code == 409
    # a second admin resets the first admin's 2FA (lost phone)
    with SessionLocal() as db:
        from app.auth import hash_pw
        db.add(User(username="admin2", password_hash=hash_pw(PW), role="admin")); db.commit()
        a1 = db.query(User).filter_by(username="admin").first().id
    t = client.post("/api/login", json={"username": "admin2", "password": PW}).json()
    assert t["mfa_setup"]
    _, en2 = _enrol(client, {"Authorization": "Bearer " + t["token"]})
    H4 = {"Authorization": "Bearer " + en2["token"]}
    u = client.post(f"/api/users/{a1}/reset-2fa", headers=H4).json()
    assert u["mfa"] is False
    assert client.get("/api/cases", headers=H2).status_code == 401          # their sessions are gone
    # policy off again → admin may work without 2FA
    client.put("/api/settings/security", json={"mfa_enabled": False, "mfa_roles": ["admin"]}, headers=H4)
    assert client.get("/api/cases", headers=login(client)).status_code == 200


def test_cli_reset_2fa(env_, capsys):
    *_, client, ids = env_
    _enrol(client, login(client))
    from app.cli import main
    assert main(["reset-2fa", "admin"]) == 0
    assert "token" in client.post("/api/login", json={"username": "admin", "password": PW}).json()


def test_verify_totp_window_and_replay():
    s = pyotp.random_base32(); now = time.time(); step = int(now // 30)
    assert mfa.verify_totp(s, pyotp.TOTP(s).at(now), None, now) == step
    assert mfa.verify_totp(s, pyotp.TOTP(s).at(now - 30), None, now) == step - 1      # clock drift tolerated
    assert mfa.verify_totp(s, pyotp.TOTP(s).at(now - 90), None, now) is None
    assert mfa.verify_totp(s, pyotp.TOTP(s).at(now), step, now) is None               # replay
    assert mfa.verify_totp(s, "12345", None, now) is None and mfa.verify_totp(s, "abcdef", None, now) is None

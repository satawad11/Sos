import json

from app import gwcmd
from app.db import SessionLocal
from app.models import Gateway, GatewayCommand
from tests.test_all import GW, GOOD, _gw_row, login


def test_gateway_desired_config_signed_apply_and_report_reconcile(env_):
    eng, downs, notif, client, ids = env_
    sent = []
    eng.gw_cmd_publish = lambda gid, msg: (sent.append((gid, msg)), True)[1]
    with SessionLocal() as db:
        gid = _gw_row(db)
        g = db.get(Gateway, gid); g.report = {"agent": True, "sw": "sos-gw-report 4"}; db.commit()
    h = login(client)
    assert client.post(f"/api/gateways/{gid}/commands", headers=h, json={"action": "apply_config"}).status_code == 422
    assert client.put(f"/api/gateways/{gid}/config", headers=h, json={
        "antenna_gain": 2, "log_level": "INFO", "latitude": 0, "longitude": 0, "report_interval_s": 60,
    }).status_code == 422
    cfg = {"antenna_gain": 3.5, "log_level": "WARN", "latitude": 13.9126, "longitude": 100.6068,
           "report_interval_s": 120}
    r = client.put(f"/api/gateways/{gid}/config", headers=h, json=cfg)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and r.json()["command"]["action"] == "apply_config"
    got_gid, msg = sent.pop()
    assert got_gid == GW and msg["params"] == {"version": 1, **cfg}
    assert msg["sig"] == gwcmd.sign(gwcmd.agent_key(GW), msg["id"], "apply_config", msg["ts"], msg["params"])
    with SessionLocal() as db:
        g = db.get(Gateway, gid); c = db.query(GatewayCommand).filter_by(cmd_id=msg["id"]).one()
        assert g.config_desired == cfg and g.config_status == "pending" and c.payload["version"] == 1
        assert eng.handle_gateway_result(db, GW, {"id": msg["id"], "ok": True, "detail": "ok", "output": ""}) == "ok"
        db.refresh(g); assert g.config_status == "applied" and g.config_applied_ver == 1 and g.config_applied_at
        report = {**GOOD, "agent": True, "versions": {"agent": "2", "report": "4", "bundle": "1.0.1"},
                  "config": {"version": 1, **cfg}}
        assert eng.handle_gateway_report(db, GW, report) == "ok"
    out = client.get(f"/api/gateways/{gid}/config", headers=h).json()
    assert out["reported"]["version"] == 1 and out["versions"]["bundle"] == "1.0.1" and out["status"] == "applied"


def test_gateway_release_allowlist_and_checksum_payload(env_, monkeypatch):
    eng, downs, notif, client, ids = env_
    sent = []; eng.gw_cmd_publish = lambda gid, msg: (sent.append(msg), True)[1]
    with SessionLocal() as db:
        gid = _gw_row(db); g = db.get(Gateway, gid); g.report = {"agent": True}; db.commit()
    h = login(client)
    sha = "a" * 64
    releases = [{"version": "1.2.3", "url": "https://github.com/example/sos/releases/download/gw-1.2.3/gateway.tar.gz", "sha256": sha},
                {"version": "bad", "url": "http://insecure/x", "sha256": sha}]
    monkeypatch.setattr("app.api_devices.env.gateway_releases_json", json.dumps(releases))
    cfg = client.get(f"/api/gateways/{gid}/config", headers=h).json()
    assert [x["version"] for x in cfg["releases"]] == ["1.2.3"]
    assert client.post(f"/api/gateways/{gid}/update", headers=h, json={"version": "bad"}).status_code == 422
    r = client.post(f"/api/gateways/{gid}/update", headers=h, json={"version": "1.2.3"})
    assert r.status_code == 200 and sent[0]["action"] == "update" and sent[0]["params"] == releases[0]
    assert sent[0]["sig"] == gwcmd.sign(gwcmd.agent_key(GW), sent[0]["id"], "update", sent[0]["ts"], releases[0])


def test_gateway_config_report_sanitizes_unknown_fields():
    from app.gateways import sanitize_report
    r = sanitize_report({"versions": {"agent": "2", "evil": "secret"},
                         "config": {"version": 7, "antenna_gain": 2, "log_level": "INFO", "unknown": "x",
                                    "latitude": 13.9, "longitude": 100.6, "report_interval_s": 60}})
    assert r["versions"] == {"agent": "2"}
    assert "unknown" not in r["config"] and r["config"]["version"] == 7
    assert gwcmd.canonical_params({"x": 2.0, "y": 13.0}) == '{"x":2,"y":13}'

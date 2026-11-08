"""USB firmware updater: the backend lists/serves the images the host updater unpacked from the firmware-bin
branch, never anything outside that folder, and records each flash in the audit log."""
import hashlib, json, os
from app.config import env
from app.db import SessionLocal
from app.models import AuditLog, Device
from tests.test_all import login

EUI = "70b3d57ed0000001"


def _publish(builds):
    d = os.path.join(env.update_dir, "firmware"); os.makedirs(d, exist_ok=True)
    out = []
    for e, chip in builds:
        b = bytes([0xE9]) + os.urandom(2048)
        open(os.path.join(d, e + ".bin"), "wb").write(b)
        out.append({"env": e, "chip": chip, "label": e, "version": "0.2", "file": e + ".bin", "offset": 65536,
                    "size": len(b), "sha256": hashlib.sha256(b).hexdigest(), "md5": hashlib.md5(b).hexdigest()})
    json.dump({"commit": "abc123", "built_at": "2026-11-08T05:00:00+00:00", "builds": out}, open(os.path.join(d, "manifest.json"), "w"))
    return out


def test_firmware_list_file_and_audit(env_, tmp_path, monkeypatch):
    eng, downs, notif, client, ids = env_
    monkeypatch.setattr(env, "update_dir", str(tmp_path))
    H, V = login(client), login(client, "viewer")
    assert client.get("/api/firmware", headers=H).json() == {"available": False, "builds": []}
    pub = _publish([("heltec_v2", "ESP32"), ("heltec_v3", "ESP32-S3")])
    os.remove(os.path.join(str(tmp_path), "firmware", "heltec_v3.bin"))           # listed but missing → not offered
    r = client.get("/api/firmware", headers=H).json()
    assert r["available"] and [b["env"] for b in r["builds"]] == ["heltec_v2"] and r["builds"][0]["md5"] == pub[0]["md5"]
    assert client.get("/api/firmware", headers=V).status_code == 403
    f = client.get("/api/firmware/heltec_v2.bin", headers=H)
    assert f.status_code == 200 and hashlib.md5(f.content).hexdigest() == pub[0]["md5"]
    for bad in ("heltec_v3", "..%2Fstatus", "manifest"):
        assert client.get(f"/api/firmware/{bad}.bin", headers=H).status_code == 404
    assert client.get("/api/firmware/heltec_v2.bin", headers=V).status_code == 403
    r = client.post("/api/firmware/flashed", headers=H, json={"dev_eui": EUI.upper(), "env": "heltec_v2", "version": "0.2", "from_version": "0.1", "ok": True})
    assert r.status_code == 200 and r.json()["device_id"]
    with SessionLocal() as db:
        assert db.query(Device).filter_by(dev_eui=EUI).one().fw_version == "0.2"
        a = db.query(AuditLog).filter_by(action="firmware_flash").one()
        assert a.target == EUI
    assert client.post("/api/firmware/flashed", headers=H, json={"dev_eui": "xyz", "env": "heltec_v2", "version": "0.2", "ok": True}).status_code == 422
    assert client.post("/api/firmware/flashed", headers=H, json={"env": "heltec_v2", "version": "0.2", "ok": False, "error": "timeout"}).status_code == 200

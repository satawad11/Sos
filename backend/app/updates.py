"""In-app "update from GitHub" — the backend only talks to the host updater through a shared folder.

The backend container never runs git or docker itself (that would need the Docker socket = root on the host).
Instead (see docs/UPDATE.md):
  * the dashboard asks the backend → backend writes  <update_dir>/request.json
  * a systemd .path unit on the host sees the file and runs infra/scripts/sos-update.sh,
    which fetches GitHub, backs up the DB, rebuilds, health-checks and rolls back on failure
  * the script writes progress to <update_dir>/status.json and update.log, which the backend reads back.
"""
from __future__ import annotations
import json, os, secrets, tempfile
from datetime import datetime, timezone
from .config import env

BUSY = ("queued", "checking", "updating", "rolling_back")
REQUEST, STATUS, LOG = "request.json", "status.json", "update.log"


class UpdateError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg); self.code = code


def _p(name: str) -> str:
    return os.path.join(env.update_dir, name)


def _read_json(name: str) -> dict | None:
    try:
        with open(_p(name), encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def _tail(name: str, n: int = 80) -> list[str]:
    try:
        with open(_p(name), "rb") as f:
            f.seek(0, os.SEEK_END); size = f.tell(); f.seek(max(0, size - 32768))
            return f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return []


def installed() -> bool:
    """The host updater writes status.json once it is set up (scripts/setup-updater.sh)."""
    return os.path.isfile(_p(STATUS))


def state() -> dict:
    st = _read_json(STATUS) or {}
    pending = _read_json(REQUEST)
    busy = bool(pending) or st.get("state") in BUSY
    return {"installed": installed(), "status": st, "pending": pending, "busy": busy, "log": _tail(LOG)}


def request(action: str, by: str) -> dict:
    if action not in ("check", "update", "firmware"):
        raise UpdateError(400, "unknown action")
    if not installed():
        raise UpdateError(503, "ยังไม่ได้ติดตั้งตัวอัปเดตบนเซิร์ฟเวอร์ — รัน infra/scripts/setup-updater.sh (ดู docs/UPDATE.md)")
    s = state()
    if s["busy"]:
        raise UpdateError(409, "กำลังทำงานอยู่ — รอให้เสร็จก่อน")
    req = {"id": secrets.token_hex(6), "action": action, "by": by,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:   # atomic: the host must never see a half-written file
        fd, tmp = tempfile.mkstemp(dir=env.update_dir, prefix=".req-")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(req, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, _p(REQUEST))
    except OSError as e:
        raise UpdateError(503, f"เขียนคำขอไม่ได้ ({e.__class__.__name__}) — ตรวจสิทธิ์โฟลเดอร์ infra/update (ดู docs/UPDATE.md)")
    return req


# ---------------- firmware images for the dashboard's USB updater (docs/FIRMWARE-UPDATE.md) ----------------
# CI publishes them on the firmware-bin branch; the host updater (fw_sync) unpacks that branch to <update_dir>/firmware.
FW_DIR = "firmware"


def firmware() -> dict:
    m = _read_json(os.path.join(FW_DIR, "manifest.json"))
    if not m or not isinstance(m.get("builds"), list):
        return {"available": False, "builds": []}
    keep = ("env", "chip", "label", "version", "size", "sha256", "md5", "offset")
    builds = [{k: b.get(k) for k in keep} for b in m["builds"]
              if isinstance(b, dict) and firmware_path(str(b.get("env", ""))) and os.path.isfile(firmware_path(str(b["env"])))]
    return {"available": bool(builds), "commit": str(m.get("commit", ""))[:12], "built_at": m.get("built_at"), "builds": builds}


def firmware_path(env_name: str) -> str | None:
    """Path of <env>.bin — env names are a fixed charset, so nothing outside the firmware folder can be named."""
    if not env_name or len(env_name) > 40 or not all(c.isalnum() or c in "_-" for c in env_name):
        return None
    return _p(os.path.join(FW_DIR, env_name + ".bin"))

"""Remote actions for gateways, over MQTT, with signed commands.

backend --publish--> <prefix>/gateway/<id>/sos/cmd         {"id","action","ts","sig"}
agent   --publish--> <prefix>/gateway/<id>/sos/cmd-result  {"id","ok","detail","output"}

Safety model (see docs/GATEWAY-REMOTE.md):
  * only a fixed whitelist of actions, no arguments, no shell strings -> nothing to inject
  * every command carries HMAC-SHA256(per-gateway key, "id|action|ts"); the agent also rejects commands older than
    2 minutes and ids it has already seen (replay)
  * per-gateway key = HMAC(master, gateway_id); master is derived from the backend secret, so there is nothing new to
    configure and one gateway's key does not work on another
  * MQTT ACL: only the backend may write .../sos/cmd; a gateway can only read its own
"""
from __future__ import annotations
import hashlib, hmac, json, re, secrets, time
from .config import env

ACTIONS = {
    "report_now": "ส่งรายงานสถานะทันที",
    "logs": "ดู log ล่าสุด",
    "restart_services": "รีสตาร์ทบริการรับสัญญาณ (LoRa)",
    "reboot": "รีบูตเครื่อง",
}
EXPIRE_S = 180
_ID = re.compile(r"^[0-9a-f]{16}$")


def _master() -> bytes:
    return hmac.new(env.jwt_secret.encode(), b"sos-gateway-agent-v1", hashlib.sha256).digest()


def agent_key(gateway_id: str) -> str:
    return hmac.new(_master(), gateway_id.lower().encode(), hashlib.sha256).hexdigest()[:40]


def sign(key: str, cmd_id: str, action: str, ts: int) -> str:
    return hmac.new(key.encode(), f"{cmd_id}|{action}|{ts}".encode(), hashlib.sha256).hexdigest()


def make_command(gateway_id: str, action: str, now: float | None = None) -> tuple[str, dict]:
    if action not in ACTIONS:
        raise ValueError("unknown action")
    cid, ts = secrets.token_hex(8), int(now if now is not None else time.time())
    return cid, {"id": cid, "action": action, "ts": ts, "sig": sign(agent_key(gateway_id), cid, action, ts)}


def cmd_topic(gateway_id: str, prefix: str | None = None) -> str:
    return f"{prefix or env.gw_topic_prefix}/gateway/{gateway_id}/sos/cmd"


_SECRET_RE = re.compile(r"(?i)(pass(word)?|secret|token|key)\s*[=:]\s*\S+")


def parse_result(topic: str, raw: bytes, prefix: str | None = None) -> tuple[str, dict] | None:
    """<prefix>/gateway/<id>/sos/cmd-result -> (gateway_id, {"id","ok","detail","output"}) or None."""
    prefix = prefix or env.gw_topic_prefix
    parts = topic.split("/")
    if len(parts) != 5 or parts[0] != prefix or parts[1] != "gateway" or parts[3:] != ["sos", "cmd-result"]:
        return None
    gid = parts[2].lower()
    if not _ID.match(gid) or len(raw) > 8192:
        return None
    try:
        j = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(j, dict) or not isinstance(j.get("id"), str) or not _ID.match(j["id"]) or not isinstance(j.get("ok"), bool):
        return None
    det = j.get("detail") if isinstance(j.get("detail"), str) else ""
    out = j.get("output") if isinstance(j.get("output"), str) else ""
    out = _SECRET_RE.sub(r"\1=***", out)[-4000:]
    return gid, {"id": j["id"], "ok": j["ok"], "detail": det[:240], "output": out}

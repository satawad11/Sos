"""Is the system really working? /healthz (public, booleans only) and /api/system/health (admin, details) read this.
Also the outgoing heartbeat: while everything is healthy the server pings an external dead-man's-switch URL
(e.g. healthchecks.io); when the server, its power or its internet link dies, the pings stop and THAT service alerts."""
from __future__ import annotations
import logging, threading, time
from datetime import datetime, timezone
from sqlalchemy import text

log = logging.getLogger("sos.health")
TICK_STALE_S = 60
state: dict = {"started": time.time(), "loop": False, "tick_at": 0.0, "mqtt_down_since": 0.0,
               "hb_at": None, "hb_ok": None, "hb_detail": ""}


def beat():
    state["loop"] = True; state["tick_at"] = time.time()


def snapshot(session_factory, engine=None, notify=None) -> dict:
    now = time.time()
    try:
        with session_factory() as db:
            db.execute(text("SELECT 1"))
            q = notify.stats(db) if notify is not None else None
        db_ok = True
    except Exception as e:
        log.error("health: database check failed: %s", e)
        db_ok, q = False, None
    mqtt = bool(engine.mqtt_ok()) if engine is not None else False
    if mqtt: state["mqtt_down_since"] = 0.0
    elif not state["mqtt_down_since"]: state["mqtt_down_since"] = now
    loop = (not state["loop"]) or now - state["tick_at"] < TICK_STALE_S       # not started (tests/CLI) counts as fine
    line = q is None or (q["alive"] or not state["loop"]) and q["oldest_s"] < 300
    core = db_ok and loop
    return {"ok": core, "db": db_ok, "loop": loop, "mqtt": mqtt, "line_queue": line,
            "mqtt_down_s": round(now - state["mqtt_down_since"]) if state["mqtt_down_since"] else 0,
            "all_ok": core and line and (mqtt or now - state["mqtt_down_since"] < 120),
            "uptime_s": round(now - state["started"]), "queue": q}


def heartbeat_loop(session_factory, get_engine, get_notify, stop: threading.Event):
    import httpx
    from . import settings_store
    last = 0.0
    while not stop.wait(20):
        try:
            with session_factory() as db:
                cfg = settings_store.get(db, "monitoring")
            url = (cfg.get("heartbeat_url") or "").strip()
            if not url or time.time() - last < max(1, int(cfg.get("interval_min", 5))) * 60:
                continue
            last = time.time()
            snap = snapshot(session_factory, get_engine(), get_notify())
            state["hb_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if not snap["all_ok"]:
                # say nothing: the monitoring service raises the alarm when the pings stop
                state["hb_ok"], state["hb_detail"] = False, "ไม่ได้ส่ง เพราะระบบไม่ปกติ: " + ", ".join(k for k in ("db", "loop", "mqtt", "line_queue") if not snap[k])
                continue
            r = httpx.get(url, timeout=10, follow_redirects=True)
            state["hb_ok"], state["hb_detail"] = r.status_code < 400, f"HTTP {r.status_code}"
        except Exception as e:
            state["hb_ok"], state["hb_detail"] = False, f"{e.__class__.__name__}: {e}"[:200]

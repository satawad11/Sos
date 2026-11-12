from __future__ import annotations
import asyncio, logging, threading, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from . import api, health, privacy, settings_store
from .auth import audit, hash_pw, user_from_token
from .config import env
from .db import SessionLocal, engine as dbengine, migrate
from .engine import Engine
from .line import LineNotifier
from .models import User

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("sos")


class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set(); self.loop = None

    def publish(self, event: dict):
        if self.loop and self.clients:
            asyncio.run_coroutine_threadsafe(self._send(event), self.loop)

    async def _send(self, event):
        # all screens at once; a stalled connection (sleeping laptop, dead Wi-Fi) is dropped instead of delaying the rest
        async def one(ws):
            try: await asyncio.wait_for(ws.send_json(event), timeout=5)
            except Exception:
                self.clients.discard(ws)
                try: await ws.close()
                except Exception: pass
        await asyncio.gather(*(one(ws) for ws in list(self.clients)))


hub = Hub()


class DashboardFiles(StaticFiles):
    """The dashboard is index.html + app.css + js/*.js. They must always be the same release, so the browser is told to
    check with the server every time (a cheap 304 when nothing changed). Fonts, icons and vendored libraries never change
    under the same name and stay cacheable (the service worker keeps them too)."""
    LONG = ("fonts/", "icons/", "vendor/")

    async def get_response(self, path: str, scope):
        resp = await super().get_response(path, scope)
        if not path.replace("\\", "/").startswith(self.LONG):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


def bootstrap_admin():
    with SessionLocal() as db:
        if db.query(User).count() == 0:
            if not env.bootstrap_admin_password:
                log.warning("No users and SOS_BOOTSTRAP_ADMIN_PASSWORD empty — set it and restart")
                return
            db.add(User(username=env.bootstrap_admin_user, password_hash=hash_pw(env.bootstrap_admin_password),
                        role="admin", display_name="ผู้ดูแลระบบ", must_change=True))
            db.commit(); log.info("bootstrap admin created")


def escalation_loop(eng: Engine, stop: threading.Event):
    n = 0
    while not stop.wait(5):
        try:
            with SessionLocal() as db:
                eng.tick(db)
            health.beat()
        except Exception:
            log.exception("escalation tick failed")
        n += 1
        if n % 720 == 0:                       # hourly: housekeeping that never touches personal data
            try:
                with SessionLocal() as db:
                    from .outbox import NotifyWorker
                    from . import telemetry
                    NotifyWorker.purge(db); telemetry.purge(db); db.commit()
                    from . import backupinfo
                    import time as _t
                    bk = backupinfo.state(int(settings_store.get(db, "monitoring").get("backup_max_age_h", 36)))
                    if bk.get("known") and bk["problem"] and _t.time() - health.state.get("backup_alert_at", 0) > 86400:
                        health.state["backup_alert_at"] = _t.time()      # once a day at most
                        res = eng.gw.send_system(db, "⚠️ การสำรองข้อมูลระบบแจ้งเหตุฉุกเฉินมีปัญหา\n" + bk["problem"])
                        audit(db, "system", "backup_alert", "", problem=bk["problem"], results=res); db.commit()
            except Exception:
                log.exception("housekeeping failed")
        if n % 720 == 0:                       # hourly (720 x 5 s): retention purge, only when the admin switched it on
            try:
                with SessionLocal() as db:
                    if settings_store.get(db, "privacy")["auto_purge"]:
                        res = privacy.retention(db, dry_run=False)
                        if res["cases"] or res["audit_rows"]:
                            audit(db, "system", "retention_run", "", auto=True, **{k: v for k, v in res.items() if isinstance(v, int)})
                        db.commit()
            except Exception:
                log.exception("retention purge failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if env.jwt_secret in ("", "CHANGE_ME") or len(env.jwt_secret) < 32:
        raise RuntimeError("SOS_JWT_SECRET must be set to a random string of at least 32 characters")
    log.info("database schema: %s", migrate())
    bootstrap_admin()
    from . import linecfg
    with SessionLocal() as _db: linecfg.load(_db)
    hub.loop = asyncio.get_running_loop()
    from .mqtt_worker import MqttWorker
    from .push import PushSender
    pusher = PushSender(SessionLocal); pusher.start(); api._engine["push"] = pusher
    from .outbox import NotifyWorker, QueuedNotifier
    publish = lambda e: (hub.publish(e), pusher.on_event(e))
    line = LineNotifier()
    notify = NotifyWorker(SessionLocal, line, publish)          # LINE is sent off the MQTT thread, with retries (app/outbox.py)
    worker = MqttWorker(lambda dl: Engine(dl, QueuedNotifier(line, notify.wake), publish))
    api._engine["engine"] = worker.engine; api._engine["notify"] = notify
    notify.start()

    def _flush(eui: str):
        from . import provision as prov
        cs = prov.get_cs()
        if cs.configured:
            cs.flush_queue(eui)
        with SessionLocal() as db:          # a ping in the queue is gone now: say why instead of letting it expire
            worker.engine.netmon.cancel_pings(db, eui, "ยกเลิก: มีเหตุฉุกเฉิน/ข้อความจริงต้องส่งก่อน")
    worker.engine.flush_queue = _flush
    worker.start()
    stop = threading.Event()
    threading.Thread(target=escalation_loop, args=(worker.engine, stop), daemon=True).start()
    threading.Thread(target=health.heartbeat_loop, daemon=True,
                     args=(SessionLocal, lambda: worker.engine, lambda: notify, stop)).start()
    yield
    stop.set(); worker.stop(); notify.stop()


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(title="SOS LoRa", lifespan=lifespan if with_lifespan else None)
    app.include_router(api.router)
    from . import api_auth, api_devices, api_admin, api_line, api_ai, api_setup, api_app
    for part in (api_auth, api_devices, api_admin, api_line, api_ai, api_setup, api_app):
        app.include_router(part.router)        # the rest of /api, one router per area (see api.py)
    from . import csgate
    app.include_router(csgate.router)          # sign-in gate for the ChirpStack domain
    from . import dataapi
    app.include_router(dataapi.router)         # system health, device history, CSV import, data readiness
    from . import netapi
    app.include_router(netapi.router)          # radio history, diagnosis, ping tests, live events (app/netmon.py)

    @app.get("/healthz")
    def healthz(strict: bool = False):
        """Public, booleans only. 503 when the database or the escalation loop is down — the updater and Docker rely on that.
        ?strict=1 also fails when MQTT has been disconnected for 2 minutes or the LINE queue is stuck (for an uptime monitor)."""
        from fastapi.responses import JSONResponse
        s = health.snapshot(SessionLocal, api._engine.get("engine"), api._engine.get("notify"))
        body = {k: s[k] for k in ("ok", "db", "loop", "mqtt", "line_queue")}
        good = s["all_ok"] if strict else s["ok"]
        return JSONResponse(body, status_code=200 if good else 503)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket, token: str = ""):
        with SessionLocal() as db:
            try: user_from_token(db, token)
            except Exception:
                await websocket.close(code=4401); return
        await websocket.accept(); hub.clients.add(websocket)
        try:
            while True:                       # the dashboard sends "ping" every 25 s to notice a dead link quickly
                if await websocket.receive_text() == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(websocket)

    import os
    static = os.environ.get("SOS_STATIC_DIR", "/app/static")
    if os.path.isdir(static):
        app.mount("/", DashboardFiles(directory=static, html=True), name="static")
    return app


app = create_app()

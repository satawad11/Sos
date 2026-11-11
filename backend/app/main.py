from __future__ import annotations
import asyncio, logging, threading, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from . import api, privacy, settings_store
from .auth import audit, hash_pw, user_from_token
from .config import env
from .db import Base, SessionLocal, engine as dbengine, ensure_columns
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
        except Exception:
            log.exception("escalation tick failed")
        n += 1
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
    Base.metadata.create_all(dbengine)
    ensure_columns()
    bootstrap_admin()
    from . import linecfg
    with SessionLocal() as _db: linecfg.load(_db)
    hub.loop = asyncio.get_running_loop()
    from .mqtt_worker import MqttWorker
    from .push import PushSender
    pusher = PushSender(SessionLocal); pusher.start(); api._engine["push"] = pusher
    worker = MqttWorker(lambda dl: Engine(dl, LineNotifier(), lambda e: (hub.publish(e), pusher.on_event(e))))
    api._engine["engine"] = worker.engine

    def _flush(eui: str):
        from . import provision as prov
        cs = prov.get_cs()
        if cs.configured:
            cs.flush_queue(eui)
    worker.engine.flush_queue = _flush
    worker.start()
    stop = threading.Event()
    threading.Thread(target=escalation_loop, args=(worker.engine, stop), daemon=True).start()
    yield
    stop.set(); worker.stop()


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(title="SOS LoRa", lifespan=lifespan if with_lifespan else None)
    app.include_router(api.router)
    from . import csgate
    app.include_router(csgate.router)          # sign-in gate for the ChirpStack domain

    @app.get("/healthz")
    def healthz(): return {"ok": True}

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
        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app


app = create_app()

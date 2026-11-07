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
        for ws in list(self.clients):
            try: await ws.send_json(event)
            except Exception: self.clients.discard(ws)


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
    hub.loop = asyncio.get_running_loop()
    from .mqtt_worker import MqttWorker
    from .push import PushSender
    pusher = PushSender(SessionLocal); pusher.start(); api._engine["push"] = pusher
    worker = MqttWorker(lambda dl: Engine(dl, LineNotifier(), lambda e: (hub.publish(e), pusher.on_event(e))))
    api._engine["engine"] = worker.engine
    worker.start()
    stop = threading.Event()
    threading.Thread(target=escalation_loop, args=(worker.engine, stop), daemon=True).start()
    yield
    stop.set(); worker.stop()


def create_app(with_lifespan: bool = True) -> FastAPI:
    app = FastAPI(title="SOS LoRa", lifespan=lifespan if with_lifespan else None)
    app.include_router(api.router)

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
            while True: await websocket.receive_text()
        except WebSocketDisconnect:
            hub.clients.discard(websocket)

    import os
    static = os.environ.get("SOS_STATIC_DIR", "/app/static")
    if os.path.isdir(static):
        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app


app = create_app()

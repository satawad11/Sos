"""ChirpStack v4 MQTT integration consumer + downlink publisher."""
from __future__ import annotations
import json, logging, ssl, threading
import paho.mqtt.client as mqtt
from . import codec
from .config import env
from .db import SessionLocal
from .engine import Engine

log = logging.getLogger("sos.mqtt")


def parse_up_event(raw: bytes) -> tuple[str, int, bytes, dict] | None:
    """Return (dev_eui, fport, payload, meta) or None if not a usable uplink."""
    try:
        j = json.loads(raw)
        dev_eui = j["deviceInfo"]["devEui"].lower()
        fport = int(j.get("fPort", 0))
        data = codec.unb64(j.get("data", "")) if j.get("data") else b""
    except (KeyError, ValueError, TypeError):
        return None
    meta = {}
    rx = j.get("rxInfo") or []
    if rx:
        best = max(rx, key=lambda r: r.get("rssi", -999))
        meta = {"gateway_id": best.get("gatewayId", ""), "rssi": best.get("rssi"), "snr": best.get("snr")}
    return dev_eui, fport, data, meta


class MqttWorker:
    def __init__(self, engine_factory):
        self.engine: Engine = engine_factory(self.send_downlink)
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sos-backend")
        if env.mqtt_user:
            self.client.username_pw_set(env.mqtt_user, env.mqtt_password)
        if env.mqtt_tls:
            self.client.tls_set(ca_certs=env.mqtt_ca or None, cert_reqs=ssl.CERT_REQUIRED)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 30)
        self.connected = False

    def start(self):
        self.client.connect_async(env.mqtt_host, env.mqtt_port, keepalive=30)
        self.client.loop_start()

    def stop(self):
        self.client.loop_stop(); self.client.disconnect()

    def _on_connect(self, client, userdata, flags, rc, props=None):
        self.connected = (rc == 0)
        log.info("MQTT connected rc=%s", rc)
        client.subscribe("application/+/device/+/event/up", qos=1)

    def _on_message(self, client, userdata, msg):
        parsed = parse_up_event(msg.payload)
        if not parsed:
            return
        dev_eui, fport, data, meta = parsed
        if not data:
            return
        with SessionLocal() as db:
            try:
                res = self.engine.handle_uplink(db, dev_eui, data, meta)
                log.info("uplink %s fport=%s -> %s", dev_eui, fport, res)
            except Exception:
                log.exception("uplink handling failed"); db.rollback()

    def send_downlink(self, dev_eui: str, fport: int, payload: bytes):
        app = env.app_id
        topic = f"application/{app}/device/{dev_eui}/command/down"
        body = {"devEui": dev_eui, "confirmed": False, "fPort": fport, "data": codec.b64(payload)}
        info = self.client.publish(topic, json.dumps(body), qos=1)
        log.info("downlink queued %s fport=%s rc=%s", dev_eui, fport, info.rc)

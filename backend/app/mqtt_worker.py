"""ChirpStack v4 MQTT integration consumer + downlink publisher."""
from __future__ import annotations
import json, logging, ssl, threading
import paho.mqtt.client as mqtt
from . import codec, gwcmd
from .config import env
from .db import SessionLocal
from .engine import Engine
from .gateways import GW_RE, MAX_REPORT_BYTES

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
        meta = {"gateway_id": best.get("gatewayId", ""), "rssi": best.get("rssi"), "snr": best.get("snr"),
                "gateways": sorted({str(r.get("gatewayId", "")).lower() for r in rx if r.get("gatewayId")})}
    return dev_eui, fport, data, meta


def parse_gw_health(topic: str, raw: bytes, prefix: str | None = None) -> tuple[str, dict] | None:
    """<prefix>/gateway/<gateway_id>/sos/health  ->  (gateway_id, report) or None."""
    prefix = prefix or env.gw_topic_prefix
    parts = topic.split("/")
    if len(parts) != 5 or parts[0] != prefix or parts[1] != "gateway" or parts[3:] != ["sos", "health"]:
        return None
    gid = parts[2].lower()
    if not GW_RE.match(gid) or len(raw) > MAX_REPORT_BYTES:
        return None
    try:
        rep = json.loads(raw)
    except ValueError:
        return None
    return (gid, rep) if isinstance(rep, dict) else None


class MqttWorker:
    def __init__(self, engine_factory):
        self.engine: Engine = engine_factory(self.send_downlink)
        self.engine.mqtt_ok = lambda: self.connected
        self.engine.gw_cmd_publish = self.send_gw_command
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="sos-backend")
        if env.mqtt_user:
            self.client.username_pw_set(env.mqtt_user, env.mqtt_password)
        if env.mqtt_tls:
            self.client.tls_set(ca_certs=env.mqtt_ca or None, cert_reqs=ssl.CERT_REQUIRED)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect
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
        client.subscribe("application/+/device/+/event/+", qos=1)     # up + join/ack/txack/log/status (app/netmon.py)
        client.subscribe(f"{env.gw_topic_prefix}/gateway/+/sos/health", qos=1)
        client.subscribe(f"{env.gw_topic_prefix}/gateway/+/sos/cmd-result", qos=1)
        if self.connected:
            self.engine.gw.mqtt_connected()      # reports need a moment to arrive before gateways are judged

    def _on_disconnect(self, client, userdata, flags, rc, props=None):
        self.connected = False
        log.warning("MQTT disconnected rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        if msg.topic.endswith("/sos/cmd-result"):
            r = gwcmd.parse_result(msg.topic, msg.payload)
            if r:
                with SessionLocal() as db:
                    try:
                        log.info("gateway %s command result -> %s", r[0], self.engine.handle_gateway_result(db, r[0], r[1]))
                    except Exception:
                        log.exception("gateway command result handling failed"); db.rollback()
            return
        if "/sos/health" in msg.topic:
            gh = parse_gw_health(msg.topic, msg.payload)
            if gh:
                with SessionLocal() as db:
                    try:
                        self.engine.handle_gateway_report(db, gh[0], gh[1])
                    except Exception:
                        log.exception("gateway report handling failed"); db.rollback()
            return
        from . import netmon
        tk = netmon.parse_topic(msg.topic)
        if tk and tk[1] != "up":
            j = netmon.loads(msg.payload)
            if j is not None:
                with SessionLocal() as db:
                    try: self.engine.netmon.on_event(db, tk[1], tk[0], j)
                    except Exception: log.exception("net event handling failed"); db.rollback()
            return
        if tk:
            j = netmon.loads(msg.payload)
            if j is not None:                       # radio history first, separately: it must never block an alarm
                with SessionLocal() as db:
                    try: self.engine.netmon.on_uplink(db, tk[0], j)
                    except Exception: log.exception("uplink logging failed"); db.rollback()
        parsed = parse_up_event(msg.payload)
        if not parsed:
            return
        dev_eui, fport, data, meta = parsed
        if not data:
            return
        with SessionLocal() as db:
            try:
                if fport == 5:
                    from .models import AuditLog, Device
                    if len(data) == 5 and data[0] == 1:
                        dev = db.query(Device).filter_by(dev_eui=dev_eui).first()
                        if dev:
                            db.add(AuditLog(actor="device", action="device_diagnostic", target=dev_eui,
                                detail={"reset_reason":data[1],"config_ver":data[2],"heap_kb":data[3],"battery":data[4]}))
                            db.commit()
                    return
                if fport == codec.FPORT_LOC:
                    log.info("location %s -> %s", dev_eui, self.engine.handle_location(db, dev_eui, data, meta))
                    return
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
        with SessionLocal() as db:
            try: self.engine.netmon.on_downlink(db, dev_eui, fport, len(payload))
            except Exception: log.exception("downlink logging failed"); db.rollback()

    def send_gw_command(self, gateway_id: str, payload: dict) -> bool:
        """Publish a signed command to one gateway (QoS 1, not retained: a command must never fire later, after a reconnect)."""
        if not self.connected:
            return False
        info = self.client.publish(gwcmd.cmd_topic(gateway_id), json.dumps(payload), qos=1, retain=False)
        log.info("gateway command %s %s rc=%s", gateway_id, payload.get("action"), info.rc)
        return info.rc == mqtt.MQTT_ERR_SUCCESS

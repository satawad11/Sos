#!/usr/bin/env python3
"""Device + ChirpStack-MQTT simulator.

Pretends to be a patient device AND the ChirpStack integration, so the backend can be tested with no radio.
  python sim.py --host localhost --port 1883 --app-id APP --dev-eui 70b3d57ed0000001 press
Commands: press | heartbeat | test | cancel | poll
`press` behaves like the real firmware: sends ALERT, then ALERT_POLL every --poll seconds until ACK_L2 arrives.
"""
import argparse, base64, json, ssl, sys, threading, time
import paho.mqtt.client as mqtt

HDR = lambda t: (1 << 4) | t


def frame(t, alert_id, flags=0x01, batt=90, cfg=1, poll=0):
    return bytes([HDR(t), alert_id, flags, batt, 0, 1, cfg, poll])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["press", "heartbeat", "test", "cancel", "poll"])
    ap.add_argument("--host", default="localhost"); ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--tls", action="store_true"); ap.add_argument("--user"); ap.add_argument("--password")
    ap.add_argument("--app-id", required=True); ap.add_argument("--dev-eui", required=True)
    ap.add_argument("--gateway", default="0016c001ff10abcd"); ap.add_argument("--alert-id", type=int, default=1)
    ap.add_argument("--poll", type=float, default=10); ap.add_argument("--timeout", type=float, default=120)
    ap.add_argument("--mains-off", action="store_true"); ap.add_argument("--batt", type=int, default=90)
    a = ap.parse_args()

    got = threading.Event(); result = {}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{a.dev_eui}")
    if a.user: c.username_pw_set(a.user, a.password)
    if a.tls: c.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    def on_msg(cl, ud, m):
        j = json.loads(m.payload); d = base64.b64decode(j["data"])
        print(f"<- DOWNLINK fport={j['fPort']} bytes={d.hex(' ')}")
        if j["fPort"] == 10 and len(d) == 3 and d[0] == 0x16:
            names = {1: "รับเรื่องแล้ว", 2: "ปิดเคสแล้ว", 3: "เหตุผิดพลาด", 4: "นอกเวลา โทร 1669"}
            print(f"   ACK_L2 alert={d[1]} result={d[2]} ({names.get(d[2], '?')})")
            result["ack"] = d[2]; got.set()

    c.on_message = on_msg
    c.connect(a.host, a.port, 30); c.loop_start()
    c.subscribe(f"application/{a.app_id}/device/{a.dev_eui}/command/down", qos=1)
    flags = 0x00 if a.mains_off else 0x01
    up_topic = f"application/{a.app_id}/device/{a.dev_eui}/event/up"
    fcnt = [0]

    def send(t, port, poll=0):
        fcnt[0] += 1
        p = frame(t, a.alert_id, flags, a.batt, poll=poll)
        ev = {"deviceInfo": {"devEui": a.dev_eui, "applicationId": a.app_id}, "fCnt": fcnt[0], "fPort": port,
              "data": base64.b64encode(p).decode(),
              "rxInfo": [{"gatewayId": a.gateway, "rssi": -92, "snr": 6.5}]}
        c.publish(up_topic, json.dumps(ev), qos=1).wait_for_publish()
        print(f"-> UPLINK type={t} fport={port} bytes={p.hex(' ')}")

    if a.cmd == "heartbeat": send(4, 2)
    elif a.cmd == "test": send(3, 1)
    elif a.cmd == "cancel": send(2, 1)
    elif a.cmd == "poll": send(5, 3)
    else:
        send(1, 1); t0 = time.time(); n = 0
        while not got.is_set() and time.time() - t0 < a.timeout:
            if got.wait(a.poll): break
            n = (n + 1) % 256; send(5, 3, poll=n)
        print("RESULT:", f"ACK_L2={result['ack']}" if got.is_set() else "TIMEOUT (red light would blink)")
        c.loop_stop(); sys.exit(0 if got.is_set() else 2)
    time.sleep(1); c.loop_stop()


if __name__ == "__main__":
    main()

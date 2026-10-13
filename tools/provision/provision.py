#!/usr/bin/env python3
"""Provision one patient device: generate keys -> register in ChirpStack + backend -> flash keys over USB serial.

Usage:
  export CS_URL=https://sos.example.org/chirpstack  CS_API_KEY=...  CS_APP_ID=...  CS_PROFILE_ID=...
  export SOS_URL=https://sos.example.org  SOS_USER=admin  SOS_PASSWORD=...
  python provision.py --port /dev/ttyACM0 --label "SOS-001"
Dry run (no network / no device):  python provision.py --dry-run --label TEST
Keys are written to the device and to ChirpStack only; the local CSV keeps DevEUI + label, NOT the AppKey
(unless --save-key is given — then protect that file).
"""
import argparse, csv, os, secrets, sys, time
from datetime import datetime, timezone
import httpx


def gen_identity() -> tuple[str, str]:
    dev_eui = "02" + secrets.token_hex(7)           # 0x02 = locally administered unicast EUI-64
    return dev_eui, secrets.token_hex(16)


class ChirpStack:
    def __init__(self, base, api_key, app_id, profile_id, client=None):
        self.base, self.app_id, self.profile_id = base.rstrip("/"), app_id, profile_id
        self.http = client or httpx.Client(timeout=15)
        self.h = {"Authorization": f"Bearer {api_key}"}

    def register(self, dev_eui, app_key, name, desc=""):
        r = self.http.post(f"{self.base}/api/devices", headers=self.h, json={"device": {
            "devEui": dev_eui, "name": name, "description": desc, "applicationId": self.app_id,
            "deviceProfileId": self.profile_id, "isDisabled": False, "skipFcntCheck": False}})
        r.raise_for_status()
        # LoRaWAN 1.0.x: ChirpStack v4 stores the root key in nwk_key; set app_key too for 1.1-safety.
        r = self.http.post(f"{self.base}/api/devices/{dev_eui}/keys", headers=self.h, json={"deviceKeys": {
            "devEui": dev_eui, "nwkKey": app_key, "appKey": app_key}})
        r.raise_for_status()

    def delete(self, dev_eui):
        self.http.delete(f"{self.base}/api/devices/{dev_eui}", headers=self.h)


class Backend:
    def __init__(self, base, user, password, client=None):
        self.base = base.rstrip("/"); self.http = client or httpx.Client(timeout=15)
        r = self.http.post(f"{self.base}/api/login", json={"username": user, "password": password})
        r.raise_for_status()
        self.h = {"Authorization": f"Bearer {r.json()['token']}"}

    def register(self, dev_eui, label):
        r = self.http.post(f"{self.base}/api/devices", headers=self.h,
                           json={"dev_eui": dev_eui, "label": label, "lifecycle": "ready"})
        r.raise_for_status()


def flash_serial(ser, dev_eui, app_key, join_eui="0000000000000000", timeout=8.0) -> str:
    """ser: object with write(bytes), readline()->bytes. Returns INFO line after reboot."""
    def ask(cmd, until, t=timeout):
        ser.write((cmd + "\n").encode()); end = time.time() + t
        while time.time() < end:
            line = ser.readline().decode(errors="ignore").strip()
            if line.startswith(until): return line
            if line.startswith("ERR"): raise RuntimeError(line)
        raise TimeoutError(f"no '{until}' reply to {cmd.split()[0]}")
    ask("PING", "PONG")
    ask(f"PROV {dev_eui} {app_key} {join_eui}", "OK", 5)
    time.sleep(2.5)                                   # device reboots
    getattr(ser, "reset_input_buffer", lambda: None)()
    info = ask("INFO", "INFO", 10)
    if f"deveui={dev_eui}" not in info or "provisioned=1" not in info:
        raise RuntimeError(f"verification failed: {info}")
    return info


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port"); ap.add_argument("--label", required=True)
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--save-key", action="store_true")
    ap.add_argument("--csv", default="provisioned_devices.csv")
    ap.add_argument("--qr", action="store_true", help="write <label>.png QR (DevEUI) for the box label")
    a = ap.parse_args(argv)
    dev_eui, app_key = gen_identity()
    print(f"DevEUI  : {dev_eui}\nLabel   : {a.label}")
    if a.dry_run:
        print("(dry run) AppKey not shown; nothing registered or flashed"); return 0
    env = os.environ
    for k in ("CS_URL", "CS_API_KEY", "CS_APP_ID", "CS_PROFILE_ID", "SOS_URL", "SOS_USER", "SOS_PASSWORD"):
        if not env.get(k): sys.exit(f"missing env {k}")
    if not a.port: sys.exit("--port required (or use --dry-run)")
    import serial
    cs = ChirpStack(env["CS_URL"], env["CS_API_KEY"], env["CS_APP_ID"], env["CS_PROFILE_ID"])
    be = Backend(env["SOS_URL"], env["SOS_USER"], env["SOS_PASSWORD"])
    cs.register(dev_eui, app_key, a.label); print("ChirpStack: registered")
    try:
        be.register(dev_eui, a.label); print("Backend   : registered")
        with serial.Serial(a.port, 115200, timeout=1) as ser:
            print("Device    :", flash_serial(ser, dev_eui, app_key))
    except Exception as e:
        print("FAILED:", e, "— rolling back ChirpStack registration"); cs.delete(dev_eui); return 1
    new = not os.path.exists(a.csv)
    with open(a.csv, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(["time_utc", "dev_eui", "label"] + (["app_key"] if a.save_key else []))
        w.writerow([datetime.now(timezone.utc).isoformat(), dev_eui, a.label] + ([app_key] if a.save_key else []))
    if a.qr:
        import qrcode; qrcode.make(dev_eui).save(f"{a.label}.png")
    print("DONE. Next: power-cycle the device, watch it join in ChirpStack > Devices > LoRaWAN frames,")
    print("      then assign it to a patient in the dashboard (Devices page) and set lifecycle=active.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Build manifest.json for the firmware-bin branch (run by CI after `pio run` for every board).

usage: fw_manifest.py <dir-with-ENV.bin-files> <platformio.ini> <commit-sha>
Each <env>.bin is the application image only (written at 0x10000): bootloader, partition table and the NVS
partition holding the LoRaWAN keys are left untouched when the dashboard flashes it over USB.
"""
import datetime, hashlib, json, pathlib, re, sys

BOARDS = {  # env → (chip name esptool reports, label shown in the dashboard)
    "heltec_v2":     ("ESP32",    "Heltec V2 (ESP32 + SX1276)"),
    "heltec_v3":     ("ESP32-S3", "Heltec V3 (ESP32-S3 + SX1262)"),
    "heltec_v3_gps": ("ESP32-S3", "Heltec V3 + GPS"),
}
APP_OFFSET = 0x10000


def env_version(ini: str, env: str) -> str:
    """FW_MAJOR/FW_MINOR of an env, following `extends =` (PlatformIO syntax, not configparser's)."""
    secs = dict(re.findall(r"^\[env:([\w-]+)\]\s*\n(.*?)(?=^\[|\Z)", ini, re.S | re.M))
    seen = set()
    while env in secs and env not in seen:
        seen.add(env); body = secs[env]
        ma, mi = re.search(r"-D\s*FW_MAJOR=(\d+)", body), re.search(r"-D\s*FW_MINOR=(\d+)", body)
        if ma and mi: return f"{ma.group(1)}.{mi.group(1)}"
        ext = re.search(r"^extends\s*=\s*env:([\w-]+)", body, re.M)
        if not ext: break
        env = ext.group(1)
    raise SystemExit(f"no FW_MAJOR/FW_MINOR for env {env}")


def main(out: str, ini_path: str, sha: str) -> None:
    d, ini = pathlib.Path(out), pathlib.Path(ini_path).read_text()
    builds = []
    for env, (chip, label) in BOARDS.items():
        f = d / f"{env}.bin"
        if not f.exists(): continue
        b = f.read_bytes()
        if len(b) < 1024 or b[0] != 0xE9: raise SystemExit(f"{f} is not an ESP32 app image")
        builds.append({"env": env, "chip": chip, "label": label, "version": env_version(ini, env), "file": f.name,
                       "offset": APP_OFFSET, "size": len(b), "sha256": hashlib.sha256(b).hexdigest(), "md5": hashlib.md5(b).hexdigest()})
    if not builds: raise SystemExit("no firmware images found")
    m = {"commit": sha[:12], "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"), "builds": builds}
    (d / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))
    print(json.dumps(m, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 4: raise SystemExit(__doc__)
    main(*sys.argv[1:])

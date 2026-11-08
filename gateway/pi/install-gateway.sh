#!/usr/bin/env bash
# SOS LoRa gateway installer — Raspberry Pi 4B + Seeed WM1302 (SPI), Raspberry Pi OS Lite 64-bit (Bookworm/Trixie).
#
#   sudo ./install-gateway.sh --show-id                    # print the Gateway ID only (do this FIRST, before the VPS step)
#   sudo ./install-gateway.sh                              # interactive install
#   sudo DOMAIN=sos.example.org MQTT_PASSWORD=... CA_FILE=./ca.crt LAT=13.91 LON=100.60 ./install-gateway.sh   # unattended
#
# Re-runnable. Binaries come from the official ChirpStack downloads at artifacts.chirpstack.io (override with CONCENTRATORD_URL / MQTT_FORWARDER_URL).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }

# ---------- Gateway ID = EUI-64 from eth0 MAC (aa:bb:cc:dd:ee:ff -> aabbccfffeddeeff) ----------
mac="$(cat /sys/class/net/eth0/address 2>/dev/null || true)"
[ -n "$mac" ] || { echo "eth0 not found: connect the LAN cable (or set GATEWAY_ID=16hex manually)"; [ -n "${GATEWAY_ID:-}" ] || exit 1; }
GATEWAY_ID="${GATEWAY_ID:-$(echo "$mac" | tr -d ':' | awk '{print tolower(substr($0,1,6)) "fffe" tolower(substr($0,7,6))}')}"
[[ "$GATEWAY_ID" =~ ^[0-9a-f]{16}$ ]] || { echo "bad gateway id $GATEWAY_ID"; exit 1; }
if [ "${1:-}" = "--show-id" ]; then echo "$GATEWAY_ID"; exit 0; fi

echo "== Gateway ID: $GATEWAY_ID"
[ "$(uname -m)" = "aarch64" ] || { echo "need 64-bit Raspberry Pi OS (uname -m = aarch64), got $(uname -m)"; exit 1; }

ask() { local v="$1" prompt="$2" def="${3:-}"; [ -n "${!v:-}" ] && return; read -r -p "$prompt${def:+ [$def]}: " x; printf -v "$v" '%s' "${x:-$def}"; }
ask DOMAIN "VPS domain (e.g. sos.example.org)"
ask MQTT_PASSWORD "MQTT password for this gateway (from add-gateway.sh on the VPS)"
ask CA_FILE "Path to ca.crt copied from the VPS (infra/certs/ca.crt)" "./ca.crt"
ask LAT "Latitude of gateway" "0"
ask LON "Longitude of gateway" "0"
ask ANTENNA_GAIN "Antenna gain dBi (kit antenna ~2)" "2"
[ -f "$CA_FILE" ] || { echo "CA file not found: $CA_FILE"; exit 1; }
[[ "$DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "bad domain"; exit 1; }
[[ "$MQTT_PASSWORD" =~ ^[A-Za-z0-9._~-]+$ ]] || { echo "password has unexpected characters (use the generated one)"; exit 1; }
[[ "$LAT$LON$ANTENNA_GAIN" =~ ^[-0-9.]+$ ]] || { echo "lat/lon/gain must be numbers"; exit 1; }

# ---------- packages ----------
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q curl ca-certificates openssl jq chrony ufw fail2ban unattended-upgrades mosquitto-clients

# ---------- SPI + I2C ----------
CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
if ! grep -qE '^dtparam=spi=on' "$CFG"; then
  echo "dtparam=spi=on" >> "$CFG"; NEED_REBOOT=1
fi
# I2C: the WM1302 temperature sensor (0x39) sits on /dev/i2c-1; concentratord refuses to start without it
if ! grep -qE '^dtparam=i2c_arm=on' "$CFG"; then
  echo "dtparam=i2c_arm=on" >> "$CFG"; NEED_REBOOT=1
fi
echo i2c-dev > /etc/modules-load.d/sos-i2c-dev.conf
modprobe i2c-dev 2>/dev/null || true
# also when the lines are there but this boot has not loaded them yet (e.g. an earlier run stopped before rebooting)
[ -e /dev/spidev0.0 ] || NEED_REBOOT=1
[ -e /dev/i2c-1 ] || NEED_REBOOT=1
# do not let the Pi's own watchdog/serial console steal anything we need; enable hardware watchdog via systemd
mkdir -p /etc/systemd/system.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15\nRebootWatchdogSec=2min\n' > /etc/systemd/system.conf.d/10-watchdog.conf

# ---------- binaries ----------
# Binaries are published at artifacts.chirpstack.io (the GitHub releases carry source only):
#   https://artifacts.chirpstack.io/downloads/<repo>/<name>_<version>_linux_arm64.tar.gz
# Version = latest GitHub release tag (fallback: pinned). Overrides: CONCENTRATORD_URL / MQTT_FORWARDER_URL,
# or CONCENTRATORD_VERSION / MQTT_FORWARDER_VERSION.
latest_tag() {   # $1=repo -> 4.7.1 (empty on failure)
  curl -fsSIL -o /dev/null -w '%{url_effective}' "https://github.com/chirpstack/$1/releases/latest" 2>/dev/null \
    | sed -n 's#.*/tag/v\{0,1\}\([0-9][0-9.]*\)$#\1#p'
}
fetch_release() {   # $1=repo $2=artifact-name $3=binary $4=override-url $5=override-version $6=fallback-version
  local repo="$1" name="$2" bin="$3" url="${4:-}" ver="${5:-}" fallback="$6" cand
  if [ -z "$url" ]; then
    [ -n "$ver" ] || ver="$(latest_tag "$repo")"
    for v in $ver $fallback; do
      cand="https://artifacts.chirpstack.io/downloads/$repo/${name}_${v}_linux_arm64.tar.gz"
      if curl -fsSIL -o /dev/null "$cand"; then url="$cand"; break; fi
      echo "   (not found: $cand)"
    done
  fi
  if [ -z "$url" ]; then
    echo "!! could not find a linux arm64 download for $repo."
    echo "   Find the file at https://artifacts.chirpstack.io/downloads/$repo/ and re-run with"
    echo "   $(echo "$repo" | tr 'a-z-' 'A-Z_' | sed 's/CHIRPSTACK_//')_URL=<link>   (CONCENTRATORD_URL or MQTT_FORWARDER_URL)"
    exit 1
  fi
  echo "-- downloading $url"
  local tmp; tmp="$(mktemp -d)"; curl -fsSL "$url" -o "$tmp/pkg"
  case "$url" in
    *.deb) dpkg-deb -x "$tmp/pkg" "$tmp/x"; install -m755 "$(find "$tmp/x" -type f -name "$bin" | head -1)" "/usr/local/bin/$bin" ;;
    *.tar.gz|*.tgz) mkdir "$tmp/x"; tar -xzf "$tmp/pkg" -C "$tmp/x"; install -m755 "$(find "$tmp/x" -type f -name "$bin" | head -1)" "/usr/local/bin/$bin" ;;
    *) echo "unknown archive type: $url"; exit 1 ;;
  esac
  rm -rf "$tmp"
  "/usr/local/bin/$bin" --version || true
}
fetch_release chirpstack-concentratord chirpstack-concentratord-sx1302 chirpstack-concentratord-sx1302 \
  "${CONCENTRATORD_URL:-}" "${CONCENTRATORD_VERSION:-}" "4.7.1 4.5.3"
fetch_release chirpstack-mqtt-forwarder chirpstack-mqtt-forwarder chirpstack-mqtt-forwarder \
  "${MQTT_FORWARDER_URL:-}" "${MQTT_FORWARDER_VERSION:-}" "4.5.1 4.3.1"

# ---------- configs ----------
install -d -m750 /etc/chirpstack-concentratord /etc/chirpstack-mqtt-forwarder
install -m644 "$HERE/config/region_as923.toml" "$HERE/config/channels_as923.toml" /etc/chirpstack-concentratord/
sed -e "s|@GATEWAY_ID@|$GATEWAY_ID|g" -e "s|@LAT@|$LAT|" -e "s|@LON@|$LON|" -e "s|@ANTENNA_GAIN@|$ANTENNA_GAIN|" \
    "$HERE/config/concentratord.toml.template" > /etc/chirpstack-concentratord/concentratord.toml
sed -e "s|@GATEWAY_ID@|$GATEWAY_ID|g" -e "s|@DOMAIN@|$DOMAIN|g" -e "s|@MQTT_PASSWORD@|$MQTT_PASSWORD|g" \
    "$HERE/config/mqtt-forwarder.toml.template" > /etc/chirpstack-mqtt-forwarder/chirpstack-mqtt-forwarder.toml
install -m644 "$CA_FILE" /etc/chirpstack-mqtt-forwarder/ca.crt
chmod 600 /etc/chirpstack-mqtt-forwarder/chirpstack-mqtt-forwarder.toml
printf 'DOMAIN=%s\nGATEWAY_ID=%s\nTOPIC_PREFIX=as923\n' "$DOMAIN" "$GATEWAY_ID" > /etc/sos-gateway.env
# credentials for the health reporter (mosquitto_pub reads $XDG_CONFIG_HOME/mosquitto_pub); root-only, keeps the password out of `ps`
install -d -m700 /etc/sos-gateway
# one option per line: mosquitto_pub does NOT accept several options on one line (verified against a real broker)
printf -- '-h %s\n-p 8883\n--cafile /etc/chirpstack-mqtt-forwarder/ca.crt\n-u %s\n-P %s\n' "$DOMAIN" "$GATEWAY_ID" "$MQTT_PASSWORD" > /etc/sos-gateway/mosquitto_pub
chmod 600 /etc/sos-gateway/mosquitto_pub
cp /etc/sos-gateway/mosquitto_pub /etc/sos-gateway/mosquitto_sub; chmod 600 /etc/sos-gateway/mosquitto_sub   # same broker options for the remote-action agent
# optional remote actions from the dashboard: AGENT_KEY=<key shown in Gateways -> this gateway -> Remote control>
if [ -n "${AGENT_KEY:-}" ]; then printf 'AGENT_KEY=%s\n' "$AGENT_KEY" >> /etc/sos-gateway.env; fi
chmod 600 /etc/sos-gateway.env

# ---------- services ----------
install -m644 "$HERE"/systemd/*.service "$HERE"/systemd/*.timer /etc/systemd/system/
install -m755 "$HERE/bin/sos-gateway-health" /usr/local/bin/sos-gateway-health
install -m755 "$HERE/bin/sos-gateway-report" /usr/local/bin/sos-gateway-report
install -m755 "$HERE/bin/sos-gateway-agent" /usr/local/bin/sos-gateway-agent
systemctl daemon-reload
systemctl enable chirpstack-concentratord chirpstack-mqtt-forwarder sos-gateway-health.timer sos-gateway-report.timer >/dev/null

# ---------- basic hardening ----------
ufw --force reset >/dev/null; ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null
ufw allow 22/tcp >/dev/null; ufw --force enable >/dev/null
systemctl enable --now fail2ban chrony >/dev/null 2>&1 || true
if ! ls /home/*/.ssh/authorized_keys >/dev/null 2>&1; then
  echo "!! No SSH key found for any user: add one (ssh-copy-id) and then disable password login in /etc/ssh/sshd_config."
fi

if [ "${NEED_REBOOT:-0}" = 1 ]; then
  echo; echo "SPI was just enabled — reboot now:  sudo reboot   (services start automatically afterwards)"
  exit "${REBOOT_EXIT_CODE:-0}"     # the Windows one-click installer sets 10 so it knows to reboot and re-run
fi
systemctl restart chirpstack-concentratord; sleep 5; systemctl restart chirpstack-mqtt-forwarder; sleep 5
systemctl restart sos-gateway-report.timer
if grep -q '^AGENT_KEY=' /etc/sos-gateway.env; then systemctl enable --now sos-gateway-agent >/dev/null && systemctl restart sos-gateway-agent; echo "Remote-action agent: enabled"
else echo "Remote-action agent: not enabled (no AGENT_KEY). Later: add AGENT_KEY=... to /etc/sos-gateway.env, then: systemctl enable --now sos-gateway-agent"; fi
echo; /usr/local/bin/sos-gateway-health || true
echo; echo "Health report to VPS (first one now):"; /usr/local/bin/sos-gateway-report && echo "  report sent OK" || echo "  !! report FAILED — check MQTT user/password/CA and that the VPS ACL allows topic as923/gateway/$GATEWAY_ID/sos/health"
echo; echo "Gateway ID: $GATEWAY_ID   — register it in ChirpStack (Gateways > Add) if you have not yet."
echo "Logs:  journalctl -u chirpstack-concentratord -u chirpstack-mqtt-forwarder -f"

#!/usr/bin/env bash
# Register one gateway on the VPS. Run from anywhere (cd's into infra/).
#   1) MQTT account for the gateway (username = Gateway ID, 16 lowercase hex) — re-running ROTATES the password
#   2) the gateway on the SOS dashboard + in ChirpStack, through the backend container (same code as Gateways -> Add);
#      falls back to the ChirpStack REST proxy (CHIRPSTACK_API_KEY + CHIRPSTACK_APP_ID in .env) if needed
#   3) the gateway's remote-action AGENT_KEY (same value the dashboard shows under Gateways -> Remote control)
#
#   sudo bash scripts/add-gateway.sh <gateway_id> [--name NAME] [--lat X --lon Y] [--cred-file PATH]
#
# --cred-file: write "MQTT_PASSWORD=..." to PATH (mode 600, owned by the sudo user) instead of printing it.
#              Used by the Windows one-click installer (gateway/windows/Setup-Gateway.ps1).
set -euo pipefail
cd "$(dirname "$0")/.."
GW="${1:-}"; shift || true
[[ "$GW" =~ ^[0-9a-f]{16}$ ]] || { echo "usage: add-gateway.sh <gateway_id_16hex> [--name N] [--lat X --lon Y] [--cred-file PATH]"; exit 1; }
NAME="gw-$GW"; LAT=0; LON=0; CRED=""
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --lat) LAT="$2"; shift 2 ;;
    --lon) LON="$2"; shift 2 ;;
    --cred-file) CRED="$2"; shift 2 ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
done
[[ "$NAME" =~ ^[A-Za-z0-9._-]{1,60}$ ]] || { echo "name: letters, digits, . _ - only (max 60)"; exit 1; }
[[ "$LAT$LON" =~ ^[-0-9.]+$ ]] || { echo "lat/lon must be numbers"; exit 1; }

# ---------- 1) MQTT account ----------
PW="$(openssl rand -hex 16)"
docker run --rm -v "$PWD/mosquitto:/m" eclipse-mosquitto:2 mosquitto_passwd -b /m/passwd "$GW" "$PW"
chown 1883:1883 mosquitto/passwd; chmod 600 mosquitto/passwd
docker compose restart mosquitto >/dev/null
echo "[OK] MQTT account: $GW"

# ---------- 2) dashboard + ChirpStack + agent key, via the backend (same code as dashboard -> Gateways -> Add) ----------
CS_OK=0; AGENT_KEY=""
BACKEND_PY='
import json, sys
from app.db import SessionLocal
from app.models import Gateway
from app.auth import audit
from app import provision as prov, gwcmd
gid, name, lat, lon = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
has_loc = bool(lat or lon)
out = {}
with SessionLocal() as db:
    g = db.query(Gateway).filter_by(gateway_id=gid).first()
    if g is None:
        g = Gateway(gateway_id=gid, label=name[:64], enabled=True, state="unknown", uplinks_total=0,
                    lat=lat if has_loc else None, lon=lon if has_loc else None)
        db.add(g); audit(db, "add-gateway.sh", "gateway_add", gid); db.commit(); out["dashboard"] = "created"
    else:
        out["dashboard"] = "exists"
    cs = prov.get_cs()
    if cs.configured:
        try: out["chirpstack"] = cs.ensure_gateway(gid, g.label or name, g.lat, g.lon)
        except Exception as e: out["chirpstack"] = "error: " + str(e)[:150]
    else:
        out["chirpstack"] = "not_configured"
    out["agent_key"] = gwcmd.agent_key(gid)
print("SOSRESULT " + json.dumps(out))
'
jget() { python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2],""))' "$1" "$2"; }
RES="$(docker compose exec -T backend python -c "$BACKEND_PY" "$GW" "$NAME" "$LAT" "$LON" 2>&1 | tr -d '\r' | grep '^SOSRESULT ' | tail -1 | cut -d' ' -f2- || true)"
if [ -n "$RES" ]; then
  CSR="$(jget "$RES" chirpstack)"; AGENT_KEY="$(jget "$RES" agent_key)"
  echo "[OK] SOS dashboard: gateway $(jget "$RES" dashboard)"
  case "$CSR" in created|exists) CS_OK=1; echo "[OK] ChirpStack: gateway $CSR" ;; *) echo "[!!] ChirpStack via backend: $CSR" ;; esac
else
  echo "[!!] backend not reachable (docker compose exec backend) — add the gateway on the dashboard later (Gateways -> Add)"
fi

# fallback: ChirpStack REST proxy on the host (needs CHIRPSTACK_API_KEY + CHIRPSTACK_APP_ID in .env)
KEY="$(grep -E '^CHIRPSTACK_API_KEY=' .env 2>/dev/null | cut -d= -f2- || true)"
APP="$(grep -E '^CHIRPSTACK_APP_ID=' .env 2>/dev/null | cut -d= -f2- || true)"
if [ $CS_OK = 0 ] && [ -n "$KEY" ] && [ -n "$APP" ] && command -v python3 >/dev/null; then
  if CS_KEY="$KEY" CS_APP="$APP" GW="$GW" NAME="$NAME" LAT="$LAT" LON="$LON" python3 - <<'PY'
import json, os, sys, urllib.request, urllib.error
base = "http://127.0.0.1:8090"
h = {"Authorization": "Bearer " + os.environ["CS_KEY"], "Content-Type": "application/json"}
def call(method, path, body=None):
    req = urllib.request.Request(base + path, method=method, headers=h,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"{}")
try:
    tenant = call("GET", "/api/applications/" + os.environ["CS_APP"])["application"]["tenantId"]
    gw = os.environ["GW"]
    try:
        call("GET", "/api/gateways/" + gw)
        print("[OK] ChirpStack: gateway already exists")
        sys.exit(0)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    call("POST", "/api/gateways", {"gateway": {
        "gatewayId": gw, "name": os.environ["NAME"], "description": "SOS LoRa gateway",
        "tenantId": tenant, "statsInterval": 30, "tags": {"source": "add-gateway.sh"},
        "location": {"latitude": float(os.environ["LAT"]), "longitude": float(os.environ["LON"]), "altitude": 0}}})
    print("[OK] ChirpStack: gateway created")
except Exception as e:
    print("[!!] ChirpStack registration failed:", e.__class__.__name__, getattr(e, "code", ""), file=sys.stderr)
    sys.exit(1)
PY
  then CS_OK=1; fi
fi
if [ $CS_OK = 0 ]; then
  echo "[!!] Gateway not added to ChirpStack automatically — add it on the dashboard (Gateways -> Add) or in ChirpStack UI, ID $GW"
fi

# ---------- 3) remote-action agent key (docs/GATEWAY-REMOTE.md) ----------
if [[ "$AGENT_KEY" =~ ^[0-9a-f]{32,64}$ ]]; then echo "[OK] remote-action agent key ready"
else AGENT_KEY=""; echo "[!!] agent key not available (backend not running) — remote actions stay off; see docs/GATEWAY-REMOTE.md"; fi

# ---------- output ----------
if [ -n "$CRED" ]; then
  owner="${SUDO_USER:-$(id -un)}"
  ( umask 077; printf 'MQTT_PASSWORD=%s\nCHIRPSTACK_REGISTERED=%s\nAGENT_KEY=%s\n' "$PW" "$CS_OK" "$AGENT_KEY" > "$CRED" )
  chown "$owner" "$CRED"
  echo "[OK] credentials written to $CRED"
else
  echo "Gateway MQTT username: $GW"
  echo "Gateway MQTT password: $PW   (enter in the gateway's MQTT Forwarder config; shown only once)"
  if [ -n "$AGENT_KEY" ]; then echo "Gateway AGENT_KEY    : $AGENT_KEY   (for remote actions; install with AGENT_KEY=... on the Pi)"; fi
fi

#!/usr/bin/env bash
# Create an MQTT account for one gateway. Username = Gateway ID (16 hex chars, lowercase). Run from infra/.
set -euo pipefail
cd "$(dirname "$0")/.."
GW="${1:?usage: add-gateway.sh <gateway_id_16hex>}"
[[ "$GW" =~ ^[0-9a-f]{16}$ ]] || { echo "gateway id must be 16 lowercase hex chars"; exit 1; }
PW="$(openssl rand -hex 16)"
docker run --rm -v "$PWD/mosquitto:/m" eclipse-mosquitto:2 mosquitto_passwd -b /m/passwd "$GW" "$PW"
chown 1883:1883 mosquitto/passwd; chmod 600 mosquitto/passwd
docker compose restart mosquitto >/dev/null
echo "Gateway MQTT username: $GW"
echo "Gateway MQTT password: $PW   (enter in the gateway's MQTT Forwarder config; shown only once)"

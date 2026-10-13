#!/usr/bin/env bash
# First-time setup on the VPS (run from infra/): generates secrets, certs, MQTT users.
set -euo pipefail
cd "$(dirname "$0")/.."
DOMAIN="${1:?usage: scripts/init.sh <domain>}"
[ -f .env ] && { echo ".env exists — refusing to overwrite"; exit 1; }
r() { openssl rand -hex "${1:-16}"; }
ADMIN_PW="$(r 9)Aa1"
sed -e "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|" \
    -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${ADMIN_PW}|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(r)|" \
    -e "s|^CHIRPSTACK_DB_PASSWORD=.*|CHIRPSTACK_DB_PASSWORD=$(r)|" \
    -e "s|^SOS_DB_PASSWORD=.*|SOS_DB_PASSWORD=$(r)|" \
    -e "s|^CHIRPSTACK_API_SECRET=.*|CHIRPSTACK_API_SECRET=$(r 32)|" \
    -e "s|^MQTT_CHIRPSTACK_PASSWORD=.*|MQTT_CHIRPSTACK_PASSWORD=$(r)|" \
    -e "s|^MQTT_BACKEND_PASSWORD=.*|MQTT_BACKEND_PASSWORD=$(r)|" \
    -e "s|^JWT_SECRET=.*|JWT_SECRET=$(r 32)|" .env.example > .env
chmod 600 .env
set -a; . ./.env; set +a
bash scripts/make-certs.sh "$DOMAIN"
: > mosquitto/passwd
for u in chirpstack backend; do
  p="MQTT_$(echo $u | tr a-z A-Z)_PASSWORD"
  docker run --rm -v "$PWD/mosquitto:/m" eclipse-mosquitto:2 mosquitto_passwd -b /m/passwd "$u" "${!p}"
done
chown 1883:1883 mosquitto/passwd mosquitto/acl.conf; chmod 600 mosquitto/passwd; chmod 640 mosquitto/acl.conf
echo
echo "=== Saved in infra/.env (keep it safe, back it up OFFLINE) ==="
echo "Dashboard admin user : ${ADMIN_USER}"
echo "Dashboard admin pass : ${ADMIN_PW}   <- change it after first login"
echo "Next: docker compose up -d --build   (see docs/INSTALL-vps.md)"

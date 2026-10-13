#!/usr/bin/env bash
# Private CA + server certificate for Mosquitto (gateway <-> VPS TLS). Run once on the VPS, from infra/.
set -euo pipefail
DOMAIN="${1:?usage: make-certs.sh <domain>}"
mkdir -p certs && cd certs
if [ ! -f ca.key ]; then
  openssl genrsa -out ca.key 4096
  openssl req -x509 -new -key ca.key -sha256 -days 3650 -subj "/CN=SOS-LoRa-CA" -out ca.crt
fi
openssl genrsa -out server.key 2048
openssl req -new -key server.key -subj "/CN=${DOMAIN}" -out server.csr
printf "subjectAltName=DNS:%s\nextendedKeyUsage=serverAuth\n" "$DOMAIN" > san.cnf
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out server.crt -days 825 -sha256 -extfile san.cnf
rm -f server.csr san.cnf
chmod 600 ca.key; chmod 644 ca.crt server.crt
chown 1883:1883 server.key 2>/dev/null || true; chmod 600 server.key
echo "Done. Copy certs/ca.crt to each gateway (see gateway/README). Server cert expires in 825 days — renew by re-running this script."

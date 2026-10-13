#!/usr/bin/env bash
# Daily DB backup (cron: 0 2 * * * /opt/sos/infra/scripts/backup.sh). Keeps 14 days.
# Backups contain patient data (PDPA): encrypt/off-site copy only to storage your data controller approved.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="${BACKUP_DIR:-/var/backups/sos}"; mkdir -p "$DIR"; chmod 700 "$DIR"
TS="$(date +%Y%m%d-%H%M)"
for db in sos chirpstack; do
  docker compose exec -T postgres pg_dump -U postgres -Fc "$db" > "$DIR/${db}-${TS}.dump"
done
find "$DIR" -name '*.dump' -mtime +14 -delete
echo "backup ok $TS"

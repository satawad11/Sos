#!/usr/bin/env bash
# Restore one database from a backup made by backup.sh.   usage: restore.sh /var/backups/sos/sos-20261112-0200.dump[.enc]
# The database name is taken from the file name (sos-… or chirpstack-…). Existing data in that database is REPLACED.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f /etc/default/sos-backup ] && . /etc/default/sos-backup
F="${1:?usage: restore.sh <backup file>}"; [ -f "$F" ] || { echo "ไม่พบไฟล์ $F" >&2; exit 1; }
B="$(basename "$F")"; DB="${B%%-*}"
case "$DB" in sos|chirpstack) ;; *) echo "ชื่อไฟล์ต้องขึ้นต้นด้วย sos- หรือ chirpstack-" >&2; exit 1 ;; esac
read -r -p "จะแทนที่ข้อมูลทั้งหมดในฐานข้อมูล \"$DB\" ด้วย $B — พิมพ์ yes เพื่อยืนยัน: " A; [ "$A" = yes ] || { echo "ยกเลิก"; exit 1; }
TMP=""; trap '[ -n "$TMP" ] && rm -f "$TMP"' EXIT
if [[ "$F" == *.enc ]]; then
  KEY="${BACKUP_KEY_FILE:-/etc/sos/backup.key}"; [ -r "$KEY" ] || { echo "ไม่พบไฟล์กุญแจ $KEY" >&2; exit 1; }
  TMP="$(mktemp)"; chmod 600 "$TMP"
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass "file:$KEY" -in "$F" -out "$TMP"; F="$TMP"
fi
docker compose stop backend chirpstack
docker compose exec -T postgres pg_restore -U postgres --clean --if-exists --no-owner -d "$DB" < "$F"
docker compose start chirpstack backend
echo "กู้คืน $DB เรียบร้อย — ตรวจ https://<โดเมน>/healthz"

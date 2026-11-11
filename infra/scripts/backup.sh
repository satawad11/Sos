#!/usr/bin/env bash
# Daily DB backup (cron: 0 2 * * * /opt/sos/infra/scripts/backup.sh). Also run by the updater before every update.
#   * dumps the sos and chirpstack databases, checks each dump can be read back (pg_restore --list)
#   * encrypts them (AES-256, openssl) when a key file exists            -> BACKUP_KEY_FILE (default /etc/sos/backup.key)
#   * copies them off this machine when a destination is configured      -> BACKUP_REMOTE
#   * writes infra/update/backup-status.json, which the dashboard shows (and alerts on when the last backup is too old)
# Settings (optional) in /etc/default/sos-backup:
#   BACKUP_DIR=/var/backups/sos   BACKUP_KEEP_DAYS=14   BACKUP_KEY_FILE=/etc/sos/backup.key
#   BACKUP_REMOTE=rclone:gdrive:sos-backup      (any rclone remote)      or
#   BACKUP_REMOTE=scp:backup@10.0.0.5:/srv/sos  (plain scp/ssh key)
# Backups contain patient data (PDPA): they are only sent off-site ENCRYPTED, and only to storage your data controller approved.
# Restore: infra/scripts/restore.sh <file>      Make the key once:  install -d -m 700 /etc/sos && openssl rand -base64 48 > /etc/sos/backup.key && chmod 600 /etc/sos/backup.key
#          (keep a copy of the key somewhere else — without it an encrypted backup cannot be restored)
set -uo pipefail
cd "$(dirname "$0")/.."
[ -f /etc/default/sos-backup ] && . /etc/default/sos-backup
DIR="${BACKUP_DIR:-/var/backups/sos}"; KEEP="${BACKUP_KEEP_DAYS:-14}"
KEY="${BACKUP_KEY_FILE:-/etc/sos/backup.key}"; REMOTE="${BACKUP_REMOTE:-}"
STATUS="update/backup-status.json"; TS="$(date +%Y%m%d-%H%M)"
mkdir -p "$DIR" update; chmod 700 "$DIR"
FILES=(); ENC=0; OFF_CFG=0; OFF_OK=0; OFF_MSG=""; ERR=""

status() {   # status <ok 0|1>
  python3 - "$STATUS" "$1" "$ENC" "$OFF_CFG" "$OFF_OK" "$OFF_MSG" "$ERR" "$KEEP" "$DIR" "${FILES[@]}" <<'PY' || true
import json, os, sys, tempfile
from datetime import datetime, timezone
path, ok, enc, off_cfg, off_ok, off_msg, err, keep, d, *files = sys.argv[1:]
out = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": ok == "1", "encrypted": enc == "1",
       "offsite": {"configured": off_cfg == "1", "ok": off_ok == "1", "detail": off_msg[:200]}, "error": err[:300],
       "keep_days": int(keep), "dir": d,
       "files": [{"name": os.path.basename(f), "size": os.path.getsize(f)} for f in files if os.path.isfile(f)]}
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".bk-")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
os.chmod(tmp, 0o644); os.replace(tmp, path)
PY
}
fail() { ERR="$1"; echo "backup FAILED: $1" >&2; status 0; exit 1; }

for db in sos chirpstack; do
  f="$DIR/${db}-${TS}.dump"
  docker compose exec -T postgres pg_dump -U postgres -Fc "$db" > "$f" || { rm -f "$f"; fail "pg_dump $db ไม่สำเร็จ"; }
  [ -s "$f" ] || { rm -f "$f"; fail "ไฟล์สำรอง $db ว่างเปล่า"; }
  docker compose exec -T postgres pg_restore --list < "$f" > /dev/null || fail "ไฟล์สำรอง $db อ่านกลับไม่ได้"
  if [ -r "$KEY" ]; then
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass "file:$KEY" -in "$f" -out "$f.enc" || { rm -f "$f.enc"; fail "เข้ารหัส $db ไม่สำเร็จ"; }
    rm -f "$f"; f="$f.enc"; ENC=1
  fi
  chmod 600 "$f"; FILES+=("$f")
done

if [ -n "$REMOTE" ]; then
  OFF_CFG=1
  if [ "$ENC" != 1 ]; then
    OFF_MSG="ไม่ได้ส่งออกนอกเครื่อง: ยังไม่มีไฟล์กุญแจ $KEY (ส่งเฉพาะไฟล์ที่เข้ารหัสแล้ว)"
  else
    case "$REMOTE" in
      scp:*)    if scp -q -o BatchMode=yes -o ConnectTimeout=20 "${FILES[@]}" "${REMOTE#scp:}/" 2>/tmp/sos-backup-off.err; then OFF_OK=1; OFF_MSG="scp ${REMOTE#scp:}"; else OFF_MSG="scp ไม่สำเร็จ: $(tail -c 150 /tmp/sos-backup-off.err)"; fi ;;
      rclone:*) OFF_OK=1; for f in "${FILES[@]}"; do rclone copy "$f" "${REMOTE#rclone:}" 2>/tmp/sos-backup-off.err || { OFF_OK=0; OFF_MSG="rclone ไม่สำเร็จ: $(tail -c 150 /tmp/sos-backup-off.err)"; break; }; done
                [ "$OFF_OK" = 1 ] && OFF_MSG="rclone ${REMOTE#rclone:}" ;;
      *)        OFF_MSG="BACKUP_REMOTE ต้องขึ้นต้นด้วย rclone: หรือ scp:" ;;
    esac
  fi
  [ "$OFF_OK" = 1 ] || echo "off-site copy failed: $OFF_MSG" >&2
fi

find "$DIR" \( -name '*.dump' -o -name '*.dump.enc' \) -mtime +"$KEEP" -delete
status 1
echo "backup ok $TS (encrypted=$ENC offsite=$OFF_OK)"

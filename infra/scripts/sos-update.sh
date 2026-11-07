#!/usr/bin/env bash
# Host-side updater: GitHub → backup → rebuild → health check → (rollback on failure).
# Run by systemd (infra/systemd/*), never by the web app directly. See docs/UPDATE.md.
#   sos-update.sh handle   consume infra/update/request.json written by the dashboard, then check|update
#   sos-update.sh check    git fetch only; tells the dashboard whether a newer version exists
#   sos-update.sh update   update now (same as pressing the button)
set -uo pipefail

# Copy ourselves to /tmp first: `git merge` below may replace this very file while bash is still reading it.
if [ -z "${SOS_UPD_COPY:-}" ]; then
  SRC="$(cd "$(dirname "$0")" && pwd)"; T="$(mktemp -d /tmp/sos-update.XXXXXX)"
  cp "$SRC/sos-update.sh" "$SRC/update_status.py" "$T/"
  SOS_UPD_COPY="$T" SOS_REPO_DIR="${SOS_REPO_DIR:-$(cd "$SRC/../.." && pwd)}" exec bash "$T/sos-update.sh" "$@"
fi
trap 'rm -rf "$SOS_UPD_COPY"' EXIT

[ -f /etc/default/sos-update ] && . /etc/default/sos-update
REPO="${SOS_REPO_DIR:?}"; BRANCH="${SOS_BRANCH:-main}"
INFRA="$REPO/infra"; UPD="$INFRA/update"; STATUS="$UPD/status.json"; LOG="$UPD/update.log"; REQ="$UPD/request.json"
HEALTH_TIMEOUT="${SOS_HEALTH_TIMEOUT:-180}"
mkdir -p "$UPD"

log()  { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
st()   { python3 "$SOS_UPD_COPY/update_status.py" "$REPO" "$BRANCH" "$STATUS" "$@" || true; }
g()    { git -C "$REPO" "$@"; }
dc()   { (cd "$INFRA" && docker compose "$@"); }

exec 9>/run/sos-update.lock
flock -n 9 || { echo "another update is running"; exit 0; }

fetch() {
  log "git fetch origin $BRANCH"
  if ! g fetch --prune origin "$BRANCH" >>"$LOG" 2>&1; then
    st failed "ติดต่อ GitHub ไม่ได้ (ตรวจอินเทอร์เน็ต / deploy key) — ดู log" checked=1 finished=1; return 1
  fi
}

healthy() {   # backend answers /healthz from inside its container and nothing is crash-looping
  local end=$(( $(date +%s) + HEALTH_TIMEOUT ))
  while [ "$(date +%s)" -lt "$end" ]; do
    if dc exec -T backend python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status==200 else 1)" >/dev/null 2>&1; then
      sleep 10
      if [ -z "$(dc ps --status restarting -q 2>/dev/null)" ]; then return 0; fi
      log "a service is restarting:"; dc ps >>"$LOG" 2>&1
    fi
    sleep 5
  done
  return 1
}

deploy() {    # $1 = previous commit (to diff which bind-mounted configs changed)
  local prev="$1" changed
  chmod +x "$INFRA"/scripts/*.sh "$INFRA"/postgres/initdb/*.sh 2>/dev/null || true
  # git rewrites changed files as root:644 — mosquitto (uid 1883) must keep owning its ACL/password files
  chown 1883:1883 "$INFRA"/mosquitto/acl.conf "$INFRA"/mosquitto/passwd 2>/dev/null || true
  chmod 640 "$INFRA"/mosquitto/acl.conf 2>/dev/null || true
  log "docker compose up -d --build"
  dc up -d --build --remove-orphans >>"$LOG" 2>&1 || return 1
  changed="$(g diff --name-only "$prev" HEAD 2>/dev/null)"
  for svc in mosquitto chirpstack caddy; do      # config files are bind mounts: recreate doesn't reload them
    if grep -q "^infra/$svc/" <<<"$changed"; then log "config changed → restart $svc"; dc restart "$svc" >>"$LOG" 2>&1; fi
  done
  log "health check (max ${HEALTH_TIMEOUT}s)"
  healthy
}

do_check() {
  st checking "กำลังตรวจหาเวอร์ชันใหม่จาก GitHub…"
  fetch || return 1
  local n; n="$(g rev-list --count "HEAD..origin/$BRANCH" 2>/dev/null || echo 0)"
  if [ "$n" -gt 0 ]; then st idle "มีเวอร์ชันใหม่ $n รายการ" checked=1; else st idle "เป็นเวอร์ชันล่าสุดแล้ว" checked=1; fi
  log "check done: $n new commit(s)"
}

do_update() {
  [ -f "$LOG" ] && mv -f "$LOG" "$LOG.1"
  st updating "เริ่มอัปเดต…" started=1
  log "=== update requested by ${REQ_BY:-host} (branch $BRANCH) ==="
  fetch || return 1
  local prev new dirty missing=""
  prev="$(g rev-parse HEAD)"; new="$(g rev-parse "origin/$BRANCH")"
  if [ "$prev" = "$new" ]; then st idle "เป็นเวอร์ชันล่าสุดแล้ว ไม่มีอะไรต้องอัปเดต" checked=1 finished=1; log "already up to date"; return 0; fi
  dirty="$(g -c core.fileMode=false status --porcelain --untracked-files=no)"
  if [ -n "$dirty" ]; then
    log "local changes in tracked files:"; echo "$dirty" >>"$LOG"
    st failed "มีไฟล์ในเซิร์ฟเวอร์ถูกแก้เอง จึงไม่อัปเดตทับ: $(echo "$dirty" | awk '{print $2}' | head -5 | tr '\n' ' ')" checked=1 finished=1; return 1
  fi
  if ! g merge-base --is-ancestor HEAD "origin/$BRANCH"; then
    st failed "ประวัติโค้ดในเซิร์ฟเวอร์ไม่ต่อกับ GitHub (มี commit ที่ไม่ได้อยู่บน $BRANCH) — ต้องแก้ด้วยมือ" checked=1 finished=1; return 1
  fi
  st updating "สำรองฐานข้อมูลก่อนอัปเดต…"
  log "backup"
  if ! bash "$INFRA/scripts/backup.sh" >>"$LOG" 2>&1; then
    st failed "สำรองฐานข้อมูลไม่สำเร็จ จึงยกเลิกการอัปเดต (ระบบเดิมยังทำงานตามปกติ)" finished=1; return 1
  fi
  st updating "ดึงโค้ดใหม่ ${new:0:7}…"
  log "git merge --ff-only ${prev:0:7} → ${new:0:7}"
  g merge --ff-only "origin/$BRANCH" >>"$LOG" 2>&1 || { st failed "รวมโค้ดไม่สำเร็จ — ดู log" finished=1; return 1; }
  if [ -f "$INFRA/.env.example" ] && [ -f "$INFRA/.env" ]; then
    missing="$(comm -23 <(grep -oE '^[A-Z_]+=' "$INFRA/.env.example" | sort -u) <(grep -oE '^[A-Z_]+=' "$INFRA/.env" | sort -u) | tr -d '=' | tr '\n' ' ')"
    [ -n "$missing" ] && log "NOTE: new settings in .env.example not in .env: $missing"
  fi
  st updating "กำลัง build และเริ่มระบบใหม่ (ระบบจะหยุดตอบชั่วครู่)…"
  if deploy "$prev"; then
    st done "อัปเดตสำเร็จ ${prev:0:7} → ${new:0:7}${missing:+ · มีค่าตั้งใหม่ใน .env.example: $missing}" finished=1 from="$prev" to="$new" ok=1
    log "=== update OK ==="; return 0
  fi
  log "!!! update failed health check → rolling back to ${prev:0:7}"
  dc logs --tail 40 backend >>"$LOG" 2>&1
  st rolling_back "อัปเดตไม่ผ่านการตรวจ กำลังย้อนกลับเวอร์ชันเดิม…"
  g reset --hard "$prev" >>"$LOG" 2>&1
  if deploy "$new"; then
    st failed "อัปเดตไม่สำเร็จ ย้อนกลับเป็นเวอร์ชันเดิม ${prev:0:7} แล้ว ระบบใช้งานได้ตามปกติ — ดู log" finished=1 from="$prev" to="$new" ok=0
  else
    st failed "อัปเดตและย้อนกลับไม่สำเร็จ — ระบบอาจหยุดทำงาน ต้องแก้ด้วยมือทันที (ดู docs/UPDATE.md)" finished=1 from="$prev" to="$new" ok=0
  fi
  log "=== rollback finished ==="; return 1
}

case "${1:-}" in
  check)  do_check ;;
  update) do_update ;;
  handle)
    [ -f "$REQ" ] || exit 0
    # parse with python (never source/eval what the web app wrote); remove first so the .path unit doesn't re-fire
    read -r REQ_ID REQ_ACTION REQ_BY < <(python3 -c 'import json,re,sys
d=json.load(open(sys.argv[1]))
c=lambda k:re.sub(r"[^A-Za-z0-9_.@-]","",str(d.get(k,"")))[:64] or "-"
print(c("id"),c("action"),c("by"))' "$REQ" 2>/dev/null || echo "- - -")
    rm -f "$REQ"
    case "$REQ_ACTION" in
      check)  st checking "กำลังตรวจหาเวอร์ชันใหม่…" request="$REQ_ID" by="$REQ_BY" action=check; do_check ;;
      update) st updating "รับคำขออัปเดตแล้ว…" request="$REQ_ID" by="$REQ_BY" action=update; do_update ;;
      *)      log "ignored bad request"; st idle "คำขอไม่ถูกต้อง" ;;
    esac ;;
  *) echo "usage: $0 handle|check|update"; exit 2 ;;
esac

#!/usr/bin/env bash
# One-time setup of the in-app updater on the server (run as root). Safe to run again.
#   sudo bash setup-updater.sh                      # repo git@github.com:satawad11/Sos.git, branch main, dir /opt/sos
#   sudo bash setup-updater.sh <git-url> <branch> <dir>
# What it does (docs/UPDATE.md):
#   1. read-only GitHub "deploy key" for this server (private repo)
#   2. turns <dir> into a git checkout of <branch> (a copy made from a ZIP is converted in place;
#      local files that differ from GitHub are backed up first, then replaced by the GitHub version)
#   3. folder infra/update shared with the backend + systemd units (button → update, check every 6 h)
#   4. rebuilds the stack once so the backend has the update API
set -euo pipefail
URL="${1:-git@github.com:satawad11/Sos.git}"; BRANCH="${2:-main}"; REPO="${3:-/opt/sos}"
KEY=/root/.ssh/sos_deploy
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
[ -d "$REPO/infra" ] || { echo "$REPO/infra not found"; exit 1; }
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "1/5 packages"
command -v git >/dev/null && command -v python3 >/dev/null && command -v flock >/dev/null || { apt-get update -q; apt-get install -y -q git python3 util-linux; }

say "2/5 GitHub access"
SSHCMD=""
if [[ "$URL" == git@* || "$URL" == ssh://* ]]; then
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
  [ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "sos-updater@$(hostname)" -f "$KEY"
  SSHCMD="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
  until GIT_SSH_COMMAND="$SSHCMD" git ls-remote "$URL" "refs/heads/$BRANCH" >/dev/null 2>&1; do
    echo
    echo "ยังเข้า GitHub ไม่ได้ — เพิ่ม Deploy key นี้ (อ่านอย่างเดียว) ที่ GitHub ก่อน:"
    echo "  repo → Settings → Deploy keys → Add deploy key → Title: sos-server → วางบรรทัดนี้ → ห้ามติ๊ก Allow write access → Add key"
    echo
    cat "$KEY.pub"
    echo
    read -r -p "เพิ่มแล้วกด Enter (หรือ Ctrl+C เพื่อยกเลิก) " _
  done
  echo "GitHub OK"
fi

say "3/5 git checkout of $BRANCH in $REPO"
git config --system --get-all safe.directory | grep -qx "$REPO" || git config --system --add safe.directory "$REPO"   # --system: systemd has no $HOME
cd "$REPO"
if [ ! -d .git ]; then
  git init -q -b "$BRANCH"
  git remote add origin "$URL"
fi
git remote set-url origin "$URL"
[ -n "$SSHCMD" ] && git config core.sshCommand "$SSHCMD"
git config core.fileMode false          # chmod by scp/setup must not count as a "local change"
git fetch -q origin "$BRANCH"
if ! git rev-parse -q --verify HEAD >/dev/null; then
  git reset -q "origin/$BRANCH"          # index = GitHub, files on disk untouched → shows what differs
fi
DIFF="$(git status --porcelain --untracked-files=no)"
if [ -n "$DIFF" ]; then
  echo "ไฟล์ในเซิร์ฟเวอร์ที่ต่างจาก GitHub ($BRANCH):"; echo "$DIFF" | sed 's/^/   /'
  BK="/var/backups/sos/local-files-$(date +%Y%m%d-%H%M%S).tar.gz"; mkdir -p /var/backups/sos; chmod 700 /var/backups/sos
  echo "$DIFF" | awk '{print $2}' | while read -r f; do [ -e "$f" ] && echo "$f"; done | tar -czf "$BK" -T - 2>/dev/null || true
  echo "สำรองไฟล์เหล่านี้ไว้ที่ $BK"
  read -r -p "แทนที่ด้วยเวอร์ชันจาก GitHub? [y/N] " a
  [[ "$a" =~ ^[Yy] ]] || { echo "ยกเลิก — ยังไม่ได้เปลี่ยนอะไร"; exit 1; }
  git checkout -q -- .
fi
if [ "$(git rev-list --count "HEAD..origin/$BRANCH")" != 0 ]; then
  git merge -q --ff-only "origin/$BRANCH" || { echo "merge ไม่ได้ — ตรวจ git status"; exit 1; }
fi
echo "ตอนนี้ $REPO = $(git log -1 --format='%h %s')"
chmod +x infra/scripts/*.sh infra/postgres/initdb/*.sh 2>/dev/null || true

say "4/5 shared folder + systemd"
mkdir -p infra/update
chown 10001:10001 infra/update; chmod 755 infra/update      # 10001 = user "sos" inside the backend image
printf 'SOS_BRANCH=%s\nSOS_REPO_DIR=%s\n' "$BRANCH" "$REPO" > /etc/default/sos-update
for u in sos-update.path sos-update.service sos-update-check.service sos-update-check.timer; do
  sed "s|@REPO@|$REPO|g" "infra/systemd/$u" > "/etc/systemd/system/$u"
done
systemctl daemon-reload
systemctl enable --now sos-update.path sos-update-check.timer
bash infra/scripts/sos-update.sh check || true

say "5/5 rebuild once (backend gets the update API + shared folder)"
(cd infra && docker compose up -d --build)
echo
echo "เสร็จแล้ว — แดชบอร์ด → เมนู \"อัปเดตระบบ\" (เฉพาะ admin)"
echo "สถานะ: systemctl status sos-update.path sos-update-check.timer · log: $REPO/infra/update/update.log"

#!/usr/bin/env bash
# Outside watchdog: run this from cron on ANOTHER machine (a different building / network if possible), e.g. every 2 minutes:
#   */2 * * * * SOS_URL=https://sos.example.ac.th LINE_TOKEN_FILE=/etc/sos-watchdog/token LINE_TO=Uxxxxxxxx /opt/sos-watchdog.sh
# It asks the server "is everything working?" (/healthz?strict=1). After FAILS bad answers in a row it sends one LINE message
# to LINE_TO (a user or group id) with the bot's channel access token; it sends another one when the server is back.
# The server cannot report its own death — this script (or an uptime service pointed at the same URL) is what does.
set -uo pipefail
URL="${SOS_URL:?set SOS_URL}"; TO="${LINE_TO:?set LINE_TO}"; TOKEN="$(cat "${LINE_TOKEN_FILE:?set LINE_TOKEN_FILE}")"
FAILS="${FAILS:-3}"; STATE="${STATE_FILE:-/var/tmp/sos-watchdog.state}"
n=0; down=0; [ -f "$STATE" ] && read -r n down < "$STATE"
push() { curl -fsS -m 20 -X POST https://api.line.me/v2/bot/message/push -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
         -d "{\"to\":\"$TO\",\"messages\":[{\"type\":\"text\",\"text\":\"$1\"}]}" >/dev/null; }
if curl -fsS -m 15 "$URL/healthz?strict=1" >/dev/null 2>&1; then
  [ "$down" = 1 ] && push "✅ ระบบแจ้งเหตุฉุกเฉินกลับมาทำงานแล้ว ($URL)"
  n=0; down=0
else
  n=$((n+1))
  if [ "$n" -ge "$FAILS" ] && [ "$down" != 1 ]; then
    push "🚨 ระบบแจ้งเหตุฉุกเฉินไม่ตอบสนอง ($URL) — ปุ่มของผู้ป่วยอาจแจ้งเหตุไม่ได้ กรุณาตรวจเซิร์ฟเวอร์ทันที" && down=1
  fi
fi
echo "$n $down" > "$STATE"

#!/usr/bin/env bash
# HamaliVpn: health-монитор. Шлёт админу алерт в Telegram, если сервис упал,
# и сообщение о восстановлении. Алертит только при СМЕНЕ состояния (без спама).
#
# Установка:
#   sudo cp /tmp/monitor.sh /opt/hamalivpn/monitor.sh && sudo chmod +x /opt/hamalivpn/monitor.sh
#   (cron) sudo crontab -e ->  */3 * * * * /opt/hamalivpn/monitor.sh >> /var/log/hamali-monitor.log 2>&1
set -uo pipefail

ADMIN_ID="${HAMALI_ADMIN_ID:-5392719643}"
STATE=/opt/hamalivpn/.monitor_state
BOT_TOKEN=$(grep -E '^BOT_TOKEN=' /opt/hamalivpn/.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')

alert() {
  [ -z "${BOT_TOKEN}" ] && return
  curl -s -m 10 "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${ADMIN_ID}" \
    --data-urlencode "parse_mode=HTML" \
    --data-urlencode "text=$1" >/dev/null 2>&1 || true
}

problems=""

# control (главный API + вебхуки)
curl -fsS -m 8 http://127.0.0.1:8080/health >/dev/null 2>&1 \
  || problems+="• control (8080) недоступен"$'\n'

# портал (systemd)
systemctl is-active --quiet hamalivpn-api \
  || problems+="• hamalivpn-api (портал) не активен"$'\n'

# ключевые контейнеры
for c in hamalivpn-control-1 hamalivpn-bot-1 hamalivpn-postgres-1 hamalivpn-redis-1 remnawave remnawave-db; do
  st=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)
  [ "$st" = "running" ] || problems+="• контейнер ${c}: ${st}"$'\n'
done

# диск
disk=$(df -P / | awk 'NR==2{gsub("%","",$5); print $5}')
[ "${disk:-0}" -ge 90 ] && problems+="• диск заполнен на ${disk}%"$'\n'

if [ -n "${problems}" ]; then
  if [ ! -f "${STATE}" ]; then
    alert "🚨 <b>HamaliVPN — проблема</b>"$'\n\n'"${problems}"
    echo "${problems}" > "${STATE}"
  fi
  echo "$(date -u +%FT%TZ) DOWN:"$'\n'"${problems}"
else
  if [ -f "${STATE}" ]; then
    alert "✅ <b>HamaliVPN — всё восстановлено</b>, сервисы в норме."
    rm -f "${STATE}"
  fi
  echo "$(date -u +%FT%TZ) all ok"
fi

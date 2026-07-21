#!/usr/bin/env bash
# HamaliVPN production health monitor.
# Sends alerts only when the actual problem set changes and one recovery message.
set -uo pipefail

BASE_DIR=/opt/hamalivpn
ADMIN_ID="${HAMALI_ADMIN_ID:-5392719643}"
STATE="${BASE_DIR}/.monitor_state"
BACKUP_MAX_AGE_MINUTES="${HAMALI_BACKUP_MAX_AGE_MINUTES:-1800}"
BOT_TOKEN=$(grep -E '^BOT_TOKEN=' "${BASE_DIR}/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')

alert() {
  [ -z "${BOT_TOKEN}" ] && return 0
  curl -fsS -m 10 "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${ADMIN_ID}" \
    --data-urlencode "parse_mode=HTML" \
    --data-urlencode "text=$1" >/dev/null 2>&1 || true
}

http_up() {
  local url="$1" attempt
  for attempt in 1 2 3; do
    curl -fsS --connect-timeout 3 -m 8 "$url" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

tcp_up() {
  local host="$1" port="$2" attempt
  for attempt in 1 2 3; do
    timeout 5 bash -c "</dev/tcp/${host}/${port}" >/dev/null 2>&1 && return 0
    sleep 2
  done
  return 1
}

backup_recent() {
  local prefix="$1"
  find "${BASE_DIR}/backups" -maxdepth 1 -type f \
    -name "${prefix}_*.sql.gz" -size +1024c \
    -mmin "-${BACKUP_MAX_AGE_MINUTES}" -print -quit 2>/dev/null | grep -q .
}

problems=""

# Internal APIs. The control container is intentionally not published on the host.
control_ip=$(docker inspect -f '{{with index .NetworkSettings.Networks "hamalivpn_backend"}}{{.IPAddress}}{{end}}' hamalivpn-control-1 2>/dev/null || true)
if [ -z "$control_ip" ] || ! http_up "http://${control_ip}:8080/health"; then
  problems+="• control API (8080) не отвечает"$'\n'
fi
http_up "http://127.0.0.1:8000/health" || problems+="• sub-injector (8000) не отвечает"$'\n'
http_up "http://127.0.0.1:8001/health" || problems+="• portal API (8001) не отвечает"$'\n'

# Canonical systemd units.
systemctl is-active --quiet hamali-sub-injector.service \
  || problems+="• hamali-sub-injector.service не активен"$'\n'

# A green application with stale backups is not healthy. Check both the job
# result and the artifacts so a disabled timer, failed pg_dump or empty file is
# reported within the regular three-minute monitoring cycle.
systemctl is-active --quiet hamalivpn-backup.timer \
  || problems+="• таймер ежедневных резервных копий не активен"$'\n'
backup_result=$(systemctl show hamalivpn-backup.service -p Result --value 2>/dev/null || true)
if [ -n "$backup_result" ] && [ "$backup_result" != "success" ]; then
  problems+="• последнее резервное копирование завершилось: ${backup_result}"$'\n'
fi
backup_recent hamalivpn \
  || problems+="• резервная копия HamaliVPN старше $((BACKUP_MAX_AGE_MINUTES / 60)) ч"$'\n'
backup_recent remnawave \
  || problems+="• резервная копия Remnawave старше $((BACKUP_MAX_AGE_MINUTES / 60)) ч"$'\n'

# Required containers. Respect their restart policies; the monitor does not create restart loops.
containers=(
  hamalivpn-control-1 hamalivpn-bot-1 hamalivpn-maintenance-1
  hamalivpn-caddy-1 hamalivpn-postgres-1 hamalivpn-redis-1
  remnawave remnawave-db remnawave-redis
)
for container in "${containers[@]}"; do
  if ! docker inspect "$container" >/dev/null 2>&1; then
    problems+="• контейнер ${container}: отсутствует"$'\n'
    continue
  fi
  status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || echo unknown)
  case "$status" in
    running|healthy) ;;
    *) problems+="• контейнер ${container}: ${status}"$'\n' ;;
  esac
done

# Exit nodes used in production. London is intentionally removed from checks.
nodes=(
  "Франция новая|107.161.160.220|443"
  "Финляндия|62.60.249.228|443"
  "Нидерланды|103.112.69.188|443"
  "Франция|45.92.218.178|443"
  "Германия|92.119.166.192|443"
  "United Kingdom|85.137.249.225|2053"
)
for node in "${nodes[@]}"; do
  IFS='|' read -r name host port <<<"$node"
  tcp_up "$host" "$port" || problems+="• нода ${name} (${host}:${port}) недоступна"$'\n'
done

disk=$(df -P / | awk 'NR==2{gsub("%","",$5); print $5}')
[ "${disk:-0}" -ge 90 ] && problems+="• диск заполнен на ${disk}%"$'\n'

if [ -n "$problems" ]; then
  previous=$(cat "$STATE" 2>/dev/null || true)
  if [ "$previous" != "$problems" ]; then
    alert "🚨 <b>HamaliVPN — проблема</b>"$'\n\n'"${problems}"
    printf '%s' "$problems" > "$STATE"
  fi
  printf '%s DOWN:\n%s' "$(date -u +%FT%TZ)" "$problems"
  exit 1
fi

if [ -f "$STATE" ]; then
  alert "✅ <b>HamaliVPN — всё восстановлено</b>. Сервисы работают штатно."
  rm -f "$STATE"
fi
echo "$(date -u +%FT%TZ) all ok"

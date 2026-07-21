#!/usr/bin/env bash
# Verify the latest local database pair in disposable PostgreSQL containers.
set -Eeuo pipefail
umask 077

BACKUP_DIR="${HAMALI_BACKUP_DIR:-/opt/hamalivpn/backups}"
run_id="local-restore-$RANDOM-$$"
container_name=""

cleanup() {
  if [[ -n "$container_name" ]]; then
    docker rm -f "$container_name" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

for command_name in docker gzip; do
  command -v "$command_name" >/dev/null || {
    echo "Required command is missing: ${command_name}" >&2
    exit 1
  }
done

latest_control=$(find "$BACKUP_DIR" -maxdepth 1 -type f \
  -name 'hamalivpn_[0-9]*_[0-9]*.sql.gz' -printf '%f\n' | sort | tail -n 1)
test -n "$latest_control" || { echo "No HamaliVPN backup found" >&2; exit 1; }
timestamp=${latest_control#hamalivpn_}
timestamp=${timestamp%.sql.gz}
control_file="${BACKUP_DIR}/${latest_control}"
remnawave_file="${BACKUP_DIR}/remnawave_${timestamp}.sql.gz"
test -s "$remnawave_file" || { echo "Matching Remnawave backup is missing" >&2; exit 1; }
gzip -t "$control_file"
gzip -t "$remnawave_file"

restore_one() {
  local label="$1" backup_file="$2" owner_role="$3"
  container_name="hamali-${run_id}-${label}"
  docker run -d --rm --name "$container_name" \
    -e POSTGRES_PASSWORD=restore-only -e POSTGRES_DB=restore_test \
    postgres:17-alpine >/dev/null
  for _attempt in $(seq 1 30); do
    docker exec "$container_name" pg_isready -U postgres -d restore_test >/dev/null 2>&1 && break
    sleep 1
  done
  docker exec "$container_name" pg_isready -U postgres -d restore_test >/dev/null
  if [[ "$owner_role" != "postgres" ]]; then
    docker exec "$container_name" psql -v ON_ERROR_STOP=1 -U postgres restore_test \
      -c "CREATE ROLE ${owner_role}" >/dev/null
  fi
  gzip -dc "$backup_file" \
    | docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U postgres restore_test >/dev/null
  table_count=$(docker exec "$container_name" psql -At -U postgres restore_test \
    -c "select count(*) from information_schema.tables where table_schema='public';")
  [[ "$table_count" =~ ^[1-9][0-9]*$ ]] \
    || { echo "${label}: restored database is empty" >&2; exit 1; }
  docker rm -f "$container_name" >/dev/null
  container_name=""
  echo "${label}: restore verified (${table_count} public tables)"
}

restore_one control "$control_file" hamalivpn
restore_one remnawave "$remnawave_file" postgres
echo "$(date -u +%FT%TZ) local restore drill passed for ${timestamp}"

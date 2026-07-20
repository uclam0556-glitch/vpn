#!/usr/bin/env bash
# Restore the latest encrypted offsite backup into disposable PostgreSQL containers.
set -Eeuo pipefail
umask 077

CONFIG_FILE="${HAMALI_OPERATIONS_ENV:-/etc/hamalivpn/operations.env}"
if [[ -r "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

: "${BACKUP_AGE_IDENTITY_FILE:?Set BACKUP_AGE_IDENTITY_FILE on the trusted restore host}"
: "${BACKUP_RCLONE_REMOTE:?Set BACKUP_RCLONE_REMOTE}"
: "${BACKUP_SOURCE_HOST:?Set BACKUP_SOURCE_HOST to the control-server hostname}"

for command_name in age rclone docker gzip; do
  command -v "$command_name" >/dev/null || {
    echo "Required command is missing: ${command_name}" >&2
    exit 1
  }
done

run_id="restore-$RANDOM-$$"
work_dir=$(mktemp -d)
container_name=""
cleanup() {
  if [[ -n "$container_name" ]]; then
    docker rm -f "$container_name" >/dev/null 2>&1 || true
  fi
  find "$work_dir" -type f -delete 2>/dev/null || true
  rmdir "$work_dir" 2>/dev/null || true
}
trap cleanup EXIT

remote_root="${BACKUP_RCLONE_REMOTE%/}/${BACKUP_SOURCE_HOST}"
latest_control=$(rclone lsf "$remote_root" --recursive --files-only \
  | grep '/hamalivpn_[0-9]\{8\}_[0-9]\{6\}\.sql\.gz\.age$' | sort | tail -n 1)
test -n "$latest_control" || { echo "No HamaliVPN backup found" >&2; exit 1; }
latest_dir=${latest_control%/*}
timestamp=$(basename "$latest_control" | sed -E 's/^hamalivpn_([0-9]{8}_[0-9]{6})\.sql\.gz\.age$/\1/')
latest_remnawave="${latest_dir}/remnawave_${timestamp}.sql.gz.age"

rclone copyto "${remote_root}/${latest_control}" "$work_dir/hamalivpn.sql.gz.age"
rclone copyto "${remote_root}/${latest_remnawave}" "$work_dir/remnawave.sql.gz.age"

restore_one() {
  local label="$1" encrypted_file="$2"
  container_name="hamali-${run_id}-${label}"
  docker run -d --rm --name "$container_name" \
    -e POSTGRES_PASSWORD=restore-only -e POSTGRES_DB=restore_test \
    postgres:17-alpine >/dev/null
  for _attempt in $(seq 1 30); do
    docker exec "$container_name" pg_isready -U postgres -d restore_test >/dev/null 2>&1 && break
    sleep 1
  done
  docker exec "$container_name" pg_isready -U postgres -d restore_test >/dev/null
  age --decrypt -i "$BACKUP_AGE_IDENTITY_FILE" "$encrypted_file" \
    | gzip -dc | docker exec -i "$container_name" psql -v ON_ERROR_STOP=1 -U postgres restore_test >/dev/null
  table_count=$(docker exec "$container_name" psql -At -U postgres restore_test \
    -c "select count(*) from information_schema.tables where table_schema='public';")
  [[ "$table_count" =~ ^[1-9][0-9]*$ ]] || { echo "${label}: restored database is empty" >&2; exit 1; }
  docker rm -f "$container_name" >/dev/null
  container_name=""
  echo "${label}: restore verified (${table_count} public tables)"
}

restore_one control "$work_dir/hamalivpn.sql.gz.age"
restore_one remnawave "$work_dir/remnawave.sql.gz.age"
echo "$(date -u +%FT%TZ) restore drill passed for ${timestamp}"

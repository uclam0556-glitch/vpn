#!/usr/bin/env bash
# Encrypted, zero-plaintext offsite backup for HamaliVPN and Remnawave.
set -Eeuo pipefail
umask 077

BASE_DIR="${HAMALI_BASE_DIR:-/opt/hamalivpn}"
CONFIG_FILE="${HAMALI_OPERATIONS_ENV:-/etc/hamalivpn/operations.env}"
LOCAL_DIR="${HAMALI_ENCRYPTED_BACKUP_DIR:-${BASE_DIR}/backups/encrypted}"
KEEP_DAYS="${HAMALI_BACKUP_KEEP_DAYS:-14}"

if [[ -r "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

: "${BACKUP_AGE_RECIPIENT:?Set BACKUP_AGE_RECIPIENT in ${CONFIG_FILE}}"
: "${BACKUP_RCLONE_REMOTE:?Set BACKUP_RCLONE_REMOTE in ${CONFIG_FILE}}"

for command_name in age rclone docker gzip sha256sum; do
  command -v "$command_name" >/dev/null || {
    echo "Required command is missing: ${command_name}" >&2
    exit 1
  }
done

timestamp=$(date -u +%Y%m%d_%H%M%S)
host_label=$(hostname -s | tr -cd 'A-Za-z0-9._-')
stage_dir=$(mktemp -d)
trap 'find "$stage_dir" -type f -delete 2>/dev/null || true; rmdir "$stage_dir" 2>/dev/null || true' EXIT
install -d -m 700 "$LOCAL_DIR"

dump_encrypted() {
  local output_name="$1"
  shift
  "$@" | gzip -9 | age -r "$BACKUP_AGE_RECIPIENT" -o "${stage_dir}/${output_name}"
  test -s "${stage_dir}/${output_name}"
}

# Plain SQL only exists inside the pipe. --no-owner/--no-acl makes restore drills portable.
dump_encrypted "hamalivpn_${timestamp}.sql.gz.age" \
  docker exec hamalivpn-postgres-1 pg_dump --no-owner --no-acl -U hamalivpn hamalivpn
dump_encrypted "remnawave_${timestamp}.sql.gz.age" \
  docker exec remnawave-db pg_dump --no-owner --no-acl -U postgres postgres

(
  cd "$stage_dir"
  sha256sum ./*.sql.gz.age > "manifest_${timestamp}.sha256"
  age -r "$BACKUP_AGE_RECIPIENT" \
    -o "manifest_${timestamp}.sha256.age" "manifest_${timestamp}.sha256"
  unlink "manifest_${timestamp}.sha256"
)

remote_path="${BACKUP_RCLONE_REMOTE%/}/${host_label}/$(date -u +%Y/%m/%d)/${timestamp}"
rclone copy "$stage_dir" "$remote_path" --checksum --immutable --transfers 2
rclone check "$stage_dir" "$remote_path" --checksum --one-way

find "$stage_dir" -maxdepth 1 -type f -exec cp -p {} "$LOCAL_DIR/" \;
find "$LOCAL_DIR" -type f -name '*.age' -mtime "+${KEEP_DAYS}" -delete

echo "$(date -u +%FT%TZ) encrypted offsite backup verified: ${remote_path}"

#!/usr/bin/env bash
# HamaliVpn: ежедневный бэкап обеих баз (control + Remnawave).
# Установка:
#   sudo cp /tmp/backup.sh /opt/hamalivpn/backup.sh && sudo chmod +x /opt/hamalivpn/backup.sh
#   (cron) sudo crontab -e  ->  0 4 * * * /opt/hamalivpn/backup.sh >> /var/log/hamali-backup.log 2>&1
set -Eeuo pipefail

DIR=/opt/hamalivpn/backups
TS=$(date +%Y%m%d_%H%M%S)
KEEP_DAYS=14

mkdir -p "$DIR"

# control-база (балансы, платежи, подписки)
docker exec hamalivpn-postgres-1 pg_dump -U hamalivpn hamalivpn \
  | gzip > "$DIR/hamalivpn_$TS.sql.gz"

# Remnawave-база (пользователи панели, ноды, ключи)
docker exec remnawave-db pg_dump -U postgres postgres \
  | gzip > "$DIR/remnawave_$TS.sql.gz"

# чистим старше KEEP_DAYS дней
find "$DIR" -name '*.sql.gz' -mtime "+$KEEP_DAYS" -delete

echo "$(date -u +%FT%TZ) backup ok -> $DIR (hamalivpn_$TS, remnawave_$TS)"

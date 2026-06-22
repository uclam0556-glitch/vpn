#!/usr/bin/env bash
set -Eeuo pipefail

SERVER_IP="${1:-}"
if [[ -z "${SERVER_IP}" ]]; then
  echo "Usage: $0 SERVER_IP" >&2
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

PANEL_HOST="panel.${SERVER_IP}.sslip.io"
INSTALL_DIR="/opt/remnawave"

install -d -m 0750 "${INSTALL_DIR}"
cd "${INSTALL_DIR}"

curl -fsSLo docker-compose.yml \
  https://raw.githubusercontent.com/remnawave/backend/refs/heads/main/docker-compose-prod.yml
curl -fsSLo .env \
  https://raw.githubusercontent.com/remnawave/backend/refs/heads/main/.env.sample

sed -i "s/^JWT_AUTH_SECRET=.*/JWT_AUTH_SECRET=$(openssl rand -hex 64)/" .env
sed -i "s/^JWT_API_TOKENS_SECRET=.*/JWT_API_TOKENS_SECRET=$(openssl rand -hex 64)/" .env
sed -i "s/^METRICS_PASS=.*/METRICS_PASS=$(openssl rand -hex 32)/" .env
sed -i "s/^WEBHOOK_SECRET_HEADER=.*/WEBHOOK_SECRET_HEADER=$(openssl rand -hex 64)/" .env

postgres_password="$(openssl rand -hex 24)"
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${postgres_password}/" .env
sed -i \
  "s|^DATABASE_URL=.*|DATABASE_URL=\"postgresql://postgres:${postgres_password}@remnawave-db:5432/postgres\"|" \
  .env

sed -i "s|^PANEL_DOMAIN=.*|PANEL_DOMAIN=${PANEL_HOST}|" .env
sed -i "s|^FRONT_END_DOMAIN=.*|FRONT_END_DOMAIN=${PANEL_HOST}|" .env
sed -i "s|^SUB_PUBLIC_DOMAIN=.*|SUB_PUBLIC_DOMAIN=${PANEL_HOST}/api/sub|" .env

chmod 0600 .env
docker compose up -d

echo
echo "Remnawave started."
echo "Panel host: https://${PANEL_HOST}"
echo "The public route becomes available after HamaliVpn Caddy is started."

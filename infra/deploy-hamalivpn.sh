#!/usr/bin/env bash
set -Eeuo pipefail

SERVER_IP="${1:-}"
if [[ -z "${SERVER_IP}" ]]; then
  echo "Usage: $0 SERVER_IP" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

public_host="${SERVER_IP}.sslip.io"
panel_host="panel.${SERVER_IP}.sslip.io"

sed -i "s|^PUBLIC_HOST=.*|PUBLIC_HOST=${public_host}|" .env
sed -i "s|^PUBLIC_BASE_URL=.*|PUBLIC_BASE_URL=https://${public_host}|" .env
sed -i "s|^PANEL_HOST=.*|PANEL_HOST=${panel_host}|" .env

replace_if_placeholder() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=GENERATE_WITH_OPENSSL$" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  fi
}

replace_if_placeholder POSTGRES_PASSWORD "$(openssl rand -hex 24)"
replace_if_placeholder ADMIN_PASSWORD "$(openssl rand -base64 24 | tr -d '\n')"
replace_if_placeholder SESSION_SECRET "$(openssl rand -hex 64)"

chmod 0600 .env

if [[ ! -f /opt/remnawave/docker-compose.yml ]]; then
  echo "Install Remnawave first: sudo infra/install-remnawave.sh ${SERVER_IP}" >&2
  exit 1
fi

docker compose build --pull
docker compose up -d postgres redis control maintenance caddy
docker compose ps

echo
echo "HamaliVpn Control: https://${public_host}/admin"
echo "Remnawave Panel:   https://${panel_host}"
echo
echo "Edit .env to add BOT_TOKEN and ADMIN_TELEGRAM_IDS, then run:"
echo "docker compose up -d bot"

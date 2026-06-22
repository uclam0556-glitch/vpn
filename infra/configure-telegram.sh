#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/opt/hamalivpn/.env}"
PROJECT_DIR="${PROJECT_DIR:-/opt/hamalivpn}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Environment file not found: ${ENV_FILE}" >&2
  exit 1
fi

read -rsp "Telegram Bot token from @BotFather: " BOT_TOKEN
echo
read -rp "Your numeric Telegram ID: " ADMIN_TELEGRAM_IDS

if [[ ! "${BOT_TOKEN}" =~ ^[0-9]+:[A-Za-z0-9_-]{20,}$ ]]; then
  echo "Telegram Bot token format is invalid." >&2
  exit 1
fi

if [[ ! "${ADMIN_TELEGRAM_IDS}" =~ ^[0-9]+$ ]]; then
  echo "Telegram ID must contain digits only." >&2
  exit 1
fi

export BOT_TOKEN ADMIN_TELEGRAM_IDS
python3 - "${ENV_FILE}" <<'PY'
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
updates = {
    "BOT_TOKEN": os.environ["BOT_TOKEN"],
    "ADMIN_TELEGRAM_IDS": os.environ["ADMIN_TELEGRAM_IDS"],
}
lines = path.read_text().splitlines()
found = set()
result = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in updates:
        result.append(f"{key}={updates[key]}")
        found.add(key)
    else:
        result.append(line)
for key, value in updates.items():
    if key not in found:
        result.append(f"{key}={value}")
path.write_text("\n".join(result) + "\n")
PY

chmod 0600 "${ENV_FILE}"
unset BOT_TOKEN

cd "${PROJECT_DIR}"
docker compose up -d bot
docker compose ps bot

echo
echo "Telegram bot started in the current Remnawave mode."
echo "Open https://t.me/HamaliVpn_bot and send /start."

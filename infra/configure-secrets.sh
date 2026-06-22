#!/usr/bin/env bash
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/opt/hamalivpn/.env}"
PROJECT_DIR="${PROJECT_DIR:-/opt/hamalivpn}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Environment file not found: ${ENV_FILE}" >&2
  exit 1
fi

read -rsp "Telegram Bot token: " BOT_TOKEN
echo
read -rp "Your numeric Telegram ID: " ADMIN_TELEGRAM_IDS
read -rsp "Remnawave API token: " REMNAWAVE_API_TOKEN
echo
read -rp "CHECHNYA-LAB squad UUID: " REMNAWAVE_INTERNAL_SQUADS

if [[ ! "${BOT_TOKEN}" =~ ^[0-9]+:[A-Za-z0-9_-]{20,}$ ]]; then
  echo "Telegram Bot token format is invalid." >&2
  exit 1
fi

if [[ ! "${ADMIN_TELEGRAM_IDS}" =~ ^[0-9]+$ ]]; then
  echo "Telegram ID must contain digits only." >&2
  exit 1
fi

if [[ -z "${REMNAWAVE_API_TOKEN}" ]]; then
  echo "Remnawave API token cannot be empty." >&2
  exit 1
fi

if [[ ! "${REMNAWAVE_INTERNAL_SQUADS}" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  echo "Squad UUID format is invalid." >&2
  exit 1
fi

export BOT_TOKEN ADMIN_TELEGRAM_IDS REMNAWAVE_API_TOKEN REMNAWAVE_INTERNAL_SQUADS
python3 - "${ENV_FILE}" <<'PY'
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
updates = {
    "BOT_TOKEN": os.environ["BOT_TOKEN"],
    "ADMIN_TELEGRAM_IDS": os.environ["ADMIN_TELEGRAM_IDS"],
    "REMNAWAVE_API_TOKEN": os.environ["REMNAWAVE_API_TOKEN"],
    "REMNAWAVE_INTERNAL_SQUADS": os.environ["REMNAWAVE_INTERNAL_SQUADS"],
    "REMNAWAVE_MOCK": "false",
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
unset BOT_TOKEN REMNAWAVE_API_TOKEN

cd "${PROJECT_DIR}"
docker compose up -d control bot maintenance
docker compose ps

echo
echo "Secrets saved securely and Telegram bot started."

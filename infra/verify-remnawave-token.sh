#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# HamaliVpn — безопасная привязка API-токена Remnawave
#
# Что делает скрипт:
#   1. Запрашивает API-токен без отображения символов.
#   2. Делает два проверочных запроса: /api/system и /api/users.
#   3. Если оба успешны — сохраняет токен в .env.
#   4. Переключает REMNAWAVE_MOCK=false.
#   5. Предлагает ввести UUID squad (или пропустить — добавить позже).
#   6. Перезапускает control, bot, maintenance.
#
# Запуск на сервере:
#   cd /opt/hamalivpn
#   bash infra/verify-remnawave-token.sh
# ────────────────────────────────────────────────────────────────
set -Eeuo pipefail

ENV_FILE="${ENV_FILE:-/opt/hamalivpn/.env}"
PROJECT_DIR="${PROJECT_DIR:-/opt/hamalivpn}"

# ── цвета ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "${RED}✗${RESET}  $*" >&2; }
info() { echo -e "${CYAN}→${RESET} $*"; }

echo
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  HamaliVpn — привязка боевого Remnawave API  ${RESET}"
echo -e "${BOLD}══════════════════════════════════════════════${RESET}"
echo

# ── проверка окружения ───────────────────────────────────────────
if [[ ! -f "${ENV_FILE}" ]]; then
  err "Файл .env не найден: ${ENV_FILE}"
  err "Сначала выполните: bash infra/deploy-hamalivpn.sh SERVER_IP"
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  err "curl не установлен. Установите: apt-get install -y curl"
  exit 1
fi

# ── определяем URL панели из .env ───────────────────────────────
PANEL_URL="$(grep -E '^PANEL_BASE_URL=' "${ENV_FILE}" | cut -d= -f2- | tr -d '"' || true)"
if [[ -z "${PANEL_URL}" ]]; then
  # Docker-внутреннее имя
  PANEL_URL="http://remnawave:3000"
fi
info "URL панели Remnawave: ${PANEL_URL}"
echo

# ── ввод токена ──────────────────────────────────────────────────
echo -e "${BOLD}Шаг 1/4 — API-токен${RESET}"
echo "Где взять: Remnawave Panel → Settings → API Tokens → создайте новый."
echo "(символы не отображаются при вводе)"
read -rsp "API-токен: " REMNAWAVE_API_TOKEN
echo

if [[ -z "${REMNAWAVE_API_TOKEN}" ]]; then
  err "Токен не может быть пустым."
  exit 1
fi
echo

# ── проверочные запросы ──────────────────────────────────────────
echo -e "${BOLD}Шаг 2/4 — Проверка токена реальными запросами${RESET}"

HTTP_ARGS=(
  -s -o /tmp/hwrn_resp.json -w "%{http_code}"
  -H "Authorization: Bearer ${REMNAWAVE_API_TOKEN}"
  -H "Content-Type: application/json"
  --connect-timeout 10
  --max-time 20
)

# Запрос 1: /api/system
info "Запрос GET /api/system ..."
STATUS=$(curl "${HTTP_ARGS[@]}" "${PANEL_URL}/api/system" || true)
if [[ "${STATUS}" == "200" ]]; then
  ok "Панель отвечает (200 OK)"
elif [[ "${STATUS}" == "401" || "${STATUS}" == "403" ]]; then
  err "Ошибка авторизации (${STATUS}). Токен неверный или истёк."
  err "Создайте новый токен в Remnawave → Settings → API Tokens."
  exit 1
elif [[ "${STATUS}" == "000" ]]; then
  err "Панель недоступна. Убедитесь, что Remnawave запущен:"
  err "  cd /opt/remnawave && docker compose ps"
  exit 1
else
  err "Неожиданный ответ: HTTP ${STATUS}"
  err "Ответ сервера:"
  cat /tmp/hwrn_resp.json 2>/dev/null || true
  exit 1
fi

# Запрос 2: /api/users (проверяем, что токен реально имеет доступ)
info "Запрос GET /api/users ..."
STATUS=$(curl "${HTTP_ARGS[@]}" "${PANEL_URL}/api/users?page=1&size=1" || true)
if [[ "${STATUS}" == "200" ]]; then
  ok "Доступ к пользователям подтверждён (200 OK)"
elif [[ "${STATUS}" == "401" || "${STATUS}" == "403" ]]; then
  err "Токен не имеет прав на управление пользователями (${STATUS})."
  err "Убедитесь, что при создании токена выбраны все необходимые права."
  exit 1
else
  warn "GET /api/users вернул HTTP ${STATUS}. Продолжаем с предупреждением."
fi

rm -f /tmp/hwrn_resp.json
echo

# ── squad UUID ──────────────────────────────────────────────────
echo -e "${BOLD}Шаг 3/4 — Squad UUID (опционально)${RESET}"
echo "Squad задаёт, через какие ноды пойдут ваши пользователи."
echo "Если squad ещё не создан — просто нажмите Enter, добавите позже."
read -rp "UUID squad (или Enter чтобы пропустить): " SQUAD_UUID

SQUAD_UUID="${SQUAD_UUID// /}"  # убираем пробелы

if [[ -n "${SQUAD_UUID}" ]]; then
  # Проверяем формат UUID
  if [[ "${SQUAD_UUID}" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; then
    ok "UUID squad принят: ${SQUAD_UUID}"
  else
    warn "Формат UUID не стандартный — сохраняем как есть. Проверьте в Remnawave."
  fi
else
  warn "Squad не указан. Пользователи без squad могут не получать конфиги нод."
  warn "Добавьте позже: REMNAWAVE_INTERNAL_SQUADS=UUID в .env и перезапустите control."
fi
echo

# ── запись в .env ────────────────────────────────────────────────
echo -e "${BOLD}Шаг 4/4 — Сохранение и перезапуск${RESET}"
info "Обновляю ${ENV_FILE} ..."

# Используем Python для безопасного редактирования .env
SQUAD_VALUE="${SQUAD_UUID}" API_VALUE="${REMNAWAVE_API_TOKEN}" \
python3 - "${ENV_FILE}" <<'PY'
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
updates = {
    "REMNAWAVE_API_TOKEN": os.environ["API_VALUE"],
    "REMNAWAVE_MOCK":      "false",
}
squad = os.environ.get("SQUAD_VALUE", "").strip()
if squad:
    updates["REMNAWAVE_INTERNAL_SQUADS"] = squad

lines = path.read_text().splitlines()
found = set()
result = []
for line in lines:
    stripped = line.lstrip()
    if stripped.startswith("#") or "=" not in stripped:
        result.append(line)
        continue
    key = line.split("=", 1)[0].strip()
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
unset REMNAWAVE_API_TOKEN

ok ".env обновлён: REMNAWAVE_MOCK=false, токен сохранён"
echo

# ── перезапуск контейнеров ───────────────────────────────────────
info "Перезапуск control, bot, maintenance ..."
cd "${PROJECT_DIR}"
docker compose up -d control bot maintenance
echo

# ── итог ────────────────────────────────────────────────────────
echo -e "${GREEN}${BOLD}══════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  Готово! Бот переключён на боевой ${RESET}"
echo -e "${GREEN}${BOLD}  Remnawave API.                    ${RESET}"
echo -e "${GREEN}${BOLD}══════════════════════════════════${RESET}"
echo
echo "Статус контейнеров:"
docker compose ps
echo
echo "Проверка:"
echo "  1. Откройте Telegram → @HamaliVpn_bot → /start"
echo "  2. Нажмите «Получить тестовый доступ»"
echo "  3. Нажмите «Подключить устройство» — откроется страница подключения"
echo "  4. Проверьте, что пользователь появился в Remnawave Panel → Users"
echo
if [[ -z "${SQUAD_UUID:-}" ]]; then
  warn "Не забудьте добавить Squad UUID:"
  warn "  1. Remnawave Panel → Squads → Create Squad"
  warn "  2. bash infra/verify-remnawave-token.sh (повторный запуск — введите тот же токен)"
fi

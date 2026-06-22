#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# HamaliVpn — установка VPN-ноды Remnawave Node на арендованном VPS
#
# Использование (запустить на новом VPS от root или через sudo):
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/YOURREPO/vpn/main/infra/install-node.sh) \
#     --panel-url https://panel.IP.sslip.io \
#     --node-token TOKEN_FROM_REMNAWAVE
#
# Или с ключами окружения:
#
#   PANEL_URL=https://panel.IP.sslip.io \
#   NODE_TOKEN=... \
#   bash infra/install-node.sh
#
# Поддерживаемые ОС:
#   Ubuntu 22.04, Ubuntu 24.04 (x86_64 / arm64)
# ────────────────────────────────────────────────────────────────
set -Eeuo pipefail

# ── цвета ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "${RED}✗${RESET}  $*" >&2; }
info() { echo -e "${CYAN}→${RESET} $*"; }

echo
echo -e "${BOLD}══════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  HamaliVpn — Установка Remnawave Node (VPN-нода)     ${RESET}"
echo -e "${BOLD}══════════════════════════════════════════════════════${RESET}"
echo

# ── разбор аргументов ────────────────────────────────────────────
PANEL_URL="${PANEL_URL:-}"
NODE_TOKEN="${NODE_TOKEN:-}"
INSTALL_DIR="/opt/remnawave-node"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --panel-url)  PANEL_URL="$2";   shift 2 ;;
    --node-token) NODE_TOKEN="$2";  shift 2 ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    *) err "Неизвестный параметр: $1"; exit 1 ;;
  esac
done

# ── интерактивный ввод если не передано ─────────────────────────
if [[ -z "${PANEL_URL}" ]]; then
  read -rp "URL панели Remnawave (напр. https://panel.1.2.3.4.sslip.io): " PANEL_URL
fi

if [[ -z "${NODE_TOKEN}" ]]; then
  echo "Где взять: Remnawave Panel → Nodes → Add Node → скопируйте Node Token."
  read -rsp "Node Token: " NODE_TOKEN
  echo
fi

PANEL_URL="${PANEL_URL%%/}"  # убрать trailing slash

if [[ -z "${PANEL_URL}" || -z "${NODE_TOKEN}" ]]; then
  err "PANEL_URL и NODE_TOKEN обязательны."
  exit 1
fi

echo

# ── проверка ОС ──────────────────────────────────────────────────
info "Проверка системы..."
if [[ "${EUID}" -ne 0 ]]; then
  err "Запустите скрипт от root или через sudo."
  exit 1
fi

if [[ ! -f /etc/os-release ]]; then
  err "/etc/os-release не найден. Поддерживается только Ubuntu."
  exit 1
fi

source /etc/os-release
if [[ "${ID}" != "ubuntu" ]]; then
  warn "Тестировалось на Ubuntu. Продолжаем на ${PRETTY_NAME:-${ID}}..."
fi

ARCH="$(uname -m)"
if [[ "${ARCH}" == "x86_64" ]]; then ARCH="amd64"
elif [[ "${ARCH}" == "aarch64" ]]; then ARCH="arm64"
else warn "Нетипичная архитектура: ${ARCH}. Продолжаем..."; fi

ok "ОС: ${PRETTY_NAME:-${ID}} | Арх: ${ARCH}"
echo

# ── базовые пакеты ───────────────────────────────────────────────
info "Установка базовых пакетов..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl openssl ufw >/dev/null
ok "Базовые пакеты установлены"
echo

# ── Docker ──────────────────────────────────────────────────────
info "Проверка Docker..."
if ! command -v docker >/dev/null 2>&1; then
  info "Docker не найден. Устанавливаю официальным скриптом..."
  curl -fsSL https://get.docker.com | sh >/dev/null
  ok "Docker установлен"
else
  ok "Docker уже установлен: $(docker --version)"
fi
systemctl enable --now docker
echo

# ── часовой пояс UTC ─────────────────────────────────────────────
timedatectl set-timezone UTC 2>/dev/null || true

# ── UFW файрвол ──────────────────────────────────────────────────
info "Настройка файрвола..."
ufw --force reset >/dev/null 2>&1
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
# VLESS Reality
ufw allow 443/tcp >/dev/null
# Hysteria2
ufw allow 443/udp >/dev/null
# Порт для апи ноды (панель → нода)
ufw allow 2095/tcp >/dev/null
ufw --force enable >/dev/null
ok "Файрвол: SSH(22), 443/tcp, 443/udp, 2095/tcp"
echo

# ── создание директории ───────────────────────────────────────────
install -d -m 0750 "${INSTALL_DIR}"
cd "${INSTALL_DIR}"
info "Директория: ${INSTALL_DIR}"

# ── создаём .env ноды ────────────────────────────────────────────
cat > "${INSTALL_DIR}/.env" <<NODEENV
NODE_PORT=2095
SECRET_KEY=${NODE_TOKEN}
LOG_LEVEL=info
NODEENV
chmod 0600 "${INSTALL_DIR}/.env"
ok ".env ноды создан"

# ── docker-compose.yml для ноды ──────────────────────────────────
cat > "${INSTALL_DIR}/docker-compose.yml" <<'COMPOSE'
# Remnawave Node — VPN-нода HamaliVpn
# Не изменять вручную: управляется через install-node.sh
services:
  remnawave-node:
    image: remnawave/node:latest
    container_name: remnawave-node
    restart: unless-stopped
    network_mode: host
    env_file: .env
    volumes:
      - /dev/net/tun:/dev/net/tun
    cap_add:
      - NET_ADMIN
    ulimits:
      nofile:
        soft: 65536
        hard: 65536
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://127.0.0.1:${NODE_PORT:-2095}/health || exit 1"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 10s
COMPOSE

ok "docker-compose.yml создан"
echo

# ── запуск ноды ──────────────────────────────────────────────────
info "Скачиваю образ и запускаю ноду..."
docker compose pull 2>&1 | tail -3
docker compose up -d
echo

# ── ожидание готовности ──────────────────────────────────────────
info "Жду запуска ноды (до 30 секунд)..."
for i in $(seq 1 6); do
  sleep 5
  STATUS=$(docker compose ps --format json 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('Health','') or d.get('State',''))" 2>/dev/null || true)
  if [[ "${STATUS}" == "healthy" || "${STATUS}" == "running" ]]; then
    ok "Нода запущена и отвечает!"
    break
  fi
  echo -n "  ожидание... (${i}/6)"
  echo
done

# ── финальный отчёт ──────────────────────────────────────────────
NODE_IP=$(curl -s --connect-timeout 5 https://ifconfig.me || hostname -I | awk '{print $1}')

echo
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  Нода установлена!                             ${RESET}"
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════${RESET}"
echo
echo -e "  ${BOLD}IP ноды:${RESET}          ${NODE_IP}"
echo -e "  ${BOLD}Директория:${RESET}       ${INSTALL_DIR}"
echo -e "  ${BOLD}Панель:${RESET}           ${PANEL_URL}"
echo
echo -e "${BOLD}Что делать дальше:${RESET}"
echo "  1. В Remnawave Panel → Nodes — нода появится автоматически"
echo "     или статус станет 'Online' в течение 1-2 минут."
echo "  2. Присвойте ноде имя (например: Gcore-Frankfurt)."
echo "  3. Нажмите 'Configure Inbounds' — настройте VLESS Reality и Hysteria2."
echo "  4. В Telegram боте нажмите 'Получить тестовый доступ'."
echo "  5. Откройте 'Подключить устройство' → импортируйте в Hiddify или v2rayTun."
echo
echo -e "${BOLD}Полезные команды на ноде:${RESET}"
echo "  cd ${INSTALL_DIR}"
echo "  docker compose logs -f        # логи в реальном времени"
echo "  docker compose ps             # статус"
echo "  docker compose pull && docker compose up -d  # обновление"
echo
echo -e "${YELLOW}${BOLD}УДАЛЕНИЕ ноды (после теста):${RESET}"
echo "  bash /path/to/remove-node.sh"
echo "  # или вручную:"
echo "  cd ${INSTALL_DIR} && docker compose down && cd / && rm -rf ${INSTALL_DIR}"

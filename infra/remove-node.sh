#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# HamaliVpn — полное и чистое удаление VPN-ноды
#
# Запуск на нодовом VPS (перед удалением самого VPS):
#   bash infra/remove-node.sh
#
# Что делает:
#   1. Корректно останавливает и удаляет Docker-контейнер ноды.
#   2. Удаляет Docker-образ, чтобы не занимать место.
#   3. Удаляет директорию ноды.
#   4. Выдаёт чеклист: что ещё нужно сделать вручную в Gcore
#      и Remnawave Panel, чтобы не осталось лишних списаний.
# ────────────────────────────────────────────────────────────────
set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/remnawave-node}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
info() { echo -e "${CYAN}→${RESET} $*"; }

echo
echo -e "${BOLD}══════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  HamaliVpn — Удаление VPN-ноды (Remnawave Node)  ${RESET}"
echo -e "${BOLD}══════════════════════════════════════════════════${RESET}"
echo
warn "Это удалит ноду. Все активные подключения разорвутся."
read -rp "Продолжить? (yes/no): " CONFIRM
if [[ "${CONFIRM}" != "yes" ]]; then
  echo "Отменено."
  exit 0
fi
echo

# ── остановка контейнера ─────────────────────────────────────────
if [[ -d "${INSTALL_DIR}" ]]; then
  info "Остановка контейнера ноды..."
  cd "${INSTALL_DIR}"
  docker compose down --remove-orphans --timeout 10 || true
  ok "Контейнер остановлен"

  info "Удаление Docker-образа remnawave/node..."
  docker image rm remnawave/node:latest 2>/dev/null || true
  ok "Образ удалён"

  info "Удаление директории ${INSTALL_DIR}..."
  cd /
  rm -rf "${INSTALL_DIR}"
  ok "Директория удалена"
else
  warn "${INSTALL_DIR} не найден — нода уже удалена или не устанавливалась."
fi

# ── очистка Docker ───────────────────────────────────────────────
info "Очистка неиспользуемых Docker-ресурсов..."
docker system prune -f --volumes >/dev/null 2>&1 || true
ok "Docker очищен"
echo

# ── чеклист ──────────────────────────────────────────────────────
echo -e "${YELLOW}${BOLD}══════════════════════════════════════════════════════════${RESET}"
echo -e "${YELLOW}${BOLD}  ВАЖНО: Чеклист для полного удаления без лишних списаний  ${RESET}"
echo -e "${YELLOW}${BOLD}══════════════════════════════════════════════════════════${RESET}"
echo
echo -e "${BOLD}В Remnawave Panel (panel.YOUR_IP.sslip.io):${RESET}"
echo "  [ ] Перейдите в Nodes"
echo "  [ ] Найдите эту ноду (по IP или имени)"
echo "  [ ] Нажмите Delete — удалите ноду из панели"
echo "  [ ] Убедитесь, что нода исчезла из списка"
echo
echo -e "${BOLD}В Gcore (gcore.com/cloud → ваш проект):${RESET}"
echo "  [ ] Instances → найдите сервер → Delete Instance"
echo "  [ ] ⚠  Volumes → убедитесь, что диск (Volume) тоже удалён"
echo "         (по умолчанию Gcore НЕ удаляет диск вместе с сервером)"
echo "  [ ] ⚠  Networking → Floating IPs → освободите зарезервированный IP"
echo "         (зарезервированный но не привязанный IP списывается отдельно)"
echo "  [ ] Reserved IPs → проверьте список — там не должно остаться ничего"
echo
echo -e "${BOLD}Финальная проверка:${RESET}"
echo "  [ ] Gcore Billing → убедитесь, что Instance, Volume и IP не фигурируют"
echo "      в активных ресурсах"
echo
echo -e "${GREEN}${BOLD}Нода удалена с сервера. Выполните чеклист выше!${RESET}"

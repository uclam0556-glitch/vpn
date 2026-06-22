#!/usr/bin/env bash
# HamaliVpn: диагностика подключения к ноде с мобильной сети.
# Запускать на VPN-ноде от root:
#   sudo bash infra/diagnose-mobile-path.sh
#   sudo bash infra/diagnose-mobile-path.sh --capture 60
set -Eeuo pipefail

CAPTURE_SECONDS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --capture)
      CAPTURE_SECONDS="${2:-60}"
      shift 2
      ;;
    *)
      echo "Неизвестный аргумент: $1" >&2
      exit 2
      ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Запустите от root: sudo bash $0 [--capture 60]" >&2
  exit 1
fi

command -v ss >/dev/null || {
  apt-get update -qq
  apt-get install -y -qq iproute2 >/dev/null
}

echo "== HamaliVpn: состояние ноды =="
date -u
echo

echo "-- Публичные адреса и маршрут --"
ip -brief address
ip route get 1.1.1.1 || true
echo

echo "-- MTU интерфейсов --"
ip -o link show | awk -F': ' '{print $2}' | while read -r iface; do
  ip link show "${iface}" | awk -v iface="${iface}" '/mtu/ {for (i=1;i<=NF;i++) if ($i=="mtu") print iface ": " $(i+1)}'
done
echo

echo "-- Слушающие VPN-порты --"
ss -lntup | grep -E ':(443|8443|2053|2083|2095)\b' || {
  echo "ВНИМАНИЕ: ожидаемые порты не слушаются."
}
echo

echo "-- Docker / Remnawave Node --"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null || true
docker logs --since 10m remnawave-node 2>&1 | tail -n 80 || true
echo

echo "-- Firewall --"
ufw status verbose 2>/dev/null || true
echo

echo "-- TCP congestion control --"
sysctl net.ipv4.tcp_congestion_control 2>/dev/null || true
sysctl net.core.default_qdisc 2>/dev/null || true
echo

if [[ "${CAPTURE_SECONDS}" -gt 0 ]]; then
  command -v tcpdump >/dev/null || {
    apt-get update -qq
    apt-get install -y -qq tcpdump >/dev/null
  }

  echo "== Захват ${CAPTURE_SECONDS} секунд =="
  echo "Сейчас тестировщик должен:"
  echo "1) выключить Wi-Fi; 2) оставить мобильный интернет;"
  echo "3) открыть Happ; 4) подключиться к серверу; 5) открыть любой сайт."
  echo
  echo "Смотрим TCP 443/8443/2053 и UDP 443. Ctrl+C завершит раньше."

  timeout "${CAPTURE_SECONDS}" \
    tcpdump -ni any -tttt -vv \
    'tcp port 443 or tcp port 8443 or tcp port 2053 or udp port 443' \
    || true
fi


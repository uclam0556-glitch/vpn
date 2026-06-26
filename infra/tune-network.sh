#!/usr/bin/env bash
# ────────────────────────────────────────────────────────────────
# HamaliVpn — сетевой тюнинг VPN-ноды «мирового уровня».
# BBR + fq (против bufferbloat/скачков под нагрузкой) + большие буферы
# под Hysteria2 (UDP/QUIC) и Reality, conntrack, forwarding, лимиты.
#
# Идемпотентно и обратимо: всё пишется в /etc/sysctl.d/99-hamali-node.conf
# (откатить = удалить файл + sysctl --system).
#
# Запускать на НОВОЙ ноде от root ДО install-node.sh:
#   curl -fsSLo /tmp/tune.sh https://raw.githubusercontent.com/uclam0556-glitch/vpn/main/infra/tune-network.sh
#   sudo bash /tmp/tune.sh
# ────────────────────────────────────────────────────────────────
set -Eeuo pipefail

[ "${EUID}" -eq 0 ] || { echo "Запустите от root (sudo)."; exit 1; }

# BBR-модуль на старте системы
modprobe tcp_bbr 2>/dev/null || true
echo 'tcp_bbr' > /etc/modules-load.d/bbr.conf

cat > /etc/sysctl.d/99-hamali-node.conf <<'SYSCTL'
# ── HamaliVpn VPN-нода: сеть мирового уровня ──

# Congestion control: BBR + честная очередь (убирает скачки латентности под нагрузкой)
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# Большие буферы — критично для Hysteria2 (UDP/QUIC) и многопоточного Reality
net.core.rmem_max = 16777216
net.core.wmem_max = 16777216
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576
net.ipv4.tcp_rmem = 4096 1048576 16777216
net.ipv4.tcp_wmem = 4096 1048576 16777216
net.core.netdev_max_backlog = 16384
net.core.somaxconn = 8192

# TCP: MTU-проба (против чёрных дыр на мобильных), fastopen, без slow-start после простоя
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_fastopen = 3
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_notsent_lowat = 16384

# UDP-минимумы (Hysteria2)
net.ipv4.udp_rmem_min = 8192
net.ipv4.udp_wmem_min = 8192

# Форвардинг — VPN-нода маршрутизирует трафик клиентов
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1

# Меньше своп — нода работает из RAM
vm.swappiness = 10
SYSCTL

# conntrack — отдельно (модуль может быть не загружен на голой системе)
cat > /etc/sysctl.d/98-hamali-conntrack.conf <<'CT'
net.netfilter.nf_conntrack_max = 262144
CT

sysctl --system >/dev/null 2>&1 || true

# Лимиты файловых дескрипторов (много одновременных соединений)
cat > /etc/security/limits.d/99-hamali-node.conf <<'LIM'
* soft nofile 1048576
* hard nofile 1048576
LIM

echo "=== Применено ==="
sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc 2>/dev/null
echo
echo "✓ BBR + fq, большие TCP/UDP-буферы, conntrack, forwarding, лимиты — включены."
echo "✓ Для Hysteria2 особенно важны поднятые UDP-буферы."
echo "Далее: установка ноды — install-node.sh."

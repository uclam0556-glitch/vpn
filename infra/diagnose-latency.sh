#!/usr/bin/env bash
# HamaliVpn: диагностика случайных скачков латентности на ноде/панели.
# Только чтение — ничего не меняет на сервере.
#
# Запуск на сервере (от root):
#   sudo bash infra/diagnose-latency.sh              # один снимок состояния
#   sudo bash infra/diagnose-latency.sh --watch 90   # 90 сек ловим скачок в динамике
#
# Цель: отличить реальную задержку прокси от "цифры в Happ", и поймать причину
# случайных лагов в простое (CPU steal, retransmits, conntrack, OVH/ICMP, swap).
set -Eeuo pipefail

WATCH_SECONDS=0
UPSTREAM="1.1.1.1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --watch) WATCH_SECONDS="${2:-90}"; shift 2 ;;
    --upstream) UPSTREAM="${2}"; shift 2 ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "Запустите от root: sudo bash $0 [--watch 90]" >&2
  exit 1
fi

ensure() { command -v "$1" >/dev/null 2>&1 || { apt-get update -qq; apt-get install -y -qq "$2" >/dev/null 2>&1 || true; }; }
ensure mpstat sysstat
ensure nstat iproute2
ensure ss iproute2

hr() { printf '\n=== %s ===\n' "$1"; }

hr "ДАТА / UPTIME / ЯДРО"
date -u; uptime; uname -r

hr "CPU: модель, ядра, STEAL (главный подозреваемый при лагах в простое)"
# %steal = такты, украденные гипервизором у твоей VM. >1-2% уже плохо для VPN.
lscpu | grep -E "Model name|^CPU\(s\)|Thread|Core|Socket|Hypervisor|Virtualization" || true
echo "-- mpstat 1s x5 (смотри колонку %steal и %iowait) --"
mpstat 1 5 || top -bn1 | head -5

hr "ТОП ПРОЦЕССОВ ПО CPU"
top -bn1 -o %CPU | head -18

hr "ПАМЯТЬ / SWAP (своп = многосекундные стопы)"
free -h
echo "-- vmstat 1s x3 (колонки si/so = swap in/out должны быть 0) --"
vmstat 1 3

hr "DOCKER: что крутится на этой коробке (нода+панель вместе = борьба за CPU)"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null || echo "docker недоступен"
echo
docker stats --no-stream 2>/dev/null | head -20 || true

hr "СЕТЬ: ошибки/дропы на интерфейсах (errors/dropped/overrun должны быть ~0)"
ip -s -h link 2>/dev/null || ip -s link

hr "TCP-АНОМАЛИИ: ретрансмиты, таймауты, потери, дропы листен-очереди"
nstat -az 2>/dev/null | grep -iE "Retrans|TCPLostRetransmit|TCPTimeouts|TCPLoss|ListenDrops|ListenOverflows|TCPBacklogDrop|TCPSynRetrans" || \
  netstat -s 2>/dev/null | grep -iE "retransmit|timeout|listen"

hr "UDP-ОШИБКИ (важно если включён Hysteria2): receive/buffer errors"
nstat -az 2>/dev/null | grep -iE "Udp.*Errors|RcvbufErrors|SndbufErrors|NoPorts" || \
  netstat -su 2>/dev/null | grep -iE "error|receive|buffer"

hr "CONNTRACK: заполнение таблицы (full = дропы пакетов и лаги)"
sysctl net.netfilter.nf_conntrack_count net.netfilter.nf_conntrack_max 2>/dev/null || echo "conntrack модуль не загружен"

hr "TCP CONGESTION CONTROL / QDISC (должно быть bbr + fq/cake)"
sysctl net.ipv4.tcp_congestion_control net.core.default_qdisc 2>/dev/null
echo "-- доступные алгоритмы --"
sysctl net.ipv4.tcp_available_congestion_control 2>/dev/null
echo "-- qdisc на интерфейсах --"
tc qdisc show 2>/dev/null | head -20 || true

hr "СОКЕТЫ: сколько ESTAB соединений, переполнен ли backlog слушающих портов"
ss -s 2>/dev/null
echo "-- слушающие VPN-порты и их accept-очереди (Recv-Q у LISTEN = backlog) --"
ss -lntp 2>/dev/null | grep -E ':(443|8443|2053|2083|2087|2096)\b' || echo "ожидаемые порты не слушаются"

hr "DMESG: throttling / OOM / conntrack full / drops за последнее время"
dmesg -T 2>/dev/null | grep -iE "oom|throttl|conntrack.*full|nf_conntrack: table full|segfault|martian|rx_dropped|Out of memory|hung task" | tail -30 || echo "подозрительных записей нет"

hr "РЕАЛЬНАЯ ЛАТЕНТНОСТЬ САМОЙ НОДЫ ДО UPSTREAM (${UPSTREAM})"
# Если ЗДЕСЬ ровно ~стабильно, а Happ показывает скачки — проблема в пути
# оператор↔OVH или в самом измерении Happ (ICMP/anti-DDoS), а не в ноде.
ping -c 10 -i 0.3 "${UPSTREAM}" 2>/dev/null | tail -4 || echo "ping недоступен"

if [[ "${WATCH_SECONDS}" -gt 0 ]]; then
  hr "WATCH ${WATCH_SECONDS}с: ловим скачок с контекстом (steal/load/ping синхронно)"
  echo "Колонки: time  load1  steal%  iowait%  ping_ms_to_${UPSTREAM}"
  echo "Запусти Happ и пользуйся в это время. Скачок ping в Happ ищем рядом с"
  echo "ростом steal% или load — это укажет причину."
  end=$(( $(date +%s) + WATCH_SECONDS ))
  while [[ $(date +%s) -lt ${end} ]]; do
    read -r steal iowait < <(mpstat 1 1 2>/dev/null | awk '/Average:/ {print $(NF-1)" "$5}')
    load1=$(awk '{print $1}' /proc/loadavg)
    png=$(ping -c1 -W1 "${UPSTREAM}" 2>/dev/null | awk -F'time=' '/time=/{print $2}' | awk '{print $1}')
    printf '%s  load=%s  steal=%s  iowait=%s  ping=%sms\n' "$(date +%H:%M:%S)" "${load1}" "${steal:-?}" "${iowait:-?}" "${png:-LOSS}"
  done
fi

hr "ГОТОВО"
echo "Скопируй весь вывод выше и пришли в чат — разберу и дам точечный фикс."

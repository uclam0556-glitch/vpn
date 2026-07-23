#!/usr/bin/env bash
# Recover local Remnawave Node and WARP proxy failures without rebooting the VPS.
# External network/provider failures remain the responsibility of the central
# monitor because restarting healthy VPN processes cannot repair a dead uplink.
set -Eeuo pipefail

readonly CONTAINER="${HAMALI_NODE_CONTAINER:-remnawave-node}"
readonly FAILURE_LIMIT="${HAMALI_NODE_FAILURE_LIMIT:-3}"
readonly STATE_DIR="/run/hamalivpn-node-watchdog"

install -d -m 0755 "${STATE_DIR}"

failure_count() {
  local name="$1"
  local file="${STATE_DIR}/${name}.failures"
  [[ -r "${file}" ]] && cat "${file}" || printf '0'
}

record_success() {
  rm -f "${STATE_DIR}/$1.failures"
}

record_failure() {
  local name="$1"
  local count
  count=$(( $(failure_count "${name}") + 1 ))
  printf '%s\n' "${count}" > "${STATE_DIR}/${name}.failures"
  printf '%s\n' "${count}"
}

node_healthy() {
  [[ "$(docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null || true)" == "true" ]] &&
    timeout 3 bash -c '</dev/tcp/127.0.0.1/2095' &&
    timeout 3 bash -c '</dev/tcp/127.0.0.1/443'
}

if node_healthy; then
  record_success node
else
  node_failures="$(record_failure node)"
  logger -t hamalivpn-node-watchdog \
    "Remnawave local health check failed (${node_failures}/${FAILURE_LIMIT})"
  if (( node_failures >= FAILURE_LIMIT )); then
    docker restart "${CONTAINER}" >/dev/null
    record_success node
    logger -t hamalivpn-node-watchdog \
      "Remnawave container restarted after ${node_failures} consecutive failures"
  fi
fi

if systemctl is-enabled --quiet warp-svc.service 2>/dev/null; then
  if timeout 5 warp-cli --accept-tos status 2>/dev/null |
      grep -q '^Status update: Connected$'; then
    record_success warp
  else
    warp_failures="$(record_failure warp)"
    logger -t hamalivpn-node-watchdog \
      "WARP proxy health check failed (${warp_failures}/${FAILURE_LIMIT})"
    if (( warp_failures >= FAILURE_LIMIT )); then
      systemctl restart warp-svc.service
      record_success warp
      logger -t hamalivpn-node-watchdog \
        "WARP proxy restarted after ${warp_failures} consecutive failures"
    fi
  fi
fi

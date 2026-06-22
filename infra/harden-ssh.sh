#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

cat >/etc/ssh/sshd_config.d/90-hamalivpn-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
MaxAuthTries 3
X11Forwarding no
AllowTcpForwarding yes
EOF

sshd -t
systemctl reload ssh

echo "SSH hardening applied: password and direct root login are disabled."

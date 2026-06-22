#!/usr/bin/env bash
set -Eeuo pipefail

ADMIN_USER="${ADMIN_USER:-hamaliadmin}"
PUBLIC_KEY_FILE="${PUBLIC_KEY_FILE:-/root/hamalivpn_control.pub}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root." >&2
  exit 1
fi

if [[ ! -f /etc/os-release ]]; then
  echo "Unsupported system: /etc/os-release is missing." >&2
  exit 1
fi

source /etc/os-release

if [[ "${ID}" != "ubuntu" ]]; then
  echo "Expected Ubuntu 24.04; detected ${PRETTY_NAME:-unknown}." >&2
  exit 1
fi

architecture="$(dpkg --print-architecture)"
if [[ "${architecture}" != "amd64" ]]; then
  echo "Expected amd64/x86_64; detected ${architecture}." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get -y upgrade
apt-get install -y \
  ca-certificates \
  curl \
  fail2ban \
  git \
  jq \
  openssl \
  rsync \
  unattended-upgrades \
  ufw

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

systemctl enable --now docker
systemctl enable --now fail2ban

timedatectl set-timezone UTC

if [[ ! -f /swapfile ]]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

cat >/etc/sysctl.d/99-hamalivpn.conf <<'EOF'
vm.swappiness=10
vm.vfs_cache_pressure=50
net.ipv4.tcp_syncookies=1
net.ipv4.conf.all.rp_filter=1
net.ipv4.conf.default.rp_filter=1
EOF
sysctl --system >/dev/null

if ! id "${ADMIN_USER}" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "${ADMIN_USER}"
fi
usermod -aG sudo,docker "${ADMIN_USER}"

if [[ ! -s "${PUBLIC_KEY_FILE}" ]]; then
  echo "SSH public key is missing: ${PUBLIC_KEY_FILE}" >&2
  echo "Upload it before running this script." >&2
  exit 1
fi

admin_home="$(getent passwd "${ADMIN_USER}" | cut -d: -f6)"
install -d -m 0700 -o "${ADMIN_USER}" -g "${ADMIN_USER}" "${admin_home}/.ssh"
install -m 0600 -o "${ADMIN_USER}" -g "${ADMIN_USER}" \
  "${PUBLIC_KEY_FILE}" "${admin_home}/.ssh/authorized_keys"

cat >/etc/sudoers.d/90-hamalivpn-admin <<EOF
${ADMIN_USER} ALL=(ALL) NOPASSWD:ALL
EOF
chmod 0440 /etc/sudoers.d/90-hamalivpn-admin
visudo -cf /etc/sudoers.d/90-hamalivpn-admin >/dev/null

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

install -d -m 0750 /opt/hamalivpn
install -d -m 0750 /opt/hamalivpn/backups
install -d -m 0750 /opt/hamalivpn/monitoring

docker_version="$(docker --version)"
compose_version="$(docker compose version)"

echo
echo "Control server bootstrap completed."
echo "OS: ${PRETTY_NAME}"
echo "Architecture: ${architecture}"
echo "Docker: ${docker_version}"
echo "Compose: ${compose_version}"
echo "Admin user: ${ADMIN_USER}"
echo "Swap: $(swapon --show --noheadings --bytes | awk '{sum += $3} END {print sum + 0}') bytes"
echo
echo "Next:"
echo "1. Verify SSH access in a second terminal:"
echo "   ssh -i ~/.ssh/hamalivpn_control ${ADMIN_USER}@SERVER_IP"
echo "2. Run infra/harden-ssh.sh only after the new login works."
echo "3. Install Remnawave and Bedolaga."

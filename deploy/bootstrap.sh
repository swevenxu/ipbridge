#!/usr/bin/env bash
# One-shot install on a fresh Ubuntu/Debian VPS (tested path: Oracle free ARM,
# Hetzner, DigitalOcean, Lightsail). Puts the app on :80, plain HTTP, so both
# phones can open it — the 4S cannot do TLS at all.
#
#   HOST_PORT=80 bash bootstrap.sh
#
set -euo pipefail

REPO="${REPO:-https://github.com/swevenxu/ipbridge.git}"
DIR="${DIR:-/opt/ipbridge}"
HOST_PORT="${HOST_PORT:-80}"

[ "$(id -u)" -eq 0 ] || { echo "run as root (or via sudo)" >&2; exit 1; }

if ! command -v docker >/dev/null 2>&1; then
  echo "==> installing docker"
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl git
  curl -fsSL https://get.docker.com | sh
fi

if [ -d "$DIR/.git" ]; then
  echo "==> updating $DIR"
  git -C "$DIR" pull --ff-only
else
  echo "==> cloning into $DIR"
  git clone --depth 1 "$REPO" "$DIR"
fi

cd "$DIR"
printf 'HOST_PORT=%s\n' "$HOST_PORT" > .env

echo "==> building and starting"
docker compose up -d --build

# Plain HTTP on 80 is the whole point: no certificate for the 4S to reject.
if command -v ufw >/dev/null 2>&1; then
  ufw allow "$HOST_PORT"/tcp >/dev/null 2>&1 || true
fi

IP="$(curl -fsSL --max-time 5 https://api.ipify.org || echo '<server-ip>')"
echo
echo "Live at: http://$IP:$HOST_PORT/"
echo
echo "Reminder: this is cleartext and there is no login. Anyone who reaches the URL"
echo "sees the photos and can delete them."
echo "Also open port $HOST_PORT in your provider's firewall/security list (Oracle: Security List)."

#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/opt/voice-bridge
REPO_DIR=$(cd "$(dirname "$0")" && pwd)

echo "[1/6] Sync files"
mkdir -p "$APP_DIR"
rsync -av --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  "$REPO_DIR/" "$APP_DIR/"

echo "[2/6] Python venv"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "[3/6] Env file check"
if [[ ! -f /etc/voice-bridge.env ]]; then
  echo "Missing /etc/voice-bridge.env"
  exit 1
fi

echo "[4/6] Install systemd unit"
cp "$APP_DIR/deploy/systemd/voice-bridge.service" /etc/systemd/system/voice-bridge.service
systemctl daemon-reload
systemctl enable --now voice-bridge.service

echo "[5/6] Install nginx"
cp "$APP_DIR/deploy/nginx/voice-bridge-rate-limit.conf" /etc/nginx/conf.d/voice-bridge-rate-limit.conf
cp "$APP_DIR/deploy/nginx/voice-bridge.conf" /etc/nginx/sites-available/voice-bridge.conf
ln -sf /etc/nginx/sites-available/voice-bridge.conf /etc/nginx/sites-enabled/voice-bridge.conf
nginx -t
systemctl reload nginx

echo "[6/6] Done"
systemctl status --no-pager voice-bridge.service | sed -n '1,12p'

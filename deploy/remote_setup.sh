#!/usr/bin/env bash
# Runs ON the Lightsail instance. Installs deps, registers the server as a
# systemd service, and puts Caddy in front for automatic HTTPS.
set -euo pipefail
HOST="${1:?usage: remote_setup.sh <public-hostname>}"
APP=/opt/atlastra
sudo chown -R ubuntu:ubuntu "$APP"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip curl gnupg debian-keyring debian-archive-keyring apt-transport-https
cd "$APP"
python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
# server as a service (auto-restart, starts on boot)
sudo cp deploy/atlastra.service /etc/systemd/system/atlastra.service
sudo systemctl daemon-reload
sudo systemctl enable --now atlastra
# Caddy reverse proxy with auto Let's Encrypt TLS
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
sudo apt-get update -y
sudo apt-get install -y caddy
printf '%s {\n\treverse_proxy 127.0.0.1:8000\n}\n' "$HOST" | sudo tee /etc/caddy/Caddyfile >/dev/null
sudo systemctl restart caddy
sleep 3
echo "=== service status ==="; systemctl is-active atlastra caddy
echo "DONE -> https://$HOST"

#!/usr/bin/env bash
set -euo pipefail

BOT_USER="${1:-$USER}"
PROJECT_DIR="${2:-/home/${BOT_USER}/unciv_bot}"
SERVICE_NAME="unciv-bot"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ ! -d "${PROJECT_DIR}" ]]; then
  echo "[error] Project directory not found: ${PROJECT_DIR}" >&2
  exit 1
fi

if [[ ! -f "${PROJECT_DIR}/requirements.txt" ]]; then
  echo "[error] requirements.txt not found in ${PROJECT_DIR}" >&2
  exit 1
fi

echo "[1/5] Installing system packages"
sudo apt update
sudo apt install -y python3 python3-pip

echo "[2/5] Installing Python dependencies globally"
sudo python3 -m pip install --break-system-packages -r "${PROJECT_DIR}/requirements.txt"

echo "[3/5] Ensuring .env exists"
if [[ ! -f "${PROJECT_DIR}/.env" ]]; then
  if [[ -f "${PROJECT_DIR}/.env.example" ]]; then
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    echo "[info] Created ${PROJECT_DIR}/.env from .env.example"
    echo "[info] Edit the token before starting service: nano ${PROJECT_DIR}/.env"
  else
    echo "[error] .env missing and .env.example not found" >&2
    exit 1
  fi
fi

echo "[4/5] Writing systemd service"
sudo sed \
  -e "s|__BOT_USER__|${BOT_USER}|g" \
  -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
  "${PROJECT_DIR}/deploy/systemd/unciv-bot.service" | sudo tee "${SERVICE_PATH}" > /dev/null

echo "[5/5] Enabling and starting service"
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

echo "\n[done] Service status"
sudo systemctl --no-pager --full status "${SERVICE_NAME}"

echo "\n[next] Live logs"
echo "journalctl -u ${SERVICE_NAME} -f"

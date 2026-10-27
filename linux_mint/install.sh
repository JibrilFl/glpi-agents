#!/bin/bash

set -e
: "${GLPI_SERVER:?GLPI_SERVER is required}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[1] Установка пакетов"
sudo apt update
sudo apt install -y curl ca-certificates libfuse2

echo "[2] Распаковка агента"
cd "$SCRIPT_DIR"

if [ ! -f "glpi-agent-1.16-x86_64.AppImage" ]; then
  echo "Агент не найден!"
  exit 1
fi

sudo chmod +x glpi-agent-1.16-x86_64.AppImage

echo "[3] Установка агента"
sudo ./glpi-agent-1.16-x86_64.AppImage \
  --install \
  --server="$GLPI_SERVER" \
  --runnow

echo "[✔] Готово"

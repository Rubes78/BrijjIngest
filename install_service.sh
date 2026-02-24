#!/bin/bash
# Installs and enables the BrijjData systemd service.
# Run once as root: bash install_service.sh

set -e

SERVICE_NAME="brijjdata"
SERVICE_FILE="$(dirname "$(realpath "$0")")/brijjdata.service"
DEST="/etc/systemd/system/${SERVICE_NAME}.service"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: Please run as root (sudo bash install_service.sh)"
  exit 1
fi

echo "Copying service file to $DEST..."
cp "$SERVICE_FILE" "$DEST"

echo "Reloading systemd daemon..."
systemctl daemon-reload

echo "Enabling service (starts on boot)..."
systemctl enable "$SERVICE_NAME"

echo "Starting service..."
systemctl start "$SERVICE_NAME"

echo ""
systemctl status "$SERVICE_NAME" --no-pager
echo ""
echo "Done. BrijjData Config UI is running."
echo "Access it at http://$(hostname -I | awk '{print $1}'):5000"

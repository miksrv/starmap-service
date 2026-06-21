#!/usr/bin/env bash
#
# Start starmap-service and enable autostart on boot.

set -euo pipefail

SERVICE_NAME="starmap.service"

echo "→ $SERVICE_NAME"
sudo systemctl enable "$SERVICE_NAME" 2>/dev/null || true

if ! sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    sudo systemctl start "$SERVICE_NAME"
    echo "  started"
else
    echo "  already running"
fi

sudo systemctl --no-pager status "$SERVICE_NAME" --lines=5 || true
echo ""
echo "Logs: journalctl -u $SERVICE_NAME -f"

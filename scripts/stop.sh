#!/usr/bin/env bash
#
# Stop starmap-service and disable autostart.

set -euo pipefail

SERVICE_NAME="starmap.service"

echo "→ $SERVICE_NAME"
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    sudo systemctl stop "$SERVICE_NAME"
    echo "  stopped"
else
    echo "  already stopped"
fi

sudo systemctl disable "$SERVICE_NAME" 2>/dev/null || true
echo "  autostart disabled"

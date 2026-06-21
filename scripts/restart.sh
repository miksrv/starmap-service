#!/usr/bin/env bash
#
# Restart starmap-service (e.g. after a code update).

set -euo pipefail

SERVICE_NAME="starmap.service"

echo "→ restarting $SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
echo "  restarted"

sudo systemctl --no-pager status "$SERVICE_NAME" --lines=5 || true
echo ""
echo "Logs: journalctl -u $SERVICE_NAME -f"

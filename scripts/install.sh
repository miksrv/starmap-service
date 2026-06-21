#!/usr/bin/env bash
#
# Install starmap-service on a Linux host (Raspberry Pi target).
# Creates a venv, installs dependencies, and registers the systemd service
# with the current user and project path filled in.

set -euo pipefail

SERVICE_NAME="starmap.service"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CURRENT_USER="$(whoami)"

cd "$PROJECT_DIR"

# Checks
if [ ! -f "requirements.txt" ]; then
    echo "Error: requirements.txt not found!"
    exit 1
fi

# Step 1: Virtual environment
VENV_DIR="$PROJECT_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "Virtual environment created in $VENV_DIR"
else
    echo "Virtual environment already exists"
fi

# Step 2: Dependencies
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt
deactivate
echo "Dependencies installed"

# Step 3: starplot data catalogs (download if missing — large files are not in git)
if ! ls "$PROJECT_DIR"/data/stars*.parquet >/dev/null 2>&1; then
    echo "Star catalog not found — downloading starplot data..."
    bash "$PROJECT_DIR/scripts/fetch-data.sh"
else
    echo "starplot data catalogs already present in data/"
fi

# Step 4: Output directory (used when output.mode = file)
mkdir -p "$PROJECT_DIR/output"

# Step 5: Install the systemd unit, filling in user and project path
TMP_UNIT="$(mktemp)"
sed -e "s|__USER__|$CURRENT_USER|g" \
    -e "s|__WORKDIR__|$PROJECT_DIR|g" \
    "systemd/$SERVICE_NAME" > "$TMP_UNIT"
sudo cp "$TMP_UNIT" "/etc/systemd/system/$SERVICE_NAME"
rm -f "$TMP_UNIT"
echo "Service file installed to /etc/systemd/system/$SERVICE_NAME"

# Step 6: Reload systemd and start the service
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl --no-pager status "$SERVICE_NAME" --lines=5 || true

echo ""
echo "Installation complete! Check logs with:"
echo "  journalctl -u $SERVICE_NAME -f"

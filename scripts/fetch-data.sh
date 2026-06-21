#!/usr/bin/env bash
#
# Download starplot's data catalogs into ./data.
# starplot setup fetches the star/constellation/DSO catalogs, installs the
# DuckDB spatial extension, and builds the matplotlib font cache. It reads the
# target directory from STARPLOT_DATA_PATH (here pinned to ./data).
#
# Idempotent: re-running only fetches what is missing.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export STARPLOT_DATA_PATH="$PROJECT_DIR/data"
mkdir -p "$STARPLOT_DATA_PATH"

# Prefer the project venv's starplot CLI if it exists, otherwise the one on PATH.
if [ -x "$PROJECT_DIR/venv/bin/starplot" ]; then
    STARPLOT="$PROJECT_DIR/venv/bin/starplot"
else
    STARPLOT="starplot"
fi

echo "Downloading starplot catalogs into $STARPLOT_DATA_PATH ..."
"$STARPLOT" setup
echo "Done. Catalogs are in $STARPLOT_DATA_PATH"

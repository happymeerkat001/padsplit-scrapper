#!/usr/bin/env zsh
set -euo pipefail

WORKSPACE="/Users/leon/Documents/Code/padsplit-scraper"
VENV="$WORKSPACE/venv/bin/python3"

echo "[$(date)] Starting afternoon run"

echo "[$(date)] Running PadSplit scraper (messages only)..."
cd "$WORKSPACE/padsplit_scraper"
"$VENV" scraper.py --messages-only

echo "[$(date)] Afternoon run complete"

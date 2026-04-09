#!/usr/bin/env zsh
set -euo pipefail

WORKSPACE="/Users/leon/Documents/Code/padsplit-scraper"
VENV="$WORKSPACE/venv/bin/python3"

echo "[$(date)] Starting morning run"

echo "[$(date)] Running thermostat scraper..."
cd "$WORKSPACE/thermostat"
"$VENV" scraper.py

echo "[$(date)] Running PadSplit scraper (messages + tasks)..."
cd "$WORKSPACE/padsplit_scraper"
"$VENV" scraper.py

echo "[$(date)] Morning run complete"

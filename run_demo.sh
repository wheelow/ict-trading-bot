#!/usr/bin/env bash
# ICT Trading Bot — Quick Start
# Runs the bot in simulator mode (no MT5 needed) for a 100-step demo

set -e
cd "$(dirname "$0")"

# Create venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate venv and install deps
source .venv/bin/activate
echo "Installing dependencies..."
pip install -q -r requirements.txt 2>/dev/null

# Run the bot
echo ""
echo "===================================================="
echo "  ICT Trading Bot — Simulator Demo (100 steps)"
echo "===================================================="
echo ""
python main.py --steps 100 --fast

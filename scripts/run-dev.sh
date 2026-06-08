#!/usr/bin/env bash
# SnowLink – Linux / macOS development launcher
# Usage: bash scripts/run-dev.sh

set -e
cd "$(dirname "$0")/.."

# ── Virtual environment ───────────────────────────────────────────────────
if [ ! -f "venv/bin/python" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# ── Install backend dependencies ──────────────────────────────────────────
echo "Installing backend dependencies..."
pip install -r backend/requirements.txt --quiet

# ── Start server ──────────────────────────────────────────────────────────
echo ""
echo "Starting SnowLink at http://127.0.0.1:8000"
echo "Press Ctrl+C to stop."
echo ""

python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

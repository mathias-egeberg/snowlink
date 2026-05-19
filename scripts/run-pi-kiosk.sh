#!/usr/bin/env bash
# SnowLink – Raspberry Pi kiosk launcher
#
# Starts the FastAPI backend (uvicorn) then opens Chromium in kiosk mode.
# Intended for autostart / systemd use.  See install-pi.sh.
#
# Usage: bash scripts/run-pi-kiosk.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

source venv/bin/activate

# ── Backend ───────────────────────────────────────────────────────────────
echo "[kiosk] Starting SnowLink backend..."
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!
echo "[kiosk] Backend PID: $BACKEND_PID"

# Wait for backend to be ready.
for i in $(seq 1 20); do
    if curl -s -o /dev/null http://127.0.0.1:8000/; then
        echo "[kiosk] Backend is up."
        break
    fi
    sleep 0.5
done

# ── Chromium kiosk ────────────────────────────────────────────────────────
echo "[kiosk] Launching Chromium..."
chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --disable-features=Translate \
    --no-first-run \
    --app=http://127.0.0.1:8000 \
    &

CHROMIUM_PID=$!
echo "[kiosk] Chromium PID: $CHROMIUM_PID"

# Keep running until either process exits.
wait -n $BACKEND_PID $CHROMIUM_PID

# Shut both down on exit.
kill $BACKEND_PID  2>/dev/null || true
kill $CHROMIUM_PID 2>/dev/null || true

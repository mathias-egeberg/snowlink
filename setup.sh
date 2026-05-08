#!/usr/bin/env bash
# ============================================================
# SnowLink – Raspberry Pi setup script
# Run once: chmod +x setup.sh && ./setup.sh
# ============================================================
set -e

echo ">>> Updating system packages..."
sudo apt-get update -y

# Map runtime: pywebview backed by WebKitGTK. xdotool reparents the webview's
# top-level X11 window into the Kivy SDL2 window as a strict (override-redirect)
# child so it cannot be dragged or detached.
BASE_PACKAGES=(
    make build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
    libsqlite3-dev wget curl llvm libncursesw5-dev xz-utils tk-dev \
    libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev \
    libportmidi-dev libswscale-dev libavformat-dev libavcodec-dev \
    zlib1g-dev libgstreamer1.0-dev gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good libmtdev-dev xclip xsel \
    xdotool wmctrl \
    pkg-config libgirepository1.0-dev libcairo2-dev \
    gir1.2-gtk-3.0 \
)

echo ">>> Installing base packages..."
sudo apt-get install -y "${BASE_PACKAGES[@]}"

# WebKitGTK introspection bindings: prefer 4.1 (Trixie/Bookworm), fall back to
# 4.0 (Bullseye). pywebview auto-detects whichever is present at runtime.
echo ">>> Installing WebKitGTK introspection..."
sudo apt-get install -y gir1.2-webkit2-4.1 \
    || sudo apt-get install -y gir1.2-webkit2-4.0 \
    || {
        echo "!!! Could not install gir1.2-webkit2-4.1 or 4.0."
        echo "    pywebview will not be able to render the map."
        exit 1
    }

# ── Ensure Python 3.11 via pyenv (Kivy requires <3.13) ───────────────────────
PYTHON_VERSION="3.11.9"
export PYENV_ROOT="$HOME/.pyenv"
if [ ! -d "$PYENV_ROOT" ]; then
    echo ">>> Installing pyenv..."
    curl https://pyenv.run | bash
fi
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"

if ! pyenv versions | grep -q "$PYTHON_VERSION"; then
    echo ">>> Installing Python $PYTHON_VERSION (this may take a few minutes)..."
    pyenv install "$PYTHON_VERSION"
fi
pyenv local "$PYTHON_VERSION"
echo ">>> Using $(python --version)"

echo ">>> Creating Python virtual environment (Python $PYTHON_VERSION)..."
python -m venv venv
source venv/bin/activate

echo ">>> Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements-pi.txt

# Sanity check: xdotool must be on PATH for the map to embed as a strict child.
command -v xdotool >/dev/null 2>&1 || {
    echo "!!! xdotool not found on PATH. Map cannot be embedded."
    exit 1
}

# ── Shared .desktop content ──────────────────────────────────────────────────
SNOWLINK_DIR="$(pwd)"
DESKTOP_CONTENT="[Desktop Entry]
Version=1.0
Type=Application
Name=SnowLink
Comment=Snow and ice management system
Icon=$SNOWLINK_DIR/assets/snowlink.svg
Exec=/bin/bash -c 'cd $SNOWLINK_DIR && source venv/bin/activate && SNOWLINK_FULLSCREEN=1 python main.py'
Terminal=false
StartupNotify=false
Categories=Utility;"

echo ">>> Creating autostart entry..."
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
echo "$DESKTOP_CONTENT" > "$AUTOSTART_DIR/snowlink.desktop"

echo ">>> Creating desktop icon..."
DESKTOP_DIR="$HOME/Desktop"
mkdir -p "$DESKTOP_DIR"
echo "$DESKTOP_CONTENT" > "$DESKTOP_DIR/snowlink.desktop"
chmod +x "$DESKTOP_DIR/snowlink.desktop"

# Trust the icon on GNOME (silently ignores failure on non-GNOME)
gio set "$DESKTOP_DIR/snowlink.desktop" metadata::trusted true 2>/dev/null || true

echo ""
echo "✅ Setup complete!"
echo "   Start manually : source venv/bin/activate && SNOWLINK_FULLSCREEN=1 python main.py"
echo "   Autostart added: $AUTOSTART_DIR/snowlink.desktop"
echo "   Desktop icon  : $DESKTOP_DIR/snowlink.desktop"

#!/usr/bin/env bash
# ============================================================
# SnowLink – Raspberry Pi setup script
# Run once: chmod +x setup.sh && ./setup.sh
# ============================================================
set -e

has_apt_package() {
    apt-cache show "$1" >/dev/null 2>&1
}

first_available_package() {
    for package in "$@"; do
        if has_apt_package "$package"; then
            printf '%s\n' "$package"
            return 0
        fi
    done
    return 1
}

echo ">>> Updating system packages..."
sudo apt-get update -y

BASE_PACKAGES=(
    make build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
    libsqlite3-dev wget curl llvm libncursesw5-dev xz-utils tk-dev \
    libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev \
    libportmidi-dev libswscale-dev libavformat-dev libavcodec-dev \
    zlib1g-dev libgstreamer1.0-dev gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good libmtdev-dev xclip xsel \
    python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
)

BROWSER_PACKAGE="$(first_available_package chromium chromium-browser || true)"
WEBKIT_GIR_PACKAGE="$(first_available_package gir1.2-webkit2-4.1 gir1.2-webkit2-4.0 || true)"
WEBKIT_DEV_PACKAGE="$(first_available_package libwebkit2gtk-4.1-dev libwebkit2gtk-4.0-dev || true)"

if [ -z "$BROWSER_PACKAGE" ]; then
    echo ">>> Could not find a Chromium package in apt repositories."
    echo "    Expected one of: chromium, chromium-browser"
    exit 1
fi

if [ -z "$WEBKIT_GIR_PACKAGE" ]; then
    echo ">>> Could not find a WebKit GTK GIR package in apt repositories."
    echo "    Expected one of: gir1.2-webkit2-4.1, gir1.2-webkit2-4.0"
    exit 1
fi

EXTRA_PACKAGES=("$BROWSER_PACKAGE" "$WEBKIT_GIR_PACKAGE")
if [ -n "$WEBKIT_DEV_PACKAGE" ]; then
    EXTRA_PACKAGES+=("$WEBKIT_DEV_PACKAGE")
fi

echo ">>> Installing browser/runtime packages: $BROWSER_PACKAGE $WEBKIT_GIR_PACKAGE${WEBKIT_DEV_PACKAGE:+ $WEBKIT_DEV_PACKAGE}"
sudo apt-get install -y "${BASE_PACKAGES[@]}" "${EXTRA_PACKAGES[@]}"

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

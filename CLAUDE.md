# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**SnowLink** is a snow management dashboard for monitoring and controlling an automated snowcat vehicle. The repository now contains two UI surfaces:

- **Current web UI**: FastAPI backend with a plain HTML/CSS/JS frontend, designed for Chromium kiosk use on Raspberry Pi. See [README-web.md](README-web.md) before working in `backend/`, `frontend/`, or `scripts/`.
- **Legacy Kivy UI**: Original touchscreen dashboard for a 10" 1280×800 Raspberry Pi display, preserved in `app/`, `kv/`, and `main.py`.

## Running the Application

### Web UI

Use [README-web.md](README-web.md) as the source of truth for web development, API routes, state fields, and kiosk scripts.

```powershell
# Windows development
pip install -r backend/requirements.txt
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

# Or use the script:
.\scripts\run-dev.ps1
```

Open `http://127.0.0.1:8000` in Chrome/Edge. No Raspberry Pi hardware is required for local development; hardware services fail gracefully and sensor data is simulated.

```bash
# Linux / macOS development
bash scripts/run-dev.sh

# Raspberry Pi kiosk
bash scripts/install-pi.sh
bash scripts/run-pi-kiosk.sh
```

### Legacy Kivy UI

```bash
# Windows development
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

```bash
# Raspberry Pi (automated)
chmod +x setup.sh
./setup.sh

# Pi manual start (fullscreen)
source venv/bin/activate
SNOWLINK_FULLSCREEN=1 python main.py
```

`SNOWLINK_FULLSCREEN=1` enables fullscreen mode (set automatically on Pi, off by default on Windows).

## Architecture

### Web UI Architecture

The web UI is documented in [README-web.md](README-web.md). Its main structure is:

```
backend/
|-- main.py              # FastAPI app serving frontend, REST API, and WebSocket state
|-- state.py             # AppState dataclass and thread-safe StateManager
|-- api/                 # State, settings, control, and recording routes
`-- services/            # Simulation, GPS, IMU, NTRIP, recording, settings, and self-tests

frontend/
|-- index.html           # Single-page app shell
`-- src/                 # Plain JS modules for state, API, WebSocket, nav, screens, and map

scripts/                 # Windows, Linux/macOS, and Raspberry Pi kiosk launchers
```

For web changes, keep the frontend dependency-free unless there is a deliberate reason to add tooling. The backend serves the SPA at `/`, exposes REST routes under `/api/*`, and streams live state on `/ws/state`.

### Legacy Kivy Architecture

### Entry Point & Layout Loading

`main.py` configures the Kivy window to 1280×800, loads all 9 `.kv` files in order, then instantiates `RootWidget`. KV files must be loaded before their corresponding Python classes are instantiated.

### Layer Structure

```
main.py
└── RootWidget (BoxLayout)
    ├── ScreenManager → 4 screens
    └── NavBar (bottom tabs)

app/
├── screens/      # Full-page views
├── widgets/      # Reusable UI components
├── services/     # Data/state layer
└── theme.py      # Color palette, font sizes, app metadata

kv/               # Kivy DSL layout files (mirror of app/)
assets/           # Static assets (3D model file, not used at runtime)
```

### Data Flow

`DataService` (`app/services/data_service.py`) is a **Kivy EventDispatcher singleton** holding all live sensor and device state. Screens bind to its `NumericProperty`/`StringProperty`/`BooleanProperty` fields on `on_enter` and unbind on `on_leave`.

- Sensor state is currently **simulated** via a `Clock.schedule_interval` every 5s — replace with real GPIO/I2C calls on Pi.
- `DataService.toggle_device(name)` updates state and logs the event.
- All screen-to-data coupling goes through DataService; screens never talk to each other directly.

### Map Screen

`app/screens/map_screen.py` mirrors the Raven .NET approach exactly:
- Generates a **MapLibre GL JS** HTML page at runtime (same tile sources as `Raven_/Desktop/appsettings.desktop.json`)
- Launches **Chromium** (Raspberry Pi) or **Chrome/Edge** (Windows) as a subprocess positioned over the map area in the Kivy window
- MapLibre provides: Kartverket topo tiles, 3D terrain via AWS Terrarium DEM, hillshade, 55° initial pitch, full pan/tilt/zoom/rotate, topo↔aerial toggle, snowcat marker
- `MapView.start()` / `MapView.stop()` manage the browser subprocess lifecycle on screen enter/leave
- The Kivy `MapView` widget is just a dark placeholder; the browser sits on top of it
- Requires Chrome or Chromium to be installed; `setup.sh` ensures `chromium-browser` is present on Pi

### Theme

All colors, font sizes, and app metadata (name, version) are defined in `app/theme.py`. Do not hardcode palette values elsewhere.

## KV File Loading Order

`main.py` loads KV files in this order (order matters for widget class resolution):
1. `kv/theme.kv`
2. `kv/status_card.kv`
3. `kv/device_toggle.kv`
4. `kv/conn_indicator.kv`
5. `kv/nav_bar.kv`
6. `kv/dashboard.kv`
7. `kv/map.kv`
8. `kv/control.kv`
9. `kv/settings.kv`
10. `kv/root.kv`

## Raspberry Pi Deployment

`setup.sh` handles full Pi setup: installs SDL2/GStreamer system dependencies, installs Python 3.11 via pyenv (Kivy requires Python <3.13), creates a venv, installs `requirements-pi.txt`, and creates GNOME autostart desktop entries.

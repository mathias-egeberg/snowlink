# SnowLink – Web UI

Chromium-kiosk-ready local web app replacing the Kivy GUI.  
FastAPI backend · plain HTML/CSS/JS frontend · MapLibre GL + Three.js map.

---

## Quick start – Windows development

```powershell
# 1. Install backend deps (once)
pip install -r backend/requirements.txt

# 2. Start the server
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000

# Or use the script:
.\scripts\run-dev.ps1
```

Open **http://127.0.0.1:8000** in Chrome/Edge.  
No Raspberry Pi hardware required – services fail gracefully, sensor data is simulated.

---

## Quick start – Linux / macOS

```bash
bash scripts/run-dev.sh
```

---

## Raspberry Pi kiosk

```bash
# Install everything (once)
bash scripts/install-pi.sh

# Run kiosk manually
bash scripts/run-pi-kiosk.sh
```

`install-pi.sh` offers to create a systemd service that autostarts on boot.

---

## Architecture

```
snowlink/
  backend/
    main.py                     FastAPI app – serves frontend, WS, REST
    state.py                    AppState dataclass + thread-safe StateManager
    services/
      data_service.py           Coordinates simulation and hardware services
      gps_service.py            USB ZED-F9P polling (no Kivy)
      imu_service.py            USB WheelTech N100 polling (no Kivy)
      imu_heading.py            Pure yaw-math helpers (copied from legacy)
      settings_service.py       JSON settings persistence
    api/
      routes_state.py           GET  /api/state
      routes_settings.py        GET/POST /api/settings, POST /api/settings/calibrate-imu
      routes_control.py         POST /api/control/device

  frontend/
    index.html                  SPA shell – all four screens in one file
    src/
      styles.css                Theme (matches app/theme.py colours)
      state.js                  Client-side state store
      api.js                    REST helper functions
      websocket.js              WS auto-reconnect manager
      nav.js                    Tab navigation + screenchange event
      dashboard.js              Dashboard screen
      controls.js               Device control screen
      settings.js               Settings screen
      map.js                    MapLibre + Three.js map (ES module)
      lidar.js                  LiDAR placeholder (TODO)

  scripts/
    run-dev.ps1                 Windows dev launcher
    run-dev.sh                  Linux/macOS dev launcher
    run-pi-kiosk.sh             Pi: start backend + Chromium kiosk
    install-pi.sh               Pi: install deps + optional systemd

  assets/                       GLB model, PNG marker, SVG logo (unchanged)
  app/                          Legacy Kivy app (preserved, not deleted)
  tests/                        All tests pass (27 total)
```

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Frontend SPA |
| GET | `/api/state` | Full state snapshot |
| GET | `/api/settings` | Persisted settings |
| POST | `/api/settings` | Update settings |
| POST | `/api/settings/calibrate-imu` | Set current IMU yaw as zero |
| POST | `/api/control/device` | Toggle device `{"device_key":"heat_roof_on"}` |
| WS | `/ws/state` | Live state at ~5 Hz |

---

## State fields broadcast via WebSocket

All fields from `backend/state.py::AppState`:

- Sensors: `temperature_outside`, `temperature_roof`, `snow_depth_cm`, `humidity_pct`, `wind_speed_ms`, `power_watts`
- System: `system_status`, `last_event`, `active_devices`
- Devices: `heat_roof_on`, `heat_gutter_on`, `pump_on`, `sensor_light_on`
- GPS: `gps_ok`, `gps_float_rtk`, `gps_status_text`, `gps_lat`, `gps_lon`, `gps_altitude_m`, `gps_fix_quality`, `gps_satellites`, `gps_hdop`
- IMU: `imu_ok`, `imu_yaw_deg`, `imu_yaw_valid`, `imu_heading_deg`, `imu_heading_calibrated`
- NTRIP: `ntrip_ok`, `ntrip_status_text`
- Map: `marker_style`, `imu_heading_enabled`
- LiDAR (placeholder): `lidar_available`, `lidar_point_count`

---

## Legacy Kivy app

The original Kivy files are **untouched** in `app/`, `kv/`, and `main.py`.  
Run the old app with:

```bash
pip install -r requirements.txt
python main.py
```

/**
 * Map screen – MapLibre GL JS + Three.js custom layer.
 *
 * Ported from app/screens/map_screen.py.
 * Features:
 *   - Kartverket topo tiles + Esri aerial toggle
 *   - Snowcat 3-D GLB model rendered via Three.js custom layer sharing
 *     MapLibre's WebGL context
 *   - GPS dot fallback marker
 *   - Live position + bearing updates from WebSocket state
 *   - Marker style switching (snowcat / dot) driven by settings
 *
 * LiDAR placeholder:
 *   Future LiDAR point-cloud overlay can be added as another custom layer
 *   keyed 'lidar-layer'.  The state fields `lidar_available` and
 *   `lidar_point_count` are already broadcast by the backend.
 *   TODO: implement LiDAR overlay when hardware is available.
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

// ── Tile sources (identical to Raven appsettings.desktop.json) ────────────
const TOPO_TILES   = 'https://cache.kartverket.no/v1/wmts/1.0.0/topo/default/webmercator/{z}/{y}/{x}.png';
const AERIAL_TILES = 'https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';

// ── Default position (used when GPS is unavailable) ───────────────────────
const DEFAULT_LAT = 60.0137563;
const DEFAULT_LON = 11.0196226;

// ── Real-world scale factor for the snowcat model ─────────────────────────
// Model extents: ~375 mm × 214 mm × 162 mm native units.
// Real snowcat ≈ 8 m long → world scale = 8000/375 ≈ 21.3
const MODEL_SCALE_FACTOR = 21.3;

// ── State ─────────────────────────────────────────────────────────────────
let _map        = null;
let _dotMarker  = null;
let _showSnowcat = true;
let _modelLat   = DEFAULT_LAT;
let _modelLon   = DEFAULT_LON;
let _modelBearing = 0.0;
let _lastBearing  = null;
let _lastBearingTime = 0;
const HEADING_MIN_INTERVAL = 100;  // ms
const HEADING_MIN_DELTA    = 0.5;  // degrees

// ── Shortest angular delta ─────────────────────────────────────────────────
function shortestDelta(a, b) {
  return Math.abs(((a - b + 180) % 360) - 180);
}

// ── Mercator transform helpers ─────────────────────────────────────────────
function mercatorTransform(lat, lon, bearingDeg) {
  const coord = maplibregl.MercatorCoordinate.fromLngLat([lon, lat], 0);
  const mpu   = coord.meterInMercatorCoordinateUnits();
  return {
    tx: coord.x, ty: coord.y, tz: coord.z,
    scale:   (mpu / 1000) * MODEL_SCALE_FACTOR,
    bearing: bearingDeg,
  };
}

// ── Three.js custom layer ──────────────────────────────────────────────────
let _modelLoaded = false;

const snowcatLayer = {
  id: 'snowcat-3d',
  type: 'custom',
  renderingMode: '3d',

  onAdd(map, gl) {
    this._map    = map;
    this.camera  = new THREE.Camera();
    this.scene   = new THREE.Scene();

    this.scene.add(new THREE.AmbientLight(0xffffff, 0.8));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(1, -1, 2).normalize();
    this.scene.add(sun);
    const fill = new THREE.DirectionalLight(0xffffff, 0.5);
    fill.position.set(-1, 1, 0.5).normalize();
    this.scene.add(fill);

    const loader = new GLTFLoader();
    fetch('/assets/snowcat.glb')
      .then(r => r.arrayBuffer())
      .then(buf => {
        loader.parse(buf, '', (gltf) => {
          gltf.scene.traverse((child) => {
            if (child.isMesh && child.geometry.attributes.color) {
              const mats = Array.isArray(child.material)
                ? child.material : [child.material];
              mats.forEach(m => { m.vertexColors = true; m.needsUpdate = true; });
            }
          });
          this.scene.add(gltf.scene);
          _modelLoaded = true;
          map.triggerRepaint();
        }, (err) => console.error('[snowcat-3d] parse error:', err));
      })
      .catch(err => console.error('[snowcat-3d] fetch error:', err));

    // Share MapLibre's WebGL context — use powerPreference hint to avoid
    // context loss on low-end GPU (Raspberry Pi VideoCore).
    this.renderer = new THREE.WebGLRenderer({
      canvas: map.getCanvas(),
      context: gl,
      antialias: true,
      powerPreference: 'low-power',
    });
    this.renderer.autoClear = false;
  },

  render(gl, args) {
    // Skip rendering until the model is loaded — prevents empty Three.js
    // render passes from corrupting MapLibre's WebGL state on every frame.
    if (!_showSnowcat || !_modelLoaded) return;

    const t = mercatorTransform(_modelLat, _modelLon, _modelBearing);

    const rotZ_init = new THREE.Matrix4().makeRotationAxis(
      new THREE.Vector3(0, 0, 1), -Math.PI / 2
    );
    const rotZ = new THREE.Matrix4().makeRotationAxis(
      new THREE.Vector3(0, 0, 1), -t.bearing * Math.PI / 180
    );

    // Support MapLibre 4.x (args.defaultProjectionData.mainMatrix) and older.
    const rawMatrix = (args && args.defaultProjectionData)
      ? args.defaultProjectionData.mainMatrix
      : args;
    const proj  = new THREE.Matrix4().fromArray(rawMatrix);
    const local = new THREE.Matrix4()
      .makeTranslation(t.tx, t.ty, t.tz)
      .scale(new THREE.Vector3(t.scale, -t.scale, t.scale))
      .multiply(rotZ)
      .multiply(rotZ_init);

    this.camera.projectionMatrix = proj.multiply(local);
    this.renderer.resetState();
    this.renderer.render(this.scene, this.camera);
    // triggerRepaint() removed from here — the model is static between GPS
    // updates; re-render is triggered by moveMarker() and setMarkerStyle().
  },
};

// ── Marker style API ───────────────────────────────────────────────────────
function setMarkerStyle(style) {
  _showSnowcat = (style === 'snowcat');
  if (_dotMarker) {
    _dotMarker.getElement().style.display = (style === 'dot') ? '' : 'none';
  }
  if (_map) _map.triggerRepaint();
}

// ── Move marker + camera ───────────────────────────────────────────────────
function moveMarker(lat, lon, bearing) {
  _modelLat     = lat;
  _modelLon     = lon;
  _modelBearing = bearing;
  if (_dotMarker) _dotMarker.setLngLat([lon, lat]);
  if (_map) {
    _map.easeTo({ center: [lon, lat], bearing, duration: 120 });
    _map.triggerRepaint();
  }
}

// ── Basemap toggle ─────────────────────────────────────────────────────────
function setBasemap(name) {
  if (!_map) return;
  _map.setLayoutProperty('topo-layer',   'visibility', name === 'topo'   ? 'visible' : 'none');
  _map.setLayoutProperty('aerial-layer', 'visibility', name === 'aerial' ? 'visible' : 'none');
  document.getElementById('btn-topo')  ?.classList.toggle('active', name === 'topo');
  document.getElementById('btn-aerial')?.classList.toggle('active', name === 'aerial');
}

// ── Map initialisation ─────────────────────────────────────────────────────
let _mapInited = false;

function initMap(initialState) {
  if (_mapInited) return;
  _mapInited = true;

  const lat = initialState.gps_lat ?? DEFAULT_LAT;
  const lon = initialState.gps_lon ?? DEFAULT_LON;
  const bearing = _imuBearing(initialState);
  const style    = initialState.marker_style ?? 'snowcat';

  _modelLat     = lat;
  _modelLon     = lon;
  _modelBearing = bearing;

  _map = new maplibregl.Map({
    container: 'map',
    center: [lon, lat],
    zoom: 17,
    pitch: 55,
    bearing,
    maxPitch: 85,
    touchZoomRotate: true,
    dragRotate: true,
    style: {
      version: 8,
      sources: {
        topo: {
          type: 'raster',
          tiles: [TOPO_TILES],
          tileSize: 256, maxzoom: 18,
          attribution: '© Kartverket',
        },
        aerial: {
          type: 'raster',
          tiles: [AERIAL_TILES],
          tileSize: 256, maxzoom: 18,
          attribution: '© Esri',
        },
      },
      layers: [
        { id: 'topo-layer',   type: 'raster', source: 'topo',
          layout: { visibility: 'visible' } },
        { id: 'aerial-layer', type: 'raster', source: 'aerial',
          layout: { visibility: 'none' } },
      ],
    },
  });

  _map.addControl(
    new maplibregl.NavigationControl({ visualizePitch: true }),
    'bottom-right',
  );

  _map.on('load', () => {
    _map.addLayer(snowcatLayer);

    // GPS dot fallback
    const dotEl = document.createElement('div');
    dotEl.style.cssText = [
      'width:20px', 'height:20px', 'border-radius:50%',
      'background:#00b4d8', 'border:3px solid #fff',
      'box-shadow:0 0 12px rgba(0,180,216,0.9)',
    ].join(';');
    _dotMarker = new maplibregl.Marker({ element: dotEl, occludedOpacity: 1 })
      .setLngLat([lon, lat])
      .addTo(_map);

    setMarkerStyle(style);
    _map.triggerRepaint();
  });

  // Inject the basemap toggle buttons into the map container.
  _injectBasemapToggle();
}

function _injectBasemapToggle() {
  const container = document.getElementById('map-container');
  if (!container || container.querySelector('#btn-topo')) return;
  const wrap = document.createElement('div');
  wrap.id = 'basemap-toggle';
  wrap.style.cssText = 'position:absolute;top:10px;right:10px;z-index:10;display:flex;gap:6px';

  const mkBtn = (id, label, active) => {
    const b = document.createElement('button');
    b.id = id;
    b.textContent = label;
    b.className = active ? 'map-btn active' : 'map-btn';
    b.style.cssText = [
      'background:rgba(13,24,41,0.88)', 'color:#e2e8f0',
      'border:1.5px solid #00b4d8', 'border-radius:6px',
      'padding:8px 18px', 'font-size:14px', 'font-family:sans-serif',
      'cursor:pointer', 'touch-action:manipulation', 'user-select:none',
    ].join(';');
    b.addEventListener('click', () => setBasemap(label.toLowerCase()));
    return b;
  };
  wrap.appendChild(mkBtn('btn-topo',   'Topo',   true));
  wrap.appendChild(mkBtn('btn-aerial', 'Aerial', false));

  // Active style
  const style = document.createElement('style');
  style.textContent = `
    #basemap-toggle .map-btn.active {
      background: #00b4d8 !important;
      color: #0d1829 !important;
      font-weight: 700;
    }
  `;
  document.head.appendChild(style);
  container.appendChild(wrap);
}

// ── IMU heading helper ─────────────────────────────────────────────────────
function _imuBearing(s) {
  if (
    s.imu_heading_enabled &&
    s.imu_ok &&
    s.imu_yaw_valid &&
    s.imu_heading_calibrated
  ) {
    return s.imu_heading_deg ?? 0;
  }
  return 0;
}

// ── WebSocket state handler ────────────────────────────────────────────────
function _onState(s) {
  if (!_map) return;

  // Marker style (controlled via settings).
  const style = s.marker_style ?? 'snowcat';
  setMarkerStyle(style);

  // Update GPS badge on the map header.
  const gpsTxt = document.getElementById('map-gps-text');
  if (gpsTxt) {
    gpsTxt.textContent = s.gps_status_text ?? 'No Fix';
    const cls = s.gps_ok ? 'ok' : (s.gps_float_rtk ? 'warning' : 'danger');
    gpsTxt.className = `badge-value ${cls}`;
  }
  _setConn('ind-map-5g',  s.cellular_ok);
  _setConn('ind-map-gps', s.gps_ok, s.gps_float_rtk);
  _setConn('ind-map-imu', s.imu_ok);

  // Heading update with throttling (mirrors _apply_imu_heading logic).
  const bearing = _imuBearing(s);
  const now     = Date.now();
  const forcible = _lastBearing === null;

  if (!forcible) {
    if (now - _lastBearingTime < HEADING_MIN_INTERVAL) return;
    if (shortestDelta(bearing, _lastBearing) < HEADING_MIN_DELTA) return;
  }

  const lat = (s.gps_ok || s.gps_float_rtk) ? (s.gps_lat ?? DEFAULT_LAT) : DEFAULT_LAT;
  const lon = (s.gps_ok || s.gps_float_rtk) ? (s.gps_lon ?? DEFAULT_LON) : DEFAULT_LON;

  moveMarker(lat, lon, bearing);
  _lastBearing = bearing;
  _lastBearingTime = now;
}

function _setConn(id, ok, warn = false) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('ok',      ok && !warn);
  el.classList.toggle('warning', !ok && warn);
}

// ── Map clock ──────────────────────────────────────────────────────────────
let _clockTimer = null;
function _startClock() {
  function tick() {
    const now = new Date();
    const te  = document.getElementById('map-time');
    const de  = document.getElementById('map-date');
    if (te) te.textContent = now.toLocaleTimeString('en-GB', { hour12: false });
    if (de) de.textContent = now.toLocaleDateString('en-GB', {
      weekday: 'long', day: '2-digit', month: 'short', year: 'numeric',
    });
  }
  tick();
  _clockTimer = setInterval(tick, 1000);
}
function _stopClock() {
  clearInterval(_clockTimer);
  _clockTimer = null;
}

// ── Screen visibility integration ─────────────────────────────────────────
document.addEventListener('screenchange', (e) => {
  if (e.detail === 'map') {
    _startClock();
    const s = AppState.get();
    if (!_mapInited && s && Object.keys(s).length > 0) {
      initMap(s);
    } else if (_map) {
      _map.resize();
    }
    if (s && _map) _onState(s);
  } else {
    _stopClock();
  }
});

// Re-render on every state update while map screen is visible.
AppState.subscribe((s) => {
  if (!document.getElementById('screen-map')?.classList.contains('active')) return;
  if (!_mapInited) {
    initMap(s);
  }
  _onState(s);
});

// ── LiDAR placeholder ──────────────────────────────────────────────────────
// TODO: When LiDAR hardware is available, implement a custom MapLibre layer
// here that reads from state.lidar_available and state.lidar_point_count.
// The backend already exposes these fields via /api/state and /ws/state.
// Suggested approach:
//   1. Backend POSTs LiDAR point data to a new /api/lidar/points endpoint
//      or streams it over a dedicated /ws/lidar WebSocket.
//   2. Frontend renders a THREE.Points object inside a second custom layer
//      keyed 'lidar-layer', added after 'snowcat-3d'.

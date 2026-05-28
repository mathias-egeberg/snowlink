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
const HEADING_SMOOTHING_RATE = 18.0;
const HEADING_EPSILON_DEG = 0.05;

// ── State ─────────────────────────────────────────────────────────────────
let _map        = null;
let _dotMarker  = null;
let _showSnowcat = true;
let _modelLat   = DEFAULT_LAT;
let _modelLon   = DEFAULT_LON;
let _modelBearing = 0.0;
let _targetBearing = 0.0;
let _displayBearing = 0.0;
let _lastValidBearing = 0.0;
let _headingAnimationId = null;
let _lastHeadingFrameAt = null;
let _headingCameraControlled = false;
let _releaseCameraAfterHeadingAnimation = false;
let _prevImuReady = false;

function normalizeBearing(degrees) {
  return (degrees % 360 + 360) % 360;
}

function signedBearingDelta(fromDeg, toDeg) {
  return ((toDeg - fromDeg + 540) % 360) - 180;
}

function shortestBearingDelta(firstDeg, secondDeg) {
  return Math.abs(signedBearingDelta(secondDeg, firstDeg));
}

function _applyDisplayedBearing(bearing) {
  _displayBearing = normalizeBearing(bearing);
  _modelBearing = _displayBearing;
  if (_headingCameraControlled && _map) {
    _map.setBearing(_displayBearing);
  }
  if (_map) _map.triggerRepaint();
}

function _finishHeadingAnimation() {
  if (_headingAnimationId !== null) {
    cancelAnimationFrame(_headingAnimationId);
  }
  if (_releaseCameraAfterHeadingAnimation) {
    _headingCameraControlled = false;
    _releaseCameraAfterHeadingAnimation = false;
  }
  _headingAnimationId = null;
  _lastHeadingFrameAt = null;
}

function _cancelHeadingAnimationForUser() {
  if (_headingAnimationId !== null) {
    cancelAnimationFrame(_headingAnimationId);
  }
  _headingAnimationId = null;
  _lastHeadingFrameAt = null;
  _headingCameraControlled = false;
  _releaseCameraAfterHeadingAnimation = false;
}

function _animateHeading(timestamp) {
  if (!_map) {
    _finishHeadingAnimation();
    return;
  }

  if (_lastHeadingFrameAt === null) {
    _lastHeadingFrameAt = timestamp;
    _headingAnimationId = requestAnimationFrame(_animateHeading);
    return;
  }

  const delta = signedBearingDelta(_displayBearing, _targetBearing);
  if (Math.abs(delta) <= HEADING_EPSILON_DEG) {
    _applyDisplayedBearing(_targetBearing);
    _finishHeadingAnimation();
    return;
  }

  // Cap frame delta so tab focus changes do not create a huge bearing jump.
  const elapsedSeconds = Math.min((timestamp - _lastHeadingFrameAt) / 1000, 0.1);
  _lastHeadingFrameAt = timestamp;
  const step = 1 - Math.exp(-HEADING_SMOOTHING_RATE * elapsedSeconds);
  _applyDisplayedBearing(_displayBearing + delta * step);
  _headingAnimationId = requestAnimationFrame(_animateHeading);
}

function _setHeadingTarget(bearing, controlCamera, releaseCameraWhenDone = false) {
  _targetBearing = normalizeBearing(bearing);
  _headingCameraControlled = controlCamera;
  _releaseCameraAfterHeadingAnimation = releaseCameraWhenDone;

  if (shortestBearingDelta(_displayBearing, _targetBearing) <= HEADING_EPSILON_DEG) {
    _applyDisplayedBearing(_targetBearing);
    _finishHeadingAnimation();
    return;
  }

  if (_headingAnimationId === null) {
    _lastHeadingFrameAt = null;
    _headingAnimationId = requestAnimationFrame(_animateHeading);
  }
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
    // updates; re-render is triggered by _onState() and setMarkerStyle().
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
  const bearing = _isImuHeadingReady(initialState) ? _imuBearing(initialState) : 0;
  const style    = initialState.marker_style ?? 'snowcat';

  _modelLat     = lat;
  _modelLon     = lon;
  _modelBearing = bearing;
  _targetBearing = bearing;
  _displayBearing = bearing;
  _lastValidBearing = bearing;

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

  _map.on('rotatestart', (event) => {
    if (event.originalEvent) {
      _cancelHeadingAnimationForUser();
    }
  });

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
function _isImuHeadingReady(s) {
  if (s.imu_heading_enabled && s.imu_ok && s.imu_yaw_valid && s.imu_heading_calibrated) {
    return true;
  }
  return false;
}

function _imuBearing(s) {
  return s.imu_heading_deg ?? 0;
}

// ── WebSocket state handler ────────────────────────────────────────────────
function _onState(s) {
  if (!_map) return;

  setMarkerStyle(s.marker_style ?? 'snowcat');

  // Sync GPS position and dot marker every tick.
  const lat = (s.gps_ok || s.gps_float_rtk) ? (s.gps_lat ?? DEFAULT_LAT) : DEFAULT_LAT;
  const lon = (s.gps_ok || s.gps_float_rtk) ? (s.gps_lon ?? DEFAULT_LON) : DEFAULT_LON;
  const positionChanged = lat !== _modelLat || lon !== _modelLon;
  if (positionChanged) {
    _modelLat = lat;
    _modelLon = lon;
    if (_dotMarker) _dotMarker.setLngLat([lon, lat]);
  }

  // Process bearing every WebSocket tick so slow rotation is not dropped.
  // Rendering still uses small delta gates to avoid unnecessary repaints.
  const imuReady = _isImuHeadingReady(s);
  const bearing = imuReady ? _imuBearing(s) : _lastValidBearing;
  if (imuReady) {
    _lastValidBearing = bearing;
  }
  const imuReadyChanged = imuReady !== _prevImuReady;

  // Log connect/disconnect so the user can verify detection in the browser
  // console (F12 → Console) while debugging.
  if (imuReadyChanged) {
    console.info('[SnowLink] IMU', imuReady ? 'connected' : 'disconnected',
      '— heading', bearing.toFixed(1) + '°');
  }

  const headingChanged =
    shortestBearingDelta(_targetBearing, bearing) > HEADING_EPSILON_DEG ||
    shortestBearingDelta(_displayBearing, bearing) > HEADING_EPSILON_DEG;
  if (imuReady || imuReadyChanged || headingChanged) {
    _setHeadingTarget(bearing, imuReady || imuReadyChanged, !imuReady);
  }
  if (positionChanged) {
    _map.setCenter([lon, lat]);
    _map.triggerRepaint();
  }
  _prevImuReady = imuReady;
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
    _finishHeadingAnimation();
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

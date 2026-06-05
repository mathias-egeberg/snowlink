/**
 * Snow-depth map overlay.
 *
 * Receives `snow_tile_update` messages over the WebSocket (routed by
 * websocket.js) and renders each cell as a coloured polygon on top of
 * the MapLibre base map.
 *
 * Public surface (window.SnowOverlay):
 *   attach(map)            - bind to a MapLibre map instance (call after style 'load')
 *   handleMessage(msg)     - feed one `snow_tile_update` message
 *   setVisible(on)         - toggle overlay visibility
 *   isVisible()            - bool
 *   getStatus()            - { tileCount, cellSize, tileSize, simulated, lastUpdateAgeMs, hasOrigin }
 *   ramp                   - the colour ramp (editable from devtools)
 *
 * Implementation notes:
 *   - Each tile is converted to N polygons (one per non-null cell) with
 *     `depth` and `confidence` properties.
 *   - All tiles share one GeoJSON source named 'snow-grid'.
 *   - Colour & opacity come from the MapLibre style expression below
 *     (centralised so we can tune the ramp from one place).
 *   - 3x3 tiles x 1600 cells = 14 400 polygons - fine for desktop,
 *     acceptable for Pi.  We rebuild the FeatureCollection only when
 *     tiles change, never per frame.
 *
 * Coordinate model:
 *   Backend sends tile_x, tile_y in a local ENU grid anchored at
 *   (origin_lat, origin_lon).  We approximate ENU<->lat/lon using
 *   metres-per-degree at the origin latitude.  Matches the backend
 *   helpers in snow_grid_service.py.
 */
(function () {
  'use strict';

  const SOURCE_ID = 'snow-grid';
  const LAYER_ID  = 'snow-grid-fill';

  // ── Colour ramp (centralised; edit here to tune) ────────────────────
  // Stops are [depth_m, [r, g, b]]; alpha comes from confidence.
  const COLOR_RAMP = [
    [0.00, [173, 216, 230]],  // light blue
    [0.25, [100, 180, 255]],  // blue
    [0.50, [ 60, 200, 180]],  // teal
    [0.75, [120, 220,  80]],  // green
    [1.00, [240, 230,  60]],  // yellow
    [1.25, [255, 160,  40]],  // orange
    [1.50, [200,  40, 200]],  // purple   (per spec)
    [2.00, [120,   0, 160]],  // deep purple
  ];

  // Visual scaling: confidence -> fill-opacity multiplier.
  const BASE_OPACITY = 0.78;

  // ── State ───────────────────────────────────────────────────────────
  let _map = null;
  let _visible = true;
  let _originLat = null;
  let _originLon = null;
  const _tiles = new Map();      // key "tx,ty" -> tile object
  let _lastUpdateMs = 0;
  let _rebuildScheduled = false;
  let _attachedRetryTimer = null;

  // ── Coordinate helpers (mirror backend) ─────────────────────────────
  const METERS_PER_DEG_LAT = 111320.0;
  function metersPerDegLon(lat) {
    return METERS_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180.0);
  }
  function localMetersToLatLon(x, y, originLat, originLon) {
    const lat = originLat + y / METERS_PER_DEG_LAT;
    const mpl = metersPerDegLon(originLat);
    const lon = originLon + (mpl ? x / mpl : 0);
    return [lon, lat];   // GeoJSON order
  }

  // ── MapLibre integration ───────────────────────────────────────────
  function attach(map) {
    if (_map === map) return;
    _map = map;
    if (_attachedRetryTimer) {
      clearTimeout(_attachedRetryTimer);
      _attachedRetryTimer = null;
    }
    _ensureLayer();
    _scheduleRebuild();
  }

  function _ensureLayer() {
    if (!_map) return;
    if (_map.getSource(SOURCE_ID)) return;

    _map.addSource(SOURCE_ID, {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] },
    });

    // Build a MapLibre interpolation expression from COLOR_RAMP.
    const fillColorExpr = ['interpolate', ['linear'], ['get', 'depth']];
    for (const [d, [r, g, b]] of COLOR_RAMP) {
      fillColorExpr.push(d, `rgb(${r},${g},${b})`);
    }

    _map.addLayer({
      id: LAYER_ID,
      type: 'fill',
      source: SOURCE_ID,
      layout: { visibility: _visible ? 'visible' : 'none' },
      paint: {
        'fill-color': fillColorExpr,
        // Confidence (0..1) modulates final opacity around BASE_OPACITY.
        'fill-opacity': [
          '*',
          BASE_OPACITY,
          ['max', 0.15, ['get', 'confidence']],
        ],
        'fill-antialias': false,
      },
    });
  }

  // ── Message handling ────────────────────────────────────────────────
  function handleMessage(msg) {
    if (!msg || msg.type !== 'snow_tile_update') return;
    if (typeof msg.origin_lat !== 'number' || typeof msg.origin_lon !== 'number') return;
    if (!Array.isArray(msg.depths) || !Array.isArray(msg.confidence)) return;

    // Detect origin change -> wipe cache (avoids ghost tiles on reset).
    if (_originLat !== msg.origin_lat || _originLon !== msg.origin_lon) {
      _originLat = msg.origin_lat;
      _originLon = msg.origin_lon;
      _tiles.clear();
    }

    const key = `${msg.tile_x},${msg.tile_y}`;
    _tiles.set(key, {
      tile_x: msg.tile_x,
      tile_y: msg.tile_y,
      tile_size_m: msg.tile_size_m,
      cell_size_m: msg.cell_size_m,
      width: msg.width,
      height: msg.height,
      depths: msg.depths,
      confidence: msg.confidence,
      updated_at: msg.updated_at,
    });
    _lastUpdateMs = Date.now();
    _scheduleRebuild();
  }

  function _scheduleRebuild() {
    if (_rebuildScheduled) return;
    _rebuildScheduled = true;
    // Coalesce rapid bursts (e.g. 9 tiles arriving in the same WS tick).
    requestAnimationFrame(() => {
      _rebuildScheduled = false;
      _rebuildFeatures();
    });
  }

  function _rebuildFeatures() {
    if (!_map) return;
    const src = _map.getSource(SOURCE_ID);
    if (!src) {
      // Style not yet loaded; try again shortly.
      if (!_attachedRetryTimer) {
        _attachedRetryTimer = setTimeout(() => {
          _attachedRetryTimer = null;
          _ensureLayer();
          _scheduleRebuild();
        }, 200);
      }
      return;
    }
    if (_originLat === null) {
      src.setData({ type: 'FeatureCollection', features: [] });
      return;
    }

    const features = [];
    for (const tile of _tiles.values()) {
      _appendTileFeatures(tile, features);
    }
    src.setData({ type: 'FeatureCollection', features });
  }

  function _appendTileFeatures(tile, out) {
    const { tile_x, tile_y, tile_size_m, cell_size_m, width, height, depths, confidence } = tile;
    const tileOriginX = tile_x * tile_size_m;
    const tileOriginY = tile_y * tile_size_m;

    for (let j = 0; j < height; j++) {
      for (let i = 0; i < width; i++) {
        const idx = j * width + i;
        const depth = depths[idx];
        if (depth === null || depth === undefined) continue;
        const conf = confidence ? (confidence[idx] ?? 1) : 1;
        if (conf <= 0) continue;

        const x0 = tileOriginX + i * cell_size_m;
        const y0 = tileOriginY + j * cell_size_m;
        const x1 = x0 + cell_size_m;
        const y1 = y0 + cell_size_m;

        const c00 = localMetersToLatLon(x0, y0, _originLat, _originLon);
        const c10 = localMetersToLatLon(x1, y0, _originLat, _originLon);
        const c11 = localMetersToLatLon(x1, y1, _originLat, _originLon);
        const c01 = localMetersToLatLon(x0, y1, _originLat, _originLon);

        out.push({
          type: 'Feature',
          geometry: { type: 'Polygon', coordinates: [[c00, c10, c11, c01, c00]] },
          properties: { depth, confidence: conf },
        });
      }
    }
  }

  // ── Visibility API ──────────────────────────────────────────────────
  function setVisible(on) {
    _visible = !!on;
    if (_map && _map.getLayer(LAYER_ID)) {
      _map.setLayoutProperty(LAYER_ID, 'visibility', _visible ? 'visible' : 'none');
    }
  }
  function isVisible() { return _visible; }

  function getStatus() {
    return {
      tileCount: _tiles.size,
      cellSize: _firstTileField('cell_size_m'),
      tileSize: _firstTileField('tile_size_m'),
      lastUpdateAgeMs: _lastUpdateMs ? (Date.now() - _lastUpdateMs) : null,
      hasOrigin: _originLat !== null,
      originLat: _originLat,
      originLon: _originLon,
    };
  }
  function _firstTileField(name) {
    const it = _tiles.values().next();
    return it.done ? null : it.value[name];
  }

  // ── Export ──────────────────────────────────────────────────────────
  window.SnowOverlay = {
    attach,
    handleMessage,
    setVisible,
    isVisible,
    getStatus,
    ramp: COLOR_RAMP,
  };
})();

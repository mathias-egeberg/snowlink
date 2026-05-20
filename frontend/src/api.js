/**
 * REST API helpers.
 *
 * All functions return a Promise that resolves to the JSON response body.
 * Errors are re-thrown so callers can handle them.
 */
const API = (() => {
  async function _json(method, path, body) {
    const opts = {
      method,
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
    };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(path, opts);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`${method} ${path} → ${res.status}: ${text}`);
    }
    return res.json();
  }

  return {
    getState:    ()       => _json('GET',  '/api/state'),
    getSettings: ()       => _json('GET',  '/api/settings'),
    postSettings:(payload) => _json('POST', '/api/settings', payload),
    toggleDevice:(key)    => _json('POST', '/api/control/device', { device_key: key }),
    calibrateImu:()       => _json('POST', '/api/settings/calibrate-imu'),
    resetCellularUsage:() => _json('POST', '/api/settings/cellular-usage/reset'),
  };
})();

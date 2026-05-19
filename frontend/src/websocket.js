/**
 * WebSocket connection manager.
 *
 * Connects to /ws/state and feeds every received message into AppState.
 * Reconnects automatically with exponential back-off (max 8 s).
 */
const WS = (() => {
  let _ws = null;
  let _backoff = 500;  // ms

  function connect() {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${location.host}/ws/state`;
    _ws = new WebSocket(url);

    _ws.onopen = () => {
      _backoff = 500;
      console.debug('[WS] connected');
    };

    _ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data);
        AppState.update(data);
      } catch (e) {
        console.warn('[WS] bad message', e);
      }
    };

    _ws.onclose = () => {
      console.debug(`[WS] disconnected, reconnecting in ${_backoff} ms`);
      setTimeout(() => {
        _backoff = Math.min(_backoff * 2, 8000);
        connect();
      }, _backoff);
    };

    _ws.onerror = () => {
      _ws.close();
    };
  }

  return { connect };
})();

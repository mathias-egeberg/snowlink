/**
 * Application entry point.
 *
 * Starts the WebSocket and loads the initial state from the REST API.
 * All screen modules are already loaded by <script> tags above this file.
 */
(async () => {
  // Connect WebSocket (will auto-reconnect on disconnect).
  WS.connect();

  // Hydrate state from REST so the UI is populated before the first WS
  // message arrives (which can take up to 200 ms).
  try {
    const snapshot = await API.getState();
    AppState.update(snapshot);
  } catch (e) {
    console.warn('[app] initial state fetch failed', e);
  }
})();

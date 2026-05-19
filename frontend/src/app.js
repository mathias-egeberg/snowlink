/**
 * Application entry point.
 *
 * Starts the WebSocket and loads the initial state from the REST API.
 * All screen modules are already loaded by <script> tags above this file.
 */
(async () => {
  WS.connect();

  try {
    const snapshot = await API.getState();
    AppState.update(snapshot);
  } catch (e) {
    console.warn('[app] initial state fetch failed', e);
  }
})();

const ExitDialog = (() => {
  const overlay = () => document.getElementById('exit-overlay');

  function show() { overlay().classList.remove('hidden'); }
  function hide() { overlay().classList.add('hidden'); }

  async function confirm() {
    try {
      await fetch('/api/exit', { method: 'POST' });
    } catch (_) {
      // Backend shut down before the response arrived — that's fine.
    }
    window.close();
  }

  return { show, hide, confirm };
})();

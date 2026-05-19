/**
 * Client-side state store.
 *
 * Holds the latest snapshot received from the backend WebSocket and
 * notifies all registered listeners on every update.
 */
const AppState = (() => {
  let _data = {};
  const _listeners = [];

  return {
    /** Replace the current snapshot and notify all listeners. */
    update(snapshot) {
      _data = snapshot;
      for (const fn of _listeners) {
        try { fn(snapshot); } catch (e) { console.error('[AppState]', e); }
      }
    },

    /** Return the latest snapshot (shallow copy). */
    get() {
      return _data;
    },

    /** Subscribe to state updates.  Returns an unsubscribe function. */
    subscribe(fn) {
      _listeners.push(fn);
      // Immediately call with current data if we already have a snapshot.
      if (Object.keys(_data).length > 0) fn(_data);
      return () => {
        const idx = _listeners.indexOf(fn);
        if (idx >= 0) _listeners.splice(idx, 1);
      };
    },
  };
})();

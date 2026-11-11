/*
 * ui/usb-prep.js: the USB Matrix's state, prepared when the inventory changes
 * instead of when the tab is clicked.
 *
 * The same idea as ui/matrix-prep.js, with one addition. /api/usb_state reads every
 * USB-capable unit, so what is prepared is worth painting only while the inventory is
 * still the one it was built from. The snapshot therefore carries the server's inventory
 * signature, and opening the page asks /api/usb_state/signature -- a read from memory and
 * disk that touches no device -- and paints the snapshot only if the two agree. A device
 * added, removed, cleared or moved since then is a mismatch and the snapshot is dropped,
 * so a prepared matrix can be a little behind on live state but never shows a member that
 * is gone or hides one that arrived.
 *
 * What it is not: a poller, a cache the matrix trusts, or a place state is invented. The
 * snapshot is the server's own answer, expires after a minute, and is replaced by the
 * page's first real response. One request in flight at a time, and no more often than every
 * two seconds.
 */
(function (root) {
  'use strict';
  var KEY = 'omni.usb.prepared';
  var MIN_INTERVAL_MS = 2000;
  var inflight = null;
  var lastAt = 0;
  var generation = 0;   // bumped by every inventory change; a read begun before one describes a world that has moved on

  function store(state) {
    if (!state || !state.ok || !state.inventory_signature) return;
    try { sessionStorage.setItem(KEY, JSON.stringify({t: Date.now(), state: state})); } catch (err) { /* storage unavailable: the page simply loads */ }
  }

  function readRaw(maxAgeMs) {
    try {
      var raw = JSON.parse(sessionStorage.getItem(KEY) || 'null');
      if (!raw || !raw.state || typeof raw.t !== 'number') return null;
      if (Date.now() - raw.t > maxAgeMs) return null;
      return raw.state;
    } catch (err) { return null; }
  }

  function invalidate() {
    try { sessionStorage.removeItem(KEY); } catch (err) { /* nothing to clear */ }
  }

  // Resolves to the prepared state when it is fresh and the inventory still matches, else null.
  function readIfCurrent(maxAgeMs) {
    var snap = readRaw(maxAgeMs);
    if (!snap) return Promise.resolve(null);
    return fetch('/api/usb_state/signature', {headers: {Accept: 'application/json'}})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (j && j.ok && j.signature === snap.inventory_signature) return snap;
        invalidate();
        return null;
      })
      .catch(function () { return null; });
  }

  function prepare() {
    if (inflight) return inflight;
    if (Date.now() - lastAt < MIN_INTERVAL_MS) return Promise.resolve(false);
    lastAt = Date.now();
    var began = generation;
    inflight = fetch('/api/usb_state', {headers: {Accept: 'application/json'}})
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        // Discarded, not stored, when the inventory changed while it was in flight.
        if (s && s.ok && began === generation) { store(s); return true; }
        return false;
      })
      .catch(function () { return false; })
      .then(function (ok) { inflight = null; return ok; });
    return inflight;
  }

  // The inventory changed: what was prepared describes endpoints that may no longer be
  // there, so it goes first, and the replacement is fetched in the background.
  function inventoryChanged() {
    generation += 1;
    invalidate();
    if (inflight) {
      // That read is superseded; a second one follows it rather than being skipped.
      return inflight.then(function () { lastAt = 0; return prepare(); });
    }
    lastAt = 0;
    return prepare();
  }

  root.OmniUsbPrep = {store: store, readIfCurrent: readIfCurrent, invalidate: invalidate, prepare: prepare, inventoryChanged: inventoryChanged, KEY: KEY};
})(window);

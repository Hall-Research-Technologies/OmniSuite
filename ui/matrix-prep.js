/*
 * ui/matrix-prep.js: the A/V Matrix's state, prepared when the inventory changes
 * instead of when the tab is clicked.
 *
 * Each admin page is its own document, so "opening the A/V Matrix" is a page
 * load. Without this, that load had nothing to draw until /api/state answered.
 * With it, an event that changes the inventory -- a scan finishing, Clear Units,
 * a cache reload -- fetches /api/state once and keeps the answer for the session,
 * and the matrix paints that on open and then confirms it with its own request.
 *
 * What it is not: a poller, a cache the matrix trusts, or a place state is
 * invented. The snapshot is the server's own answer, expires after a minute, and
 * is replaced by the matrix's first real response. One request in flight at a
 * time, and no more often than every two seconds.
 */
(function (root) {
  'use strict';
  var KEY = 'omni.matrix.prepared';
  var MIN_INTERVAL_MS = 2000;
  var inflight = null;
  var lastAt = 0;
  var generation = 0;   // bumped by every inventory change; a read begun before one describes a world that has moved on

  function store(state) {
    try { sessionStorage.setItem(KEY, JSON.stringify({t: Date.now(), state: state})); } catch (err) { /* storage unavailable: the matrix simply loads */ }
  }

  function read(maxAgeMs) {
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

  function prepare() {
    if (inflight) return inflight;
    if (Date.now() - lastAt < MIN_INTERVAL_MS) return Promise.resolve(false);
    lastAt = Date.now();
    var began = generation;
    inflight = fetch('/api/state', {headers: {Accept: 'application/json'}})
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

  // The inventory changed: what was prepared describes devices that may no longer
  // be there, so it goes first, and the replacement is fetched in the background.
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

  root.OmniMatrixPrep = {store: store, read: read, invalidate: invalidate, prepare: prepare, inventoryChanged: inventoryChanged, KEY: KEY};
})(window);

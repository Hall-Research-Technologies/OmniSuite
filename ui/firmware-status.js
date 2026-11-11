/*
 * Firmware availability, as the operator sees it.
 *
 * One module for every page that shows it, so Device Info and Settings cannot
 * disagree about what a device's status is. It reads /api/firmware_status, which
 * answers from the last good catalogue: this page never waits on Hall Research
 * and never triggers a download. It reports; it does not change a device.
 *
 * Three states, in the operator's words -- "Up to Date", "Available" and
 * "Unknown" -- and Unknown always comes with its reason. "N/A" is never used for
 * a model that has a status.
 */
(function () {
  'use strict';
  if (window.omniFirmware) return;

  const STATES = {
    up_to_date: 'Up to Date',
    update_available: 'Available',
    unknown: 'Unknown',
  };

  function csrf() {
    try { return sessionStorage.getItem('omni_csrf') || ''; } catch (err) { return ''; }
  }

  async function fetchStatus() {
    try {
      const res = await fetch('/api/firmware_status', {credentials: 'same-origin'});
      if (!res.ok) return null;
      const body = await res.json();
      return body && body.ok ? body : null;
    } catch (err) {
      return null;
    }
  }

  function stateOf(entry) {
    return STATES[entry && entry.state] ? entry.state : 'unknown';
  }

  function labelOf(entry) {
    return STATES[stateOf(entry)];
  }

  // The status goes on the Version cell as data, drawn by CSS beside the number.
  // Several handlers rewrite that cell's text; attributes survive that, and a
  // child element would not.
  function paint(cell, entry) {
    if (!cell) return;
    if (!entry) {
      delete cell.dataset.fw;
      delete cell.dataset.fwLabel;
      return;
    }
    const state = stateOf(entry);
    cell.dataset.fw = state;
    cell.dataset.fwLabel = labelOf(entry);
    cell.setAttribute('data-fw-reason', entry.reason || '');
  }

  function applyToTable(data, root) {
    const scope = root || document;
    if (!data) return 0;
    let painted = 0;
    scope.querySelectorAll('tr[data-ip]').forEach((row) => {
      const entry = (data.units || {})[row.getAttribute('data-ip')];
      if (row.cells && row.cells[11]) { paint(row.cells[11], entry); painted += entry ? 1 : 0; }
    });
    scope.querySelectorAll('tr[data-extender-ip]').forEach((row) => {
      const entry = (data.extenders || {})[row.getAttribute('data-extender-ip')];
      if (row.cells && row.cells[11]) { paint(row.cells[11], entry); painted += entry ? 1 : 0; }
    });
    return painted;
  }

  // One row per model present in the inventory. A model with several units takes
  // the most pressing state: Available outranks Unknown outranks Up to Date, so
  // one device that can be updated is never hidden behind its neighbours.
  const URGENCY = {update_available: 2, unknown: 1, up_to_date: 0};

  function perModel(data) {
    const groups = new Map();
    const add = (entry) => {
      if (!entry) return;
      const key = String(entry.model || '').toLowerCase();
      if (!key) return;
      if (!groups.has(key)) groups.set(key, {model: key, units: 0, entries: []});
      const group = groups.get(key);
      group.units += 1;
      group.entries.push(entry);
    };
    Object.values((data && data.units) || {}).forEach(add);
    Object.values((data && data.extenders) || {}).forEach(add);
    return Array.from(groups.values()).map((group) => {
      const worst = group.entries.slice().sort(
        (a, b) => URGENCY[stateOf(b)] - URGENCY[stateOf(a)])[0];
      const installed = Array.from(new Set(
        group.entries.map((e) => e.installed).filter(Boolean))).sort();
      return {
        model: group.model.toUpperCase(),
        units: group.units,
        installed: installed.join(', '),
        latest: worst.latest || '',
        state: stateOf(worst),
        label: labelOf(worst),
        reason: worst.reason || '',
      };
    }).sort((a, b) => a.model.localeCompare(b.model));
  }

  function when(seconds) {
    if (!seconds) return '';
    return new Date(seconds * 1000).toLocaleString();
  }

  // What the Firmware section of Settings says about the whole inventory.
  function summarize(data) {
    if (!data) return {text: 'Firmware status is unavailable right now.', tone: 'muted'};
    const rows = perModel(data);
    const check = data.check || {};
    const available = rows.filter((r) => r.state === 'update_available').length;
    let text;
    let tone = 'muted';
    if (!check.last_success) {
      text = check.state === 'unavailable'
        ? 'Could not reach Hall Research yet. It will try again.'
        : 'Checking Hall Research…';
    } else if (available) {
      text = available + ' model' + (available === 1 ? ' has' : 's have')
        + ' newer firmware available.';
      tone = 'warn';
    } else if (rows.length) {
      text = 'Everything with a published version is up to date.';
      tone = 'ok';
    } else {
      text = 'No devices to compare yet.';
    }
    let detail = check.last_success ? 'Last checked ' + when(check.last_success) + '.' : '';
    if (check.stale) {
      detail += ' The last check could not reach Hall Research, so this is the'
        + ' last known result.';
    }
    return {text, tone, detail: detail.trim(), rows, check};
  }

  // A manual check: start it, then watch the (local) status until it is done.
  async function check(onDone) {
    let started = false;
    try {
      const headers = {'Content-Type': 'application/json'};
      const token = csrf();
      if (token) headers['X-OmniSuite-CSRF'] = token;
      const res = await fetch('/api/firmware_status/check', {
        method: 'POST', credentials: 'same-origin', headers: headers, body: '{}'});
      started = res.status === 202;
    } catch (err) {
      started = false;
    }
    if (!started) { if (onDone) onDone(null); return null; }
    let data = null;
    for (let i = 0; i < 30; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      data = await fetchStatus();
      if (data && data.check && !data.check.running) break;
    }
    if (onDone) onDone(data);
    return data;
  }

  window.omniFirmware = {fetchStatus, applyToTable, perModel, summarize, check,
                         STATES};
})();

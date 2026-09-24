/*
 * The restricted routing shell.
 *
 * Everything this page knows comes from /api/user/state, which is a projection
 * built for this account rather than the administrative model with parts
 * hidden. That is the point: there is no full inventory in the browser to
 * un-hide, and no device address anywhere in it.
 *
 * The interaction is deliberately two choices and a button. An operator who
 * has never seen OmniSuite should be able to put a camera on a screen without
 * being told what an encoder is.
 */
(function () {
  'use strict';

  const state = {
    features: [],
    sources: [],
    destinations: [],
    source: null,
    destination: null,
    tab: null,
    busy: false,
  };

  const el = (id) => document.getElementById(id);

  // The CSRF token the login page put in sessionStorage. Memory for the life
  // of the tab; the session cookie itself is HttpOnly and never readable here.
  function csrf() {
    try { return sessionStorage.getItem('omni_csrf') || ''; }
    catch (err) { return ''; }
  }

  function toLogin() {
    const next = encodeURIComponent(location.pathname);
    location.replace('/login?next=' + next);
  }

  async function api(path, options) {
    const opts = Object.assign({credentials: 'same-origin'}, options || {});
    opts.headers = Object.assign({}, opts.headers || {});
    if (opts.method && opts.method !== 'GET') {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-OmniSuite-CSRF'] = csrf();
    }
    const res = await fetch(path, opts);
    if (res.status === 401) {
      // The session expired, was revoked, or the account was disabled. All of
      // them mean the same thing to somebody holding a phone.
      toLogin();
      throw new Error('signed out');
    }
    return {status: res.status, body: await res.json().catch(() => ({}))};
  }

  // ---------------------------------------------------------- rendering
  function card(item, kind) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pick' + (item.online ? '' : ' offline');
    button.setAttribute('aria-pressed', 'false');
    button.dataset.id = item.id;
    button.dataset.kind = kind;

    const name = document.createElement('span');
    name.className = 'pick-name';
    name.textContent = item.name;

    const sub = document.createElement('span');
    sub.className = 'pick-state';
    sub.textContent = describe(item, kind);

    button.append(name, sub);
    // A permitted device that is not answering stays visible and stays
    // selectable-looking, but cannot be acted on: hiding it would suggest the
    // assignment had been taken away.
    if (!item.online) button.disabled = true;
    return button;
  }

  function describe(item, kind) {
    if (!item.online) return 'Offline';
    if (kind !== 'destination') return 'Ready';
    const showing = item.showing || {};
    if (showing.mode === 'source') return 'Showing ' + showing.source_name;
    if (showing.mode === 'multiview') return 'Showing a Multiview layout';
    if (showing.mode === 'other') return 'Showing another source';
    return 'Nothing shown';
  }

  function renderList(hostId, emptyId, items, kind) {
    const host = el(hostId);
    host.replaceChildren();
    items.forEach((item) => host.appendChild(card(item, kind)));
    el(emptyId).hidden = items.length > 0;
  }

  function renderSelection() {
    document.querySelectorAll('.pick').forEach((button) => {
      const chosen = button.dataset.kind === 'source'
        ? state.source : state.destination;
      button.setAttribute('aria-pressed', String(button.dataset.id === chosen));
    });

    const source = state.sources.find((s) => s.id === state.source);
    const destination = state.destinations.find((d) => d.id === state.destination);
    const summary = el('summary');
    const action = el('route_btn');

    if (state.busy) {
      summary.textContent = 'Working…';
      action.disabled = true;
      return;
    }
    if (source && destination) {
      summary.replaceChildren();
      summary.append('Show ');
      const a = document.createElement('strong'); a.textContent = source.name;
      const b = document.createElement('strong'); b.textContent = destination.name;
      summary.append(a, ' on ', b);
      action.disabled = false;
      return;
    }
    // Say what is still missing rather than withdrawing the action.
    summary.textContent = !source && !destination
      ? 'Choose a source and a display.'
      : (!source ? 'Now choose a source.' : 'Now choose a display.');
    action.disabled = true;
  }

  function renderTabs() {
    const tabs = el('tabs');
    tabs.replaceChildren();
    const available = [
      ['av', 'A/V'],
      ['usb', 'USB'],
      ['multiview', 'Multiview'],
    ].filter(([id]) => state.features.indexOf(id) !== -1);

    // One granted feature needs no navigation at all.
    tabs.hidden = available.length < 2;
    available.forEach(([id, label]) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'tab';
      button.setAttribute('role', 'tab');
      button.textContent = label;
      button.setAttribute('aria-selected', String(state.tab === id));
      button.addEventListener('click', () => selectTab(id));
      tabs.appendChild(button);
    });
  }

  function selectTab(id) {
    state.tab = id;
    ['av', 'usb', 'multiview'].forEach((name) => {
      el('panel_' + name).hidden = name !== id;
    });
    // Only A/V has an action at this checkpoint.
    el('action_bar').hidden = id !== 'av';
    renderTabs();
    renderSelection();
  }

  // ------------------------------------------------------------- events
  document.addEventListener('click', (event) => {
    const button = event.target.closest('.pick');
    if (!button || button.disabled) return;
    const kind = button.dataset.kind;
    const id = button.dataset.id;
    // Tapping the chosen one again clears it.
    if (kind === 'source') {
      state.source = state.source === id ? null : id;
    } else {
      state.destination = state.destination === id ? null : id;
    }
    renderSelection();
  });

  el('route_btn').addEventListener('click', async () => {
    if (state.busy || !state.source || !state.destination) return;
    state.busy = true;
    renderSelection();
    try {
      const {status, body} = await api('/api/user/route', {
        method: 'POST',
        body: JSON.stringify({
          source: state.source,
          destination: state.destination,
          mode: 'av',
        }),
      });
      if (status === 200 && body.ok) {
        window.toast && window.toast('Done', 'ok');
        await refresh();
      } else {
        window.toast && window.toast(body.error || 'That did not work', 'error');
      }
    } catch (err) {
      if (String(err.message) !== 'signed out') {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
    } finally {
      state.busy = false;
      renderSelection();
    }
  });

  el('signout').addEventListener('click', async () => {
    try { await api('/api/auth/logout', {method: 'POST'}); } catch (err) { /* going anyway */ }
    toLogin();
  });

  // -------------------------------------------------------------- load
  async function refresh() {
    const {body} = await api('/api/user/state');
    state.features = body.features || [];
    state.sources = body.sources || [];
    state.destinations = body.destinations || [];

    // A device that went away is not a selection any more.
    if (!state.sources.some((s) => s.id === state.source)) state.source = null;
    if (!state.destinations.some((d) => d.id === state.destination)) {
      state.destination = null;
    }

    el('who').textContent = body.username
      ? body.username : 'On this computer';
    el('signout').hidden = !body.username;

    renderList('sources', 'sources_empty', state.sources, 'source');
    renderList('destinations', 'destinations_empty', state.destinations,
               'destination');
    if (!state.tab || state.features.indexOf(state.tab) === -1) {
      state.tab = state.features[0] || 'av';
    }
    selectTab(state.tab);
  }

  (async function start() {
    try {
      await refresh();
      el('loading').hidden = true;
    } catch (err) {
      if (String(err.message) !== 'signed out') {
        el('loading').textContent = 'Could not load your devices.';
      }
    }
  })();

  // Deliberately no polling timer. The host is the device-I/O authority and
  // this page reads once on load and again after an action it took. Ten
  // browsers open cost the host no more device traffic than one.
})();

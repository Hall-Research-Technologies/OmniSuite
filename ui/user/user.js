/*
 * The restricted routing shell.
 *
 * Everything this page knows comes from the /api/user/ projections, which are
 * built for this account rather than the administrative model with parts
 * hidden. That is the point: there is no full inventory in the browser to
 * un-hide, and no device address anywhere in it.
 *
 * Three capabilities, one interaction shape: choose a thing, choose where it
 * goes, press the button. An operator who has never seen OmniSuite should be
 * able to put a camera on a screen without being told what an encoder is.
 */
(function () {
  'use strict';

  const state = {
    features: [],
    tab: null,
    busy: false,

    // A/V
    sources: [],
    destinations: [],
    source: null,
    destination: null,

    // USB
    computers: [],
    peripherals: [],
    computer: null,
    peripheral: null,

    // Multiview
    mvDisplays: [],
    mvDisplay: null,
    mvPresets: [],
    mvPreset: null,
    mvWindow: null,
  };

  const el = (id) => document.getElementById(id);

  function csrf() {
    try { return sessionStorage.getItem('omni_csrf') || ''; }
    catch (err) { return ''; }
  }

  function toLogin() {
    location.replace('/login?next=' + encodeURIComponent(location.pathname));
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
      // Expired, revoked, or the account was disabled. All the same thing to
      // somebody holding a phone.
      toLogin();
      throw new Error('signed out');
    }
    return {status: res.status, body: await res.json().catch(() => ({}))};
  }

  // Messages an operator can act on. The server already refuses to say more
  // than it should; this turns what it does say into plain words.
  function failureText(body, status) {
    if (status === 403) return body.error || 'You do not have permission for that.';
    if (status === 404) return 'That equipment is not available right now.';
    if (status === 502) return body.error || 'That equipment did not respond.';
    return body.error || 'That did not work.';
  }

  // ------------------------------------------------------------- picking
  function card(item, kind, subtitle) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'pick' + (item.online === false ? ' offline' : '');
    button.setAttribute('aria-pressed', 'false');
    button.dataset.id = item.id;
    button.dataset.kind = kind;

    const name = document.createElement('span');
    name.className = 'pick-name';
    name.textContent = item.name;

    const sub = document.createElement('span');
    sub.className = 'pick-state';
    sub.textContent = subtitle;

    button.append(name, sub);
    // A permitted device that is not answering stays visible and cannot be
    // acted on: hiding it would suggest the assignment had been taken away.
    if (item.online === false) button.disabled = true;
    return button;
  }

  function renderList(hostId, emptyId, items, kind, subtitleFor) {
    const host = el(hostId);
    host.replaceChildren();
    items.forEach((item) => host.appendChild(
      card(item, kind, item.online === false ? 'Offline' : subtitleFor(item))));
    el(emptyId).hidden = items.length > 0;
  }

  function markSelected() {
    document.querySelectorAll('.pick').forEach((button) => {
      const chosen = {
        source: state.source, destination: state.destination,
        computer: state.computer, peripheral: state.peripheral,
        mvDisplay: state.mvDisplay, mvPreset: state.mvPreset,
      }[button.dataset.kind];
      button.setAttribute('aria-pressed', String(button.dataset.id === chosen));
    });
  }

  // ------------------------------------------------------------- summary
  function named(list, id) {
    const found = list.find((entry) => entry.id === id || entry.name === id);
    return found ? found.name : null;
  }

  function renderAction() {
    const summary = el('summary');
    const action = el('route_btn');
    el('action_bar').hidden = state.tab === 'multiview';
    if (state.tab === 'multiview') return;

    if (state.busy) {
      summary.textContent = 'Working…';
      action.disabled = true;
      return;
    }

    if (state.tab === 'av') {
      const a = named(state.sources, state.source);
      const b = named(state.destinations, state.destination);
      action.textContent = 'Show';
      if (a && b) {
        summary.replaceChildren();
        const s = document.createElement('strong'); s.textContent = a;
        const d = document.createElement('strong'); d.textContent = b;
        summary.append('Show ', s, ' on ', d);
        action.disabled = false;
        return;
      }
      summary.textContent = !a && !b ? 'Choose a source and a display.'
        : (!a ? 'Now choose a source.' : 'Now choose a display.');
      action.disabled = true;
      return;
    }

    // USB
    const computer = named(state.computers, state.computer);
    const peripheral = state.peripherals.find((p) => p.id === state.peripheral);
    const attached = peripheral && peripheral.attached_to
      && peripheral.attached_to.computer_id === state.computer;
    action.textContent = attached ? 'Disconnect' : 'Connect';
    if (computer && peripheral) {
      summary.replaceChildren();
      const c = document.createElement('strong'); c.textContent = computer;
      const p = document.createElement('strong'); p.textContent = peripheral.name;
      summary.append(attached ? 'Disconnect ' : 'Connect ', p,
                     attached ? ' from ' : ' to ', c);
      action.disabled = false;
      return;
    }
    summary.textContent = !computer && !peripheral
      ? 'Choose a computer and its USB devices.'
      : (!computer ? 'Now choose a computer.' : 'Now choose the USB devices.');
    action.disabled = true;
  }

  // ------------------------------------------------------------- A/V
  function describeDestination(item) {
    const showing = item.showing || {};
    if (showing.mode === 'source') return 'Showing ' + showing.source_name;
    if (showing.mode === 'multiview') return 'Showing a Multiview layout';
    if (showing.mode === 'other') return 'Showing another source';
    return 'Nothing shown';
  }

  function renderAv() {
    renderList('sources', 'sources_empty', state.sources, 'source',
               () => 'Ready');
    renderList('destinations', 'destinations_empty', state.destinations,
               'destination', describeDestination);
    markSelected();
  }

  // ------------------------------------------------------------- USB
  function describePeripheral(item) {
    const attached = item.attached_to || {};
    if (attached.mode === 'computer') return 'Connected to ' + attached.computer_name;
    if (attached.mode === 'other') return 'Connected to another computer';
    return 'Not connected';
  }

  async function loadUsb() {
    const {body} = await api('/api/user/usb_state');
    state.computers = body.computers || [];
    state.peripherals = body.peripherals || [];
    if (!state.computers.some((c) => c.id === state.computer)) state.computer = null;
    if (!state.peripherals.some((p) => p.id === state.peripheral)) {
      state.peripheral = null;
    }
    renderList('usb_computers', 'usb_computers_empty', state.computers,
               'computer', () => 'Ready');
    renderList('usb_peripherals', 'usb_peripherals_empty', state.peripherals,
               'peripheral', describePeripheral);
    markSelected();
    renderAction();
  }

  // ------------------------------------------------------- Multiview
  //
  // The layout is drawn the way it appears on the display, so a window is
  // chosen by pointing at where it is rather than by reading a name like
  // bottom_left. The geometry comes from the window list the projection sent;
  // nothing here knows about scalers, inputs or encoders, because none of that
  // is an operator's business.
  const LAYOUT_POSITIONS = {
    main: {row: '1 / 3', col: '1 / 3'},
    top_left: {row: '1', col: '1'},
    top_right: {row: '1', col: '3'},
    top_middle: {row: '1', col: '2'},
    bottom_left: {row: '3', col: '1'},
    bottom_middle: {row: '3', col: '2'},
    bottom_right: {row: '3', col: '3'},
    left: {row: '1 / 4', col: '1'},
    right: {row: '1 / 4', col: '3'},
    top: {row: '1', col: '1 / 4'},
    bottom: {row: '3', col: '1 / 4'},
    pip: {row: '3', col: '3'},
  };

  function renderWindows() {
    const host = el('mv_layout');
    host.replaceChildren();
    const preset = state.mvPresets.find((p) => p.name === state.mvPreset);
    el('mv_windows_panel').hidden = !preset;
    if (!preset) return;

    preset.windows.forEach((window_) => {
      const tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'mv-window';
      tile.dataset.cell = window_.cell;
      const place = LAYOUT_POSITIONS[window_.cell];
      if (place) {
        tile.style.gridRow = place.row;
        tile.style.gridColumn = place.col;
      }
      const where = document.createElement('span');
      where.className = 'mv-window-where';
      where.textContent = window_.label;
      const what = document.createElement('span');
      what.className = 'mv-window-what';
      what.textContent = window_.source && window_.source.name
        ? window_.source.name : 'Empty';
      if (!window_.live && window_.source && window_.source.name) {
        what.textContent += ' · no picture';
      }
      tile.append(where, what);
      host.appendChild(tile);
    });
  }

  async function loadMultiviewPresets() {
    if (!state.mvDisplay) {
      el('mv_presets_panel').hidden = true;
      el('mv_windows_panel').hidden = true;
      return;
    }
    el('mv_presets_panel').hidden = false;
    el('mv_presets_hint').textContent = 'Loading…';
    let body = {};
    try {
      const result = await api('/api/user/multiview_state?display='
                               + encodeURIComponent(state.mvDisplay));
      body = result.body || {};
      if (result.status !== 200) {
        el('mv_presets_hint').textContent = failureText(body, result.status);
        state.mvPresets = [];
        renderList('mv_presets', 'mv_presets_empty', [], 'mvPreset', () => '');
        renderWindows();
        return;
      }
    } catch (err) {
      if (String(err.message) === 'signed out') return;
      el('mv_presets_hint').textContent = 'Could not reach OmniSuite.';
      return;
    }
    state.mvPresets = body.presets || [];
    const display = named(state.mvDisplays, state.mvDisplay);
    el('mv_presets_hint').textContent = display
      ? 'Approved layouts for ' + display : ' ';
    if (!state.mvPresets.some((p) => p.name === state.mvPreset)) {
      const live = state.mvPresets.find((p) => p.showing);
      state.mvPreset = live ? live.name : null;
    }
    renderList('mv_presets', 'mv_presets_empty',
               state.mvPresets.map((p) => ({id: p.name, name: p.label,
                                            online: p.available})),
               'mvPreset',
               (item) => {
                 const preset = state.mvPresets.find((p) => p.label === item.name);
                 return preset && preset.showing ? 'On the display now'
                   : (preset && !preset.available ? 'Not available' : 'Tap to show');
               });
    markSelected();
    renderWindows();
  }

  function openSourceSheet(cell) {
    state.mvWindow = cell;
    const host = el('mv_sheet_sources');
    host.replaceChildren();
    state.sources.forEach((source) => {
      const button = card(source, 'mvSource',
                          source.online === false ? 'Offline' : 'Ready');
      button.setAttribute('aria-pressed', 'false');
      host.appendChild(button);
    });
    el('mv_sheet').hidden = false;
  }

  function closeSourceSheet() {
    el('mv_sheet').hidden = true;
    state.mvWindow = null;
  }

  async function switchWindow(sourceId) {
    const cell = state.mvWindow;
    closeSourceSheet();
    if (!cell || !state.mvDisplay || !state.mvPreset) return;
    state.busy = true;
    try {
      const {status, body} = await api('/api/user/multiview_switch', {
        method: 'POST',
        body: JSON.stringify({display: state.mvDisplay, preset: state.mvPreset,
                              window: cell, source: sourceId}),
      });
      if (status === 200 && body.ok) {
        window.toast && window.toast('Changed', 'ok');
        await loadMultiviewPresets();
      } else {
        window.toast && window.toast(failureText(body, status), 'error');
      }
    } catch (err) {
      if (String(err.message) !== 'signed out') {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
    } finally {
      state.busy = false;
    }
  }

  async function showPreset(name) {
    state.mvPreset = name;
    markSelected();
    state.busy = true;
    try {
      const {status, body} = await api('/api/user/multiview_show', {
        method: 'POST',
        body: JSON.stringify({display: state.mvDisplay, preset: name}),
      });
      if (status === 200 && body.ok) {
        window.toast && window.toast('Shown', 'ok');
      } else {
        window.toast && window.toast(failureText(body, status), 'error');
      }
      await loadMultiviewPresets();
    } catch (err) {
      if (String(err.message) !== 'signed out') {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
    } finally {
      state.busy = false;
    }
  }

  // ------------------------------------------------------------- events
  document.addEventListener('click', (event) => {
    const tile = event.target.closest('.mv-window');
    if (tile) { openSourceSheet(tile.dataset.cell); return; }

    const button = event.target.closest('.pick');
    if (!button || button.disabled) return;
    const kind = button.dataset.kind;
    const id = button.dataset.id;

    if (kind === 'mvSource') { switchWindow(id); return; }
    if (kind === 'mvPreset') { showPreset(id); return; }
    if (kind === 'mvDisplay') {
      state.mvDisplay = state.mvDisplay === id ? null : id;
      state.mvPreset = null;
      markSelected();
      loadMultiviewPresets();
      return;
    }
    // Tapping the chosen one again clears it.
    const field = {source: 'source', destination: 'destination',
                   computer: 'computer', peripheral: 'peripheral'}[kind];
    if (!field) return;
    state[field] = state[field] === id ? null : id;
    markSelected();
    renderAction();
  });

  el('mv_sheet_cancel').addEventListener('click', closeSourceSheet);
  el('mv_sheet').addEventListener('click', (event) => {
    if (event.target === el('mv_sheet')) closeSourceSheet();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !el('mv_sheet').hidden) closeSourceSheet();
  });

  el('route_btn').addEventListener('click', async () => {
    if (state.busy) return;
    const av = state.tab === 'av';
    if (av && (!state.source || !state.destination)) return;
    if (!av && (!state.computer || !state.peripheral)) return;

    state.busy = true;
    renderAction();
    try {
      let result;
      if (av) {
        result = await api('/api/user/route', {
          method: 'POST',
          body: JSON.stringify({source: state.source,
                                destination: state.destination, mode: 'av'}),
        });
      } else {
        const peripheral = state.peripherals.find((p) => p.id === state.peripheral);
        const attached = peripheral && peripheral.attached_to
          && peripheral.attached_to.computer_id === state.computer;
        result = await api('/api/user/usb_route', {
          method: 'POST',
          body: JSON.stringify({computer: state.computer,
                                peripherals: state.peripheral,
                                detach: !!attached}),
        });
      }
      if (result.status === 200 && result.body.ok) {
        window.toast && window.toast('Done', 'ok');
      } else {
        window.toast && window.toast(
          failureText(result.body, result.status), 'error');
      }
      await refresh();
    } catch (err) {
      if (String(err.message) !== 'signed out') {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
    } finally {
      state.busy = false;
      renderAction();
    }
  });

  el('signout').addEventListener('click', async () => {
    try { await api('/api/auth/logout', {method: 'POST'}); }
    catch (err) { /* going anyway */ }
    toLogin();
  });

  // --------------------------------------------------------------- tabs
  function renderTabs() {
    const tabs = el('tabs');
    tabs.replaceChildren();
    const available = [['av', 'A/V'], ['usb', 'USB'],
                       ['multiview', 'Multiview']]
      .filter(([id]) => state.features.indexOf(id) !== -1);

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
    renderTabs();
    renderAction();
    if (id === 'usb' && !state.computers.length) loadUsb().catch(() => {});
    if (id === 'multiview') renderMultiviewDisplays();
  }

  function renderMultiviewDisplays() {
    state.mvDisplays = state.destinations;
    renderList('mv_displays', 'mv_displays_empty', state.mvDisplays, 'mvDisplay',
               describeDestination);
    markSelected();
    if (state.mvDisplay) loadMultiviewPresets();
  }

  // -------------------------------------------------------------- load
  async function refresh() {
    const {body} = await api('/api/user/state');
    state.features = body.features || [];
    state.sources = body.sources || [];
    state.destinations = body.destinations || [];

    if (!state.sources.some((s) => s.id === state.source)) state.source = null;
    if (!state.destinations.some((d) => d.id === state.destination)) {
      state.destination = null;
    }

    el('who').textContent = body.username ? body.username : 'On this computer';
    el('signout').hidden = !body.username;
    el('my_link_btn').hidden = !body.username;

    renderAv();
    if (!state.tab || state.features.indexOf(state.tab) === -1) {
      state.tab = state.features[0] || 'av';
    }
    if (state.features.indexOf('usb') !== -1) {
      await loadUsb().catch(() => {});
    }
    selectTab(state.tab);
  }

  // The token this session was issued, adopted when the page did not get one
  // from the sign-in form -- which is every session opened by a Direct Access
  // link, because that path redirects straight here. Without it every action
  // would be refused for a missing CSRF token.
  //
  // In memory for the life of the tab, exactly as the sign-in page keeps it.
  // The link itself is never stored: it is a password.
  async function adoptSessionToken() {
    if (csrf()) return;
    try {
      const {body} = await api('/api/auth/session');
      if (body && body.csrf_token) {
        sessionStorage.setItem('omni_csrf', body.csrf_token);
      }
    } catch (err) { /* refresh() will report anything that matters */ }
  }

  // ------------------------------------------------------- my own link
  function showLinkSheet(payload) {
    const note = el('link_sheet_note');
    const value = el('link_sheet_value');
    const copy = el('link_sheet_copy');
    const warning = el('link_sheet_warning');
    const enabled = !!(payload && payload.direct_access
                       && payload.direct_access.enabled);
    const link = (payload && payload.link) || '';

    warning.hidden = !enabled;
    value.hidden = !link;
    copy.hidden = !link;
    value.value = link;
    if (!enabled) {
      note.textContent = 'Direct access is not enabled for this account. '
        + 'An administrator can turn it on for you.';
    } else if (!link) {
      note.textContent = (payload && payload.note)
        || 'Your link cannot be shown right now. Ask an administrator to '
           + 'create a new one.';
    } else {
      note.textContent = 'Save this to your home screen to open your controls '
        + 'without signing in.';
    }
    el('link_sheet').hidden = false;
  }

  el('my_link_btn').addEventListener('click', async () => {
    showLinkSheet(null);
    el('link_sheet_note').textContent = 'Loading\u2026';
    try {
      const {body} = await api('/api/user/direct_access');
      showLinkSheet(body);
    } catch (err) {
      if (String(err.message) !== 'signed out') {
        el('link_sheet_note').textContent = 'Could not reach OmniSuite.';
      }
    }
  });
  el('link_sheet_close').addEventListener('click', () => {
    // Dropped, not merely hidden: there is no reason for a credential to sit
    // in the page after the operator has finished with it.
    el('link_sheet_value').value = '';
    el('link_sheet').hidden = true;
  });
  el('link_sheet_copy').addEventListener('click', async () => {
    const link = el('link_sheet_value').value;
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      if (window.omniToast) window.omniToast('Link copied.');
    } catch (err) {
      el('link_sheet_value').select();
      if (window.omniToast) {
        window.omniToast('Press and hold to copy.', 'warn');
      }
    }
  });

  (async function start() {
    try {
      await adoptSessionToken();
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

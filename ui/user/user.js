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

    // Which routing interfaces the server says this account holds, and which of
    // them is on screen: 'standard', 'matrix', or 'none' when the account may
    // route but was given neither; null until the first read decides it.
    // Presentation only -- the server checks the interface a route claims
    // against the account on every request.
    interfaces: ['standard'],
    surface: null,

    // A/V
    sources: [],
    destinations: [],
    source: null,
    destination: null,
    // Display groups this account holds. A group is chosen in the same place a
    // display is, and choosing one clears the other: a route goes to one or to
    // the other, never both.
    groups: [],
    group: null,

    // USB
    computers: [],
    peripherals: [],
    computer: null,
    peripheral: null,

    // True when this browser got here through a Direct Access link. The page
    // then presents itself as a dedicated control panel rather than as
    // somebody's signed-in account.
    directLink: false,
  };

  // ------------------------------------------------- remembered selection
  //
  // The display the operator last chose, by stable identity, so a refresh or
  // a completed route does not drop them back to an empty form. Two rules
  // make it safe:
  //
  //   * it is a bookmark, never an authorisation. It is honoured only if the
  //     identity is in the list the server just sent, so a display that has
  //     been unassigned, taken away or has gone is dropped rather than
  //     presented as a working selection.
  //   * nothing is ever selected on the operator's behalf. A single assigned
  //     display is still chosen by them; a list of one is not consent.
  //
  // Presentation only: no device configuration and no credential is stored.
  // Storage is reached only through remember.js. This file can see a Direct
  // Access link, so it writes to browser storage nowhere at all; that module
  // cannot see one, and takes a device identity the page is already showing.
  const REMEMBERED_DESTINATION = 'destination';
  // Which of Routing and Matrix the operator last chose. A preference, not a
  // permission: it is honoured only while the account still holds that
  // interface, and ignored (not erased) when it does not.
  const REMEMBERED_INTERFACE = 'interface';

  function remember(key, value) {
    if (window.omniRemember) window.omniRemember.set(key, value);
  }

  function recall(key) {
    return window.omniRemember ? window.omniRemember.get(key) : null;
  }

  // Honour a remembered identity only when it is still on offer.
  function restoreSelection(list, current, key) {
    if (current && list.some((item) => item.id === current)) return current;
    const saved = recall(key);
    if (saved && list.some((item) => item.id === saved)) return saved;
    // Gone, or never set. Clear the record rather than leaving a name that
    // will never match again.
    if (saved) remember(key, null);
    return null;
  }

  const el = (id) => document.getElementById(id);

  function csrf() {
    try { return sessionStorage.getItem('omni_csrf') || ''; }
    catch (err) { return ''; }
  }

  // A panel opened from a direct access link is standing on its own
  // credential: the URL. It has no password and nobody to type one.
  function onAccessUrl() {
    return location.pathname.indexOf('/access/') === 0;
  }

  function toLogin() {
    // Leaving for the sign-in page (or reloading the link) fires pagehide, which is what stops the
    // preview refresh; nothing here needs to know about it.
    if (onAccessUrl()) {
      // Re-open the link instead of asking for a password nobody has.
      //
      // Session state lives in memory on the host, so an OmniSuite restart
      // ends every session even though no link was revoked. A wall panel that
      // met that with a sign-in page was unusable until somebody noticed and
      // reloaded it by hand. Re-requesting this page's own address mints a
      // fresh session exactly as opening the bookmark would.
      //
      // This cannot loop: a link that really has been revoked answers that
      // address with the "no longer valid" page rather than the shell, which
      // is where it stops. And the token is never put anywhere new -- this is
      // the address the browser is already on.
      location.reload();
      return;
    }
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
  // What went wrong, in words an operator can act on.
  //
  // A bare "409 CONFLICT" is not a message, and neither is "That did not
  // work" -- the two 409s this endpoint can return have completely different
  // remedies. The server names which one it is; this turns that into a
  // sentence.
  function failureText(body, status) {
    const reason = body && body.reason;
    if (reason === 'resource_busy') {
      return 'That display is busy with another routing operation. '
           + 'Try again in a moment.';
    }
    if (status === 403) {
      // The most likely 403 during ordinary use is an administrator changing
      // something while the page was open, and "you do not have permission"
      // reads as though the operator did something wrong.
      return body.error || 'Your permission for that has changed. '
                         + 'Ask an administrator.';
    }
    if (status === 404) return 'That equipment is not available right now.';
    if (status === 502) return body.error || 'That equipment did not respond.';
    if (status === 503) return body.error || 'That equipment did not answer.';
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
    // A source can be dragged onto a display or a group, but only while it is
    // answering. The drop ends in the same request as the Route button.
    if (kind === 'source' && item.online !== false) button.draggable = true;

    const name = document.createElement('span');
    name.className = 'pick-name';
    name.textContent = item.name;

    const sub = document.createElement('span');
    sub.className = 'pick-state';
    sub.textContent = subtitle;

    // A source card carries the picture, and the whole card stays the target.
    // Nothing is requested here: the image is fetched when the card is
    // actually on screen, so a long list costs nothing until it is scrolled
    // to, and an idle page makes no preview traffic at all.
    // A display carries the picture of whatever it is currently showing, and
    // only when that source is one this account is already permitted to see.
    // `showing.source_id` is null for anything else -- a Multiview, an
    // unrouted display, or a source outside this account's world -- so a
    // thumbnail the operator is not entitled to cannot be reached from here
    // by construction rather than by a check that could be forgotten.
    const showing = item.showing || {};
    const previewId = kind === 'source' ? item.id
      : (kind === 'destination' ? showing.source_id || null : null);
    const previewNote = kind === 'destination' ? {
      none: 'Unrouted',
      multiview: 'Multiview',
      other: 'Another source',
    }[showing.mode] || '' : '';

    if ((previewId || previewNote) && item.online !== false) {
      const shot = document.createElement('span');
      shot.className = 'pick-shot empty';
      shot.dataset.note = previewNote;
      const text = document.createElement('span');
      text.className = 'pick-text';
      text.append(name, sub);
      button.classList.add('has-preview');
      button.append(shot, text);
      if (previewId) watchForPreview(button, previewId, shot);
      return button;
    }

    button.append(name, sub);
    // A permitted device that is not answering stays visible and cannot be
    // acted on: hiding it would suggest the assignment had been taken away.
    if (item.online === false) button.disabled = true;
    return button;
  }

  // One observer for the page, watching which source cards are on screen.
  //
  // Re-armed by replacing the observer's targets, never by constructing a new
  // one per render: an observer re-created on every delivery multiplies, and
  // each callback forces a layout.
  const previewSeen = new Set();
  let previewObserver = null;
  // Which cards are on screen *now*, for as long as they are cards. previewObserver above only
  // announces the first time a card arrives; this one keeps saying, so a card scrolled away or on a
  // tab that is not showing stops being refreshed and one scrolled back is refreshed again.
  let visibilityObserver = null;
  const HAS_OBSERVER = 'IntersectionObserver' in window;

  // When pictures are fetched is decided in one place (preview-refresh.js); this page says how a
  // picture is fetched and how it is drawn.
  const previews = window.OmniUserPreviews.create({
    fetchPreview: (id) => fetchPreviewFor(id),
    apply: (shot, result) => drawPreview(shot, result),
    setInterval: (fn, ms) => window.setInterval(fn, ms),
    clearInterval: (handle) => window.clearInterval(handle),
    now: () => Date.now(),
    hidden: () => document.hidden,
  });

  function watchForPreview(button, sourceId, shot) {
    // The identity whose picture this card shows. For a source that is the
    // card's own device; for a display it is whatever the display is showing,
    // which is a different thing from the card's id and must not be read from
    // it.
    button.dataset.previewId = sourceId;
    previews.track(shot, sourceId, !HAS_OBSERVER);
    if (!HAS_OBSERVER) {
      loadPreview(sourceId, shot);
      return;
    }
    if (previewObserver === null) {
      previewObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          observer.unobserve(entry.target);
          const node = entry.target;
          loadPreview(node.dataset.previewId,
                      node.querySelector('.pick-shot'));
        });
      }, {rootMargin: '150px'});
    }
    if (visibilityObserver === null) {
      visibilityObserver = new IntersectionObserver((entries) => {
        entries.forEach((entry) => {
          previews.setVisible(entry.target.querySelector('.pick-shot'),
                              entry.isIntersecting);
        });
      });
    }
    previewObserver.observe(button);
    visibilityObserver.observe(button);
  }

  // One request per source, and the answer is a picture or a reason -- never a
  // broken image, because the endpoint answers 200 with a reason rather than
  // an error status when there is simply nothing to show.
  function loadPreview(sourceId, shot) {
    if (!shot || !sourceId) return Promise.resolve();
    return previews.load(shot);
  }

  // The network half: a picture or a reason, never an exception. The server answers 200 with a reason
  // when there is simply nothing to show, so an error status here means the request itself failed.
  async function fetchPreviewFor(sourceId) {
    // The first request for a source in this page's life asks the server to
    // re-read the encoder rather than answer from its minutes-long cache, so
    // a thumbnail switched on at the device appears on a reload instead of
    // whenever that cache happens to lapse. Later requests in the same page
    // are ordinary cache-busted reads and cost the device nothing.
    const first = !previewSeen.has(sourceId);
    const extra = first ? '&fresh=1' : '&t=' + Date.now();
    previewSeen.add(sourceId);
    // Bounded: a device that stopped answering must not hold a request open, because the one request
    // per source in flight is what keeps a slow device from gathering a backlog.
    const abort = typeof AbortController === 'function' ? new AbortController() : null;
    const cutoff = abort ? window.setTimeout(() => abort.abort(), 10000) : null;
    try {
      const res = await fetch(
        '/api/user/source_preview?source=' + encodeURIComponent(sourceId) + extra,
        {credentials: 'same-origin', signal: abort ? abort.signal : undefined});
      if (res.status === 401) { previews.stop(); return {image: null, reason: 'unavailable', note: ''}; }
      if (!res.ok) return {image: null, reason: 'unavailable', note: ''};
      const kind = res.headers.get('Content-Type') || '';
      if (kind.indexOf('image/') !== 0) {
        const body = await res.json().catch(() => ({}));
        return body.reason === 'preview_disabled'
          ? {image: null, reason: 'preview_disabled', note: 'Preview disabled'}
          : {image: null, reason: 'unavailable', note: ''};
      }
      return {image: await res.blob(), reason: '', note: ''};
    } catch (err) {
      return {image: null, reason: 'unavailable', note: ''};
    } finally {
      if (cutoff !== null) window.clearTimeout(cutoff);
    }
  }

  // The drawing half. The new picture replaces the old one only once the browser has it, so a card
  // never flashes empty between two frames.
  function drawPreview(shot, result) {
    if (!shot) return;
    if (!result || !result.image) { noPreview(shot, result ? result.note : ''); return; }
    const image = new Image();
    image.alt = '';
    const url = URL.createObjectURL(result.image);
    image.onload = () => {
      // Dropped, not merely replaced: the object URL is released as soon as
      // the browser has the picture.
      URL.revokeObjectURL(url);
      shot.replaceChildren(image);
      shot.classList.remove('empty');
      shot.dataset.note = '';
    };
    image.onerror = () => { URL.revokeObjectURL(url); noPreview(shot, ''); };
    image.src = url;
  }

  function noPreview(shot, note) {
    if (!shot) return;
    shot.replaceChildren();
    shot.classList.add('empty');
    shot.dataset.note = note || '';
  }

  function renderList(hostId, emptyId, items, kind, subtitleFor) {
    const host = el(hostId);
    // The observer's targets are torn down by renderAv before either list is
    // rebuilt -- both lists carry previews now, so disconnecting here would
    // drop the observers the other list had just registered.
    host.replaceChildren();
    items.forEach((item) => host.appendChild(
      card(item, kind, item.online === false ? 'Offline' : subtitleFor(item))));
    el(emptyId).hidden = items.length > 0;
  }

  function markSelected() {
    document.querySelectorAll('.pick').forEach((button) => {
      const chosen = {
        source: state.source, destination: state.destination,
        group: state.group, computer: state.computer, peripheral: state.peripheral,
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
    const stop = el('unroute_btn');
    // The bar ships hidden so it is not drawn before there is anything to route; it is shown from the
    // first render on and stays put, saying what is still missing rather than going away. The Matrix
    // routes from its own cells and the no-routing state has nothing to route, so neither shows it.
    const bar = !(state.tab === 'av' && (state.surface === 'matrix' || state.surface === 'none'));
    el('action_bar').hidden = !bar;
    if (!bar) return;

    // Unroute belongs to A/V and needs a chosen display to act on. It is
    // hidden rather than disabled when there is nothing selected, because
    // until then it is not an action that exists.
    const target = state.tab === 'av'
      ? state.destinations.find((d) => d.id === state.destination) : null;
    stop.hidden = !target;
    if (target) {
      const showing = target.showing || {};
      // Nothing to stop if it is already showing nothing.
      stop.disabled = state.busy || showing.mode === 'none';
    }

    if (state.busy) {
      summary.textContent = 'Working…';
      action.disabled = true;
      return;
    }

    if (state.tab === 'av') {
      const a = named(state.sources, state.source);
      const grp = state.groups.find((g) => g.id === state.group);
      const b = grp ? grp.name : named(state.destinations, state.destination);
      action.textContent = 'Route';
      // A chosen unit can go quiet after it was chosen. Route is withheld and says which one, because
      // the server would refuse it anyway and a button that always fails is worse than none.
      const chosenSource = state.sources.find((s) => s.id === state.source);
      const chosenDisplay = state.destinations.find((d) => d.id === state.destination);
      const quiet = chosenSource && chosenSource.online === false ? a
        : (chosenDisplay && chosenDisplay.online === false ? b : null);
      if (a && b && quiet) {
        summary.textContent = quiet + ' is offline.';
        action.disabled = true;
        return;
      }
      if (a && b) {
        summary.replaceChildren();
        const s = document.createElement('strong'); s.textContent = a;
        const d = document.createElement('strong'); d.textContent = b;
        summary.append('Route ', s, ' to ', d);
        if (grp) {
          summary.append(' (' + grp.displays + ' display'
                         + (grp.displays === 1 ? '' : 's') + ')');
        }
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
    // Every card about to be discarded is one of the observer's targets, and
    // both lists carry previews. Disconnect once, here, before either is
    // rebuilt; each card then re-registers itself as it is created.
    if (previewObserver) previewObserver.disconnect();
    if (visibilityObserver) visibilityObserver.disconnect();
    previews.reset();
    renderList('sources', 'sources_empty', state.sources, 'source',
               () => 'Ready');
    renderList('destinations', 'destinations_empty', state.destinations,
               'destination', describeDestination);
    // Groups sit beside the displays, in the same selection area. They carry
    // no picture: a group is several displays, and a composite thumbnail of
    // them would be invented.
    const host = el('destinations');
    state.groups.forEach((group) => {
      const n = group.displays;
      host.appendChild(card(group, 'group',
        'Group \u00b7 ' + n + ' display' + (n === 1 ? '' : 's')));
    });
    el('destinations_empty').hidden =
      state.destinations.length > 0 || state.groups.length > 0;
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

  // A route (or an unroute) has finished: the state is read again, which rebuilds the cards, and the
  // pictures on screen are refreshed now rather than at the next five-second tick. `at` is when the
  // route's answer arrived; a picture request begun after that already shows the new world.
  async function refreshAfterRoute(at) {
    await refresh();
    previews.routeCompleted(at);
  }

  // The page showing again re-reads what stood still while it was hidden; leaving ends the work.
  document.addEventListener('visibilitychange', () => previews.setHidden(document.hidden));
  window.addEventListener('pagehide', () => previews.stop());

  // ------------------------------------------------------------- events
  // A display that is mid-route does not accept another selection. Stated on
  // the card rather than relying on the operator not to tap twice.
  function busyWith(node) {
    return !!state.routingTo && node.dataset.kind === 'destination'
           && node.dataset.id === state.routingTo;
  }

  document.addEventListener('click', (event) => {
    const tile = event.target.closest('.mv-window');
    if (tile) { openSourceSheet(tile.dataset.cell); return; }

    const button = event.target.closest('.pick');
    if (!button || button.disabled) return;
    // A display already mid-route does not take another selection.
    if (busyWith(button)) return;
    const kind = button.dataset.kind;
    const id = button.dataset.id;

    // Tapping the chosen one again clears it.
    const field = {source: 'source', destination: 'destination',
                   group: 'group',
                   computer: 'computer', peripheral: 'peripheral'}[kind];
    if (!field) return;
    state[field] = state[field] === id ? null : id;
    // One target at a time: a display or a group.
    if (field === 'group' && state.group) state.destination = null;
    if (field === 'destination' && state.destination) state.group = null;
    if (field === 'destination') {
      remember(REMEMBERED_DESTINATION, state.destination);
    }
    markSelected();
    renderAction();
  });

  // What the operator is told when a route finishes. One place, so a route from
  // the Standard page and one from the Matrix cannot describe the same outcome
  // in two ways.
  function announceRoute(result, group) {
    const reply = result.body || {};
    if (group) {
      if (result.status === 200 && reply.total !== undefined) {
        // The aggregate first, then the displays that did not take it.
        const line = reply.routed + ' of ' + reply.total + ' displays routed';
        const failed = (reply.results || []).filter((r) => !r.ok)
          .map((r) => r.name);
        window.toast && window.toast(
          failed.length ? line + '. Not routed: ' + failed.join(', ') : line,
          reply.ok ? 'ok' : 'error');
      } else {
        window.toast && window.toast(failureText(reply, result.status), 'error');
      }
      return;
    }
    if (result.status === 200 && reply.ok) {
      window.toast && window.toast('Routed', 'ok');
    } else {
      window.toast && window.toast(failureText(reply, result.status), 'error');
    }
  }

  // The one Route operation. The button, a drop and (through its own cells) the
  // Matrix all end in a request to the same two endpoints; this is the
  // Standard interface's side of that.
  async function routeSelected() {
    if (state.busy) return;
    const av = state.tab === 'av';
    if (av && (!state.source || !(state.destination || state.group))) return;
    if (!av && (!state.computer || !state.peripheral)) return;

    state.busy = true;
    // The display this route targets is marked in flight, so a second tap on
    // the same display is refused by the page rather than queued behind a
    // lock on the server.
    state.routingTo = av ? state.destination : null;
    renderAction();
    renderRoutingState();
    try {
      let result;
      if (av && state.group) {
        result = await api('/api/user/route_group', {
          method: 'POST',
          body: JSON.stringify({source: state.source, group: state.group,
                                interface: 'standard'}),
        });
        announceRoute(result, true);
        await refreshAfterRoute(Date.now());
        return;
      }
      if (av) {
        result = await api('/api/user/route', {
          method: 'POST',
          body: JSON.stringify({source: state.source,
                                destination: state.destination, mode: 'av',
                                interface: 'standard'}),
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
      if (av) {
        announceRoute(result, false);
      } else if (result.status === 200 && result.body.ok) {
        window.toast && window.toast('Routed', 'ok');
      } else {
        window.toast && window.toast(
          failureText(result.body, result.status), 'error');
      }
      await refreshAfterRoute(Date.now());
    } catch (err) {
      if (String(err.message) !== 'signed out') {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
    } finally {
      // Always, on every path: a failed route must leave the page usable
      // without a reload.
      state.busy = false;
      state.routingTo = null;
      renderAction();
      renderRoutingState();
    }
  }

  el('route_btn').addEventListener('click', routeSelected);

  // ------------------------------------------------------- drag and drop
  //
  // A second gesture for the operation above, not a second operation. A drop
  // chooses the source and the destination exactly as two taps would and then
  // calls routeSelected, so the request is the one the Route button sends.
  const drag = window.OmniUserDrag ? window.OmniUserDrag.create({
    enabled: () => !state.busy && state.tab === 'av' && state.surface === 'standard',
    canDrag: (id) => state.sources.some((s) => s.id === id && s.online !== false),
    canDrop: (kind, id) => (kind === 'group'
      ? state.groups.some((g) => g.id === id)
      : state.destinations.some((d) => d.id === id && d.online !== false)),
    perform: (sourceId, kind, id) => {
      state.source = sourceId;
      if (kind === 'group') {
        state.group = id;
        state.destination = null;
      } else {
        state.destination = id;
        state.group = null;
        remember(REMEMBERED_DESTINATION, id);
      }
      markSelected();
      renderAction();
      return routeSelected();
    },
  }) : null;
  if (drag) {
    ['dragstart', 'dragover', 'dragleave', 'drop', 'dragend'].forEach((type) => {
      document.addEventListener(type, drag[type]);
    });
  }

  // ------------------------------------------------------------- unroute
  //
  // Stopping a display is its own action with its own confirmation: it is
  // destructive in the only sense that matters to somebody watching, and
  // unlike a route it cannot be undone by choosing something else.
  el('unroute_btn').addEventListener('click', async () => {
    if (state.busy || !state.destination) return;
    const name = named(state.destinations, state.destination) || 'that display';
    const chosen = state.destination;

    const ask = window.omniConfirm;
    if (typeof ask !== 'function') {
      // The notice is the point of the dialog. Without it, refuse rather
      // than falling back to a browser string box that cannot carry it.
      window.toast && window.toast(
        'Cannot confirm that action on this page.', 'error');
      return;
    }
    const go = await ask({
      title: 'Stop this display?',
      message: name + ' will stop showing anything until a source is chosen '
               + 'for it again.',
      confirmText: 'Unroute',
      danger: true,
    });
    if (!go) return;

    state.busy = true;
    state.routingTo = chosen;
    renderAction();
    renderRoutingState();
    try {
      const result = await api('/api/user/unroute', {
        method: 'POST',
        body: JSON.stringify({destination: chosen}),
      });
      if (result.status === 200 && result.body.ok) {
        window.toast && window.toast('Unrouted', 'ok');
      } else {
        window.toast && window.toast(
          failureText(result.body, result.status), 'error');
      }
      await refreshAfterRoute(Date.now());
    } catch (err) {
      if (String(err.message) !== 'signed out') {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
    } finally {
      state.busy = false;
      state.routingTo = null;
      renderAction();
      renderRoutingState();
    }
  });

  // Which display is mid-route, said on the display's own card.
  function renderRoutingState() {
    document.querySelectorAll('.pick[data-kind="destination"]').forEach((node) => {
      const routing = !!state.routingTo && node.dataset.id === state.routingTo;
      node.classList.toggle('routing', routing);
      const line = node.querySelector('.pick-state');
      if (routing) {
        node.dataset.was = line ? line.textContent : '';
        if (line) line.textContent = 'Routing\u2026';
      } else if (node.dataset.was !== undefined) {
        if (line && node.dataset.was) line.textContent = node.dataset.was;
        delete node.dataset.was;
      }
    });
  }

  // ------------------------------------------------------------- Matrix
  //
  // Drawn by matrix.js from what the server built for this account. This file
  // owns the network half: the same two route endpoints the Standard page uses,
  // claiming the Matrix interface, and one read of the grid after each route.
  let matrix = null;
  // Reads are ordered against the writes they race. A read that began before a
  // route finished describes a world that route has since changed, so it is
  // discarded rather than drawn over the fresher one.
  let matrixSeq = 0;
  let matrixDenied = false;

  function buildMatrix() {
    if (matrix || !window.OmniUserMatrix) return;
    matrix = window.OmniUserMatrix.create({
      document,
      grid: el('matrix_grid'),
      scroller: el('matrix_scroll'),
      modeBar: el('matrix_modes'),
      empty: el('matrix_empty'),
      previewHost: document.body,
      route: routeFromMatrix,
      fetchPreview: (id) => fetchPreviewFor(id),
      drawPreview: (card, result) => drawPreview(card, result),
      setTimeout: (fn, ms) => window.setTimeout(fn, ms),
      clearTimeout: (handle) => window.clearTimeout(handle),
      viewportWidth: () => window.innerWidth || 1024,
    });
  }

  async function loadMatrix() {
    buildMatrix();
    if (!matrix) return;
    const mine = ++matrixSeq;
    const {status, body} = await api('/api/user/matrix_state');
    if (mine !== matrixSeq) return;
    if (status === 403) {
      // The Matrix was withdrawn between the digest and this read. Say what
      // the account holds now rather than drawing a grid it may not use. Once:
      // if the state still lists the Matrix, asking again would only be told
      // the same thing, so the nested read stops here.
      if (matrixDenied) return;
      matrixDenied = true;
      try { await refresh(); } finally { matrixDenied = false; }
      return;
    }
    if (status === 200 && body && body.ok) matrix.render(body);
  }

  async function routeFromMatrix(request) {
    if (state.busy || !matrix) return;
    state.busy = true;
    matrix.setBusy(true);
    // Anything read before this finishes describes the world before it.
    matrixSeq += 1;
    try {
      const group = request.kind === 'group';
      const common = {source: request.source, mode: request.mode, interface: 'matrix'};
      const result = await api(group ? '/api/user/route_group' : '/api/user/route', {
        method: 'POST',
        body: JSON.stringify(group ? Object.assign({group: request.id}, common)
                                   : Object.assign({destination: request.id}, common)),
      });
      announceRoute(result, group);
      await loadMatrix();
    } catch (err) {
      if (String(err.message) !== 'signed out') {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
    } finally {
      state.busy = false;
      if (matrix) matrix.setBusy(false);
    }
  }

  // ------------------------------------------------- Routing | Matrix
  function permittedSurfaces() {
    const out = [];
    if (state.interfaces.indexOf('standard') !== -1) out.push('standard');
    if (state.interfaces.indexOf('matrix') !== -1 && window.OmniUserMatrix) out.push('matrix');
    return out;
  }

  function renderSurface() {
    const allowed = permittedSurfaces();
    let chosen = allowed.indexOf(state.surface) !== -1 ? state.surface : null;
    if (!chosen) {
      // What the operator last picked, if they may still use it; otherwise the
      // first they may. Never an interface the server did not list.
      const saved = recall(REMEMBERED_INTERFACE);
      chosen = allowed.indexOf(saved) !== -1 ? saved : (allowed[0] || 'none');
    }
    state.surface = chosen;
    el('standard_routing').hidden = chosen !== 'standard';
    el('matrix_routing').hidden = chosen !== 'matrix';
    el('no_routing').hidden = chosen !== 'none';

    // Two interfaces are two tabs; one is just the page, with no useless tab.
    const tabs = el('mode_tabs');
    tabs.replaceChildren();
    tabs.hidden = allowed.length < 2;
    allowed.forEach((id) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'tab';
      button.setAttribute('role', 'tab');
      button.textContent = id === 'matrix' ? 'Matrix' : 'Routing';
      button.setAttribute('aria-selected', String(chosen === id));
      button.addEventListener('click', () => selectSurface(id));
      tabs.appendChild(button);
    });
  }

  async function selectSurface(id) {
    if (id === state.surface) return;
    // The operator's own choice is the only thing that is remembered.
    remember(REMEMBERED_INTERFACE, id);
    state.surface = id;
    renderSurface();
    renderAction();
    try {
      // Both views are read fresh on the way in, so neither shows what the
      // other has just changed as it was.
      if (id === 'matrix') await loadMatrix();
      else await refresh();
    } catch (err) { /* the next read tries again */ }
  }

  el('signout').addEventListener('click', async () => {
    try { await api('/api/auth/logout', {method: 'POST'}); }
    catch (err) { /* going anyway */ }
    // The token this tab held belongs to a session that no longer exists.
    // A Direct Access session signs out the same way, and its bearer link is
    // untouched: signing out ends this session, it does not withdraw the link.
    if (window.omniForgetCsrf) window.omniForgetCsrf();
    try { sessionStorage.removeItem('omni_csrf'); } catch (err) { /* none */ }
    toLogin();
  });

  // --------------------------------------------------------------- tabs
  function renderTabs() {
    const tabs = el('tabs');
    tabs.replaceChildren();
    const available = [['av', 'A/V'], ['usb', 'USB']]
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
    ['av', 'usb'].forEach((name) => {
      el('panel_' + name).hidden = name !== id;
    });
    renderTabs();
    renderAction();
    if (id === 'usb' && !state.computers.length) loadUsb().catch(() => {});
  }

  // -------------------------------------------------------------- load
  async function refresh() {
    const {body} = await api('/api/user/state');
    state.features = body.features || [];
    state.sources = body.sources || [];
    state.destinations = body.destinations || [];
    state.groups = body.groups || [];
    state.revision = body.revision || '';
    state.interfaces = Array.isArray(body.interfaces) ? body.interfaces : ['standard'];

    if (!state.sources.some((s) => s.id === state.source)) state.source = null;
    if (!state.groups.some((g) => g.id === state.group)) state.group = null;
    // The display survives a refresh and a completed route, but only if the
    // server still lists it for this account. Nothing is selected merely
    // because the list happens to hold one.
    state.destination = restoreSelection(
      state.destinations, state.destination, REMEMBERED_DESTINATION);

    // A Direct Access session is a dedicated control panel, not somebody's
    // account. It carries no name, no link to copy and no way to sign out --
    // there is no password behind it to sign back in with, so a sign-out
    // would strand whoever is holding the tablet. The account controls are
    // removed rather than hidden, so nothing is one CSS rule away from being
    // back.
    const panel = state.directLink;
    el('who').textContent = panel ? ''
      : (body.username ? body.username : 'On this computer');
    el('who').hidden = panel;
    el('signout').hidden = panel || !body.username;
    el('my_link_btn').hidden = panel || !body.username;
    document.body.classList.toggle('direct-panel', panel);

    renderAv();
    renderSurface();
    if (!state.tab || state.features.indexOf(state.tab) === -1) {
      state.tab = state.features[0] || 'av';
    }
    if (state.features.indexOf('usb') !== -1) {
      await loadUsb().catch(() => {});
    }
    selectTab(state.tab);
    // The grid is read again whenever the state is, so a permission that was
    // added or taken away is in it by the time the operator looks.
    if (state.surface === 'matrix' && state.tab === 'av') {
      await loadMatrix().catch(() => {});
    }
  }

  // The token this session was issued, adopted when the page did not get one
  // from the sign-in form -- which is every session opened by a Direct Access
  // link, because that path redirects straight here. Without it every action
  // would be refused for a missing CSRF token.
  //
  // In memory for the life of the tab, exactly as the sign-in page keeps it.
  // The link itself is never stored: it is a password.
  async function adoptSessionToken() {
    // Always asked, even when a CSRF token is already held. A Direct Access
    // session that reloads still has its token in sessionStorage, so an early
    // return here would leave the page not knowing it is a panel and it would
    // draw the account controls again on every refresh.
    try {
      const {body} = await api('/api/auth/session');
      // Adopted unconditionally. The endpoint answers with the token for the
      // session this request arrived on, so a value already in sessionStorage
      // is either the same one or a stale one belonging to a session that has
      // ended -- and keeping the stale one is what made every action from a
      // Direct Access panel fail for a bad CSRF token.
      if (body && body.csrf_token) {
        sessionStorage.setItem('omni_csrf', body.csrf_token);
      }
      state.directLink = !!(body && body.method === 'direct_link');
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
      if (window.toast) window.toast('Link copied.');
    } catch (err) {
      el('link_sheet_value').select();
      if (window.toast) {
        window.toast('Press and hold to copy.', 'warn');
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

  // Permissions are decided by the server on every request; this only keeps
  // what is drawn honest. One tiny request -- a digest, no device read -- when
  // the page becomes visible or regains focus, and every 30 seconds while it
  // is visible. If the digest differs the whole projection is read again,
  // which drops anything the account lost and deselects it. Single flight;
  // nothing runs in a hidden tab.
  let revisionBusy = false;
  async function checkRevision() {
    if (revisionBusy || state.busy || document.hidden || !state.revision) return;
    revisionBusy = true;
    try {
      const {status, body} = await api('/api/user/revision');
      if (status === 200 && body.revision && body.revision !== state.revision) {
        await refresh();
      }
    } catch (err) { /* the next tick tries again */ } finally {
      revisionBusy = false;
    }
  }
  // Coming back to a page that is showing the Matrix reads the grid again --
  // one request to this server, no device read -- because what is routed may
  // have been changed by somebody else while the page was away.
  async function refreshMatrixIfShowing() {
    if (document.hidden || state.busy || state.surface !== 'matrix' || state.tab !== 'av') return;
    try { await loadMatrix(); } catch (err) { /* the next return tries again */ }
  }
  document.addEventListener('visibilitychange', checkRevision);
  window.addEventListener('focus', checkRevision);
  document.addEventListener('visibilitychange', refreshMatrixIfShowing);
  window.addEventListener('focus', refreshMatrixIfShowing);
  setInterval(checkRevision, 30000);
})();

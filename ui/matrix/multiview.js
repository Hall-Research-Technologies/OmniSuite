// OmniSuite Multiview workspace.
//
// This file draws the page and collects the operator's intent. It computes no
// geometry of its own: the layout catalog comes from /api/multiview/layouts and
// the same numbers are what the server writes to the decoder, so the preview on
// screen and the hardware configuration cannot disagree. It performs no device
// programming either -- every mutation goes through the verified backend
// transaction.
//
// Nothing here mutates a device except Save, Show on Display and Delete.
// Dragging, dropping and clearing windows only change a local desired-state
// model.
(function () {
  'use strict';

  // One state model drives every control's visibility and enabled state.
  // Nothing is inferred from whether a field happens to hold a value.
  const MODE = {
    NO_DECODER: 'NO_DECODER',                  // nothing chosen yet
    LOADING: 'LOADING',                        // reading the decoder
    UNREACHABLE: 'UNREACHABLE',                // the decoder did not answer
    UNSUPPORTED: 'UNSUPPORTED',                // decoder has no Multiview node
    DECODER_SELECTED_EMPTY: 'DECODER_SELECTED_EMPTY',  // capable, none configured
    DECODER_SELECTED: 'DECODER_SELECTED',      // capable, some configured
    CREATE: 'CREATE',                          // building a new one
    EDIT: 'EDIT',                              // editing an existing one
    SAVING: 'SAVING',                          // a transaction is running
  };

  const state = {
    mode: MODE.NO_DECODER,
    // The outcome of the last transaction. Separate from `mode` because
    // VERIFIED and ERROR describe what just happened, not where the operator is.
    outcome: null,                             // null | 'VERIFIED' | 'ERROR'
    previousMode: null,                        // restored when SAVING ends
    layouts: [],
    canvas: '1920x1080',                       // the one canvas, from the server
    canvases: [],                              // kept for the availability read
    decoders: [],
    sources: [],
    excluded: [],
    decoderState: null,
    availability: [],                          // per-canvas, from the server
    canCreate: false,
    assignments: {},                           // cell -> source ip (desired only)
    editing: null,                             // object_name being edited
    plan: null,
    planSignature: '',
    planPending: false,      // a plan request is in flight
    unreachable: [],         // sources the last plan could not prepare
    pickedSource: null,
    nameTouched: false,
  };

  let stateSeq = 0;
  let planTimer = null;
  let planSeq = 0;

  const el = (id) => document.getElementById(id);
  const notify = (message, kind) => {
    if (typeof window.toast === 'function') window.toast(message, kind === 'ok');
  };

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    // textContent everywhere: hostnames, model strings and device error text
    // all pass through here.
    if (text != null) element.textContent = String(text);
    return element;
  }

  function show(id, visible) {
    const target = el(id);
    if (target) target.hidden = !visible;
  }

  async function getJSON(url, options) {
    const response = await fetch(url, options);
    let body = null;
    try { body = await response.json(); } catch (err) { body = null; }
    if (!body) throw new Error('The server returned an unreadable response.');
    if (!response.ok && body.error) throw new Error(body.error);
    return body;
  }

  // ---------------------------------------------------------------- loading

  async function loadLayouts() {
    const body = await getJSON('/api/multiview/layouts');
    state.layouts = body.layouts || [];
    state.canvases = body.canvases || [];
    // The active canvas is the server's to state, never the page's to choose.
    if (body.canvas) state.canvas = body.canvas;
    state.version = body.version || '';
    state.windowInputs = body.window_ip_inputs || [];
    const layout = el('mv_layout');
    layout.replaceChildren();
    state.layouts.forEach((entry) => {
      layout.appendChild(new Option(
        entry.label + ' (' + entry.window_count + ' windows)', entry.id));
    });
  }

  async function loadDecoders(probe) {
    const body = await getJSON('/api/multiview/decoders' + (probe ? '?probe=1' : ''));
    state.decoders = body.decoders || [];
    const select = el('mv_decoder');
    const previous = select.value;
    select.replaceChildren();
    select.appendChild(new Option('Select a decoder…', ''));
    state.decoders.forEach((decoder) => {
      const option = new Option(
        decoder.hostname + '  (' + decoder.ip + ')', decoder.ip);
      // Capability is a cached fact, not a poll. Unknown stays selectable: the
      // answer arrives when the decoder is opened.
      if (decoder.multiview_supported === false) {
        option.disabled = true;
        option.text += '  — no Multiview support';
      }
      select.appendChild(option);
    });
    if (previous) select.value = previous;
  }

  async function loadSources() {
    const body = await getJSON('/api/multiview/sources');
    state.sources = body.sources || [];
    state.excluded = body.excluded || [];
  }

  async function loadDecoderState(ip, keepEditing) {
    const token = ++stateSeq;
    setMode(MODE.LOADING);
    try {
      const body = await getJSON('/api/multiview/state?ip=' + encodeURIComponent(ip));
      if (token !== stateSeq) return;               // a newer selection won
      state.decoderState = body;
      state.availability = body.canvases || [];
      state.canCreate = !!body.can_create;
      populateTargets();
      if (keepEditing && (body.multiviews || [])
          .some((view) => view.name === keepEditing)) {
        loadIntoEditor(keepEditing);
        return;
      }
      state.editing = null;
      setMode((body.multiviews || []).length
        ? MODE.DECODER_SELECTED : MODE.DECODER_SELECTED_EMPTY);
    } catch (err) {
      if (token !== stateSeq) return;
      state.decoderState = null;
      // "Unable to read" and "none configured" are different conditions and
      // must never be presented as the same one.
      const decoder = state.decoders.find((d) => d.ip === ip);
      setMode(decoder && decoder.multiview_supported === false
        ? MODE.UNSUPPORTED : MODE.UNREACHABLE, err.message);
    }
  }

  // ---------------------------------------------------------------- mode

  function setMode(mode, detail) {
    state.mode = mode;
    state.modeDetail = detail || '';
    render();
  }

  function isEditingOrCreating() {
    return state.mode === MODE.CREATE || state.mode === MODE.EDIT
      || (state.mode === MODE.SAVING
          && (state.previousMode === MODE.CREATE || state.previousMode === MODE.EDIT));
  }

  function currentView() {
    if (!state.editing || !state.decoderState) return null;
    return (state.decoderState.multiviews || [])
      .find((view) => view.name === state.editing) || null;
  }

  // The one distinction the whole page now turns on. An active Multiview's
  // canvas is a switching surface: a drop takes effect on the display. An
  // inactive one is a preset: a drop edits it and nothing happens until Save.
  function isLive() {
    const view = currentView();
    return state.mode === MODE.EDIT && !!view && !!view.selected_on_output;
  }

  function renderMode() {
    const mode = state.mode;
    const working = isEditingOrCreating();
    const haveDecoder = !!el('mv_decoder').value;
    const capable = mode !== MODE.UNSUPPORTED && mode !== MODE.UNREACHABLE
      && mode !== MODE.NO_DECODER && mode !== MODE.LOADING;

    show('mv_field_target', capable);
    show('mv_field_new', capable);
    show('mv_field_reload', haveDecoder && mode !== MODE.LOADING);
    show('mv_field_layout', working);
    show('mv_field_name', working);
    show('mv_workspace', working);
    show('mv_actions', working);

    const view = currentView();
    show('mv_show', state.mode === MODE.EDIT && view && !view.selected_on_output);
    show('mv_shown', state.mode === MODE.EDIT && !!view);
    show('mv_delete', state.mode === MODE.EDIT);
    show('mv_cancel', working);

    const busy = mode === MODE.SAVING;
    // A decoder holds as many saved Multiviews as the operator wants, so the
    // only thing that withdraws New is an operation already in flight.
    el('mv_new').disabled = busy;
    el('mv_new').title = '';
    el('mv_target').disabled = busy;
    el('mv_decoder').disabled = busy;
    el('mv_layout').disabled = busy;
    el('mv_name').disabled = busy;
    el('mv_cancel').disabled = busy;
    el('mv_delete').disabled = busy;
    el('mv_show').disabled = busy;

    renderBanner();
    renderShownState(view);
  }

  function renderBanner() {
    const banner = el('mv_banner');
    const messages = {
      [MODE.NO_DECODER]: ['muted', 'Select a decoder to begin.'],
      [MODE.LOADING]: ['muted', 'Loading Multiviews…'],
      [MODE.UNREACHABLE]: ['error',
        'Unable to read Multiview configuration from this decoder.'
        + (state.modeDetail ? ' ' + state.modeDetail : '')],
      [MODE.UNSUPPORTED]: ['warn', 'Multiview is not supported by this decoder.'],
      [MODE.DECODER_SELECTED_EMPTY]: ['muted',
        'No Multiviews are saved on this decoder yet.'],
    };
    let entry = messages[state.mode];
    if (!entry && state.mode === MODE.DECODER_SELECTED) {
      entry = null;
    }
    // Video Wall and Fast Switching bar Multiview outright, and the operator is
    // told before being offered anything rather than after an attempt fails.
    const blocked = ((state.decoderState || {}).interlocks) || [];
    if (blocked.length) {
      entry = ['error', blocked.map((item) => item.reason).join(' ')];
    }
    if (!entry) { banner.hidden = true; banner.textContent = ''; return; }
    banner.hidden = false;
    banner.className = 'mv-banner ' + entry[0];
    banner.textContent = entry[1];
  }

  function renderShownState(view) {
    const shown = el('mv_shown');
    if (!view) { shown.textContent = ''; return; }
    const isShown = !!view.selected_on_output;
    shown.className = 'mv-shown' + (isShown ? '' : ' not-shown');
    shown.textContent = isShown
      ? 'Currently shown on display' : 'Not currently shown on display';
  }

  // ---------------------------------------------------------------- pickers

  function populateTargets() {
    const select = el('mv_target');
    const views = (state.decoderState || {}).multiviews || [];
    select.replaceChildren();
    // The dropdown holds real objects only. Creating one is an action, not an
    // entry that pretends to be a Multiview that already exists.
    select.appendChild(new Option(
      views.length ? 'Select a Multiview…' : 'No Multiviews saved', ''));
    // Name and layout distinguish one from another. The canvas is always
    // 1920x1080, so putting it on every row says nothing.
    views.forEach((view) => {
      select.appendChild(new Option(
        view.name + '  —  ' + view.layout_label
          + (view.selected_on_output ? '  (on display)' : ''),
        view.name));
    });
    select.value = state.editing || '';
  }

  // ---------------------------------------------------------------- sources

  // ---- remembering where the operator was -------------------------------
  //
  // Two identifiers, nothing else. The decoder and the Multiview the operator
  // last had open are restored when the page opens, and every piece of device
  // state behind them is read fresh: the selection is a bookmark, not a cache.
  //
  // A remembered decoder that is offline, undiscovered or no longer capable is
  // not presented as a working selection; a remembered Multiview that has been
  // deleted clears only itself and leaves the decoder in place.
  const SELECTION_DECODER_KEY = 'multiview_selected_decoder';
  const SELECTION_TARGET_KEY = 'multiview_selected_target';

  function readSelection() {
    try {
      return {decoder: localStorage.getItem(SELECTION_DECODER_KEY) || '',
              target: localStorage.getItem(SELECTION_TARGET_KEY) || ''};
    } catch (err) {
      return {decoder: '', target: ''};   // storage refused; start clean
    }
  }

  function rememberDecoder(ip) {
    try {
      if (ip) localStorage.setItem(SELECTION_DECODER_KEY, ip);
      else localStorage.removeItem(SELECTION_DECODER_KEY);
      // A different decoder cannot keep the previous decoder's Multiview.
      localStorage.removeItem(SELECTION_TARGET_KEY);
    } catch (err) { /* not fatal */ }
  }

  function rememberTarget(name) {
    try {
      if (name) localStorage.setItem(SELECTION_TARGET_KEY, name);
      else localStorage.removeItem(SELECTION_TARGET_KEY);
    } catch (err) { /* not fatal */ }
  }

  // Only a decoder the page would let the operator choose today.
  function selectableDecoder(ip) {
    if (!ip) return false;
    const entry = state.decoders.find((d) => d.ip === ip);
    return !!entry && entry.multiview_supported !== false
           && entry.reachable !== false;
  }

  async function restoreSelection() {
    const saved = readSelection();
    if (!selectableDecoder(saved.decoder)) {
      // Offline, gone, or no longer capable: forget it rather than present a
      // selection that cannot be acted on.
      if (saved.decoder) rememberDecoder('');
      return;
    }
    el('mv_decoder').value = saved.decoder;
    await loadDecoderState(saved.decoder,
                           saved.target || undefined);
    if (!saved.target) return;
    const found = ((state.decoderState || {}).multiviews || [])
      .some((view) => view.name === saved.target);
    if (found) {
      el('mv_target').value = saved.target;
      loadIntoEditor(saved.target);
    } else {
      // The decoder is fine; that Multiview is not there any more. Keep the
      // decoder, drop the Multiview, say nothing -- it is not an error.
      rememberTarget('');
    }
  }

  // ---- operator notice --------------------------------------------------
  // Multiview reconfigures shared resources. This says which ones, in the
  // operator's terms, and is shown once per application version -- suppression
  // records the version it was acknowledged for, so a release that changes what
  // Multiview touches shows it again by itself.
  const NOTICE_KEY = 'multiview_notice_acknowledged_version';
  const NOTICE_TITLE = 'Before you use Multiview';

  const NOTICE = {
    intro:
      'Multiview builds a single picture out of several sources. To do that it '
      + 'configures equipment that other people may be using, so it is worth '
      + 'knowing what it can change before you start.',
    mayHeading: 'While you build, show or switch a Multiview, OmniSuite may:',
    may: [
      'set a source encoder\u2019s Encoder 2 to take the same video input as '
      + 'its Encoder 1',
      'change Encoder 2\u2019s scaler to the size the window needs',
      'use the source\u2019s Session 2 to carry Multiview video',
      'turn off the SAP announcement on that Session 2, so no decoder picks it '
      + 'up by accident',
      'change which streams this decoder is subscribed to',
      'use the reserved decoder video inputs ip_input2, ip_input4, ip_input6 '
      + 'and ip_input8',
      'take the display\u2019s sound from the main window\u2019s source, over '
      + 'its ordinary Session 1 audio, using the audio input the display '
      + 'already uses',
      'adjust Encoder 1 or Encoder 2 bit rates, but only where the measured '
      + '900 Mb/s limits leave no alternative',
      'change a window immediately, while it is on screen, when you drop a new '
      + 'source onto an active Multiview',
      'reconcile all of the above when you recall a different Multiview',
    ],
    wontHeading: 'OmniSuite will never do these on its own:',
    wont: [
      'turn off Video Wall',
      'turn off Fast Switching where it conflicts',
      'change an encoder just because you opened this page',
      'turn on preview or thumbnail generation on a source',
    ],
    closing:
      'Nothing is written until you save, show or switch. Every change is read '
      + 'back from the device afterwards, and a change that cannot be verified '
      + 'is undone.',
  };

  function noticeText() {
    const lines = [
      NOTICE_TITLE,
      'OmniSuite ' + (state.version || 'unknown version'),
      'Generated ' + new Date().toLocaleString(),
      '',
      NOTICE.intro,
      '',
      NOTICE.mayHeading,
    ];
    NOTICE.may.forEach((item) => lines.push('  - ' + item));
    lines.push('', NOTICE.wontHeading);
    NOTICE.wont.forEach((item) => lines.push('  - ' + item));
    lines.push('', NOTICE.closing, '');
    return lines.join('\n');
  }

  function renderNotice() {
    const body = el('mv_notice_body');
    body.replaceChildren();
    body.appendChild(node('p', 'mv-notice-intro', NOTICE.intro));

    body.appendChild(node('h3', null, NOTICE.mayHeading));
    const may = node('ul', 'mv-notice-list');
    NOTICE.may.forEach((item) => may.appendChild(node('li', null, item)));
    body.appendChild(may);

    body.appendChild(node('h3', null, NOTICE.wontHeading));
    const wont = node('ul', 'mv-notice-list mv-notice-wont');
    NOTICE.wont.forEach((item) => wont.appendChild(node('li', null, item)));
    body.appendChild(wont);

    body.appendChild(node('p', 'mv-notice-closing', NOTICE.closing));
    const label = el('mv_notice_suppress_label');
    if (label && state.version) {
      label.textContent = 'Do not show this notice again for ' + state.version;
    }
  }

  function noticeAcknowledged() {
    try {
      return localStorage.getItem(NOTICE_KEY) === state.version && !!state.version;
    } catch (err) {
      return false;                       // storage refused: show it
    }
  }

  function showNotice() {
    renderNotice();
    el('mv_notice_suppress').checked = false;
    setNoticeStatus('');
    el('mv_notice').hidden = false;
    el('mv_notice_close').focus();
  }

  function closeNotice() {
    // The checkbox records the version it was acknowledged for. It is never a
    // plain "hidden = true", so the next release asks again by itself.
    try {
      if (el('mv_notice_suppress').checked && state.version) {
        localStorage.setItem(NOTICE_KEY, state.version);
      }
    } catch (err) { /* not fatal; it will simply be shown again */ }
    el('mv_notice').hidden = true;
  }

  function setNoticeStatus(text) {
    const status = el('mv_notice_status');
    if (status) status.textContent = text || '';
  }

  function noticeFilename() {
    const version = (state.version || 'unknown').replace(/[^\w.-]+/g, '');
    return 'OmniSuite_Multiview_Notice_' + version + '.txt';
  }

  async function copyNotice() {
    const text = noticeText();
    try {
      await navigator.clipboard.writeText(text);
      setNoticeStatus('Copied.');
    } catch (err) {
      setNoticeStatus('Could not copy \u2014 use Save to file instead.');
    }
  }

  function saveNotice() {
    const blob = new Blob([noticeText()], {type: 'text/plain;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const link = node('a');
    link.href = url;
    link.download = noticeFilename();
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setNoticeStatus('Saved ' + noticeFilename());
  }

  // ---- source preview ---------------------------------------------------
  // Lazy by construction: nothing is fetched until a pointer has rested on a
  // tile for PREVIEW_DELAY, and everything stops the moment it leaves. The page
  // opens, and sits open, without asking any encoder for anything.
  const PREVIEW_DELAY = 300;      // ms a pointer must rest before we ask

  // One frame, fetched when the card opens.
  //
  // The Matrix page refreshes its hover preview on a 2 s interval, and copying
  // that here would have been the obvious thing to do -- but this page is held
  // to "nothing polls", and a repeating timer is a repeating timer whether or
  // not something is on screen. A still frame taken at the moment of hovering
  // is what the operator needs to tell two encoders apart, and moving off and
  // back on fetches a new one. The thumbnail generator runs at 5 fps, so the
  // frame is at most a fifth of a second old when it is taken.
  const preview = {
    timer: null,        // the debounce, cancelled when hover ends early
    controller: null,   // aborts the status request in flight
    ip: null,           // the tile the card currently belongs to
    card: null,         // the one card, created once and reused
    image: null,        // the frame in it, so closing can drop its connection
    open: false,
  };

  // ---- previews inside the Multiview windows ----------------------------
  //
  // One managed timer for the page, not one per window. It refreshes the
  // thumbnail of every UNIQUE source that is visible and preview-enabled, so
  // two windows showing the same camera cost one image request, not two -- and
  // it does not exist at all while nothing is on screen to refresh.
  //
  // This is the one place the Multiview page runs a repeating timer. It is
  // confined to images the operator is looking at, and it stops on navigation,
  // on the page being hidden, and whenever the visible set becomes empty.
  const WINDOW_PREVIEW_REFRESH = 5000;

  const windowPreview = {
    known: new Map(),     // ip -> {status, url, reason}  (asked once per source)
    asking: new Set(),    // ip -> a request already in flight
    stamp: 0,             // cache-buster shared by every window this tick
    timer: null,
    visible: [],          // the unique source ips currently on the canvas
  };

  function previewFor(ip) {
    if (!ip) return null;
    if (windowPreview.known.has(ip)) return windowPreview.known.get(ip);
    if (!windowPreview.asking.has(ip)) {
      windowPreview.asking.add(ip);
      getJSON('/api/multiview/preview?ip=' + encodeURIComponent(ip))
        .then((body) => {
          windowPreview.known.set(ip, body || {status: 'unavailable'});
        })
        .catch(() => {
          windowPreview.known.set(ip, {status: 'unavailable',
                                       reason: 'OmniSuite could not reach it.'});
        })
        .then(() => {
          windowPreview.asking.delete(ip);
          // Only redraw if this source is still on the canvas.
          if (windowPreview.visible.indexOf(ip) >= 0) renderCanvas();
        });
    }
    return null;                        // not known yet; the window draws plain
  }

  function windowPreviewUrl(ip) {
    const known = windowPreview.known.get(ip);
    if (!known || known.status !== 'available' || !known.url) return null;
    return known.url + '?t=' + windowPreview.stamp;
  }

  // Started and stopped from exactly one place, so recalls, switches, theme
  // changes and re-renders cannot multiply it.
  function syncWindowPreviewTimer() {
    const wanted = windowPreview.visible.some(
      (ip) => (windowPreview.known.get(ip) || {}).status === 'available');
    if (!wanted || (document.hidden === true)) {
      if (windowPreview.timer) {
        clearInterval(windowPreview.timer);
        windowPreview.timer = null;
      }
      return;
    }
    if (windowPreview.timer) return;          // already running: do not add one
    windowPreview.timer = setInterval(() => {
      if (!windowPreview.visible.length || document.hidden === true) {
        syncWindowPreviewTimer();
        return;
      }
      windowPreview.stamp = Date.now();
      // One preload per unique source. Every window showing that source then
      // points at the same URL, which the browser serves from cache -- so N
      // windows on one camera stay one request.
      windowPreview.visible.forEach((ip) => {
        const url = windowPreviewUrl(ip);
        if (!url) return;
        const loader = new Image();
        loader.onload = () => {
          document.querySelectorAll('.mv-window-shot').forEach((image) => {
            if (image.dataset.ip === ip) image.src = url;
          });
        };
        loader.onerror = () => {};
        loader.src = url;
      });
    }, WINDOW_PREVIEW_REFRESH);
  }

  function stopWindowPreviews() {
    if (windowPreview.timer) {
      clearInterval(windowPreview.timer);
      windowPreview.timer = null;
    }
    windowPreview.visible = [];
  }

  function previewCard() {
    if (preview.card) return preview.card;
    const card = node('div', 'mv-preview');
    card.id = 'mv_preview';
    card.setAttribute('role', 'tooltip');
    card.hidden = true;
    document.body.appendChild(card);
    preview.card = card;
    return card;
  }

  // Everything that has to stop, in one place, so no caller can stop half of it.
  function closePreview() {
    if (preview.timer) { clearTimeout(preview.timer); preview.timer = null; }
    if (preview.controller) { preview.controller.abort(); preview.controller = null; }
    preview.ip = null;
    preview.open = false;
    const card = preview.card;
    // `image` is cleared after the card is emptied below.
    if (card) {
      // Drop the src as well as hiding the card: a hidden <img> with a src is
      // still a connection the browser may be holding open.
      if (preview.image) preview.image.removeAttribute('src');
      card.hidden = true;
      card.replaceChildren();
    }
    preview.image = null;
  }

  function schedulePreview(tile, source) {
    closePreview();
    preview.timer = setTimeout(() => {
      preview.timer = null;
      openPreview(tile, source);
    }, PREVIEW_DELAY);
  }

  async function openPreview(tile, source) {
    preview.ip = source.ip;
    const controller = new AbortController();
    preview.controller = controller;
    let body = null;
    try {
      body = await getJSON('/api/multiview/preview?ip='
                           + encodeURIComponent(source.ip),
                           {signal: controller.signal});
    } catch (err) {
      if (controller.signal.aborted) return;
      body = {status: 'unavailable', reason: 'OmniSuite could not reach it.'};
    }
    // The pointer may have moved on while that was in flight.
    if (controller.signal.aborted || preview.ip !== source.ip) return;
    preview.controller = null;
    renderPreview(tile, source, body || {});
  }

  function renderPreview(tile, source, body) {
    const card = previewCard();
    card.replaceChildren();
    card.appendChild(node('div', 'mv-preview-name', source.hostname));
    card.appendChild(node('div', 'mv-preview-meta',
      (source.model || 'Encoder') + ' · ' + source.ip));

    const frame = node('div', 'mv-preview-frame');
    if (body.status === 'available' && body.url) {
      const image = node('img');
      preview.image = image;
      image.alt = 'Live preview of ' + source.hostname;
      image.src = body.url + '?t=' + Date.now();
      // A frame that never arrives must not leave the card looking like a
      // working preview that happens to be black.
      image.addEventListener('error', () => {
        if (preview.ip !== source.ip) return;
        frame.replaceChildren(node('div', 'mv-preview-note',
                                   'Preview unavailable'));
      });
      frame.appendChild(image);
    } else {
      frame.appendChild(node('div', 'mv-preview-note',
        body.status === 'disabled' ? 'Preview disabled' : 'Preview unavailable'));
      if (body.reason) {
        frame.appendChild(node('div', 'mv-preview-why', body.reason));
      }
    }
    card.appendChild(frame);

    // Useful, not exhaustive: what the operator needs to tell two sources
    // apart, and nothing that belongs in the engineering panel.
    const streams = node('div', 'mv-preview-streams');
    streams.appendChild(node('span', null, 'S1 ' + (source.session1 || 'not set')));
    streams.appendChild(node('span', null, 'S2 ' + (source.session2 || 'not set')));
    card.appendChild(streams);
    if (source.status === 'configuration_required' && source.reason) {
      card.appendChild(node('div', 'mv-preview-warning', source.reason));
    }

    card.hidden = false;
    preview.open = true;
    positionPreview(card, tile);
  }

  // Beside the tile, flipped up near the bottom of the window, and never
  // off-screen -- a card that is clipped is worse than no card.
  function positionPreview(card, tile) {
    const rect = tile.getBoundingClientRect();
    card.style.visibility = 'hidden';
    card.style.top = '0px';
    card.style.left = '0px';
    const size = card.getBoundingClientRect();
    const gap = 12;

    let left = rect.right + gap;
    if (left + size.width > window.innerWidth - 8) {
      left = rect.left - size.width - gap;       // the other side of the tile
    }
    if (left < 8) left = Math.max(8, window.innerWidth - size.width - 8);

    let top = rect.top;
    if (top + size.height > window.innerHeight - 8) {
      top = window.innerHeight - size.height - 8;   // flip upward
    }
    if (top < 8) top = 8;

    card.style.left = Math.round(left) + 'px';
    card.style.top = Math.round(top) + 'px';
    card.style.visibility = '';
  }

  function renderSources() {
    const container = el('mv_sources');
    closePreview();
    container.replaceChildren();
    if (!state.sources.length) {
      container.appendChild(node('div', 'mv-empty',
        'No eligible encoders were discovered. Run a scan from Device Info.'));
    }
    state.sources.forEach((source) => {
      const ready = source.status !== 'configuration_required';
      const tile = node('div', 'mv-source' + (ready ? '' : ' needs-work'));
      tile.draggable = ready;
      tile.tabIndex = 0;
      tile.dataset.ip = source.ip;
      tile.dataset.status = source.status || '';
      tile.setAttribute('role', 'button');
      tile.setAttribute('aria-label',
        'Source ' + source.hostname + ' at ' + source.ip +
        (ready ? '. Select, then choose a window.'
               : '. ' + (source.reason || 'Not ready.')));
      tile.appendChild(node('div', 'mv-source-name', source.hostname));
      tile.appendChild(node('div', 'mv-source-meta',
        (source.model || 'Encoder') + ' · ' + source.ip));
      tile.appendChild(node('div', 'mv-source-streams',
        'S1 ' + (source.session1 || 'not set') +
        '   ·   S2 ' + (source.session2 || 'not set')));
      if (!ready) {
        // Not hidden: this is the one an operator can actually fix, and hiding
        // it would leave them wondering where the encoder went.
        tile.appendChild(node('div', 'mv-source-warning', source.reason));
        if (source.detail) {
          tile.appendChild(node('div', 'mv-source-meta', source.detail));
        }
      } else if (state.unreachable.indexOf(source.ip) >= 0) {
        tile.classList.add('unreachable');
        tile.appendChild(node('div', 'mv-source-warning',
          'Did not answer — cannot be prepared'));
      }

      // Hover is the whole feature. It is also the thing most likely to get in
      // the way of a drag, so a drag closes it before anything else happens.
      tile.addEventListener('pointerenter', (event) => {
        if (event.pointerType === 'touch') return;   // not a hover device
        schedulePreview(tile, source);
      });
      tile.addEventListener('pointerleave', closePreview);
      tile.addEventListener('focus', () => schedulePreview(tile, source));
      tile.addEventListener('blur', closePreview);

      tile.addEventListener('dragstart', (event) => {
        closePreview();
        if (!ready) { event.preventDefault(); return; }
        tile.classList.add('dragging');
        event.dataTransfer.setData('text/plain', source.ip);
        event.dataTransfer.effectAllowed = 'copy';
      });
      tile.addEventListener('dragend', () => tile.classList.remove('dragging'));
      tile.addEventListener('click', () => {
        closePreview();
        if (ready) pickSource(source.ip);
      });
      tile.addEventListener('keydown', (event) => {
        if ((event.key === 'Enter' || event.key === ' ') && ready) {
          event.preventDefault();
          pickSource(source.ip);
        }
      });
      container.appendChild(tile);
    });

    const excluded = el('mv_sources_excluded');
    excluded.replaceChildren();
    if (state.excluded.length) {
      const detail = node('details', 'mv-detail');
      detail.appendChild(node('summary', null,
        state.excluded.length + ' encoder(s) not eligible'));
      state.excluded.forEach((entry) => {
        detail.appendChild(node('div', 'mv-source-meta',
          entry.hostname + ' (' + (entry.model || '') + ') — ' + (entry.reason || '')));
      });
      excluded.appendChild(detail);
    }
  }

  function pickSource(ip) {
    state.pickedSource = state.pickedSource === ip ? null : ip;
    document.querySelectorAll('.mv-source').forEach((tile) => {
      tile.classList.toggle('dragging', tile.dataset.ip === state.pickedSource);
    });
    notify(state.pickedSource
      ? 'Now choose a window for this source.'
      : 'Source cleared.', 'ok');
  }

  // ---------------------------------------------------------------- canvas

  function currentLayout() {
    const id = el('mv_layout').value;
    return state.layouts.find((entry) => entry.id === id) || null;
  }

  function currentGeometry() {
    const layout = currentLayout();
    if (!layout) return null;
    return layout.canvases[state.canvas] || null;
  }

  function renderCanvas() {
    const stage = el('mv_stage');
    const caption = el('mv_caption');
    stage.replaceChildren();
    caption.replaceChildren();

    const geometry = currentGeometry();
    if (!geometry) return;
    const canvas = geometry.canvas;
    stage.style.aspectRatio = canvas.width + ' / ' + canvas.height;

    // Every unique source on the canvas, for the single refresh timer. Two
    // windows on one camera appear here once.
    const onCanvas = [];
    geometry.windows.forEach((window_) => {
      const ip = state.assignments[window_.cell]
        || ((subframeFor(window_.cell) || {}).source || {}).ip;
      if (ip && onCanvas.indexOf(ip) < 0) onCanvas.push(ip);
    });
    windowPreview.visible = onCanvas;
    onCanvas.forEach(previewFor);            // asks once per source, then caches
    if (!windowPreview.stamp) windowPreview.stamp = Date.now();

    geometry.windows.forEach((window_, index) => {
      const box = node('div', 'mv-window');
      box.style.left = (window_.x / canvas.width * 100) + '%';
      box.style.top = (window_.y / canvas.height * 100) + '%';
      box.style.width = (window_.width / canvas.width * 100) + '%';
      box.style.height = (window_.height / canvas.height * 100) + '%';
      // A PiP inset is anchored to its own corner, so its x/y is that corner.
      if (window_.anchor.indexOf('right') >= 0) {
        box.style.left = 'auto';
        box.style.right = ((canvas.width - window_.x) / canvas.width * 100) + '%';
      }
      if (window_.anchor.indexOf('bottom') >= 0) {
        box.style.top = 'auto';
        box.style.bottom = ((canvas.height - window_.y) / canvas.height * 100) + '%';
      }
      box.style.zIndex = String(10 + index);

      const assignedIp = state.assignments[window_.cell];
      const source = state.sources.find((s) => s.ip === assignedIp);
      const subframe = subframeFor(window_.cell);
      box.classList.toggle('assigned', !!assignedIp || !!(subframe && subframe.subscribed));
      if (subframe && subframe.health) {
        box.classList.add('health-' + subframe.health.replace(/ /g, '-'));
      }

      // The picture, when there is one. It sits behind everything else in the
      // window and the text sits on a scrim over it, so an overlay stays
      // readable whether the video under it is a night sky or a white slide.
      const showingIp = assignedIp
        || ((subframe || {}).source || {}).ip || null;
      const preview = showingIp ? windowPreview.known.get(showingIp) : null;
      const shotUrl = showingIp ? windowPreviewUrl(showingIp) : null;
      if (shotUrl) {
        const shot = node('img', 'mv-window-shot');
        shot.dataset.ip = showingIp;
        shot.alt = '';                        // decorative; the text says it all
        shot.src = shotUrl;
        box.appendChild(shot);
        box.classList.add('has-shot');
      }

      box.appendChild(node('div', 'mv-window-index', 'W' + (index + 1)));

      const overlay = node('div', 'mv-window-overlay');
      if (assignedIp) {
        const clear = node('button', 'mv-window-clear', '×');
        clear.type = 'button';
        clear.title = 'Clear this window';
        clear.setAttribute('aria-label', 'Clear window ' + (index + 1));
        clear.addEventListener('click', (event) => {
          event.stopPropagation();
          // Clearing a window of the Multiview on the display is a change to
          // the display, so it takes the same verified path a drop does.
          if (isLive()) { switchLive(window_.cell, ''); return; }
          delete state.assignments[window_.cell];
          render();
        });
        box.appendChild(clear);
        overlay.appendChild(node('div', 'mv-window-title',
          source ? source.hostname : assignedIp));
        overlay.appendChild(node('div', 'mv-window-sub',
          (source ? source.ip : assignedIp) + ' · ' +
          window_.width + 'x' + window_.height));
        overlay.appendChild(node('div', 'mv-window-tag',
          windowTag(window_, subframe)));
      } else if (subframe && subframe.subscribed) {
        // Nothing is assigned in the editor, but the decoder is plainly
        // receiving something here. Say what, rather than drawing it empty.
        overlay.appendChild(node('div', 'mv-window-title',
          subframe.source ? subframe.source.hostname : 'Unknown source'));
        overlay.appendChild(node('div', 'mv-window-sub', subframe.stream));
        overlay.appendChild(node('div', 'mv-window-tag',
          windowTag(window_, subframe)));
      } else {
        overlay.appendChild(node('div', 'mv-window-title',
          prettyCell(window_.cell)));
        overlay.appendChild(node('div', 'mv-window-sub',
          window_.width + 'x' + window_.height));
        overlay.appendChild(node('div', 'mv-window-hint',
          isLive() ? 'Drop a source to switch' : 'Drop a source here'));
      }
      // A preview that cannot be shown is said quietly, and never instead of
      // the source information -- the window is still routable either way. The
      // device's placeholder JPEG is deliberately not displayed: it looks like
      // a picture and is not one.
      if (showingIp && preview && preview.status === 'disabled') {
        overlay.appendChild(node('div', 'mv-window-preview-note',
                                 'Preview disabled'));
      } else if (showingIp && preview && preview.status === 'unavailable') {
        overlay.appendChild(node('div', 'mv-window-preview-note',
                                 'Preview unavailable'));
      }

      // What is happening to this window right now, while it happens.
      const busy = state.switching;
      if (busy && busy.cell === window_.cell) {
        box.classList.add('switching');
        overlay.appendChild(node('div', 'mv-window-state', 'Switching…'));
      } else if (subframe && subframe.health === 'live') {
        overlay.appendChild(node('div', 'mv-window-state mv-window-live', 'Live'));
      }
      box.appendChild(overlay);

      box.setAttribute('aria-label', prettyCell(window_.cell) + ', ' +
        window_.width + 'x' + window_.height + ', x ' + window_.x + ' y ' + window_.y +
        ', anchor ' + window_.anchor + ', encoder 2' +
        (subframe ? ', ' + subframe.health : '') +
        (isLive() ? '. Dropping a source here switches the display immediately.'
                  : ''));

      box.addEventListener('dragover', (event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
        box.classList.add('drop-target');
      });
      box.addEventListener('dragleave', () => box.classList.remove('drop-target'));
      box.addEventListener('drop', (event) => {
        event.preventDefault();
        box.classList.remove('drop-target');
        const ip = event.dataTransfer.getData('text/plain');
        if (ip) assign(window_.cell, ip);
      });
      box.addEventListener('click', () => {
        if (state.pickedSource) {
          assign(window_.cell, state.pickedSource);
          pickSource(state.pickedSource);
        }
      });
      stage.appendChild(box);
    });

    const note = el('mv_canvas_note');
    if (note) note.textContent = state.canvas;
    const mode = el('mv_canvas_mode');
    if (mode) {
      const live = isLive();
      mode.hidden = state.mode !== MODE.EDIT && state.mode !== MODE.CREATE;
      mode.className = 'mv-canvas-mode' + (live ? ' live' : '');
      mode.textContent = live
        ? 'LIVE — changes made on this canvas are applied immediately'
        : state.mode === MODE.EDIT
          ? 'Not on the display — changes are saved, and shown when you choose'
          : 'New Multiview — changes are saved, and shown when you choose';
    }
    stage.classList.toggle('live', isLive());
    caption.appendChild(captionItem('Display output', state.canvas));
    if (geometry.snapped) {
      // The compositor's canvas is snapped to the layout grid; the display
      // still runs at the preset, because the decoder has no such output mode.
      caption.appendChild(captionItem('Compositor canvas',
        canvas.width + 'x' + canvas.height + ' — snapped to the layout grid'));
    }
    caption.appendChild(captionItem('Windows', geometry.windows.length));
    syncWindowPreviewTimer();
  }

  // What the decoder is doing with this window, in the operator's terms.
  function windowTag(geometry, subframe) {
    if (!subframe) return 'Encoder 2 / Session 2';
    const parts = ['Encoder 2 / Session 2'];
    if (subframe.ip_input) parts.push(subframe.ip_input);
    if (subframe.health === 'live') parts.push('live');
    else if (subframe.health === 'no signal') parts.push('no signal');
    return parts.join(' · ');
  }

  function subframeFor(cell) {
    const view = currentView();
    if (!view) return null;
    return (view.subframes || []).find((entry) => entry.cell === cell) || null;
  }

  function captionItem(label, value) {
    const wrap = node('span', null, label + ': ');
    wrap.appendChild(node('strong', null, value));
    return wrap;
  }

  function prettyCell(cell) {
    return String(cell || '').replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function assign(cell, ip) {
    if (isLive()) { switchLive(cell, ip); return; }
    if (ip) state.assignments[cell] = ip;
    else delete state.assignments[cell];
    render();
  }

  // A live switch on an ACTIVE Multiview is applied immediately.
  //
  // There is no confirmation dialog. The canvas already says "LIVE -- changes
  // made on this canvas are applied immediately", and asking again on every
  // drop turns that warning into a formality people click through. What is NOT
  // removed is any of the safety: the whole Multiview is still replanned, every
  // rule still applies, every write is still read back, and a switch that
  // cannot be verified is still rolled back.
  async function switchLive(cell, ip) {
    const view = currentView();
    if (!view) return;
    const previous = state.assignments[cell] || null;

    // Optimistic only as far as saying something is happening. The source is
    // not presented as switched until the decoder has confirmed it.
    state.switching = {cell: cell, to: ip, from: previous};
    render();

    await runTransaction((ip ? 'Switching ' : 'Clearing ') + prettyCell(cell) + '…',
                         '/api/multiview/switch',
                         {decoder: el('mv_decoder').value, name: view.name,
                          cell: cell, source: ip},
                         (body) => {
      state.switching = null;
      if (body.ok) return;
      // The decoder rolled back, so the window goes back to the source it is
      // still showing rather than the one that was asked for.
      const restored = body.restored_source || previous;
      if (restored) state.assignments[cell] = restored;
      else delete state.assignments[cell];
    });
    state.switching = null;
  }

  // ---------------------------------------------------------------- existing

  function renderExisting() {
    const container = el('mv_existing');
    container.replaceChildren();
    const data = state.decoderState;
    if (!data) return;
    if (!data.multiviews.length) {
      container.appendChild(node('div', 'mv-empty',
        'No Multiviews are configured on this decoder yet.'));
    }
    data.multiviews.forEach((view) => {
      const item = node('div', 'mv-existing-item'
        + (view.name === state.editing ? ' current' : '')
        + (view.selected_on_output ? ' shown' : ''));
      const title = node('div', null, view.name);
      title.style.fontWeight = '600';
      if (view.selected_on_output) {
        title.appendChild(node('span', 'mv-badge on-output', 'on display'));
      }
      if (!view.layout) {
        title.appendChild(node('span', 'mv-badge custom', 'custom'));
      }
      item.appendChild(title);
      item.appendChild(node('div', 'mv-source-meta',
        view.layout_label + ' · ' + view.width + 'x' + view.height +
        ' · ' + view.subframes.length + ' window(s)'));
      if (view.layout_diverged) {
        item.appendChild(node('div', 'mv-source-meta',
          'Stored layout was ' + (view.stored_layout || 'unknown') +
          '; the geometry on the device no longer matches it.'));
      }
      view.subframes.forEach((subframe) => {
        item.appendChild(node('div', 'mv-source-meta',
          subframe.cell +
          (subframe.width ? ' (' + subframe.width + 'x' + subframe.height + ')' : '') +
          ' · ' + (subframe.ip_input || 'unassigned') +
          (subframe.source ? ' · ' + subframe.source.hostname : '') +
          (subframe.output_active ? ' · active' : '')));
      });
      container.appendChild(item);
    });

    const output = data.hdmi_output || {};
    container.appendChild(node('div', 'mv-note info',
      'Display is currently showing: ' + (output.video_input || 'nothing') +
      '   ·   audio: ' + (output.audio_input || 'none') +
      '   ·   automatic source selection: ' +
      (output.sap_enabled ? 'on' : 'off')));
  }

  // ---------------------------------------------------------------- plan

  function desiredState() {
    return {
      decoder: el('mv_decoder').value,
      layout: el('mv_layout').value,
      canvas: state.canvas,
      name: el('mv_name').value,
      object_name: state.editing || undefined,
      update_existing: !!state.editing,
      // Saving never changes what is on the display. That is Show on Display,
      // and it is a separate, deliberate action.
      select_on_output: false,
      assignments: Object.assign({}, state.assignments),
    };
  }

  function schedulePlan() {
    if (!isEditingOrCreating()) {
      state.plan = null; state.planSignature = '';
      state.planPending = false; renderPlan(); return;
    }
    const desired = desiredState();
    if (!desired.decoder || !Object.keys(desired.assignments).length) {
      state.plan = null; state.planSignature = '';
      state.planPending = false; renderPlan(); return;
    }
    const signature = JSON.stringify(desired);
    if (signature === state.planSignature) return;
    state.planSignature = signature;
    if (planTimer) clearTimeout(planTimer);
    // Shown as pending from the moment the desired state changes, not from when
    // the request goes out. A source that does not answer makes this take a
    // second or two, and during that time the page must not look idle -- still
    // less claim that nothing has been assigned.
    state.planPending = true;
    renderPlan();
    renderActions();
    // Debounced: a plan reads the decoder and each assigned encoder, so it must
    // not fire on every drag frame or every keystroke in the name field.
    planTimer = setTimeout(() => runPlan(desired), 500);
  }

  async function runPlan(desired) {
    const token = ++planSeq;
    try {
      const body = await getJSON('/api/multiview/plan', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(desired),
      });
      if (token !== planSeq) return;
      state.plan = body.plan || null;
    } catch (err) {
      if (token !== planSeq) return;
      state.plan = {ok: false, errors: [err.message], warnings: [], conflicts: [],
                    windows: [], mutations: []};
    }
    state.planPending = false;
    // A source is discovered from the cache, so it can be listed and still not
    // answer. The plan is the first thing that finds out; remember which ones,
    // so the tile says so instead of the operator learning it per-window.
    const plan = state.plan || {};
    state.unreachable = (plan.windows || [])
      .filter((w) => w.source && w.source.error)
      .map((w) => w.source.ip);
    renderPlan();
    renderSources();
    renderActions();
  }

  function renderPlan() {
    const container = el('mv_plan');
    const notes = el('mv_notes');
    container.replaceChildren();
    notes.replaceChildren();
    const plan = state.plan;
    if (state.planPending && !plan) {
      container.appendChild(node('div', 'mv-empty',
        'Checking the sources and working out what needs to change…'));
      return;
    }
    if (!plan) {
      container.appendChild(node('div', 'mv-empty',
        'Assign at least one source to see what Save would change.'));
      return;
    }
    if (state.planPending) {
      container.appendChild(node('div', 'mv-empty', 'Rechecking…'));
    }

    if (plan.windows && plan.windows.length) {
      const table = node('table', 'mv-table');
      const head = node('tr');
      ['Window', 'Source', 'Scaler', 'Encoder', 'Bitrate', 'Decoder input']
        .forEach((label) => head.appendChild(node('th', null, label)));
      table.appendChild(head);
      plan.windows.forEach((window_) => {
        const row = node('tr');
        const label = node('td', null, 'W' + (window_.window_number || '?') + ' · '
                                       + prettyCell(window_.cell));
        if (window_.is_main) {
          // The main window owns the display's audio, over the source's ordinary
          // Session 1 path. Nothing else on the page says where sound comes from.
          label.appendChild(node('div', 'mv-window-audio', 'Audio: Session 1'));
        }
        row.appendChild(label);
        row.appendChild(node('td', null,
          window_.source ? window_.source.hostname : '—'));
        row.appendChild(node('td', null, window_.scaler_format));
        row.appendChild(node('td', null,
          window_.source ? 'Encoder 2 / Session 2' : '—'));
        // The configured bitrate, and -- only when it is below the layout's
        // target -- what the target was. An operator seeing 150 where the
        // policy says 200 should be able to read why without opening the
        // engineering panel.
        const bitrate = node('td', null);
        if (window_.bitrate == null) {
          bitrate.textContent = '—';
        } else {
          bitrate.appendChild(node('div', null, window_.bitrate + ' Mb/s'));
          if (window_.bitrate_target != null
              && window_.bitrate < window_.bitrate_target) {
            bitrate.appendChild(node('div', 'mv-bitrate-target',
              'target ' + window_.bitrate_target + ' Mb/s'));
          }
        }
        row.appendChild(bitrate);
        row.appendChild(node('td', null,
          window_.ip_input ? window_.ip_input.ip_input : '—'));
        table.appendChild(row);
      });
      container.appendChild(table);
    }

    const bandwidth = plan.bandwidth;
    if (bandwidth && bandwidth.unique_streams) {
      const windows = bandwidth.window_count || 0;
      container.appendChild(node('div', 'mv-summary',
        bandwidth.unique_streams + ' stream' +
        (bandwidth.unique_streams === 1 ? '' : 's') +
        (windows > bandwidth.unique_streams
          ? ' for ' + windows + ' windows' : '') +
        ', ' + bandwidth.decoder_aggregate + ' of ' + bandwidth.budget_decoder
        + ' Mb/s to this decoder'));
    }

    const mutations = plan.mutations || [];
    const activation = plan.activation || [];
    if (plan.ok) {
      container.appendChild(node('div', 'mv-summary',
        activation.length
          ? 'Saving stores this configuration. Showing it prepares the sources '
            + 'and the decoder — ' + activation.length + ' device change'
            + (activation.length === 1 ? '' : 's') + '.'
          : 'Saving stores this configuration. The devices already match it.'));
    }

    (plan.conflicts || []).forEach((text) => notes.appendChild(note('error', text)));
    (plan.errors || []).forEach((text) => notes.appendChild(note('error', text)));
    (plan.warnings || []).forEach((text) => notes.appendChild(note('warn', text)));
    renderEngineering(plan);
  }

  // Engineering detail: the exact writes, the bandwidth arithmetic and the
  // decoder's own health fields. Collapsed, because none of it is what an
  // operator building a layout is looking at.
  function renderEngineering(plan) {
    const wrap = el('mv_engineering');
    const body = el('mv_engineering_body');
    if (!wrap || !body) return;
    body.replaceChildren();
    const mutations = (plan && plan.mutations) || [];

    const activation = (plan && plan.activation) || [];
    const summary = el('mv_engineering_summary');
    if (summary) {
      summary.textContent = 'Engineering detail — ' + mutations.length + ' on save, '
        + activation.length + ' on show';
    }

    if (mutations.length) {
      body.appendChild(node('div', 'mv-detail-head', 'Written when you save'));
      mutations.forEach((mutation) => {
        body.appendChild(node('div', 'mv-source-meta', '• ' + mutation.description));
      });
    }
    if (activation.length) {
      body.appendChild(node('div', 'mv-detail-head',
        'Written when this Multiview is shown'));
      activation.forEach((mutation) => {
        body.appendChild(node('div', 'mv-source-meta',
          '• ' + mutation.description + (mutation.shared ? '  (shared source)' : '')));
      });
    }

    const bandwidth = plan && plan.bandwidth;
    if (bandwidth && (bandwidth.allocations || []).length) {
      body.appendChild(node('div', 'mv-detail-head', 'Bandwidth'));
      bandwidth.allocations.forEach((allocation) => {
        // The arithmetic, in the order it is done: what the layout asks for,
        // what the source has left, and what that produces.
        const parts = [allocation.source_ip,
                       'layout target ' + allocation.target + ' Mb/s'];
        if (allocation.encoder1_bitrate != null) {
          parts.push('Encoder 1 ' + allocation.encoder1_bitrate + ' Mb/s');
          parts.push('headroom ' + allocation.headroom + ' Mb/s');
        }
        parts.push(allocation.bitrate == null
          ? 'Encoder 2 cannot run'
          : 'Encoder 2 ' + allocation.bitrate + ' Mb/s');
        if (allocation.error) parts.push(allocation.error);
        else if (allocation.capped_by) parts.push(allocation.capped_by);
        body.appendChild(node('div', 'mv-source-meta', '• ' + parts.join('  ·  ')));
      });
      body.appendChild(node('div', 'mv-source-meta',
        'Budgets: ' + bandwidth.budget_source + ' Mb/s per source (Encoder 1 + '
        + 'Encoder 2), ' + bandwidth.budget_decoder + ' Mb/s of unique streams '
        + 'at the decoder.'));
      if (bandwidth.targets) {
        body.appendChild(node('div', 'mv-source-meta',
          'Targets: ' + bandwidth.targets.equal + ' Mb/s for equal windows, '
          + bandwidth.targets.main + ' Mb/s for the main window, '
          + bandwidth.targets.small + ' Mb/s for the smaller ones. Encoder 2 '
          + 'gets its target or whatever Encoder 1 leaves of the source, '
          + 'whichever is smaller — Encoder 1 is never reduced to make room.'));
      }
    }

    const audio = plan && plan.audio;
    if (audio) {
      body.appendChild(node('div', 'mv-detail-head', 'Audio'));
      body.appendChild(node('div', 'mv-source-meta', audio.available
        ? '• Main window ' + prettyCell(audio.main_window) + ' — '
          + (audio.source_hostname || audio.source_ip) + ' Session 1 audio, '
          + audio.address + ':' + audio.port + '. Applied when the Multiview is shown.'
        : '• ' + (audio.reason || 'No audio source could be established.')));
    }

    const output = ((state.decoderState || {}).hdmi_output) || {};
    body.appendChild(node('div', 'mv-detail-head', 'Decoder'));
    [['Output resolution', output.output_resolution || '—'],
     ['Input status', (output.input_status || {}).active
        ? ((output.input_status.resolution || {}).width + 'x'
           + (output.input_status.resolution || {}).height)
        : 'No active video'],
     ['Video Wall', output.video_wall ? 'enabled' : 'disabled'],
     ['Fast Switching', output.fast_switching ? 'enabled' : 'disabled'],
     ['Automatic source selection (SAP)', output.sap_enabled ? 'on' : 'off'],
     ['Multiview input pool', (state.windowInputs || []).join(', ') || '—'],
    ].forEach(([label, value]) => {
      body.appendChild(node('div', 'mv-source-meta', '• ' + label + ': ' + value));
    });
  }

  function note(kind, text) {
    return node('div', 'mv-note ' + kind, text);
  }

  // Why Save cannot be used yet, in the operator's terms. Returning '' means it
  // can. The action itself is never withdrawn -- an action that disappears when
  // the design is nearly right is indistinguishable from one that was never
  // there, which is exactly how this page lost its Save button.
  function saveBlocker() {
    if (state.mode === MODE.SAVING) return 'Working…';
    const assigned = Object.keys(state.assignments).length;
    if (!assigned) return 'Assign a source to at least one window.';
    if (state.planPending || !state.plan) {
      return 'Checking the sources…';
    }
    const plan = state.plan;
    if (plan.conflicts && plan.conflicts.length) return plan.conflicts[0];
    if (plan.errors && plan.errors.length) return plan.errors[0];
    if (!plan.ok) return 'This configuration cannot be applied.';
    return '';
  }

  function renderActions() {
    const blocker = saveBlocker();
    const save = el('mv_save');
    save.disabled = !!blocker;
    save.title = blocker || '';
    // On the active Multiview a source change has already been applied by the
    // time Save is reachable, so the button must not promise to do it again.
    save.textContent = isLive() ? 'Save layout and name' : 'Save Multiview';
    const reason = el('mv_save_reason');
    reason.textContent = blocker;
    reason.className = 'mv-reason' + (blocker && blocker !== 'Checking the sources…'
      && blocker !== 'Working…' ? ' blocking' : '');
  }

  // ---------------------------------------------------------------- actions

  async function save() {
    const plan = state.plan;
    if (!plan || !plan.ok) return;
    const mutations = plan.mutations || [];

    // No confirmation when nothing would change: a dialog that asks about zero
    // mutations trains the operator to dismiss it.
    if (mutations.length) {
      const summary = [
        {label: 'Decoder', value: plan.decoder.hostname},
        {label: 'Layout', value: labelFor(plan.layout)},
        {label: 'Display output', value: plan.output_resolution || state.canvas},
        {label: plan.updating ? 'Updating' : 'Creating', value: plan.object_name},
      ];
      plan.windows.filter((w) => w.source).forEach((w, index) => {
        summary.push({
          label: 'W' + (w.window_number || index + 1) + ' · ' + prettyCell(w.cell),
          value: w.source.hostname + ' · Encoder 2 / Session 2 · ' +
                 w.scaler_format + ' · ' + w.bitrate + ' Mb/s · ' +
                 (w.ip_input ? w.ip_input.ip_input : '—') +
                 (w.is_main ? ' · audio' : ''),
        });
      });
      mutations.forEach((mutation, index) => {
        summary.push({label: index === 0 ? 'Changes' : ' ',
                      value: mutation.description});
      });
      (plan.warnings || []).forEach((text, index) => {
        summary.push({label: index === 0 ? 'Warnings' : ' ', value: text});
      });

      const confirmed = await window.omniConfirm({
        title: 'Save Multiview',
        message: 'This stores the configuration below on the decoder. No source '
          + 'and no input is changed, and the display is untouched — the sources '
          + 'are prepared when you show it.',
        summary: summary,
        confirmText: 'Save',
      });
      if (!confirmed) return;
    }

    await runTransaction('Saving…', '/api/multiview/apply', desiredState(),
                         (body) => {
                           if (body.ok && body.plan) state.editing = body.plan.object_name;
                         });
  }

  async function showOnDisplay() {
    const view = currentView();
    if (!view) return;
    // No confirmation. Selecting a Multiview and pressing Show on Display is
    // the intent; a dialog that restates the button is noise. The transaction
    // behind it is unchanged -- plan, reconcile, verify, roll back on failure.
    if (view.showable === false) {
      // The server has already said this one cannot go on the display, so the
      // page does not send a request it knows will be refused.
      setStatus(view.not_showable_reason || 'This Multiview cannot be shown.',
                false);
      notify(view.not_showable_reason || 'This Multiview cannot be shown.');
      return;
    }
    await runTransaction('Showing on display…', '/api/multiview/show',
                         {decoder: el('mv_decoder').value, name: view.name});
  }

  async function remove() {
    const view = currentView();
    if (!view) return;
    const confirmed = await window.omniConfirm({
      title: 'Delete Multiview',
      message: view.selected_on_output
        ? 'This Multiview is currently shown on the display. OmniSuite will move '
          + 'the display to another source first, then delete it.'
        : 'This removes the Multiview from the decoder.',
      summary: [
        {label: 'Decoder', value: decoderLabel()},
        {label: 'Multiview', value: view.name},
        {label: 'Layout', value: view.layout_label},
        {label: 'Other saved Multiviews', value: 'not affected'},
        {label: 'Encoder streams', value: 'left configured'},
      ],
      confirmText: 'Delete',
      danger: true,
    });
    if (!confirmed) return;
    await runTransaction('Deleting…', '/api/multiview/delete',
                         {decoder: el('mv_decoder').value, name: view.name},
                         (body) => {
      if (!body.ok) return;
      state.editing = null;
      rememberTarget('');
    });
  }

  async function runTransaction(busyText, url, payload, onSuccess) {
    state.previousMode = state.mode;
    setMode(MODE.SAVING);
    setStatus(busyText, null);
    let body = null;
    try {
      body = await getJSON(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      showResult(body);
      if (onSuccess) onSuccess(body);
    } catch (err) {
      state.outcome = 'ERROR';
      setStatus('FAILED — ' + err.message, false);
      notify(err.message);
    }
    state.planSignature = '';
    const keep = (body && body.ok) ? state.editing : state.editing;
    if (keep) rememberTarget(keep);
    await loadDecoderState(el('mv_decoder').value, keep);
    schedulePlan();
  }

  function decoderLabel() {
    const ip = el('mv_decoder').value;
    const decoder = state.decoders.find((d) => d.ip === ip);
    return decoder ? decoder.hostname + ' (' + ip + ')' : ip;
  }

  function labelFor(layoutId) {
    const layout = state.layouts.find((entry) => entry.id === layoutId);
    return layout ? layout.label : layoutId;
  }

  function showResult(body) {
    const status = body.status || (body.ok ? 'VERIFIED' : 'FAILED');
    state.outcome = body.ok ? 'VERIFIED' : 'ERROR';
    setStatus(status, !!body.ok);
    notify(body.ok ? 'Verified on the decoder.' : status, body.ok ? 'ok' : undefined);

    const detail = node('details', 'mv-detail');
    detail.open = !body.ok;
    detail.appendChild(node('summary', null, 'Engineering detail — ' + status));
    const lines = [];
    (body.verified || []).forEach((entry) => {
      lines.push((entry.verified ? '[verified] ' : '[NOT VERIFIED] ') +
        entry.step + (entry.detail ? '  — ' + entry.detail : ''));
    });
    (body.failures || []).forEach((entry) => {
      lines.push('[failed at ' + entry.stage + '] ' + entry.step + ' — ' + entry.error);
    });
    (body.rollback || []).forEach((entry) => {
      lines.push('[rollback ' + (entry.verified ? 'verified' : 'NOT VERIFIED') + '] ' +
        entry.step + (entry.error ? '  — ' + entry.error : ''));
    });
    (body.steps || []).forEach((entry) => {
      lines.push((entry.verified ? '[verified] ' : '[NOT VERIFIED] ') +
        entry.step + (entry.error ? '  — ' + entry.error : ''));
    });
    detail.appendChild(node('pre', null, lines.join('\n') || 'No changes were needed.'));
    el('mv_result').replaceChildren(detail);
  }

  function setStatus(text, ok) {
    const status = el('mv_status');
    status.textContent = text || '';
    status.className = 'mv-status' + (ok === true ? ' ok' : ok === false ? ' bad' : '');
  }

  // ---------------------------------------------------------------- editing

  function startCreate() {
    if (!state.canCreate) return;
    state.editing = null;
    state.assignments = {};
    state.nameTouched = false;
    state.plan = null;
    state.planSignature = '';
    el('mv_target').value = '';
    el('mv_result').replaceChildren();
    setStatus('');
    setMode(MODE.CREATE);
    syncNameToLayout();
    render();
  }

  function loadIntoEditor(name) {
    const view = ((state.decoderState || {}).multiviews || [])
      .find((entry) => entry.name === name);
    state.assignments = {};
    if (!view) { state.editing = null; setMode(MODE.DECODER_SELECTED); return; }
    state.editing = name;
    state.nameTouched = true;               // an existing object keeps its name
    if (view.layout) el('mv_layout').value = view.layout;
    el('mv_name').value = view.name;
    view.subframes.forEach((subframe) => {
      if (subframe.source && subframe.source.ip) {
        state.assignments[subframe.cell] = subframe.source.ip;
      }
    });
    setMode(MODE.EDIT);
    if (!view.layout) {
      notify('This Multiview does not match a known layout. Choosing one will '
             + 'replace its geometry.');
    }
    render();
  }

  function cancelEditing() {
    state.editing = null;
    state.assignments = {};
    state.plan = null;
    state.planSignature = '';
    state.planPending = false;
    state.unreachable = [];
    state.nameTouched = false;
    el('mv_target').value = '';
    el('mv_result').replaceChildren();
    setStatus('');
    setMode(((state.decoderState || {}).multiviews || []).length
      ? MODE.DECODER_SELECTED : MODE.DECODER_SELECTED_EMPTY);
  }

  function syncNameToLayout() {
    // The name follows the layout until the operator types something of their
    // own, and never afterwards.
    if (state.nameTouched) return;
    const layout = currentLayout();
    if (layout) el('mv_name').value = layout.label;
  }

  // ---------------------------------------------------------------- wiring

  function render() {
    renderMode();
    if (isEditingOrCreating()) {
      renderCanvas();
      renderSources();
    } else {
      // No canvas on screen, so there is nothing to refresh and no timer.
      stopWindowPreviews();
      syncWindowPreviewTimer();
    }
    renderExisting();
    renderActions();
    schedulePlan();
  }

  function wire() {
    // Escape closes it, and so does anything that changes what is on screen --
    // a card left behind after its tile has been re-rendered points at nothing.
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closePreview();
    });
    document.addEventListener('dragstart', closePreview, true);
    document.addEventListener('visibilitychange', syncWindowPreviewTimer);
    window.addEventListener('pagehide', stopWindowPreviews);
    window.addEventListener('beforeunload', stopWindowPreviews);
    window.addEventListener('blur', closePreview);
    window.addEventListener('scroll', closePreview, true);

    el('mv_decoder').addEventListener('change', async (event) => {
      rememberDecoder(event.target.value);
      state.assignments = {};
      state.decoderState = null;
      state.plan = null;
      state.planSignature = '';
      state.editing = null;
      state.availability = [];
      state.canCreate = false;
      el('mv_target').value = '';
      el('mv_result').replaceChildren();
      setStatus('');
      if (event.target.value) await loadDecoderState(event.target.value);
      else setMode(MODE.NO_DECODER);
    });

    el('mv_target').addEventListener('change', (event) => {
      rememberTarget(event.target.value);
      if (event.target.value) loadIntoEditor(event.target.value);
      else cancelEditing();
    });

    el('mv_new').addEventListener('click', startCreate);
    el('mv_cancel').addEventListener('click', cancelEditing);

    el('mv_layout').addEventListener('change', () => {
      // Window names differ between layouts, so an assignment cannot carry over.
      state.assignments = {};
      syncNameToLayout();
      render();
    });
    el('mv_name').addEventListener('input', () => {
      state.nameTouched = true;
      schedulePlan();
    });
    el('mv_reload').addEventListener('click', async () => {
      const ip = el('mv_decoder').value;
      if (!ip) { notify('Select a decoder first.'); return; }
      state.planSignature = '';
      await loadDecoderState(ip, state.editing);
      notify('Decoder reloaded.', 'ok');
    });
    el('mv_notice_close').addEventListener('click', closeNotice);
    el('mv_notice_copy').addEventListener('click', copyNotice);
    el('mv_notice_save').addEventListener('click', saveNotice);

    el('mv_save').addEventListener('click', save);
    el('mv_show').addEventListener('click', showOnDisplay);
    el('mv_delete').addEventListener('click', remove);
  }

  async function start() {
    wire();
    try {
      await loadLayouts();
      // Probe capability once on first load; it is cached server-side from then
      // on, so revisiting the page asks the devices nothing.
      await Promise.all([loadDecoders(true), loadSources()]);
    } catch (err) {
      notify('Multiview could not start: ' + err.message);
    }
    setMode(MODE.NO_DECODER);
    // Once, here, on a newly opened page -- not from render(), which runs on
    // every state change and would put it back in front of the operator
    // repeatedly while they were working.
    if (!noticeAcknowledged()) showNotice();
    // And put the operator back where they were, if that is still a real
    // place to be. Live state decides; the stored value is only a name.
    try {
      await restoreSelection();
    } catch (err) {
      notify('The previous Multiview selection could not be restored: '
             + err.message);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start, {once: true});
  } else {
    start();
  }
})();

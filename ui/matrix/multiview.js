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
    // The preset as it is saved on the device, for comparing against what the
    // operator has since edited. null while creating: nothing is saved yet.
    savedBaseline: null,
    // The source being dragged, and what it may be dropped on. A Multiview
    // window and the display output ask different things of the same encoder,
    // so a tile can be a legal drop for one and not the other -- and neither
    // target may light up for a drop it would refuse. dataTransfer cannot be
    // read during dragover, so the answer is held here for the duration.
    dragging: null,          // {ip, multiview: bool, normal: bool} | null
    // What the operator has typed into the source filter. A view concern and
    // nothing else: it hides cards, and it is not part of any preset, any
    // assignment or any eligibility decision.
    sourceFilter: '',
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
    if (!response.ok && body.error) {
      // Carry the body with the error. A refused group operation says which
      // source conflicts and which decoders disagree, and a caller that only
      // gets a sentence cannot show any of it.
      const failure = new Error(body.error);
      failure.body = body;
      failure.status = response.status;
      throw failure;
    }
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

  // ---- targets: a decoder, or a group of them ----------------------------
  //
  // `GROUP_PREFIX` keeps the two apart in one <select> without inventing
  // pseudo-devices: a group's value is never a valid address, so nothing that
  // expects a decoder can be handed one by accident.
  const GROUP_PREFIX = 'group:';

  function targetIsGroup(value) {
    return String(value || '').startsWith(GROUP_PREFIX);
  }

  function selectedTarget() {
    return el('mv_decoder').value || '';
  }

  function selectedGroupId() {
    const value = selectedTarget();
    return targetIsGroup(value) ? value.substring(GROUP_PREFIX.length) : '';
  }

  // The group being worked on, or null when the target is a single decoder.
  function activeGroup() {
    const id = selectedGroupId();
    return id ? (groups.list.find((group) => group.id === id) || null) : null;
  }

  // Which decoder the page reads its canvas and its Multiview list from. For a
  // group that is a member: the definition is the same on all of them, and one
  // of them has to be asked. Never presented as the group's own state -- the
  // group's state is what `groups.state` reports across every member.
  function readingDecoder() {
    const group = activeGroup();
    if (!group) return selectedTarget();
    const online = (group.members || []).find((member) => member.discovered);
    return (online || (group.members || [])[0] || {}).ip || '';
  }

  async function loadDecoders(probe) {
    const body = await getJSON('/api/multiview/decoders' + (probe ? '?probe=1' : ''));
    state.decoders = body.decoders || [];
    groups.list = body.groups || [];
    const select = el('mv_decoder');
    const previous = select.value;
    select.replaceChildren();
    select.appendChild(new Option('Select a target…', ''));

    // Two headed sections rather than one flat list, so a group is never
    // mistaken for a decoder. Groups first: an installation that has made one
    // is usually working with it.
    if (groups.list.length) {
      const groupBand = document.createElement('optgroup');
      groupBand.label = 'Groups';
      groups.list.forEach((group) => {
        const count = (group.members || []).length;
        const option = new Option(
          group.name + '  —  ' + count + (count === 1 ? ' decoder' : ' decoders'),
          GROUP_PREFIX + group.id);
        if (!count) {
          option.disabled = true;
          option.text += '  — no decoders in it yet';
        }
        groupBand.appendChild(option);
      });
      select.appendChild(groupBand);
    }

    const decoderBand = document.createElement('optgroup');
    decoderBand.label = 'Decoders';
    state.decoders.forEach((decoder) => {
      const option = new Option(
        decoder.hostname + '  (' + decoder.ip + ')', decoder.ip);
      // Capability is a cached fact, not a poll. Unknown stays selectable: the
      // answer arrives when the decoder is opened.
      if (decoder.multiview_supported === false) {
        option.disabled = true;
        option.text += '  — no Multiview support';
      }
      decoderBand.appendChild(option);
    });
    select.appendChild(decoderBand);
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
      if (keepEditing && !activeView(body) && (body.multiviews || [])
          .some((view) => view.name === keepEditing)) {
        loadIntoEditor(keepEditing);
        return;
      }
      // What the decoder is actually showing outranks anything remembered.
      // A preset the operator last looked at is a bookmark; the composition on
      // the display is what they are looking at now.
      const active = activeView(body);
      if (active) {
        el('mv_target').value = active.name;
        loadIntoEditor(active.name);
        rememberTarget(active.name);
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

  // The Multiview this decoder is compositing right now, if any. The server
  // derives `selected_on_output` from the decoder's current display selection,
  // so this is live device state and not metadata.
  function activeView(body) {
    return ((body || state.decoderState || {}).multiviews || [])
      .find((view) => view.selected_on_output) || null;
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

    const creating = mode === MODE.CREATE
      || (mode === MODE.SAVING && state.previousMode === MODE.CREATE);

    show('mv_field_target', capable && !creating);
    show('mv_field_manage', capable && !creating);
    show('mv_field_reload', haveDecoder && mode !== MODE.LOADING && !creating);
    show('mv_editbar', working);
    show('mv_field_layout', working);
    show('mv_field_name', working);
    show('mv_field_cancel_create', creating);
    show('mv_workspace', working);
    show('mv_actions', working);
    renderEditHeading(creating);

    const view = currentView();
    // Offered whenever there is something that could be shown, including a
    // Multiview being created: Show on Display saves first, so an operator
    // never has to press Save to get to it. It is withdrawn only for the one
    // that is already on the display.
    show('mv_show', (state.mode === MODE.EDIT && view && !view.selected_on_output)
                    || creating);
    show('mv_shown', state.mode === MODE.EDIT && !!view);
    show('mv_delete', state.mode === MODE.EDIT);
    show('mv_cancel', working);

    const busy = mode === MODE.SAVING;
    // A decoder holds as many saved Multiviews as the operator wants, so the
    // only thing that withdraws New is an operation already in flight.
    el('mv_new').disabled = busy;
    el('mv_new').title = '';
    el('mv_copy').disabled = busy || !currentView();
    el('mv_copy').title = currentView() ? ''
      : 'Choose a Multiview to copy.';
    el('mv_groups').disabled = busy;
    el('mv_install').disabled = busy || !haveDecoder;
    el('mv_install').title = haveDecoder
      ? 'Install the standard Multiview layouts. The display is not changed.'
      : 'Choose a decoder first.';
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

  // Which of the two operations is happening, and whether anything is unsaved.
  // "Creating" and "editing" being indistinguishable is what made the old New
  // button look as though it renamed the Multiview already on screen.
  function renderEditHeading(creating) {
    const heading = el('mv_edit_heading');
    const stateLabel = el('mv_edit_state');
    if (!heading || !stateLabel) return;
    if (creating) {
      heading.textContent = 'Create New Multiview';
      stateLabel.textContent = 'Not saved yet';
      stateLabel.className = 'mv-edit-state unsaved';
      return;
    }
    const view = currentView();
    heading.textContent = view
      ? 'Editing ' + ((view.friendly_name) || view.name)
      : 'Multiview';
    if (!view) {
      stateLabel.textContent = '';
      stateLabel.className = 'mv-edit-state';
      return;
    }
    const dirty = isDirty();
    stateLabel.textContent = dirty ? 'Unsaved changes' : 'Saved';
    stateLabel.className = 'mv-edit-state' + (dirty ? ' unsaved' : ' saved');
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
      ? 'Multiview active — this composition is on the display now'
      : 'Inactive — saved on this decoder, not currently shown';
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
    // loadDecoderState selects whatever is live on the decoder. If it found
    // one, that outranks the bookmark and there is nothing further to restore.
    if (activeView()) return;
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
  // Two records, two lifetimes. Pressing Continue is an acknowledgement for
  // this session; ticking the box is a preference for this version. Separate
  // keys, so neither can overwrite or corrupt the other.
  const NOTICE_KEY = 'multiview_notice_acknowledged_version';
  const NOTICE_SESSION_KEY = 'multiview_notice_acknowledged_session';
  const NOTICE_TITLE = 'Before you use Multiview';

  // What Multiview does to the equipment, in the order an operator needs it:
  // when anything happens at all, what gets used, and what is never touched.
  // Every claim here is measured behaviour -- if the code changes, this changes
  // with it, and `NoticeContentTests` fails until it does.
  const NOTICE = {
    intro:
      'Multiview builds one picture out of several sources. Doing that '
      + 'configures equipment other people may be using, so it is worth knowing '
      + 'what it uses and what it leaves alone.',

    // Section 9. The distinction Phase 8F introduced, and the first thing to
    // say: most of what follows does not happen when you are just designing.
    whenHeading: 'When any of this happens',
    when: [
      'Creating, installing, editing, copying or saving a Multiview that is '
      + 'not on the display changes nothing on any encoder and nothing on the '
      + 'screen. A saved Multiview is a description, not a booking.',
      'Show on Display saves the Multiview you are looking at first, and then '
      + 'shows what it saved. The changes below happen at that point, and when '
      + 'you recall a different Multiview.',
      'They also happen when you add, change or clear a source on a Multiview '
      + 'that is already on the display — and that takes effect '
      + 'immediately, while it is on screen.',
    ],

    usesHeading: 'What Multiview uses',
    uses: [
      {label: 'Encoder', items: [
        'Multiview video comes from each source’s Encoder 2 over its '
        + 'Session 2. Your normal Session 1 stream is what the A/V Matrix '
        + 'routes, and it is a different thing.',
        'OmniSuite may point Encoder 2 at the same video input Encoder 1 uses, '
        + 'set its scaler to the size the window needs, and set its bit rate.',
        'It enables Session 2 video and assigns it to Encoder 2. It uses the '
        + 'multicast address the encoder already has — encoders generate '
        + 'their own — and never invents one.',
        'It turns the Session 2 SAP announcement off, so no decoder picks the '
        + 'stream up by accident.',
      ]},
      {label: 'Decoder', items: [
        'The Multiview layout is stored on the decoder and its output runs at '
        + '1920x1080.',
        'Windows are fed through the reserved decoder inputs ip_input2, '
        + 'ip_input4, ip_input6 and ip_input8. Only the ones actually needed '
        + 'are used, and a subscription already carrying the right stream is '
        + 'reused rather than replaced.',
        'Empty windows use no stream, no input and no bandwidth.',
      ]},
      {label: 'Audio', items: [
        'The display’s sound comes from the main window’s source over '
        + 'that source’s ordinary Session 1 audio.',
        'Multiview does not create separate audio for each window. In a layout '
        + 'with one larger window that window is the main one; where every '
        + 'window is the same size, the first is.',
      ]},
    ],

    importantHeading: 'Important',
    important: [
      {label: 'Encoder 1 is never reduced',
       text: 'OmniSuite reads Encoder 1 to work out what bandwidth is spare. '
             + 'It never lowers or reconfigures it to make room for Multiview.'},
      {label: 'Bandwidth',
       text: 'Encoder 1 and Encoder 2 share one 900 Mb/s video budget. '
             + 'Multiview needs at least 20 Mb/s of that left over, and takes '
             + 'whatever is spare up to what the window needs. If Encoder 1 is '
             + 'using too much, the source is marked Configuration required '
             + 'and OmniSuite refuses rather than turning Encoder 1 down.'},
      {label: 'One scaler per source',
       text: 'Encoder 2’s scaler belongs to the source encoder, not to a '
             + 'decoder or a preset. Several displays can share one source when '
             + 'they need the same size. If another display is already using it '
             + 'at a different size, OmniSuite refuses the new Multiview rather '
             + 'than resizing a stream somebody is watching.'},
      {label: 'Presets do not reserve anything',
       text: 'A Multiview may be saved empty, partly filled, or with a source '
             + 'that is offline or still needs configuring. Whether it can '
             + 'actually run is checked when you press Show.'},
      {label: 'Not every decoder can',
       text: 'A decoder with Video Wall enabled cannot run Multiview, and Fast '
             + 'Switching blocks it on the affected 1xx family. OmniSuite will '
             + 'not turn either of them off for you.'},
    ],

    wontHeading: 'OmniSuite will never do these on its own',
    wont: [
      'reduce a source’s primary stream to make room for a Multiview '
      + 'window — a window gets a smaller share instead',
      'turn off Video Wall',
      'turn off Fast Switching where it conflicts',
      'change an encoder just because you opened this page',
      'change a stream another display is using, to suit a new Multiview',
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
      NOTICE.whenHeading,
    ];
    NOTICE.when.forEach((item) => lines.push('  - ' + item));
    lines.push('', NOTICE.usesHeading);
    NOTICE.uses.forEach((group) => {
      lines.push('  ' + group.label);
      group.items.forEach((item) => lines.push('    - ' + item));
    });
    lines.push('', NOTICE.importantHeading);
    NOTICE.important.forEach((entry) => {
      lines.push('  ' + entry.label + ': ' + entry.text);
    });
    lines.push('', NOTICE.wontHeading);
    NOTICE.wont.forEach((item) => lines.push('  - ' + item));
    lines.push('', NOTICE.closing, '');
    return lines.join('\n');
  }

  function renderNotice() {
    const body = el('mv_notice_body');
    body.replaceChildren();
    body.appendChild(node('p', 'mv-notice-intro', NOTICE.intro));

    // When it happens at all, first: after Phase 8F most of this does not
    // happen while an operator is simply designing a layout.
    body.appendChild(node('h3', null, NOTICE.whenHeading));
    const when = node('ul', 'mv-notice-list mv-notice-when');
    NOTICE.when.forEach((item) => when.appendChild(node('li', null, item)));
    body.appendChild(when);

    body.appendChild(node('h3', null, NOTICE.usesHeading));
    NOTICE.uses.forEach((group) => {
      const block = node('div', 'mv-notice-group');
      block.appendChild(node('h4', 'mv-notice-group-label', group.label));
      const list = node('ul', 'mv-notice-list');
      group.items.forEach((item) => list.appendChild(node('li', null, item)));
      block.appendChild(list);
      body.appendChild(block);
    });

    body.appendChild(node('h3', null, NOTICE.importantHeading));
    const important = node('dl', 'mv-notice-important');
    NOTICE.important.forEach((entry) => {
      important.appendChild(node('dt', null, entry.label));
      important.appendChild(node('dd', null, entry.text));
    });
    body.appendChild(important);

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
    if (!state.version) return false;
    const store = suppressionStore();
    if (!store) {
      // The shared dialog did not load. Show the notice: a missing preference
      // store is not consent.
      return false;
    }
    return store.suppressed(NOTICE_KEY, state.version)
      || store.suppressedForSession(NOTICE_SESSION_KEY, state.version);
  }

  function showNotice() {
    renderNotice();
    el('mv_notice_suppress').checked = false;
    setNoticeStatus('');
    el('mv_notice').hidden = false;
    el('mv_notice_close').focus();
  }

  function closeNotice() {
    // Closing it is itself an acknowledgement, for this session. The checkbox
    // adds the longer one. Both record the version they were given against, so
    // the next release asks again by itself and nobody has to clear anything.
    const store = suppressionStore();
    if (store && state.version) {
      store.rememberForSession(NOTICE_SESSION_KEY, state.version);
      if (el('mv_notice_suppress').checked) {
        store.remember(NOTICE_KEY, state.version);
      }
    }
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
          // ...or if it is the source the display output is showing. The
          // display output is deliberately NOT added to `visible`, so it never
          // joins the refresh timer: one request per source, on demand.
          if (displayOutputSourceIp() === ip) renderDisplayOutput();
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

  // Does this source match what the operator typed? Matched against the
  // things they would actually search by. Deliberately a pure predicate over
  // an already-loaded source: filtering asks no device anything, and cannot
  // change what a source is.
  function sourceMatchesFilter(source) {
    const needle = (state.sourceFilter || '').trim().toLowerCase();
    if (!needle) return true;
    return [source.hostname, source.model, source.ip, source.codec,
            source.status_label, source.reason]
      .some((field) => String(field || '').toLowerCase().indexOf(needle) >= 0);
  }

  function renderSourceFilter(shown, total) {
    const clear = el('mv_source_filter_clear');
    const count = el('mv_source_count');
    const active = !!(state.sourceFilter || '').trim();
    if (clear) clear.hidden = !active;
    if (count) {
      count.hidden = !active;
      count.textContent = active
        ? (shown + ' of ' + total + ' source' + (total === 1 ? '' : 's'))
        : '';
    }
  }

  function renderSources() {
    const container = el('mv_sources');
    closePreview();
    container.replaceChildren();
    // Hidden, not removed: `state.sources` is what the server said, and the
    // filter is a view over it. Eligibility, drag capability and assignments
    // are all decided from the source itself, exactly as when nothing is
    // filtered.
    const visible = state.sources.filter(sourceMatchesFilter);
    const hiddenExcluded = state.excluded.filter(sourceMatchesFilter);
    renderSourceFilter(visible.length + hiddenExcluded.length,
                       state.sources.length + state.excluded.length);
    if (!state.sources.length) {
      container.appendChild(node('div', 'mv-empty',
        'No eligible encoders were discovered. Run a scan from Device Info.'));
    } else if (!visible.length && !hiddenExcluded.length) {
      container.appendChild(node('div', 'mv-sources-empty',
        'No source matches “' + (state.sourceFilter || '').trim() + '”.'));
    }
    visible.forEach((source) => {
      const ready = source.status !== 'configuration_required';
      // A conventional route is a different question, answered by the server's
      // own rule rather than by reusing the Multiview verdict. An encoder whose
      // primary stream uses its whole budget cannot feed a window and is still
      // a perfectly good ordinary source, so it is draggable -- to the display
      // output, which accepts it, and not to a window, which does not.
      const routable = source.normal_route === 'ready';
      const tile = node('div', 'mv-source' + (ready ? '' : ' needs-work'));
      tile.draggable = ready || routable;
      tile.tabIndex = 0;
      tile.dataset.ip = source.ip;
      tile.dataset.status = source.status || '';
      tile.dataset.normalRoute = source.normal_route || '';
      tile.setAttribute('role', 'button');
      tile.setAttribute('aria-label',
        'Source ' + source.hostname + ' at ' + source.ip +
        (ready ? '. Select, then choose a window.'
               : '. ' + (source.reason || 'Not ready.')
                 + (routable ? ' Can still be routed to the display output.'
                             : '')));
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
        if (!ready && !routable) { event.preventDefault(); return; }
        tile.classList.add('dragging');
        // Recorded for the targets, which cannot read dataTransfer while the
        // drag is over them, and so arms only the ones that would accept it.
        state.dragging = {ip: source.ip, multiview: ready, normal: routable};
        renderDisplayOutputArming();
        event.dataTransfer.setData('text/plain', source.ip);
        event.dataTransfer.effectAllowed = 'copy';
      });
      tile.addEventListener('dragend', () => {
        tile.classList.remove('dragging');
        state.dragging = null;
        renderDisplayOutputArming();
      });
      tile.addEventListener('click', () => {
        closePreview();
        // Click-to-pick chooses a window, which is the Multiview question.
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
    if (hiddenExcluded.length) {
      const detail = node('details', 'mv-detail');
      detail.appendChild(node('summary', null,
        hiddenExcluded.length + ' encoder(s) not eligible'));
      hiddenExcluded.forEach((entry) => {
        // Ineligible for a Multiview WINDOW is not ineligible for everything.
        // A model excluded from Multiview still has an ordinary Session 1
        // stream, and the display output can take it. Rendering these as text
        // made a perfectly routable encoder unreachable from this page.
        const routable = entry.normal_route === 'ready';
        if (!routable) {
          detail.appendChild(node('div', 'mv-source-meta',
            entry.hostname + ' (' + (entry.model || '') + ') — '
            + (entry.reason || '')));
          return;
        }
        const tile = node('div', 'mv-source needs-work route-only');
        tile.draggable = true;
        tile.tabIndex = 0;
        tile.dataset.ip = entry.ip;
        tile.dataset.status = entry.status || '';
        tile.dataset.normalRoute = entry.normal_route || '';
        tile.setAttribute('role', 'button');
        tile.setAttribute('aria-label',
          'Source ' + entry.hostname + ' at ' + entry.ip + '. '
          + (entry.reason || 'Not eligible for Multiview.')
          + ' Can still be routed to the display output.');
        tile.appendChild(node('div', 'mv-source-name', entry.hostname));
        tile.appendChild(node('div', 'mv-source-meta',
          (entry.model || 'Encoder') + ' · ' + entry.ip));
        tile.appendChild(node('div', 'mv-source-warning', entry.reason || ''));
        tile.appendChild(node('div', 'mv-source-meta',
          'Display output only — not available for a Multiview window'));
        tile.addEventListener('dragstart', (event) => {
          closePreview();
          tile.classList.add('dragging');
          state.dragging = {ip: entry.ip, multiview: false, normal: true};
          renderDisplayOutputArming();
          event.dataTransfer.setData('text/plain', entry.ip);
          event.dataTransfer.effectAllowed = 'copy';
        });
        tile.addEventListener('dragend', () => {
          tile.classList.remove('dragging');
          state.dragging = null;
          renderDisplayOutputArming();
        });
        detail.appendChild(tile);
      });
      excluded.appendChild(detail);
    }
  }

  // ------------------------------------------------------- display output
  //
  // What the decoder is ACTUALLY showing. Every field here comes from the
  // server's `display_output`, which is derived from the decoder's own current
  // output selection -- never from the remembered selection, the saved preset
  // or the last button pressed. Nothing here polls: it is re-read whenever the
  // page reads decoder state, which is what every transaction already ends with.

  function displayOutput() {
    return (state.decoderState || {}).display_output || null;
  }

  // The group panel answers a different question -- are they all showing the
  // same thing -- and takes no drops, because dragging one tile must never
  // quietly route a room full of displays.
  function groupOutputView() {
    const group = activeGroup();
    if (!group) return null;
    const summary = groups.state && groups.state.group
      && groups.state.group.id === group.id ? groups.state : null;
    const intended = (summary && summary.expected)
      || (group.multiview || {}).name || '';
    if (!summary) {
      return {badge: 'UNKNOWN', badgeClass: '', title: group.name,
              meta: 'Group display state has not been read yet.',
              klass: 'is-unknown', kind: 'none', screenLabel: 'Group'};
    }
    const rows = summary.members || [];
    const synced = rows.filter((row) => row.state === 'SYNCHRONIZED').length;
    if (summary.state === 'SYNCHRONIZED') {
      return {badge: 'MULTIVIEW — ACTIVE', badgeClass: 'multiview',
              title: intended || group.name,
              meta: synced + ' display' + (synced === 1 ? '' : 's')
                    + ' synchronized',
              klass: 'is-multiview', kind: 'multiview',
              // No geometry is drawn for a group: the members' compositions are
              // not read here, and inventing one would claim more than is known.
              screenSub: synced + ' synchronized', windows: []};
    }
    // Anything that is not "all of them, on the same Multiview" is reported as
    // what it is. A single tile naming one source would imply every member is
    // showing it, which is exactly the claim that cannot be made.
    const detail = rows.filter((row) => row.state !== 'SYNCHRONIZED')
      .map((row) => row.hostname + ': ' + (row.showing || row.state))
      .join(' · ');
    return {badge: summary.state || 'DRIFTED', badgeClass: '',
            title: group.name,
            meta: detail || (synced + ' of ' + rows.length + ' synchronized'),
            klass: summary.state === 'DRIFTED' ? 'is-error' : 'is-unknown',
            kind: 'none', screenLabel: summary.state || 'Drifted'};
  }

  function decoderOutputView() {
    const output = displayOutput();
    if (!output) {
      return {badge: 'UNKNOWN', badgeClass: '', title: 'Display state unknown',
              meta: 'The decoder has not been read yet.', klass: 'is-unknown',
              kind: 'none', screenLabel: 'Unknown'};
    }
    if (output.state === 'multiview') {
      const shown = ((state.decoderState || {}).multiviews || [])
        .find((entry) => entry.name === output.multiview) || null;
      // The decoder reports each subframe's real position and size, so the
      // little screen can draw the composition that is actually on it.
      const windows = ((shown || {}).subframes || [])
        .filter((s) => s.width && s.height)
        .map((s) => ({x: s.x || 0, y: s.y || 0,
                      width: s.width, height: s.height}));
      return {badge: 'MULTIVIEW · ACTIVE', badgeClass: 'multiview',
              title: output.multiview,
              meta: 'This decoder is compositing a Multiview.',
              klass: 'is-multiview',
              kind: 'multiview',
              screenSub: shown && shown.layout_label ? shown.layout_label : '',
              canvas: shown && shown.width
                ? {width: shown.width, height: shown.height} : null,
              windows: windows};
    }
    if (output.state === 'source') {
      const source = (output.video || {}).source;
      const audioLeg = output.audio || {};
      const title = source
        ? source.hostname
        : 'Unrecognised stream ' + ((output.video || {}).multicast || '');
      const parts = [];
      if (source && source.model) parts.push(source.model);
      if (source && source.ip) parts.push(source.ip);
      parts.push('video ' + ((output.video || {}).multicast || '—'));
      parts.push('audio ' + (audioLeg.multicast || 'not routed'));
      return {badge: 'LIVE', badgeClass: 'live', title: title,
              meta: parts.join(' · '), klass: 'is-live',
              kind: 'source', sourceIp: source ? source.ip : null,
              screenSub: source ? source.hostname
                                : ((output.video || {}).multicast || '')};
    }
    // Selected, but carrying nothing. Saying LIVE here would be a claim the
    // readback does not support.
    return {badge: 'NO VIDEO', badgeClass: '', title: 'Nothing on the display',
            meta: output.video_input
              ? (output.video_input + ' is selected but carrying no stream.')
              : 'The decoder has no video input selected.',
            klass: 'is-unknown',
            kind: 'none', screenLabel: 'No active video'};
  }

  // The source the display output is currently showing, or null. Used to
  // decide whether a late preview answer is worth a redraw.
  function displayOutputSourceIp() {
    const output = displayOutput();
    if (!output || output.state !== 'source' || activeGroup()) return null;
    return ((output.video || {}).source || {}).ip || null;
  }

  // What goes inside the bezel. Three pictures for three states, and none of
  // them is allowed to imply something the decoder did not say.
  function renderScreen(view) {
    const screen = el('mv_output_screen');
    if (!screen) return;
    screen.replaceChildren();

    if (view.kind === 'multiview') {
      // The decoder offers no composited thumbnail, so a Multiview is drawn as
      // its own window geometry. Showing one window's picture here would say
      // the display is that source, which it is not.
      // From the SHOWN Multiview's own subframes, never from the layout that
      // happens to be open in the editor: those are routinely different
      // presets, and drawing one while naming the other would be a lie about
      // what is on the screen.
      const windows = view.windows || [];
      if (windows.length) {
        const grid = node('div', 'mv-screen-grid');
        const canvas = view.canvas || {width: 1920, height: 1088};
        grid.style.gridTemplateColumns = '1fr';
        grid.style.position = 'absolute';
        windows.forEach((window_) => {
          const cell = node('i');
          // Positioned from the real geometry, as a proportion of the canvas,
          // so the little picture is the layout rather than a generic grid.
          cell.style.position = 'absolute';
          cell.style.left = (100 * (window_.x || 0) / canvas.width) + '%';
          cell.style.top = (100 * (window_.y || 0) / canvas.height) + '%';
          cell.style.width = (100 * (window_.width || 0) / canvas.width) + '%';
          cell.style.height = (100 * (window_.height || 0) / canvas.height) + '%';
          grid.appendChild(cell);
        });
        screen.appendChild(grid);
      }
      screen.appendChild(node('div', 'mv-screen-label', 'Multiview'));
      if (view.screenSub) {
        screen.appendChild(node('div', 'mv-screen-sub', view.screenSub));
      }
      return;
    }

    if (view.kind === 'source') {
      // The encoder's existing thumbnail, asked for once and never polled. It
      // is informational: the state above comes from decoder readback, and a
      // thumbnail that never loads changes none of it.
      const ip = view.sourceIp;
      const preview = ip ? previewFor(ip) : null;
      const url = ip ? windowPreviewUrl(ip) : null;
      if (url) {
        const shot = node('img', 'mv-screen-shot');
        shot.dataset.ip = ip;
        shot.alt = '';                       // decorative; the text says it all
        shot.src = url;
        screen.appendChild(shot);
      } else {
        screen.appendChild(node('div', 'mv-screen-label', 'Live'));
        screen.appendChild(node('div', 'mv-screen-sub',
          preview && preview.status === 'disabled' ? 'Preview disabled'
            : preview && preview.status === 'unavailable' ? 'Preview unavailable'
            : view.screenSub || ''));
      }
      return;
    }

    // Nothing on the screen, drawn as a screen with nothing on it.
    screen.appendChild(node('div', 'mv-screen-label', view.screenLabel
                                                      || 'No active video'));
    if (view.screenSub) {
      screen.appendChild(node('div', 'mv-screen-sub', view.screenSub));
    }
  }

  function renderDisplayOutput() {
    const row = el('mv_output_row');
    const box = el('mv_output');
    const body = el('mv_output_body');
    const hint = el('mv_output_hint');
    if (!row || !box || !body) return;

    const haveDecoder = !!el('mv_decoder').value;
    const readable = !!state.decoderState;
    row.hidden = !(haveDecoder && readable);
    if (row.hidden) return;

    const group = activeGroup();
    const view = group ? groupOutputView() : decoderOutputView();
    box.className = 'mv-output ' + view.klass;
    box.setAttribute('aria-label', 'Display output: ' + view.badge + ', '
                                   + view.title);
    renderScreen(view);
    body.replaceChildren();
    const badge = node('div', 'mv-output-badge' +
      (view.badgeClass ? ' ' + view.badgeClass : ''), view.badge);
    const main = node('div', 'mv-output-main');
    main.appendChild(node('div', 'mv-output-title', view.title));
    main.appendChild(node('div', 'mv-output-meta', view.meta));
    body.appendChild(badge);
    body.appendChild(main);

    if (hint) {
      hint.hidden = false;
      hint.textContent = group
        ? 'Group display state. Conventional routing of a whole group is done '
          + 'from the A/V Matrix, so nothing is dropped here.'
        : 'Drag a source here to show it normally on this display. If a '
          + 'Multiview is active it is exited first, and the saved Multiview '
          + 'is kept.';
    }
    renderDisplayOutputArming();
  }

  // Armed only for a drag it would actually accept, so the panel never invites
  // a drop it would refuse. A group takes none at all.
  function renderDisplayOutputArming() {
    const box = el('mv_output');
    if (!box) return;
    const armed = !!state.dragging && state.dragging.normal && !activeGroup();
    box.classList.toggle('can-drop', armed);
    if (!state.dragging) box.classList.remove('drop-target');
  }

  // A drop here is the ordinary A/V Matrix route, asked for from this page.
  // It is deliberately the same request the A/V Matrix sends: the server owns
  // the Multiview exit, the Session 1 route, the verification and the rollback,
  // and this page implements none of them a second time.
  async function routeToDisplay(ip) {
    if (activeGroup()) return;                 // status only, by design
    // Either list: a source excluded from Multiview windows can still be a
    // perfectly ordinary conventional source.
    const source = state.sources.concat(state.excluded)
      .find((entry) => entry.ip === ip);
    if (!source || source.normal_route !== 'ready') {
      notify(source && source.normal_route_reason
        ? source.normal_route_reason
        : 'That source cannot be routed to this display.');
      return;
    }
    const output = displayOutput();
    const active = output && output.state === 'multiview' ? output.multiview : '';
    const label = source.hostname + (source.ip ? ' (' + source.ip + ')' : '');

    // Asked BEFORE anything is written. Cancel leaves the decoder untouched.
    if (active) {
      const proceed = await window.omniMultiviewExit.ask({
        decoder: decoderLabel(),
        multiview: active,
        after: label,
        message: 'Multiview is currently active on this decoder. Routing this '
          + 'source to the display output will exit Multiview and replace the '
          + 'Multiview picture with the selected source. The saved Multiview is '
          + 'kept and can be shown again later.',
      });
      if (!proceed) { notify('Nothing was changed.', 'ok'); return; }
    }

    await runTransaction('Routing ' + source.hostname + ' to the display…',
                         '/api/route',
                         {decoder: readingDecoder(), encoder: ip, mode: 'av',
                          exit_multiview: !!active});
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
        // Part of the layout, not a fault. A Multiview with empty windows is a
        // perfectly good Multiview -- the decoder composites it and keeps the
        // display active -- so the window says what it is instead of looking
        // like something that failed to load.
        box.classList.add('is-empty');
        overlay.appendChild(node('div', 'mv-window-empty', 'Empty'));
        overlay.appendChild(node('div', 'mv-window-title',
          prettyCell(window_.cell)));
        overlay.appendChild(node('div', 'mv-window-sub',
          window_.width + 'x' + window_.height));
        overlay.appendChild(node('div', 'mv-window-hint',
          isLive() ? 'Drop a source here to show it now'
                   : 'Drop a source here'));
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
        // A source that cannot feed a window is not offered one. It may still
        // be dragged -- the display output takes it -- so the window says no
        // rather than the tile being undraggable, which is what used to stop
        // an otherwise perfectly routable encoder being used at all.
        if (state.dragging && !state.dragging.multiview) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
        box.classList.add('drop-target');
      });
      box.addEventListener('dragleave', () => box.classList.remove('drop-target'));
      box.addEventListener('drop', (event) => {
        event.preventDefault();
        box.classList.remove('drop-target');
        if (state.dragging && !state.dragging.multiview) return;
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
    // Saved and shown are different states and an operator must be able to tell
    // them apart at a glance. A preset being edited is still a preset: its
    // windows describe what was asked for, not subscriptions on the display.
    const canvasState = el('mv_canvas_state');
    if (canvasState) {
      const view = currentView();
      const shown = !!view && !!view.selected_on_output;
      canvasState.hidden = state.mode !== MODE.EDIT || !view;
      canvasState.className = 'mv-canvas-state' + (shown ? ' live' : '');
      canvasState.textContent = shown ? 'LIVE' : 'INACTIVE';
    }
    const mode = el('mv_canvas_mode');
    if (mode) {
      const live = isLive();
      mode.hidden = state.mode !== MODE.EDIT && state.mode !== MODE.CREATE;
      mode.className = 'mv-canvas-mode' + (live ? ' live' : '');
      mode.textContent = live
        ? 'LIVE — changes made on this canvas are applied immediately'
        : state.mode === MODE.EDIT
          ? 'INACTIVE — saved on this decoder, not currently shown. These '
            + 'windows are the saved composition, not live subscriptions.'
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
  // In group context a source change is a group operation: it is planned across
  // every member, refused if one encoder would be asked for two window sizes,
  // applied to all of them and rolled back together. Doing it one decoder at a
  // time is exactly what the group exists to prevent.
  async function switchLiveOnGroup(cell, ip) {
    const view = currentView();
    const group = groups.context;
    if (!view || !group) return;
    await runTransaction(
      'Changing ' + prettyCell(cell) + ' on ' + group.name + '…',
      '/api/multiview/groups/show',
      {group: group.id, source_decoder: readingDecoder(),
       name: view.name, cell: cell, source: ip || ''},
      async (body) => {
        if (body.ok) await checkGroup();
      });
  }

  async function switchLive(cell, ip) {
    if (inGroupContext()) {
      await switchLiveOnGroup(cell, ip);
      return;
    }
    const view = currentView();
    if (!view) return;
    const previous = state.assignments[cell] || null;

    // Optimistic only as far as saying something is happening. The source is
    // not presented as switched until the decoder has confirmed it.
    state.switching = {cell: cell, to: ip, from: previous};
    render();

    await runTransaction((ip ? 'Switching ' : 'Clearing ') + prettyCell(cell) + '…',
                         '/api/multiview/switch',
                         {decoder: readingDecoder(), name: view.name,
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

  // ---- what counts as a change worth saving ------------------------------
  //
  // Only state the operator can persist into the preset. Live readings -- packet
  // counters, health, a source going offline, a preview refreshing -- move on
  // their own and must never light up Save.
  function editableState() {
    const cells = Object.keys(state.assignments || {})
      .filter((cell) => state.assignments[cell])
      .sort();
    return JSON.stringify({
      name: (el('mv_name').value || '').trim(),
      layout: el('mv_layout').value || '',
      windows: cells.map((cell) => [cell, state.assignments[cell]]),
    });
  }

  // The saved preset, in the same shape, so the two can be compared directly.
  function savedState(view) {
    if (!view) return null;
    const windows = (view.subframes || [])
      .filter((subframe) => subframe.source && subframe.source.ip)
      .map((subframe) => [subframe.cell, subframe.source.ip])
      .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
    return JSON.stringify({
      name: view.name || '',
      layout: view.layout || '',
      windows,
    });
  }

  function isDirty() {
    if (state.mode === MODE.CREATE
        || state.previousMode === MODE.CREATE) return true;   // nothing saved yet
    const view = currentView();
    if (!view) return false;
    const saved = state.savedBaseline !== null && state.savedBaseline !== undefined
      ? state.savedBaseline : savedState(view);
    return editableState() !== saved;
  }

  // Called after a successful Save, and whenever a preset is loaded, so the
  // comparison is against what is now on the device rather than what was there
  // when the page loaded.
  function markSaved() {
    const view = currentView();
    state.savedBaseline = view ? savedState(view) : null;
    renderActions();
  }

  function desiredState() {
    return {
      decoder: readingDecoder(),
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
    // Nothing to save is not a problem to report; it is the ordinary state of
    // a preset the operator is simply looking at.
    if (!isDirty()) return 'No changes to save.';
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
    const group = activeGroup();
    save.textContent = group
      ? 'Save group Multiview'
      : (isLive() ? 'Save layout and name' : 'Save Multiview');
    const showButton = el('mv_show');
    if (showButton) {
      const count = group ? (group.members || []).length : 0;
      showButton.textContent = group
        ? 'Show on ' + count + (count === 1 ? ' display' : ' displays')
        : 'Show on Display';
    }
    const reason = el('mv_save_reason');
    reason.textContent = blocker;
    const quiet = !blocker || blocker === 'Checking the sources…'
      || blocker === 'Working…' || blocker === 'No changes to save.';
    reason.className = 'mv-reason' + (quiet ? '' : ' blocking');
  }

  // ---- the Save confirmation, and turning it off -------------------------
  //
  // Scoped to the application version. A Save reconfigures shared encoders, and
  // what that does can change between releases; an operator who dismissed the
  // warning for one version has not agreed to whatever the next one does. The
  // stored preference names the version, so a new release asks once more on its
  // own -- nobody has to remember, and nobody has to clear browser storage.
  const SAVE_CONFIRM_KEY = 'multiview_save_confirm_suppressed_version';

  // The shared store, used by the operator notice near the top of this file as
  // well as by the Save confirmation below it.
  function suppressionStore() {
    return window.omniSuppression || null;
  }

  function saveConfirmSuppressed() {
    const store = suppressionStore();
    if (!store || !state.version) return false;
    return store.suppressed(SAVE_CONFIRM_KEY, state.version);
  }

  function rememberSaveConfirmSuppressed() {
    const store = suppressionStore();
    if (store && state.version) store.remember(SAVE_CONFIRM_KEY, state.version);
  }

  // ---------------------------------------------------------------- actions

  async function save() {
    const plan = state.plan;
    if (!plan || !plan.ok) return;
    const mutations = plan.mutations || [];

    // No confirmation when nothing would change: a dialog that asks about zero
    // mutations trains the operator to dismiss it. Nor when the operator has
    // turned it off for this version of the application.
    if (mutations.length && !saveConfirmSuppressed()) {
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
          + 'are prepared when you show it. You do not have to save first: '
          + 'Show on Display saves for you.',
        summary: summary,
        confirmText: 'Save',
        suppressLabel: 'Do not ask again for this version',
      });
      const agreed = confirmed === true || (confirmed && confirmed.ok);
      if (!agreed) return;
      if (confirmed && confirmed.suppress) rememberSaveConfirmSuppressed();
    }

    const group = activeGroup();
    if (group) {
      // Saving in group context updates the definition on every member, and
      // changes no display -- the same promise as a single-decoder Save.
      await runTransaction(
        'Saving to ' + group.name + '…', '/api/multiview/apply', desiredState(),
        async (body) => {
          if (!(body.ok && body.plan)) return;
          state.editing = body.plan.object_name;
          const copied = await getJSON('/api/multiview/groups/copy', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({group: group.id,
                                  source_decoder: readingDecoder(),
                                  name: body.plan.object_name}),
          }).catch((err) => ({ok: false, error: err.message}));
          if (!copied.ok) {
            notify('Saved here, but not on every decoder in the group: '
                   + (copied.error || 'see the group for details'));
          }
        });
      return;
    }
    await runTransaction('Saving…', '/api/multiview/apply', desiredState(),
                         (body) => {
                           if (body.ok && body.plan) state.editing = body.plan.object_name;
                         });
  }

  // Save the preset the operator is looking at, without the Save dialog and
  // without the full transaction wrapper. Returns the object name on success,
  // or null -- in which case nothing further may happen.
  //
  // The Save confirmation is deliberately not raised here: it promises "the
  // display is untouched", which is the opposite of what is about to happen.
  // The Show question is the one that belongs to this action, and it is asked
  // before anything is written.
  async function saveForShow() {
    const wanted = desiredState();
    try {
      const body = await getJSON('/api/multiview/apply', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(wanted),
      });
      if (!(body && body.ok && body.plan)) {
        showResult(body || {});
        setStatus('FAILED — ' + operatorReason(body || {}), false);
        return null;
      }
      state.editing = body.plan.object_name;
      rememberTarget(state.editing);
      // What was stored is what the editor was showing, so that is the
      // baseline. Deriving it from the next read instead makes the editor go
      // on looking unsaved whenever the read has not caught up -- and after a
      // save that succeeded and a show that did not, the work IS saved.
      state.savedBaseline = editableState();
      return body.plan.object_name;
    } catch (err) {
      const message = operatorReason(err.body || {error: err.message});
      state.outcome = 'ERROR';
      setStatus('FAILED — ' + message, false);
      notify(message);
      return null;
    }
  }

  // Show on Display is one operator action and two operations.
  //
  //   1. save what is in the editor, exactly as it stands
  //   2. show THAT, not some older stored copy of it
  //
  // They are not one transaction, and must not be. A preset that saves and
  // then cannot be shown is a normal outcome -- a source is off, another
  // display needs the encoder at a different size -- and the operator's work
  // is kept either way. Only the display rolls back.
  async function showOnDisplay() {
    const group = activeGroup();
    const view = currentView();
    const creating = state.mode === MODE.CREATE
      || (state.mode === MODE.SAVING && state.previousMode === MODE.CREATE);

    if (view && view.showable === false) {
      // The server has already said this one cannot go on the display, so the
      // page does not send a request it knows will be refused.
      setStatus(view.not_showable_reason || 'This Multiview cannot be shown.',
                false);
      notify(view.not_showable_reason || 'This Multiview cannot be shown.');
      return;
    }

    // Asked BEFORE the save, so Cancel leaves the preset alone as well as the
    // display: zero writes of any kind, and the operator's edits still in the
    // editor where they left them.
    if (group) {
      const label = (view && (view.friendly_name || view.name))
        || el('mv_name').value || 'this Multiview';
      const answer = await window.omniConfirm({
        title: 'Show this on every decoder in the group?',
        message: 'The current Multiview is saved first, then each decoder in '
          + group.name + ' is reconfigured and switched to ' + label
          + '. Their current pictures change.',
        summary: [{label: 'Group', value: group.name},
                  {label: 'Decoders', value: (group.members || []).length},
                  {label: 'Multiview', value: label},
                  {label: 'Saved first', value: 'yes — your current edits'}],
        confirmText: 'Save and show',
      });
      if (!(answer === true || (answer && answer.ok))) {
        notify('Nothing was changed.', 'ok');
        return;
      }
    }

    // 1. Save, whenever there is anything to save. A preset that is already
    // stored exactly as the editor shows it is not written again.
    let name = view ? view.name : null;
    if (creating || !view || isDirty()) {
      setStatus('Saving…', null);
      name = await saveForShow();
      if (!name) return;                  // Save failed: nothing is activated.
    }
    const savedBaseline = state.savedBaseline;
    if (!name) return;

    // 2. Show what was just saved. `name` is the object the save returned, so
    // the activation can never plan an older revision of it.
    if (group) {
      await runTransaction(
        'Showing on ' + group.name + '…', '/api/multiview/groups/show',
        {group: group.id, source_decoder: readingDecoder(), name: name},
        async (body) => { if (body.ok) await checkGroup(); });
    } else {
      await runTransaction('Showing on display…', '/api/multiview/show',
                           {decoder: readingDecoder(), name: name});
    }

    // The save succeeded even when the show did not, so say both things. The
    // editor is not dirty: what is in it is what is stored.
    if (state.outcome === 'ERROR' && savedBaseline !== undefined) {
      const reason = (el('mv_status').textContent || '')
        .replace(/^FAILED — /, '');
      setStatus('Preset saved — unable to show: ' + reason, false);
    }
    // The save stands whatever the show did, so the editor stops claiming the
    // work is unstored.
    if (savedBaseline !== undefined) {
      state.savedBaseline = savedBaseline;
      renderActions();
      renderMode();
    } else {
      markSaved();
    }
  }

  async function remove() {
    const view = currentView();
    if (!view) return;
    // Deleting is per decoder even in group context: removing a saved preset
    // from six decoders at once is not something to do behind one button.

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
                         {decoder: readingDecoder(), name: view.name},
                         (body) => {
      if (!body.ok) return;
      state.editing = null;
      rememberTarget('');
    });
  }

  // What the operator has in the editor right now, so a failed transaction can
  // give it back. Only the fields they can edit: everything else is re-read.
  function captureEdits() {
    return {
      name: el('mv_name').value,
      layout: el('mv_layout').value,
      assignments: Object.assign({}, state.assignments),
      nameTouched: state.nameTouched,
      editing: state.editing,
    };
  }

  function restoreEdits(snapshot) {
    if (!snapshot) return;
    el('mv_name').value = snapshot.name;
    el('mv_layout').value = snapshot.layout;
    state.assignments = Object.assign({}, snapshot.assignments);
    state.nameTouched = snapshot.nameTouched;
    state.planSignature = '';
    render();
  }

  async function runTransaction(busyText, url, payload, onSuccess) {
    const edits = captureEdits();
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
      // A refused group operation carries the reason it was refused: which
      // source conflicts, and which decoders disagree about it. "FAILED —
      // refused" is not something an operator can act on.
      const message = operatorReason(err.body || {error: err.message});
      setStatus('FAILED — ' + message, false);
      notify(message);
    }
    state.planSignature = '';
    const keep = state.editing;
    if (keep) rememberTarget(keep);
    // For a group this re-reads one member, which is where the canvas comes
    // from; the group's own state is refreshed by checkGroup().
    await loadDecoderState(readingDecoder(), keep);
    if (activeGroup()) await checkGroup();
    // A failed transaction changed nothing on the device, so the editor must
    // still show what the operator was trying to save -- and Save must still
    // be offered. Re-reading the decoder would otherwise quietly revert them.
    if (!(body && body.ok) && isEditingOrCreating()) restoreEdits(edits);
    schedulePlan();
  }

  function decoderLabel() {
    const group = activeGroup();
    if (group) {
      const count = (group.members || []).length;
      return group.name + ' — group of ' + count
        + (count === 1 ? ' decoder' : ' decoders');
    }
    const ip = readingDecoder();
    const decoder = state.decoders.find((d) => d.ip === ip);
    return decoder ? decoder.hostname + ' (' + ip + ')' : ip;
  }

  function labelFor(layoutId) {
    const layout = state.layouts.find((entry) => entry.id === layoutId);
    return layout ? layout.label : layoutId;
  }

  // What to put in front of the operator when something is refused. The status
  // word alone ("invalid", "conflict") describes the category, not the problem.
  function operatorReason(body) {
    // Most specific first. A group refusal carries a one-word `error` and the
    // conflicts that explain it; preferring the short one throws away the
    // sentence the operator actually needs.
    const conflicts = (body.conflicts || []).map((c) => c.detail).filter(Boolean);
    if (conflicts.length) return conflicts.join('  ');
    if ((body.problems || []).length) return body.problems.join('  ');
    if (body.error) return body.error;
    return body.status || 'The operation failed.';
  }

  function showResult(body) {
    const status = body.status || (body.ok ? 'VERIFIED' : 'FAILED');
    state.outcome = body.ok ? 'VERIFIED' : 'ERROR';
    // A failure has two things worth saying: what happened to the system, and
    // why. "FAILED — ROLLED BACK" tells the operator their equipment was put
    // back; the reason tells them what to do about it. Neither replaces the
    // other, so where the status carries an outcome, both are shown.
    let reason = status;
    if (!body.ok) {
      const why = operatorReason(body);
      reason = /FAIL|ROLL/i.test(status) && why !== status
        ? status + ' — ' + why : why;
    }
    setStatus(reason, !!body.ok);
    notify(body.ok ? 'Verified on the decoder.' : reason,
           body.ok ? 'ok' : undefined);

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
    // A read that missed and then worked is a diagnostic, not an operator
    // event: it is recorded here and nowhere else.
    (body.retried_reads || []).forEach((entry) => {
      lines.push('[retried] ' + entry.device + ' ' + entry.node
        + ' answered on attempt ' + entry.attempt);
    });
    (body.unreachable || []).forEach((entry) => {
      lines.push('[unreachable] ' + entry.hostname + ' (' + entry.ip + ') for '
        + entry.window + ' — ' + entry.attempts + ' attempts');
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

  async function installStandardLayouts() {
    const group = activeGroup();
    const where = group ? group.name : decoderLabel();
    const confirmed = await window.omniConfirm({
      title: 'Install standard layouts',
      message: 'Installs the standard Multiview layout presets on ' + where
        + '. This does not change the display and does not configure any '
        + 'source stream.',
      summary: [
        {label: 'Layouts', value: '2x2, Side-by-Side, four PiP positions, '
                                  + 'four 1+3 positions and 4-Split'},
        {label: 'Display', value: 'unchanged'},
        {label: 'Sources', value: 'untouched — nothing is prepared until you '
                                  + 'Show a Multiview'},
        {label: 'Existing', value: 'layouts already installed are left alone'},
      ],
      confirmText: 'Install',
    });
    if (!(confirmed === true || (confirmed && confirmed.ok))) return;

    setStatus('Installing standard layouts on ' + where + '…', null);
    el('mv_install').disabled = true;
    try {
      const body = await getJSON('/api/multiview/layouts/install', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(group ? {group: group.id}
                                   : {decoder: readingDecoder()}),
      });
      setStatus(body.message || 'Installed.', !!body.ok);
      notify(body.message || 'Standard layouts installed.', 'ok');
      (body.conflicts || []).forEach((conflict) => notify(conflict.reason));
      await loadDecoderState(readingDecoder(), state.editing);
    } catch (err) {
      setStatus('FAILED — ' + err.message, false);
      notify(err.message);
    } finally {
      el('mv_install').disabled = false;
    }
  }

  function startCreate() {
    if (!state.canCreate) return;
    state.editing = null;
    state.assignments = {};
    state.nameTouched = false;
    state.plan = null;
    state.planSignature = '';
    state.savedBaseline = null;             // nothing saved yet, so always dirty
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
    state.savedBaseline = savedState(view);
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
    state.savedBaseline = null;
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

  // ---- copy this Multiview to another decoder ----------------------------

  function openCopyDialog() {
    const view = currentView();
    if (!view) return;
    const select = el('mv_copy_target');
    const options = state.decoders
      .filter((decoder) => decoder.ip !== readingDecoder())
      .filter((decoder) => decoder.multiview_supported !== false);
    select.replaceChildren();
    options.forEach((decoder) => {
      const option = node('option', '', decoder.hostname
        ? decoder.hostname + ' (' + decoder.ip + ')' : decoder.ip);
      option.value = decoder.ip;
      select.appendChild(option);
    });
    el('mv_copy_summary').textContent = 'Copying ' + (view.friendly_name || view.name)
      + ' — ' + (view.layout_label || 'this layout') + ', '
      + (view.subframes || []).filter((s) => s.source).length + ' window(s).';
    el('mv_copy_status').textContent = options.length ? ''
      : 'No other decoder can take a Multiview.';
    el('mv_copy_go').disabled = !options.length;
    el('mv_copy_dialog').hidden = false;
    select.focus();
  }

  function closeCopyDialog() {
    el('mv_copy_dialog').hidden = true;
  }

  async function runCopy(onConflict) {
    const view = currentView();
    const target = el('mv_copy_target').value;
    if (!view || !target) return;
    el('mv_copy_go').disabled = true;
    el('mv_copy_status').textContent = 'Copying…';
    try {
      const body = await getJSON('/api/multiview/copy', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          source_decoder: readingDecoder(),
          target_decoder: target,
          name: view.name,
          on_conflict: onConflict || '',
        }),
      });
      (body.warnings || []).forEach((warning) => notify(warning));
      el('mv_copy_status').textContent = 'Copied. The display on that decoder '
        + 'is unchanged.';
      setStatus('Copied ' + view.name + ' to ' + target, true);
      setTimeout(closeCopyDialog, 1200);
    } catch (err) {
      // A name already in use is a decision for the operator, not a failure.
      const detail = err.body || {};
      if (detail.status === 'NAME IN USE') {
        const answer = await window.omniConfirm({
          title: 'That decoder already has a Multiview with this name',
          message: detail.error + ' Replace the one that is there, or keep both '
            + 'by saving this one as ' + detail.suggested_name + '?',
          summary: [{label: 'Existing', value: view.name},
                    {label: 'Keep both as', value: detail.suggested_name}],
          confirmText: 'Keep both',
        });
        if (answer === true || (answer && answer.ok)) {
          await runCopy('rename');
          return;
        }
        el('mv_copy_status').textContent = 'Nothing was copied.';
      } else {
        el('mv_copy_status').textContent = err.message;
      }
    }
    el('mv_copy_go').disabled = false;
  }

  // ---- decoder groups -----------------------------------------------------

  const groups = {
    list: [],
    selected: '',        // the group being edited in the dialog
    context: null,       // the group live changes apply to, or null
    state: null,         // the last group state read, for the context banner
  };

  async function loadGroups() {
    try {
      const body = await getJSON('/api/multiview/groups');
      groups.list = body.groups || [];
    } catch (err) {
      groups.list = [];
    }
    renderGroupList();
  }

  function renderGroupList() {
    const select = el('mv_group_select');
    if (!select) return;
    select.replaceChildren();
    groups.list.forEach((group) => {
      const option = node('option', '', group.name + ' (' + group.members.length
        + ' decoder' + (group.members.length === 1 ? '' : 's') + ')');
      option.value = group.id;
      select.appendChild(option);
    });
    if (groups.selected) select.value = groups.selected;
    renderGroupDetail();
  }

  function selectedGroup() {
    return groups.list.find((group) => group.id === groups.selected) || null;
  }

  function renderGroupDetail() {
    const group = selectedGroup();
    el('mv_group_name').value = group ? group.name : '';
    const members = el('mv_group_members');
    members.replaceChildren();
    const chosen = new Set((group ? group.members : []).map((m) => m.ip));
    state.decoders
      .filter((decoder) => decoder.multiview_supported !== false)
      .forEach((decoder) => {
        const row = node('label', 'mv-group-member');
        const box = node('input');
        box.type = 'checkbox';
        box.value = decoder.ip;
        box.checked = chosen.has(decoder.ip);
        row.appendChild(box);
        row.appendChild(node('span', '', decoder.hostname
          ? decoder.hostname + ' (' + decoder.ip + ')' : decoder.ip));
        members.appendChild(row);
      });
    el('mv_group_delete').disabled = !group;
    el('mv_group_use').disabled = !group || !(group.members || []).length;
    renderGroupState();
  }

  function renderGroupState() {
    const box = el('mv_group_state');
    if (!box) return;
    const report = groups.state;
    box.replaceChildren();
    if (!report || report.group.id !== groups.selected) return;
    box.appendChild(node('div', 'mv-group-state-head', report.state));
    (report.members || []).forEach((member) => {
      box.appendChild(node('div', 'mv-source-meta',
        '• ' + member.hostname + ' — ' + member.state
        + (member.showing ? ' (showing ' + member.showing + ')' : '')));
    });
  }

  function chosenMembers() {
    return Array.from(el('mv_group_members').children)
      .map((row) => row.children[0])
      .filter((box) => box && box.checked)
      .map((box) => box.value);
  }

  function openGroupsDialog() {
    el('mv_groups_status').textContent = '';
    el('mv_groups_dialog').hidden = false;
    loadGroups();
  }

  function closeGroupsDialog() {
    el('mv_groups_dialog').hidden = true;
  }

  async function groupRequest(url, payload, busyText) {
    el('mv_groups_status').textContent = busyText;
    try {
      const body = await getJSON(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      return body;
    } catch (err) {
      const detail = err.body || {};
      // A refused group operation is the interesting case: it names the source
      // and the decoders that disagree about it, and nothing was written.
      const reasons = (detail.conflicts || []).map((c) => c.detail)
        .concat(detail.problems || []);
      el('mv_groups_status').textContent = reasons.length
        ? reasons.join('  ') : err.message;
      return null;
    }
  }

  async function saveGroup() {
    const body = await groupRequest('/api/multiview/groups/save', {
      id: groups.selected || '',
      name: (el('mv_group_name').value || '').trim(),
      members: chosenMembers(),
    }, 'Saving…');
    if (!body) return;
    groups.selected = body.group.id;
    el('mv_groups_status').textContent = 'Saved. No decoder was changed.';
    await loadGroups();
    await loadDecoders(false);          // it is a target from now on
    renderGroupDetail();
  }

  async function deleteGroup() {
    const group = selectedGroup();
    if (!group) return;
    const answer = await window.omniConfirm({
      title: 'Delete this group?',
      message: 'The group is forgotten. Its decoders and the Multiviews saved '
        + 'on them are left exactly as they are.',
      summary: [{label: 'Group', value: group.name},
                {label: 'Decoders', value: group.members.length}],
      confirmText: 'Delete group',
      danger: true,
    });
    if (!(answer === true || (answer && answer.ok))) return;
    const body = await groupRequest('/api/multiview/groups/delete',
                                    {id: group.id}, 'Deleting…');
    if (!body) return;
    if (groups.context && groups.context.id === group.id) {
      leaveGroupContext();
      el('mv_decoder').value = '';
      rememberDecoder('');
      setMode(MODE.NO_DECODER);
    }
    groups.selected = '';
    el('mv_groups_status').textContent = body.message;
    await loadGroups();
    await loadDecoders(false);
    renderGroupDetail();
  }

  // Close the panel and make this group the target. Everything the operator
  // does next -- layout, sources, Save, Show, live changes -- is the ordinary
  // workflow, now scoped to the group. Copying and showing moved there, so the
  // panel no longer has its own versions of them.
  async function useSelectedGroup() {
    const group = selectedGroup();
    if (!group) return;
    closeGroupsDialog();
    await loadDecoders(false);
    el('mv_decoder').value = GROUP_PREFIX + group.id;
    rememberDecoder(el('mv_decoder').value);
    await selectGroupTarget();
  }

  async function checkGroup() {
    const group = selectedGroup() || groups.context;
    if (!group) return;
    try {
      groups.state = await getJSON('/api/multiview/groups/state?group='
                                   + encodeURIComponent(group.id));
    } catch (err) {
      groups.state = null;
    }
    renderGroupState();
    renderGroupContext();
  }

  // ---- selecting a group as the target ------------------------------------
  //
  // The group's state is read from every member; the canvas is populated from
  // one of them, because the definition is the same on all of them and
  // something has to be read. The two are never conflated: one decoder's live
  // state is not reported as the group's.
  async function selectGroupTarget() {
    const group = activeGroup();
    if (!group) { setMode(MODE.NO_DECODER); return; }
    enterGroupContext(group);
    setMode(MODE.LOADING);
    await checkGroup();

    const reading = readingDecoder();
    if (!reading) {
      setMode(MODE.UNREACHABLE, 'No decoder in this group is available.');
      return;
    }
    await loadDecoderState(reading, intendedGroupMultiview());
    // loadDecoderState picks whatever that one decoder is showing. For a group
    // the intended Multiview is the group's, so say so when they differ.
    const intended = intendedGroupMultiview();
    if (intended && el('mv_target').value !== intended
        && (state.decoderState.multiviews || [])
             .some((view) => view.name === intended)) {
      el('mv_target').value = intended;
      loadIntoEditor(intended);
    }
    renderGroupContext();
  }

  function intendedGroupMultiview() {
    const report = groups.state;
    const group = activeGroup();
    if (!report || !group || report.group.id !== group.id) return '';
    return report.expected || '';
  }

  // ---- group context (§16) ------------------------------------------------
  //
  // A drag that moves six displays must not look like a drag that moves one, so
  // group-wide live changes happen only while this context is visible.
  function enterGroupContext(group) {
    groups.context = group;
    renderGroupContext();
  }

  function leaveGroupContext() {
    groups.context = null;
    groups.state = null;
    renderGroupContext();
  }

  function inGroupContext() {
    return !!groups.context;
  }

  function renderGroupContext() {
    const bar = el('mv_group_context');
    if (!bar) return;
    const group = groups.context;
    bar.hidden = !group;
    if (!group) return;
    el('mv_group_context_name').textContent = group.name;
    el('mv_group_context_detail').textContent =
      ' — changes here apply to all ' + group.members.length + ' decoders in '
      + 'this group.';
    const report = groups.state;
    el('mv_group_context_state').textContent =
      report && report.group.id === group.id ? report.state : '';
  }

  // ---------------------------------------------------------------- wiring

  function render() {
    renderMode();
    // Outside the editor branch: what the decoder is displaying is true whether
    // or not a preset happens to be open.
    renderDisplayOutput();
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
    // A drag abandoned outside any target still ends, so the armed styling is
    // cleared from one place rather than from every target.
    document.addEventListener('dragend', () => {
      state.dragging = null;
      renderDisplayOutputArming();
    }, true);
    document.addEventListener('visibilitychange', syncWindowPreviewTimer);
    window.addEventListener('pagehide', stopWindowPreviews);
    window.addEventListener('beforeunload', stopWindowPreviews);
    window.addEventListener('blur', closePreview);
    window.addEventListener('scroll', closePreview, true);

    const output = el('mv_output');
    if (output) {
      output.addEventListener('dragover', (event) => {
        // Never armed for a group, and never for a source this decoder could
        // not actually be routed to.
        if (activeGroup()) return;
        if (state.dragging && !state.dragging.normal) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
        output.classList.add('drop-target');
      });
      output.addEventListener('dragleave', () => {
        output.classList.remove('drop-target');
      });
      output.addEventListener('drop', async (event) => {
        event.preventDefault();
        output.classList.remove('drop-target');
        if (activeGroup()) return;
        if (state.dragging && !state.dragging.normal) return;
        const ip = event.dataTransfer.getData('text/plain');
        state.dragging = null;
        renderDisplayOutputArming();
        if (ip) await routeToDisplay(ip);
      });
    }

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
      groups.state = null;
      if (!event.target.value) {
        leaveGroupContext();
        setMode(MODE.NO_DECODER);
        return;
      }
      if (targetIsGroup(event.target.value)) {
        await selectGroupTarget();
        return;
      }
      leaveGroupContext();
      await loadDecoderState(event.target.value);
    });

    el('mv_target').addEventListener('change', (event) => {
      rememberTarget(event.target.value);
      if (event.target.value) loadIntoEditor(event.target.value);
      else cancelEditing();
    });

    el('mv_new').addEventListener('click', startCreate);
    el('mv_cancel').addEventListener('click', cancelEditing);
    el('mv_cancel_create').addEventListener('click', cancelEditing);

    el('mv_copy').addEventListener('click', openCopyDialog);
    el('mv_copy_cancel').addEventListener('click', closeCopyDialog);
    el('mv_copy_go').addEventListener('click', () => runCopy(''));

    el('mv_groups').addEventListener('click', openGroupsDialog);
    el('mv_groups_close').addEventListener('click', closeGroupsDialog);
    el('mv_group_select').addEventListener('change', (event) => {
      groups.selected = event.target.value;
      renderGroupDetail();
    });
    el('mv_group_new').addEventListener('click', () => {
      groups.selected = '';
      el('mv_groups_status').textContent = '';
      renderGroupDetail();
      el('mv_group_name').focus();
    });
    el('mv_group_save').addEventListener('click', saveGroup);
    el('mv_group_delete').addEventListener('click', deleteGroup);
    el('mv_group_use').addEventListener('click', useSelectedGroup);
    el('mv_group_leave').addEventListener('click', checkGroup);

    // Escape closes whichever panel is open, like every other dialog here.
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      if (!el('mv_copy_dialog').hidden) closeCopyDialog();
      else if (!el('mv_groups_dialog').hidden) closeGroupsDialog();
    });

    el('mv_layout').addEventListener('change', () => {
      // Window names differ between layouts, so an assignment cannot carry over.
      state.assignments = {};
      syncNameToLayout();
      render();
    });
    el('mv_name').addEventListener('input', () => {
      state.nameTouched = true;
      // The plan does not change when only the name does, so re-planning would
      // not re-render: Save has to be told directly that there is now something
      // to save.
      renderActions();
      renderEditHeading(state.mode === MODE.CREATE);
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

    const filter = el('mv_source_filter');
    if (filter) {
      // Local only. No request is made, nothing is re-read, and nothing but
      // the source cards is redrawn -- so the canvas, its previews and the
      // display output all stay exactly as they were.
      filter.addEventListener('input', () => {
        state.sourceFilter = filter.value || '';
        renderSources();
      });
      filter.addEventListener('search', () => {
        state.sourceFilter = filter.value || '';
        renderSources();
      });
    }
    const clearFilter = el('mv_source_filter_clear');
    if (clearFilter) {
      clearFilter.addEventListener('click', () => {
        state.sourceFilter = '';
        if (filter) { filter.value = ''; filter.focus(); }
        renderSources();
      });
    }
    el('mv_install').addEventListener('click', installStandardLayouts);
    el('mv_save').addEventListener('click', save);
    el('mv_show').addEventListener('click', showOnDisplay);
    el('mv_delete').addEventListener('click', remove);
  }

  async function start() {
    // Groups are read once here and after a group operation; nothing polls.
    loadGroups();
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

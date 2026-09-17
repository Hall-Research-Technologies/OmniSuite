/*
 * Frontend test for the Multiview workspace.
 *
 * `node --check` cannot catch an undefined helper or a renamed element id, so
 * this loads ui/matrix/multiview.js into a minimal DOM stub, answers its fetches
 * with representative API JSON, and drives the real workflow: first load,
 * choosing a decoder, creating, editing, saving, showing and deleting.
 *
 * What it exists to prove, beyond "it renders":
 *   - progressive disclosure. Nothing but the decoder picker exists until a
 *     decoder is chosen, and no creation control until creation is started.
 *   - the page computes no geometry and speaks no device protocol, so the
 *     preview cannot drift from what is written to hardware.
 *   - Save never changes what is on the display. Only Show on Display does.
 *   - dragging, dropping and clearing reach no device.
 *
 * Run: node tests/multiview_ui_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const SOURCE = path.join(ROOT, 'ui', 'matrix', 'multiview.js');
const CONFIRM_SOURCE = path.join(ROOT, 'ui', 'confirm.js');
const PAGE = path.join(ROOT, 'ui', 'matrix', 'multiview.html');
const STYLE = path.join(ROOT, 'ui', 'matrix', 'multiview.css');

const failures = [];
async function check(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); }
  catch (err) {
    failures.push(`${name}: ${err && err.message}`);
    console.log(`  FAIL ${name}: ${err && err.message}`);
  }
}
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}
function assertEqual(actual, expected, message) {
  const a = JSON.stringify(actual), b = JSON.stringify(expected);
  if (a !== b) throw new Error(`${message || 'mismatch'}: got ${a}, wanted ${b}`);
}

// ---- minimal DOM ----------------------------------------------------------
function makeElement(tag) {
  const element = {
    tagName: String(tag || 'div').toUpperCase(),
    children: [], attributes: {}, dataset: {}, style: {},
    _classes: new Set(), textContent: '', value: '', checked: false,
    disabled: false, draggable: false, tabIndex: 0, title: '', type: '',
    open: false, hidden: false, isConnected: true, _listeners: {},
    classList: null,
    setAttribute(key, value) { this.attributes[key] = String(value); },
    getAttribute(key) {
      return Object.prototype.hasOwnProperty.call(this.attributes, key)
        ? this.attributes[key] : null;
    },
    appendChild(child) {
      this.children.push(child); child.parentNode = this;
      // A real <select> selects its first option automatically.
      if (child.tagName === 'OPTION' && !this.value) this.value = child.value;
      return child;
    },
    append(...nodes) { nodes.forEach(n => this.appendChild(n)); },
    replaceChildren(...nodes) {
      this.children = [];
      if (this._isSelect) this.value = '';
      nodes.forEach(n => this.appendChild(n));
    },
    remove() { this.isConnected = false; },
    click() {
      if (this.tagName === 'A' && this.download) lastDownloadName = this.download;
      this.dispatch('click');
    },
    removeAttribute(key) { delete this.attributes[key]; if (key === 'src') this.src = ''; },
    // The preview card positions itself against the tile it belongs to, so the
    // stub has to have a geometry for it to read. Values are arbitrary but
    // stable, and one tile is deliberately near the bottom of the window so the
    // upward-flip path is exercised.
    getBoundingClientRect() {
      if (this._rect) return this._rect;
      // The preview card is much taller than a source tile, and the difference
      // is the whole reason it has to be repositioned near the bottom of the
      // window. Giving them the same stub rect hid that.
      if (this._classes.has('mv-preview')) {
        return {top: 0, left: 0, right: 340, bottom: 300,
                width: 340, height: 300};
      }
      return {top: 100, left: 40, right: 300, bottom: 160,
              width: 260, height: 60};
    },
    focus() { DOCUMENT.activeElement = this; },
    addEventListener(type, fn) {
      (this._listeners[type] = this._listeners[type] || []).push(fn);
    },
    removeEventListener(type, fn) {
      this._listeners[type] = (this._listeners[type] || []).filter(f => f !== fn);
    },
    dispatch(type, event) {
      (this._listeners[type] || []).forEach(fn => fn(Object.assign(
        {target: this, preventDefault() {}, stopPropagation() {}}, event || {})));
    },
    findAll(predicate, found) {
      found = found || [];
      this.children.forEach(child => {
        if (predicate(child)) found.push(child);
        if (child.findAll) child.findAll(predicate, found);
      });
      return found;
    },
    text: '',
    // A real <select> exposes its options as a live collection, and the page
    // iterates it. Without this the stub threw inside the canvas picker and
    // every step after it failed for a reason that had nothing to do with them.
    get options() { return this.children.filter(c => c.tagName === 'OPTION'); },
    // className and classList are two views of one set; keeping them separate
    // made every class set through className invisible to classList.
    get className() { return Array.from(this._classes).join(' '); },
    set className(value) {
      this._classes = new Set(String(value || '').split(/\s+/).filter(Boolean));
    },
    get deepText() {
      return [this.textContent, this.text]
        .concat(this.children.map(c => (c.deepText != null ? c.deepText : '')))
        .join(' ');
    },
  };
  element.classList = {
    add: (...names) => names.forEach(n => element._classes.add(n)),
    remove: (...names) => names.forEach(n => element._classes.delete(n)),
    contains: (name) => element._classes.has(name),
    toggle: (name, force) => {
      const on = force === undefined ? !element._classes.has(name) : !!force;
      if (on) element._classes.add(name); else element._classes.delete(name);
      return on;
    },
  };
  return element;
}

// Ids and their tags come from the real page, so a renamed element fails here.
const PAGE_HTML = fs.readFileSync(PAGE, 'utf8');
const PAGE_IDS = Array.from(PAGE_HTML.matchAll(/<(\w+)[^>]*\sid="([^"]+)"/g))
  .map(m => ({tag: m[1], id: m[2]}));

const REGISTRY = new Map();
const DOCUMENT = {
  readyState: 'complete',
  activeElement: null,
  _listeners: {},
  body: makeElement('body'),
  documentElement: makeElement('html'),
  getElementById(id) { return REGISTRY.get(id) || null; },
  createElement(tag) { return makeElement(tag); },
  querySelectorAll(selector) {
    const wanted = selector.replace(/^\./, '');
    const hits = [];
    REGISTRY.forEach(node => node.findAll && node.findAll(
      n => n._classes.has(wanted), hits));
    return hits;
  },
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
  removeEventListener() {},
  hidden: false,
  dispatch(type, event) {
    (this._listeners[type] || []).forEach(fn => fn(Object.assign(
      {type, preventDefault() {}, stopPropagation() {}}, event || {})));
  },
};
PAGE_IDS.forEach(({tag, id}) => {
  const element = makeElement(tag);
  element._isSelect = tag.toLowerCase() === 'select';
  REGISTRY.set(id, element);
});

// ---- stub API -------------------------------------------------------------
const requests = [];
function windowsFor(cells) {
  return cells.map(c => Object.assign(
    {anchor: 'top left', encoder: 2, scaler_supported: true}, c));
}
const LAYOUT_CATALOG = {
  ok: true,
  version: 'V1.0.7',
  canvas: '1920x1080',
  output_resolution: '1920x1080',
  window_ip_inputs: ['ip_input2', 'ip_input4', 'ip_input6', 'ip_input8'],
  main_windows: {'2x2': 'top_left', 'pip-bottom-right': 'main'},
  budgets: {source: 900, decoder: 900},
  canvases: [{id: '1920x1080', width: 1920, height: 1080}],
  layouts: [
    {id: '2x2', label: '2x2', window_count: 4, canvases: {
      '1920x1080': {canvas: {width: 1920, height: 1088},
                    requested: {width: 1920, height: 1080}, snapped: true,
                    windows: windowsFor([
        {cell: 'top_left', x: 0, y: 0, width: 960, height: 544},
        {cell: 'top_right', x: 960, y: 0, width: 960, height: 544},
        {cell: 'bottom_left', x: 0, y: 544, width: 960, height: 544},
        {cell: 'bottom_right', x: 960, y: 544, width: 960, height: 544}])}}},
    {id: 'pip-bottom-right', label: 'PiP — Bottom Right', window_count: 2,
     canvases: {
      '1920x1080': {canvas: {width: 1920, height: 1080},
                    requested: {width: 1920, height: 1080}, snapped: false,
                    windows: [
        {cell: 'main', x: 0, y: 0, anchor: 'top left', width: 1920, height: 1080,
         encoder: 2, scaler_supported: true},
        {cell: 'bottom_right', x: 1888, y: 1048, anchor: 'bottom right',
         width: 640, height: 360, encoder: 2, scaler_supported: true}]}}},
  ],
};
const DECODERS = {ok: true, probed: 1, decoders: [
  {ip: '192.0.2.10', hostname: 'dec-test-01', model: 'hw-omni-d4511',
   multiview_supported: true},
  {ip: '192.0.2.11', hostname: 'dec-old-01', model: 'legacy',
   multiview_supported: false, reason: 'Config node not found'},
  // Somewhere for a copy to go, and a second member for a group.
  {ip: '192.0.2.12', hostname: 'dec-test-03', model: 'hw-omni-d4511',
   multiview_supported: true}]};
const SOURCES = {ok: true, ready: 2, excluded: [
  {ip: '192.0.2.22', hostname: 'old-wp-01', model: 'AT-OMNI-111-WP',
   status: 'ineligible',
   reason: 'AT-OMNI-111-WP is not supported as a Multiview source'},
  {ip: '192.0.2.23', hostname: 'enc-offline-01', model: 'hw-omni-e4111',
   status: 'ineligible', reason: 'Offline',
   detail: 'The encoder did not answer.'}], sources: [
  {ip: '192.0.2.20', hostname: 'enc-test-01', model: 'hw-omni-e4111',
   status: 'ready', status_label: 'Ready', reason: '', detail: '', action: '',
   session1: '233.252.0.11', session2: '233.252.0.21'},
  {ip: '192.0.2.24', hostname: 'new-wp-01', model: 'HW-OMNI-E4111-WP',
   status: 'ready', status_label: 'Ready', reason: '', detail: '', action: '',
   session1: '233.252.0.41', session2: '233.252.0.42'},
  // Not offline and not the wrong model -- just not configured yet. This is
  // the one an operator can fix, and the one the page must not hide.
  {ip: '192.0.2.25', hostname: 'enc-unconfigured-01', model: 'hw-omni-e4111',
   status: 'configuration_required', status_label: 'Configuration required',
   reason: 'Multicast configuration required',
   detail: 'Session 2 has no multicast address.',
   action: 'configure_multicast',
   session1: '233.252.0.51', session2: ''}]};

const FREE = [{id: '1920x1080', width: 1920, height: 1080, available: true}];
function stateBody(views, canvases) {
  const list = canvases || FREE;
  return {ok: true, decoder: {ip: '192.0.2.10', hostname: 'dec-test-01'},
          multiviews: views || [], ip_inputs: [], canvases: list,
          can_create: list.some(c => c.available), managed: [],
          canvas: '1920x1080', interlocks: [],
          hdmi_output: {video_input: 'ip_input1', audio_input: 'ip_input3',
                        sap_enabled: true, available_inputs: ['ip_input1'],
                        sap_session: '', output_resolution: '3840x2160',
                        input_status: {active: true,
                                       resolution: {width: 1920, height: 1080}},
                        video_wall: false, fast_switching: true}};
}
const EXISTING_VIEW = {
  name: 'multiview2x2', width: 1920, height: 1088, layout: '2x2',
  layout_label: '2x2', layout_source: 'stored', layout_diverged: false,
  canvas: '1920x1080', selected_on_output: false,
  subframes: [{name: 'top_left (960x544)', cell: 'top_left', width: 960, height: 544,
               x: 0, y: 0, anchor: 'top left', ip_input: 'ip_input2',
               multicast: '233.252.0.21', multicast_port: 5004,
               stream: '233.252.0.21:5004', encoder_index: 2,
               source: {ip: '192.0.2.20', hostname: 'enc-test-01',
                        resolved_from: 'subscription'},
               source_origin: 'subscription', subscribed: true,
               ip_input_enabled: true, packets: 1204, diverged: false,
               saved_source_ip: '192.0.2.20', health: 'live',
               input_active: true, output_active: true}]};

// The same Multiview on the display, with a second window that is subscribed to
// a stream the saved preset never mentioned -- the state a recall leaves behind
// when someone switched it from the front panel.
const LIVE_VIEW = Object.assign({}, EXISTING_VIEW, {
  selected_on_output: true,
  subframes: EXISTING_VIEW.subframes.concat([{
    name: 'top_right (960x544)', cell: 'top_right', width: 960, height: 544,
    x: 960, y: 0, anchor: 'top left', ip_input: 'ip_input4',
    multicast: '233.252.0.42', multicast_port: 5004,
    stream: '233.252.0.42:5004', encoder_index: 2,
    source: {ip: '192.0.2.24', hostname: 'new-wp-01',
             resolved_from: 'subscription'},
    source_origin: 'subscription', subscribed: true, ip_input_enabled: true,
    packets: 880, diverged: true, saved_source_ip: null, health: 'no signal',
    input_active: false, output_active: true}]),
});

// A window fed by a stream no discovered encoder claims -- someone switched it
// from a device OmniSuite has never scanned. It is playing, so it must not be
// drawn as an empty window waiting for a source.
const UNKNOWN_SOURCE_VIEW = Object.assign({}, LIVE_VIEW, {
  subframes: LIVE_VIEW.subframes.slice(0, 1).concat([{
    name: 'top_right (960x544)', cell: 'top_right', width: 960, height: 544,
    x: 960, y: 0, anchor: 'top left', ip_input: 'ip_input4',
    multicast: '233.252.0.99', multicast_port: 5004,
    stream: '233.252.0.99:5004', encoder_index: null,
    source: null, source_origin: 'unknown', subscribed: true,
    ip_input_enabled: true, packets: 4001, diverged: false,
    saved_source_ip: null, health: 'live',
    input_active: true, output_active: true}]),
});

let stateResponse = stateBody([]);
let stateError = null;
let applyResponse = {ok: true, status: 'VERIFIED', applied: ['Set vc2_encoder2'],
                     verified: [{step: 'Set vc2_encoder2', verified: true}],
                     plan: {object_name: 'multiview2x2'}};
const showResponse = {ok: true, status: 'VERIFIED',
                      steps: [{step: 'Show multiview2x2 on the display',
                               verified: true}]};
const deleteResponse = {ok: true, status: 'VERIFIED',
                        steps: [{step: 'Delete multiview2x2', verified: true}]};
// Every request the page makes for a preview, so "none on load" and "none
// while idle" are assertions about the log rather than about intent.
const previewRequests = [];
// Set to a promise to hold preview responses open, so a test can act while one
// is genuinely in flight rather than pretending it is.
let previewHold = null;
// How many previews had been fetched by the time the page finished loading.
let previewsAtPageLoad = 0;
let previewResponses = {
  '192.0.2.20': {ok: true, ip: '192.0.2.20', status: 'available',
                 hostname: 'enc-test-01', width: 320, height: 180,
                 url: 'http://192.0.2.20/thumbnail/thumbnail1.jpg'},
  '192.0.2.24': {ok: true, ip: '192.0.2.24', status: 'disabled',
                 hostname: 'new-wp-01',
                 reason: 'Preview is turned off on this encoder.'},
  '192.0.2.25': {ok: true, ip: '192.0.2.25', status: 'available',
                 hostname: 'enc-unconfigured-01',
                 url: 'http://192.0.2.25/thumbnail/thumbnail1.jpg'},
};

// ---- Phase 8B fixtures -----------------------------------------------------
const GROUP = {id: 'g1', name: 'Sports Bar',
               members: [{ip: '192.0.2.10', hostname: 'dec-test-01',
                          discovered: true},
                         {ip: '192.0.2.12', hostname: 'dec-test-03',
                          discovered: true}]};
let groupsResponse = {ok: true, groups: [GROUP]};
let groupSaveResponse = {ok: true, group: GROUP};
let groupCopyResponse = {ok: true, status: 'VERIFIED', shown: false,
                         saved: [], failures: [], warnings: [],
                         message: 'Saved on every decoder in the group.'};
let groupShowResponse = {ok: true, status: 'VERIFIED', shown: [],
                         shared_sources: [],
                         group: {id: 'g1', name: 'Sports Bar'},
                         message: 'Every decoder in the group is showing it.'};
let groupStateResponse = {ok: true, group: {id: 'g1', name: 'Sports Bar'},
                          state: 'SYNCHRONIZED', expected: 'multiview2x2',
                          members: []};
let copyResponse = {ok: true, status: 'VERIFIED', shown: false, warnings: [],
                    target: {decoder: '192.0.2.12', name: 'multiview2x2'}};

let switchResponse = {ok: true, status: 'VERIFIED', cell: 'top_left',
                      source: '192.0.2.24', saved: true,
                      steps: [{step: 'Point ip_input2 at 233.252.0.42',
                               verified: true}],
                      windows: [], unlocked_windows: [], regressed_windows: [],
                      released: [], kept: []};

// Where the request log stood when a live drop was dispatched, so the checks
// that follow it can look at exactly the requests it caused.
let switchMark = 0;

// When set, every plan comes back from this instead. Used to reproduce the
// unreachable-source state in which the Save button once vanished.
let forcedPlan = null;

function makePlan(desired) {
  if (forcedPlan) return {ok: true, plan: forcedPlan(desired)};
  return {ok: true, plan: {
    ok: true, object_name: desired.object_name || 'multiview2x2',
    friendly_name: desired.name, updating: !!desired.update_existing,
    layout: desired.layout, canvas: {width: 1920, height: 1088},
    requested_canvas: {width: 1920, height: 1080}, snapped: true,
    decoder: {ip: '192.0.2.10', hostname: 'dec-test-01'},
    windows: [{cell: 'top_left', width: 960, height: 544, encoder_index: 2,
               session: 'session2',
               source: {ip: '192.0.2.20', hostname: 'enc-test-01'},
               ip_input: {ip_input: 'ip_input2'},
               scaler: {status: 'change', current: '1280x720', desired: '960x544'}}],
    mutations: [{stage: 'encoder_scaler', description: 'Set vc2_encoder2 to 960x544',
                 shared: true}],
    errors: [], warnings: ['Encoder 2 will be re-scaled'], conflicts: []}};
}

function json(body) {
  return Promise.resolve({ok: true, json: () => Promise.resolve(body)});
}
function stubFetch(url, options) {
  const method = (options && options.method) || 'GET';
  let payload = null;
  try { payload = options && options.body ? JSON.parse(options.body) : null; }
  catch (err) { payload = null; }
  requests.push({url, method, payload});
  if (url.startsWith('/api/multiview/groups/state')) return json(groupStateResponse);
  if (url.startsWith('/api/multiview/groups/save')) return json(groupSaveResponse);
  if (url.startsWith('/api/multiview/groups/delete')) return json({ok: true,
    deleted: 'Sports Bar', message: 'The group is gone.'});
  if (url.startsWith('/api/multiview/groups/copy')) return json(groupCopyResponse);
  if (url.startsWith('/api/multiview/groups/show')) return groupShowResponse.ok
    ? json(groupShowResponse)
    : Promise.resolve({ok: false, status: 409,
                       json: () => Promise.resolve(groupShowResponse)});
  if (url.startsWith('/api/multiview/groups')) return json(groupsResponse);
  if (url.startsWith('/api/multiview/copy')) return copyResponse.ok
    ? json(copyResponse)
    : Promise.resolve({ok: false, status: 409,
                       json: () => Promise.resolve(copyResponse)});
  if (url.startsWith('/api/multiview/layouts')) return json(LAYOUT_CATALOG);
  if (url.startsWith('/api/multiview/decoders')) return json(DECODERS);
  if (url.startsWith('/api/multiview/sources')) return json(SOURCES);
  if (url.startsWith('/api/multiview/state')) {
    if (stateError) {
      return Promise.resolve({ok: false,
        json: () => Promise.resolve({ok: false, error: stateError})});
    }
    return json(stateResponse);
  }
  if (url.startsWith('/api/multiview/plan')) return json(makePlan(payload || {}));
  if (url.startsWith('/api/multiview/apply')) return json(applyResponse);
  if (url.startsWith('/api/multiview/preview')) {
    previewRequests.push(url);
    const ip = decodeURIComponent((url.split('ip=')[1] || '').split('&')[0]);
    const body = previewResponses[ip]
      || {ok: true, ip, status: 'unavailable',
          reason: 'OmniSuite has not discovered this device.'};
    // When a test wants a request to still be in flight, it parks it here.
    if (previewHold) return previewHold.then(() => json(body));
    return json(body);
  }
  if (url.startsWith('/api/multiview/switch')) return json(switchResponse);
  if (url.startsWith('/api/multiview/show')) return json(showResponse);
  if (url.startsWith('/api/multiview/delete')) return json(deleteResponse);
  return json({ok: false, error: 'unexpected url ' + url});
}

// ---- run ------------------------------------------------------------------
const timers = [];
// Repeating timers the page has asked for. `live` goes false on clearInterval,
// so a test can assert both that one exists and that it is taken down.
const intervals = [];
// Every image the page loaded, so "one request per unique source" is countable.
const imageLoads = [];
const toasts = [];
let confirmCalls = 0;
let confirmAnswer = true;
let lastConfirmOptions = null;

const sandbox = {
  console, JSON, Promise, Math, Date, String, Number, Object, Array, Error,
  document: DOCUMENT,
  fetch: stubFetch,
  setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; },
  clearTimeout: (id) => { if (timers[id - 1]) timers[id - 1].cancelled = true; },
  // Intervals are tracked separately from timeouts, because the whole point of
  // the rule they serve is "how many repeating timers does this page have".
  setInterval: (fn, ms) => { intervals.push({fn, ms, live: true});
                             return intervals.length; },
  clearInterval: (id) => { if (intervals[id - 1]) intervals[id - 1].live = false; },
  requestAnimationFrame: (fn) => fn(),
  Option: function Option(text, value) {
    const option = makeElement('option');
    option.text = text; option.textContent = text; option.value = value;
    return option;
  },
  encodeURIComponent,
  decodeURIComponent,
  // A real localStorage, so version-scoped suppression can actually be tested.
  localStorage: (() => {
    const store = new Map();
    return {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, String(value)),
      removeItem: (key) => store.delete(key),
      clear: () => store.clear(),
      _store: store,
    };
  })(),
  // `new Image()` is how the page preloads one frame per unique source before
  // pointing every window at it.
  Image: function Image() {
    const image = {onload: null, onerror: null};
    Object.defineProperty(image, 'src', {
      set(value) {
        imageLoads.push(value);
        this._src = value;
        setImmediate(() => { if (image.onload) image.onload(); });
      },
      get() { return this._src; },
    });
    return image;
  },
  Blob: function Blob(parts, options) {
    this.parts = parts; this.options = options;
    this.text = () => Promise.resolve((parts || []).join(''));
  },
  URL: {
    _created: [],
    createObjectURL(blob) { this._created.push(blob); return 'blob:notice'; },
    revokeObjectURL() {},
  },
  navigator: {
    clipboard: {
      _written: [],
      writeText(text) { this.clipboard_written = text;
                        sandboxClipboard.push(text);
                        return Promise.resolve(); },
    },
  },
  Date,
};
sandbox.window = sandbox;
// The page listens on `window` for the events that should dismiss a preview
// (blur, and capture-phase scroll/dragstart on the document). The sandbox is
// the window, so it needs the same listener surface every element has.
const sandboxClipboard = [];
// Whether the notice was raised by start(), and the filename a Save produced.
let noticeShownAtStart = false;
let writesAtPageLoad = -1;
let lastDownloadName = '';
const WINDOW_LISTENERS = {};
sandbox.addEventListener = (type, fn) => {
  (WINDOW_LISTENERS[type] = WINDOW_LISTENERS[type] || []).push(fn);
};
sandbox.removeEventListener = (type, fn) => {
  WINDOW_LISTENERS[type] = (WINDOW_LISTENERS[type] || []).filter(f => f !== fn);
};
sandbox.dispatchWindow = (type, event) => {
  (WINDOW_LISTENERS[type] || []).forEach(fn => fn(Object.assign(
    {type, preventDefault() {}, stopPropagation() {}}, event || {})));
};
sandbox.innerWidth = 1440;
sandbox.innerHeight = 900;
// The preview aborts a status request that is still in flight when the pointer
// moves on, so the sandbox needs the real shape of one.
sandbox.AbortController = function AbortController() {
  this.signal = {aborted: false, addEventListener() {}};
  this.abort = () => { this.signal.aborted = true; };
};
sandbox.window.toast = (message, ok) => toasts.push({message: String(message), ok: !!ok});
vm.createContext(sandbox);
// The real shared dialog first: it owns `omniSuppression`, which is where the
// version-scoped "do not ask again" preference lives. Loading a stub here
// instead would test the stub.
vm.runInContext(fs.readFileSync(CONFIRM_SOURCE, 'utf8'), sandbox,
                {filename: CONFIRM_SOURCE});
// Then the spy, so what the page asks for is observable. omniSuppression is
// left as the real one.
sandbox.window.omniConfirm = (options) => {
  confirmCalls += 1; lastConfirmOptions = options;
  return Promise.resolve(confirmAnswer);
};
vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), sandbox, {filename: SOURCE});

async function flush(rounds) {
  for (let round = 0; round < (rounds || 40); round += 1) {
    timers.filter(t => !t.cancelled && !t.fired)
          .forEach(t => { t.fired = true; t.fn(); });
    await new Promise(resolve => setImmediate(resolve));
  }
}

// One tick of every repeating timer the page currently has. Separate from
// flush() because a test has to be able to say "five seconds passed" without
// also replaying every one-shot timer it has already consumed.
async function tick(times) {
  for (let count = 0; count < (times || 1); count += 1) {
    intervals.filter(i => i.live).forEach(i => i.fn());
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
  }
}

const hidden = (id) => REGISTRY.get(id).hidden;
const windowBoxes = () => REGISTRY.get('mv_stage').children;
const banner = () => REGISTRY.get('mv_banner').deepText.trim();
async function selectDecoder(ip) {
  REGISTRY.get('mv_decoder').value = ip;
  REGISTRY.get('mv_decoder').dispatch('change');
  await flush();
}

(async function run() {
  console.log('multiview UI test:');
  await flush();

  // ---- initial state ------------------------------------------------------
  previewsAtPageLoad = previewRequests.length;
  // What the page had done by the time it finished loading: the notice is
  // raised from start(), so both of these are the state the operator meets.
  noticeShownAtStart = REGISTRY.get('mv_notice').hidden === false;
  writesAtPageLoad = requests.filter(r => r.method !== 'GET').length;
  await check('first load asks only the read endpoints, and writes nothing', () => {
    const urls = requests.map(r => r.url.split('?')[0]);
    assert(urls.includes('/api/multiview/layouts'), 'layouts not requested');
    assert(urls.includes('/api/multiview/decoders'), 'decoders not requested');
    assert(urls.includes('/api/multiview/sources'), 'sources not requested');
    assert(requests.every(r => r.method === 'GET'), 'a write was issued on load');
  });

  await check('only the decoder picker is shown before a decoder is chosen', () => {
    assert(!hidden('mv_field_decoder'), 'the decoder picker is hidden');
    ['mv_field_target', 'mv_field_manage', 'mv_field_layout',
     'mv_field_name', 'mv_workspace', 'mv_actions'].forEach(id => {
      assert(hidden(id), id + ' is visible before a decoder is chosen');
    });
    assert(/select a decoder/i.test(banner()), 'no prompt shown: ' + banner());
  });

  await check('an unsupported decoder is offered but disabled, not hidden', () => {
    const legacy = REGISTRY.get('mv_decoder').children
      .find(o => o.value === '192.0.2.11');
    assert(legacy, 'the unsupported decoder was hidden');
    assert(legacy.disabled, 'the unsupported decoder was selectable');
  });

  // ---- decoder with nothing configured ------------------------------------
  await selectDecoder('192.0.2.10');

  await check('choosing a decoder reveals the Multiview step only', () => {
    assert(!hidden('mv_field_target'), 'the Multiview selector stayed hidden');
    assert(!hidden('mv_field_manage'),
           'the Multiview management actions stayed hidden');
    ['mv_field_layout', 'mv_field_name',
     'mv_workspace', 'mv_actions'].forEach(id => {
      assert(hidden(id), id + ' appeared before creation was started');
    });
  });

  await check('an empty decoder says so, and does not call it an error', () => {
    assert(/no multiviews are saved/i.test(banner()), banner());
    assert(!/unable/i.test(banner()), 'an empty decoder read as a failure');
  });

  await check('creation is an action, never an entry in the dropdown', () => {
    const options = REGISTRY.get('mv_target').children.map(o => o.text);
    assert(!options.some(t => /new multiview/i.test(t)),
      'the dropdown offers "New Multiview" as if it existed: ' + options.join(' | '));
    assertEqual(options, ['No Multiviews saved'], 'target options');
  });

  // ---- creating -----------------------------------------------------------
  await check('New Multiview reveals the creation controls and the workspace',
    async () => {
      REGISTRY.get('mv_new').dispatch('click');
      await flush();
      ['mv_field_layout', 'mv_field_name',
       'mv_workspace', 'mv_actions'].forEach(id => {
        assert(!hidden(id), id + ' stayed hidden after New Multiview');
      });
      assert(!hidden('mv_cancel'), 'no way to back out of creation');
      assert(hidden('mv_delete'), 'Delete is offered for something not yet created');
      assert(hidden('mv_show'), 'Show on Display is offered before anything is saved');
    });

  await check('the name defaults to the layout and follows it until edited',
    async () => {
      assertEqual(REGISTRY.get('mv_name').value, '2x2', 'default name');
      REGISTRY.get('mv_layout').value = 'pip-bottom-right';
      REGISTRY.get('mv_layout').dispatch('change');
      await flush();
      assertEqual(REGISTRY.get('mv_name').value, 'PiP — Bottom Right',
                  'the name did not follow the layout');
      REGISTRY.get('mv_name').value = 'Reception Wall';
      REGISTRY.get('mv_name').dispatch('input');
      REGISTRY.get('mv_layout').value = '2x2';
      REGISTRY.get('mv_layout').dispatch('change');
      await flush();
      assertEqual(REGISTRY.get('mv_name').value, 'Reception Wall',
                  'a typed name was overwritten when the layout changed');
    });

  await check('layout options carry friendly labels and a window count', () => {
    const options = REGISTRY.get('mv_layout').children.map(o => o.text);
    assert(options.includes('2x2 (4 windows)'), options.join(' | '));
    assert(options.includes('PiP — Bottom Right (2 windows)'), options.join(' | '));
    assert(!options.some(t => /custom/i.test(t)), 'a custom layout is offered');
  });

  await check('the canvas preview is drawn from the server geometry', async () => {
    REGISTRY.get('mv_layout').value = 'pip-bottom-right';
    REGISTRY.get('mv_layout').dispatch('change');
    await flush();
    const boxes = windowBoxes();
    assertEqual(boxes.length, 2, 'window count');
    const inset = boxes[1];
    assertEqual(inset.style.right, ((1920 - 1888) / 1920 * 100) + '%', 'inset right');
    assertEqual(inset.style.bottom, ((1080 - 1048) / 1080 * 100) + '%', 'inset bottom');
    assertEqual(REGISTRY.get('mv_stage').style.aspectRatio, '1920 / 1080', 'aspect');
  });

  await check('there is no canvas selector, and the canvas states itself', () => {
    assert(!REGISTRY.get('mv_resolution'), 'the page still has a canvas selector');
    assert(!REGISTRY.get('mv_field_resolution'), 'the canvas field still exists');
    assertEqual(REGISTRY.get('mv_canvas_note').deepText.trim(), '1920x1080',
                'the canvas does not state its size');
    const caption = REGISTRY.get('mv_caption').deepText.replace(/\s+/g, ' ');
    assert(/Display output: 1920x1080/.test(caption), caption);
  });

  await check('dropping a source assigns it without touching hardware', async () => {
    const before = requests.length;
    windowBoxes()[0].dispatch('drop', {dataTransfer: {getData: () => '192.0.2.20'}});
    await flush(2);
    const assigned = windowBoxes()[0];
    assert(assigned._classes.has('assigned'), 'window not marked assigned');
    assert(assigned.deepText.includes('enc-test-01'), 'source name not shown');
    const wrote = requests.slice(before).filter(r => r.method !== 'GET'
      && !r.url.startsWith('/api/multiview/plan'));
    assertEqual(wrote, [], 'drag and drop reached a device');
  });

  await check('the 4xxx wall plate is offered as a source', () => {
    const tiles = REGISTRY.get('mv_sources').children.map(t => t.deepText);
    assert(tiles.some(t => t.includes('HW-OMNI-E4111-WP')),
      'HW-OMNI-E4111-WP is missing from the source list');
    const excluded = REGISTRY.get('mv_sources_excluded').deepText;
    assert(excluded.includes('AT-OMNI-111-WP'), 'the old wall plate is not listed');
    assert(!excluded.includes('HW-OMNI-E4111-WP'), 'the 4xxx wall plate was excluded');
  });

  await flush();

  await check('Save asks first, says it will not change the display, and posts once',
    async () => {
      confirmCalls = 0; confirmAnswer = true;
      const before = requests.filter(r =>
        r.url.startsWith('/api/multiview/apply')).length;
      REGISTRY.get('mv_save').dispatch('click');
      await flush();
      assertEqual(confirmCalls, 1, 'no confirmation was shown');
      assert(/display is untouched/i.test(lastConfirmOptions.message)
             && /prepared when you show it/i.test(lastConfirmOptions.message),
        'the confirmation does not separate saving from showing: '
        + lastConfirmOptions.message);
      const applies = requests.filter(r => r.url.startsWith('/api/multiview/apply'));
      assertEqual(applies.length - before, 1, 'apply request count');
      assertEqual(applies[applies.length - 1].payload.select_on_output, false,
        'Save asked the backend to change the display');
    });

  await check('a cancelled Save makes no request', async () => {
    confirmAnswer = false;
    const before = requests.filter(r =>
      r.url.startsWith('/api/multiview/apply')).length;
    REGISTRY.get('mv_save').dispatch('click');
    await flush();
    const after = requests.filter(r =>
      r.url.startsWith('/api/multiview/apply')).length;
    assertEqual(after, before, 'Cancel still saved');
    confirmAnswer = true;
  });

  // ---- editing an existing one -------------------------------------------
  stateResponse = stateBody([EXISTING_VIEW]);
  await selectDecoder('192.0.2.10');

  await check('an existing Multiview appears in the dropdown, by name', () => {
    const options = REGISTRY.get('mv_target').children.map(o => o.text);
    assertEqual(options.length, 2, options.join(' | '));
    assert(options[1].startsWith('multiview2x2'), options[1]);
    assert(!/no multiviews/i.test(banner()), 'a populated decoder read as empty');
  });

  await check('selecting it loads it and offers Show and Delete', async () => {
    REGISTRY.get('mv_target').value = 'multiview2x2';
    REGISTRY.get('mv_target').dispatch('change');
    await flush();
    assert(!hidden('mv_workspace'), 'the workspace stayed hidden');
    assert(!hidden('mv_delete'), 'Delete is not offered for an existing Multiview');
    assert(!hidden('mv_show'), 'Show on Display is not offered');
    assertEqual(REGISTRY.get('mv_name').value, 'multiview2x2', 'name');
    assert(/not currently shown on display/i.test(REGISTRY.get('mv_shown').deepText),
      'display status missing: ' + REGISTRY.get('mv_shown').deepText);
  });

  await check('the toolbar carries only the controls the workflow needs', () => {
    ['mv_decoder', 'mv_target', 'mv_new', 'mv_layout', 'mv_name']
      .forEach(id => assert(REGISTRY.get(id), id + ' is missing'));
    assert(!REGISTRY.get('mv_resolution'),
      'a fixed resolution is still offered as a control');
  });

  await check('Show on Display runs immediately, and exactly once', async () => {
    // Phase 7B removed the confirmation: selecting a Multiview and pressing
    // Show is the intent, and a dialog that restates the button is noise.
    // One click must still produce exactly one transaction.
    confirmCalls = 0;
    const before = requests.filter(r =>
      r.url.startsWith('/api/multiview/show')).length;
    REGISTRY.get('mv_show').dispatch('click');
    await flush();
    assertEqual(confirmCalls, 0, 'Show still asks for confirmation');
    const shows = requests.filter(r => r.url.startsWith('/api/multiview/show'));
    assertEqual(shows.length - before, 1,
      'one click produced ' + (shows.length - before) + ' requests');
    assertEqual(shows[shows.length - 1].method, 'POST', 'show method');
    assertEqual(shows[shows.length - 1].payload.name, 'multiview2x2',
                'show payload');
  });

  await check('once shown, the status says so and the action is withdrawn',
    async () => {
      stateResponse = stateBody([Object.assign({}, EXISTING_VIEW,
        {selected_on_output: true})]);
      await selectDecoder('192.0.2.10');
      REGISTRY.get('mv_target').value = 'multiview2x2';
      REGISTRY.get('mv_target').dispatch('change');
      await flush();
      assert(/currently shown on display/i.test(REGISTRY.get('mv_shown').deepText),
        REGISTRY.get('mv_shown').deepText);
      assert(hidden('mv_show'),
        'Show on Display is still offered for something already shown');
    });

  await check('Delete confirms with decoder, name and resolution', async () => {
    confirmCalls = 0;
    REGISTRY.get('mv_delete').dispatch('click');
    await flush();
    assertEqual(confirmCalls, 1, 'no confirmation');
    const labels = lastConfirmOptions.summary.map(row => row.label);
    ['Decoder', 'Multiview', 'Layout'].forEach(label => {
      assert(labels.includes(label), 'confirmation omits ' + label);
    });
    assert(lastConfirmOptions.danger, 'delete is not treated as destructive');
    assertEqual(requests.filter(r =>
      r.url.startsWith('/api/multiview/delete')).length, 1, 'delete request count');
  });

  // ---- many saved Multiviews ----------------------------------------------
  await check('New stays available however many Multiviews are saved',
    async () => {
      const many = ['multiview2x2', 'multiviewPiP', 'multiviewQuad'].map((name, i) =>
        Object.assign({}, EXISTING_VIEW, {name, layout_label: 'Layout ' + i}));
      stateResponse = stateBody(many);
      await selectDecoder('192.0.2.10');
      assert(!REGISTRY.get('mv_new').disabled,
             'New Multiview was withdrawn because others exist');
      assert(!/already has a Multiview/i.test(banner()), banner());
      const options = REGISTRY.get('mv_target').children.map(o => o.text);
      assertEqual(options.length, 4, 'every saved Multiview should be listed');
      many.forEach(view => assert(options.some(t => t.startsWith(view.name)),
        'missing ' + view.name + ' from ' + options.join(' | ')));
    });

  await check('the one on the display is marked in the list', async () => {
    stateResponse = stateBody([
      Object.assign({}, EXISTING_VIEW, {selected_on_output: true}),
      Object.assign({}, EXISTING_VIEW, {name: 'multiviewOther'})]);
    await selectDecoder('192.0.2.10');
    const options = REGISTRY.get('mv_target').children.map(o => o.text);
    const shown = options.filter(t => /on display/i.test(t));
    assertEqual(shown.length, 1, 'exactly one entry should be marked: '
      + options.join(' | '));
    assert(shown[0].startsWith('multiview2x2'), shown[0]);
  });

  // ---- decoder interlocks --------------------------------------------------
  await check('Video Wall is explained before anything is offered', async () => {
    stateResponse = Object.assign(stateBody([]), {
      interlocks: [{interlock: 'video_wall',
                    reason: 'Multiview is unavailable while Video Wall is '
                            + 'enabled on this decoder.'}]});
    await selectDecoder('192.0.2.10');
    assert(/Video Wall/.test(banner()), banner());
    assertEqual(REGISTRY.get('mv_banner').className, 'mv-banner error',
                'the interlock is not shown as a blocking condition');
  });

  await check('Fast Switching on a 1xx decoder is explained the same way',
    async () => {
      stateResponse = Object.assign(stateBody([]), {
        interlocks: [{interlock: 'fast_switching',
                      reason: 'Multiview is unavailable while Fast Switching is '
                              + 'enabled on a 1xx decoder.'}]});
      await selectDecoder('192.0.2.10');
      assert(/Fast Switching/.test(banner()), banner());
    });

  stateResponse = stateBody([]);

  // ---- loading, unreachable, unsupported ---------------------------------
  await check('an unreachable decoder is not reported as an empty one', async () => {
    stateError = 'the device did not answer';
    await selectDecoder('192.0.2.10');
    assert(/unable to read/i.test(banner()), banner());
    assert(!/no multiviews are configured/i.test(banner()),
      'an unreachable decoder read as empty');
    assert(hidden('mv_field_manage'),
           'creation is offered on an unreachable decoder');
    assert(hidden('mv_workspace'), 'the workspace is shown for an unreachable decoder');
    stateError = null;
  });

  await check('a decoder without Multiview support says so and offers nothing',
    async () => {
      stateError = 'This decoder does not expose Multiview.';
      await selectDecoder('192.0.2.11');
      assert(/not supported by this decoder/i.test(banner()), banner());
      assert(hidden('mv_field_manage'),
             'creation is offered on an incapable decoder');
      stateError = null;
    });

  // ---- static guarantees --------------------------------------------------
  const source = fs.readFileSync(SOURCE, 'utf8');
  const sourceWithoutComments = source
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  const style = fs.readFileSync(STYLE, 'utf8');

  await check('the page computes no geometry of its own', () => {
    [/slice/i, /round_?up/i, /round_?down/i, /2880/, /1584/, /layout_configs/]
      .forEach(pattern => assert(!pattern.test(sourceWithoutComments),
        'multiview.js looks like it derives geometry: ' + pattern));
    assert(sourceWithoutComments.includes('/api/multiview/layouts'),
      'the page does not read the canonical catalog');
  });

  await check('the page performs no device programming of its own', () => {
    // The operator notice names the reserved decoder inputs, because §1
    // requires it to tell the operator which resources Multiview may use.
    // That is a sentence for a human, not protocol, so the notice copy is
    // excluded by name and everything else is still held to the rule.
    const from = sourceWithoutComments.indexOf('const NOTICE = {');
    const to = sourceWithoutComments.indexOf('function noticeText()');
    const code = (from >= 0 && to > from)
      ? sourceWithoutComments.slice(0, from) + sourceWithoutComments.slice(to)
      : sourceWithoutComments;
    assert(from >= 0 && to > from, 'the notice copy could not be located');
    [/config_set/, /config_get/, /del_multiview/, /add_multiview/, /vc2_encoder/,
     /ip_input\d/, /sap_input/].forEach(pattern =>
      assert(!pattern.test(code),
        'the page speaks the device protocol directly: ' + pattern));
    // And the notice copy really is only prose: it must not contain a call.
    const notice = sourceWithoutComments.slice(from, to);
    assert(!/getJSON|fetch\(|_mv_|config_set/.test(notice),
      'the notice block contains code, not just words');
  });

  await check('the page uses no native dialog and no innerHTML', () => {
    [/\balert\s*\(/, /\bconfirm\s*\(/, /\bprompt\s*\(/, /innerHTML/]
      .forEach(pattern => assert(!pattern.test(sourceWithoutComments),
        'forbidden construct: ' + pattern));
    assert(sourceWithoutComments.includes('omniConfirm'), 'shared dialog not used');
  });

  await check('the page has exactly one repeating timer, and it is the window '
    + 'preview refresh', () => {
    // Phase 7A's rule was "no interval at all". Phase 7B supersedes it for one
    // thing only: the thumbnails visible inside Multiview windows. So the rule
    // is narrowed rather than dropped -- one interval, that one, and it is
    // cleared. Anything else creeping in still fails here.
    const created = sourceWithoutComments.match(/setInterval\(/g) || [];
    assertEqual(created.length, 1,
      'the page creates ' + created.length + ' repeating timers');
    assert(/windowPreview\.timer = setInterval\(/.test(sourceWithoutComments),
      'the one interval is not the window preview refresh');
    assert(/clearInterval\(windowPreview\.timer\)/.test(sourceWithoutComments),
      'the window preview refresh is never cleared');
    assert(!/MutationObserver/.test(sourceWithoutComments), 'an observer was added');
  });

  await check('implementation wording stays out of the operator-facing text', () => {
    assert(!/Select on Output/i.test(PAGE_HTML), 'the page still says Select on Output');
    assert(!/HDMI Output Video Input/i.test(PAGE_HTML + sourceWithoutComments),
      'the device field name is shown to the operator');
    assert(/Show on Display/.test(PAGE_HTML), 'the page never says Show on Display');
    assert(/Save Multiview/.test(PAGE_HTML), 'the page never says Save Multiview');
  });

  await check('every toolbar and action control shares one control class', () => {
    // The notice is a dialog shown before the page is used, not part of the
    // toolbar; its buttons carry mv-control, its checkbox is a checkbox.
    const controls = Array.from(PAGE_HTML.matchAll(
      /<(select|input|button)[^>]*\sid="(mv_[^"]+)"[^>]*>/g))
      .filter(([, , id]) => id !== 'mv_notice_suppress');
    assert(controls.length >= 8, 'found only ' + controls.length + ' controls');
    controls.forEach(([markup, tag, id]) => {
      assert(/class="[^"]*\bmv-control\b/.test(markup),
        id + ' (' + tag + ') is outside the shared control family');
    });
    // That class fixes the box, which is what makes the row read as one row.
    const rule = style.match(/\.mv-control\{[^}]*\}/);
    assert(rule, 'no .mv-control rule');
    ['height:', 'padding:', 'border:', 'font-size:'].forEach(property =>
      assert(rule[0].includes(property), '.mv-control does not set ' + property));
  });

  await check('the stylesheet names no colour of its own', () => {
    const withoutComments = style.replace(/\/\*[\s\S]*?\*\//g, '');
    [/#[0-9a-fA-F]{3,8}\b/, /\brgba?\(/, /\bhsla?\(/]
      .forEach(pattern => assert(!pattern.test(withoutComments),
        'a literal colour is declared: ' + pattern));
    assert(/var\(--/.test(withoutComments), 'no semantic tokens are used');
  });

  await check('the page carries the shared appearance and settings modules', () => {
    ['appearance.js', 'settings.js', 'toast.js', 'templates.css', 'settings.css']
      .forEach(asset => assert(PAGE_HTML.includes(asset), asset + ' is not loaded'));
    assert(PAGE_HTML.includes('header_gear'), 'the Settings control is missing');
  });

  await check('navigation is present on every page and points here once', () => {
    ['ui/index.html', 'ui/matrix/index.html', 'ui/matrix/configure.html',
     'ui/matrix/usb.html', 'ui/matrix/multiview.html'].forEach(name => {
      const html = fs.readFileSync(path.join(ROOT, name), 'utf8');
      const hits = html.match(/href="\/matrix\/multiview"/g) || [];
      assertEqual(hits.length, 1, name + ' Multiview nav entries');
    });
  });

  // ---- the missing Save button --------------------------------------------
  // A regression test for a real defect. With four sources assigned at 4K, one
  // of them unreachable, the page showed a populated plan and no usable way to
  // save it: the action sat below a long Configuration panel, the placeholder
  // still claimed nothing had been assigned, and nothing said why Save was
  // unavailable.
  const FOUR = {top_left: '192.0.2.20', top_right: '192.0.2.24',
                bottom_left: '192.0.2.25', bottom_right: '192.0.2.26'};

  function planFor(desired, overrides) {
    const cells = Object.keys(desired.assignments || {});
    return Object.assign({
      ok: true, object_name: 'multiview2x2', friendly_name: desired.name,
      updating: false, layout: desired.layout,
      canvas: {width: 1920, height: 1088},
      requested_canvas: {width: 1920, height: 1080}, snapped: true,
      canvas_id: '1920x1080', output_resolution: '1920x1080',
      main_window: 'top_left',
      decoder: {ip: '192.0.2.10', hostname: 'dec-test-01'},
      windows: cells.map((cell, i) => ({
        cell, width: 960, height: 544, index: i, window_number: i + 1,
        is_main: i === 0, area: 960 * 544,
        encoder_index: 2, session: 'session2', scaler_format: '960x544',
        bitrate: 220, reserved_ip_input: 'ip_input' + (2 * i + 2),
        source: {ip: desired.assignments[cell], hostname: 'enc-' + i},
        ip_input: {ip_input: 'ip_input' + (2 * i + 2)},
        scaler: {status: 'change', current: '1280x720', desired: '960x544'}})),
      bandwidth: {budget_source: 900, budget_decoder: 900, floor: 150,
                  unique_streams: cells.length, decoder_aggregate: 220 * cells.length,
                  allocations: cells.map((cell, i) => ({
                    source_ip: desired.assignments[cell], area: 960 * 544,
                    share: 1 / cells.length, allocated: 220, bitrate: 220,
                    capped_by: '', encoder1_bitrate: 700, encoder1_target: null,
                    headroom: 200, error: ''}))},
      audio: {main_window: 'top_left', available: true, session: 'session1',
              source_ip: desired.assignments[cells[0]],
              source_hostname: 'enc-0', address: '233.252.0.12', port: 1000,
              enabled: true, reason: ''},
      mutations: [{stage: 'multiview', description: 'Create multiview2x2'},
                  {stage: 'ip_input', description: 'Point ip_input2 at a stream'}],
      errors: [], warnings: [], conflicts: [], ip_input_collisions: [],
      interlocks: [],
    }, overrides || {});
  }

  async function buildFourSourcePlan() {
    stateResponse = stateBody([]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_new').dispatch('click');
    await flush();
    REGISTRY.get('mv_layout').value = '2x2';
    REGISTRY.get('mv_layout').dispatch('change');
    await flush();
    const boxes = windowBoxes();
    Object.values(FOUR).forEach((ip, index) => {
      boxes[index].dispatch('drop', {dataTransfer: {getData: () => ip}});
    });
    await flush();
  }

  await check('with a valid four-source plan, Save is visible AND enabled',
    async () => {
      forcedPlan = (desired) => planFor(desired);
      await buildFourSourcePlan();
      const save = REGISTRY.get('mv_save');
      assert(!hidden('mv_actions'), 'the action bar is hidden');
      assert(!save.hidden, 'Save Multiview is not visible');
      assert(!save.disabled, 'Save Multiview is disabled for a valid plan');
      assertEqual(REGISTRY.get('mv_save_reason').textContent, '',
                  'a reason is shown for an action that is available');
    });

  await check('an unreachable source disables Save but never hides it',
    async () => {
      forcedPlan = (desired) => planFor(desired, {
        ok: false,
        errors: ['Encoder enc-08412 did not answer, so its Session 2 cannot be '
                 + 'prepared for window top_left.'],
        windows: planFor(desired).windows.map((w, i) => i === 0
          ? Object.assign({}, w, {source: Object.assign({}, w.source,
              {error: 'unreachable'})})
          : w),
      });
      await buildFourSourcePlan();
      const save = REGISTRY.get('mv_save');
      assert(!hidden('mv_actions'), 'the action bar vanished');
      assert(!save.hidden, 'Save Multiview was hidden instead of disabled');
      assert(save.disabled, 'Save is enabled for a plan that cannot be applied');
      const reason = REGISTRY.get('mv_save_reason').textContent;
      assert(/did not answer/.test(reason),
             'the reason Save is unavailable is not shown: ' + reason);
      assert(save.title === reason, 'the control does not carry the reason');
    });

  await check('a source that did not answer is marked in the source list', () => {
    const tile = REGISTRY.get('mv_sources').children
      .find(t => t.dataset.ip === '192.0.2.20');
    assert(tile, 'the source tile is gone');
    assert(tile._classes.has('unreachable'), 'the tile is not marked');
    assert(/did not answer/i.test(tile.deepText), tile.deepText);
  });

  await check('while the plan is being computed the page says so', async () => {
    forcedPlan = (desired) => planFor(desired);
    stateResponse = stateBody([]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_new').dispatch('click');
    await flush();
    REGISTRY.get('mv_layout').value = '2x2';
    REGISTRY.get('mv_layout').dispatch('change');
    await flush();
    // Drop a source but do not let the debounced request run yet.
    windowBoxes()[0].dispatch('drop', {dataTransfer: {getData: () => '192.0.2.20'}});
    const planText = REGISTRY.get('mv_plan').deepText;
    assert(/checking the sources/i.test(planText),
      'the page still claims nothing is assigned while it is working: ' + planText);
    assert(!/assign at least one source/i.test(planText),
      'the misleading placeholder is shown while sources are assigned');
    assertEqual(REGISTRY.get('mv_save_reason').textContent, 'Checking the sources…',
                'no indication that Save is waiting on something');
    await flush();
  });

  await check('with no source assigned, Save says what is missing', async () => {
    forcedPlan = null;
    stateResponse = stateBody([]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_new').dispatch('click');
    await flush();
    const save = REGISTRY.get('mv_save');
    assert(!save.hidden, 'Save Multiview is hidden before any source is assigned');
    assert(save.disabled, 'Save is enabled with nothing assigned');
    assert(/assign a source/i.test(REGISTRY.get('mv_save_reason').textContent),
      REGISTRY.get('mv_save_reason').textContent);
  });

  await check('saving moves CREATE to EDIT and offers Show on Display',
    async () => {
      forcedPlan = (desired) => planFor(desired);
      await buildFourSourcePlan();
      assert(hidden('mv_show'), 'Show on Display is offered before a save');
      assert(hidden('mv_delete'), 'Delete is offered before a save');
      // After the save the object exists, so the decoder reports it.
      stateResponse = stateBody([EXISTING_VIEW]);
      applyResponse = {ok: true, status: 'VERIFIED', applied: ['Create multiview2x2'],
                       verified: [{step: 'Create multiview2x2', verified: true}],
                       plan: {object_name: 'multiview2x2'}};
      confirmAnswer = true;
      REGISTRY.get('mv_save').dispatch('click');
      await flush();
      assertEqual(REGISTRY.get('mv_status').textContent, 'VERIFIED', 'save status');
      assertEqual(REGISTRY.get('mv_target').value, 'multiview2x2',
                  'the saved object did not become the selected one');
      assert(!hidden('mv_show'), 'Show on Display is not offered after a save');
      assert(!hidden('mv_delete'), 'Delete is not offered after a save');
      assert(/not currently shown on display/i.test(
        REGISTRY.get('mv_shown').deepText), 'save changed the display');
      forcedPlan = null;
    });

  // ---- Phase 7: the canvas has two meanings ------------------------------
  // An inactive Multiview's canvas is a form. The active one's canvas is the
  // display. Everything below exists because confusing the two means an
  // operator changes air without meaning to, or waits for a change that never
  // comes -- and neither is recoverable by looking at the screen.

  async function openLive() {
    stateResponse = stateBody([LIVE_VIEW]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_target').value = 'multiview2x2';
    REGISTRY.get('mv_target').dispatch('change');
    await flush();
  }
  async function openInactive() {
    stateResponse = stateBody([EXISTING_VIEW]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_target').value = 'multiview2x2';
    REGISTRY.get('mv_target').dispatch('change');
    await flush();
  }

  await check('an inactive canvas says in words that nothing is on air yet',
    async () => {
      await openInactive();
      const mode = REGISTRY.get('mv_canvas_mode');
      assert(!mode.hidden, 'the canvas never states which mode it is in');
      assert(!mode._classes.has('live'), 'an inactive canvas is styled as live');
      assert(!/^LIVE/.test(mode.textContent),
        'an inactive canvas claims to be live: ' + mode.textContent);
      assert(/saved/i.test(mode.textContent)
             && /not on the display/i.test(mode.textContent), mode.textContent);
      assert(!REGISTRY.get('mv_stage')._classes.has('live'),
        'the inactive stage carries the live styling');
    });

  await check('a drop on an inactive canvas edits the preset and reaches nothing',
    async () => {
      const before = requests.length;
      windowBoxes()[1].dispatch('drop',
        {dataTransfer: {getData: () => '192.0.2.24'}});
      await flush();
      const wrote = requests.slice(before).filter(r => r.method !== 'GET'
        && !r.url.startsWith('/api/multiview/plan'));
      assertEqual(wrote, [], 'editing a preset reached a device');
      assert(windowBoxes()[1].deepText.includes('new-wp-01'),
        'the preset did not take the new source');
    });

  await check('the active canvas is marked LIVE, in the operator words',
    async () => {
      await openLive();
      const mode = REGISTRY.get('mv_canvas_mode');
      assert(!mode.hidden, 'the live canvas is not marked at all');
      assert(mode._classes.has('live'), 'the LIVE banner is not styled as live');
      assert(/^LIVE/.test(mode.textContent),
        'the banner does not lead with LIVE: ' + mode.textContent);
      assert(/applied immediately/i.test(mode.textContent), mode.textContent);
      assert(REGISTRY.get('mv_stage')._classes.has('live'),
        'the live stage is not visually distinguished from a preset');
    });

  await check('the live mode is not something to infer from button availability',
    async () => {
      // The Phase 6 page distinguished the two only by whether Show on Display
      // was offered. That is an absence, and an absence is not a signal.
      const mode = REGISTRY.get('mv_canvas_mode').textContent;
      assert(mode && mode.length > 20, 'the mode is stated too thinly: ' + mode);
      assert(hidden('mv_show'), 'Show is still offered for the active Multiview');
      assert(/currently shown on display/i.test(REGISTRY.get('mv_shown').deepText),
        REGISTRY.get('mv_shown').deepText);
    });

  await check('a window shows what the decoder is receiving, not what was saved',
    () => {
      const box = windowBoxes()[1];
      assert(box.deepText.includes('new-wp-01'),
        'the live subscription is not named: ' + box.deepText);
      assert(box._classes.has('assigned'),
        'a subscribed window is drawn as empty');
      assert(box._classes.has('health-no-signal'),
        'a subscribed window with no video looks the same as a working one');
      assert(box.deepText.includes('ip_input4'),
        'the decoder input feeding the window is not shown: ' + box.deepText);
      const live = windowBoxes()[0];
      assert(live._classes.has('health-live'), 'a locked window is not marked');
      assert(!live._classes.has('health-no-signal'), 'health classes collided');
    });

  await check('a window fed by an unrecognised stream is shown, not erased',
    async () => {
      stateResponse = stateBody([UNKNOWN_SOURCE_VIEW]);
      await selectDecoder('192.0.2.10');
      REGISTRY.get('mv_target').value = 'multiview2x2';
      REGISTRY.get('mv_target').dispatch('change');
      await flush();
      const box = windowBoxes()[1];
      assert(!/drop a source/i.test(box.deepText),
        'a window that is playing was drawn as an empty one: ' + box.deepText);
      assert(/unknown source/i.test(box.deepText),
        'the unresolved subscription is not admitted: ' + box.deepText);
      assert(box.deepText.includes('233.252.0.99'),
        'the stream actually arriving is not shown: ' + box.deepText);
      assert(box._classes.has('assigned'),
        'a window receiving video is styled as unassigned');
      await openLive();
    });

  await check('a drop on the live canvas switches it immediately, without asking',
    async () => {
      confirmCalls = 0; confirmAnswer = true;
      const before = requests.filter(r =>
        r.url.startsWith('/api/multiview/switch')).length;
      switchMark = requests.length;
      windowBoxes()[0].dispatch('drop',
        {dataTransfer: {getData: () => '192.0.2.24'}});
      await flush();
      // Phase 7B removed the confirmation. The canvas already says LIVE, and
      // asking again on every drop turns that warning into a formality.
      assertEqual(confirmCalls, 0, 'a live drop still opened a dialog');
      const switches = requests.filter(r =>
        r.url.startsWith('/api/multiview/switch'));
      assertEqual(switches.length - before, 1, 'switch request count');
      const last = switches[switches.length - 1];
      assertEqual(last.method, 'POST', 'switch method');
      assertEqual(last.payload.name, 'multiview2x2', 'switch name');
      assertEqual(last.payload.cell, 'top_left', 'switch cell');
      assertEqual(last.payload.source, '192.0.2.24', 'switch source');
      assertEqual(last.payload.decoder, '192.0.2.10', 'switch decoder');
    });

  await check('a live switch neither saves the whole preset nor re-shows it',
    () => {
      // From the drop itself, not the last N requests -- a fixed lookback
      // reaches into the previous test and reports its Save as this one's.
      const after = requests.slice(switchMark);
      assert(!after.some(r => r.url.startsWith('/api/multiview/apply')),
        'a live drop also wrote the preset through Save');
      assert(!after.some(r => r.url.startsWith('/api/multiview/show')),
        'a live drop re-showed the whole Multiview');
    });

  await check('a live drop reaches the decoder with no dialog in the way',
    async () => {
      // The check this replaces asserted that Cancel stopped the switch. There
      // is no Cancel any more, so what matters instead is that the drop goes
      // straight through and carries the right window and source.
      await openLive();
      confirmCalls = 0;
      const before = requests.filter(r =>
        r.url.startsWith('/api/multiview/switch')).length;
      windowBoxes()[0].dispatch('drop',
        {dataTransfer: {getData: () => '192.0.2.24'}});
      await flush();
      const switches = requests.filter(r =>
        r.url.startsWith('/api/multiview/switch'));
      assertEqual(switches.length - before, 1, 'the drop did not switch');
      assertEqual(confirmCalls, 0, 'a dialog appeared');
      assertEqual(switches[switches.length - 1].payload.cell, 'top_left',
                  'the wrong window was switched');
    });

  await check('a rolled-back switch puts the window back to what is on screen',
    async () => {
      await openLive();
      switchResponse = {ok: false, status: 'FAILED - ROLLED BACK',
                        cell: 'top_left', restored_source: '192.0.2.20',
                        error: 'ip_input2 never locked',
                        steps: [{step: 'Point ip_input2 at 233.252.0.42',
                                 verified: false, error: 'never locked'}],
                        rollback: [{step: 'Restore ip_input2', verified: true}]};
      windowBoxes()[0].dispatch('drop',
        {dataTransfer: {getData: () => '192.0.2.24'}});
      await flush();
      assert(/ROLLED BACK/.test(REGISTRY.get('mv_status').textContent),
        'a failed switch did not report itself: '
        + REGISTRY.get('mv_status').textContent);
      assert(windowBoxes()[0].deepText.includes('enc-test-01'),
        'the canvas kept showing a source the decoder rejected: '
        + windowBoxes()[0].deepText);
      assert(REGISTRY.get('mv_result').deepText.includes('never locked'),
        'the failure detail was not offered');
      switchResponse = {ok: true, status: 'VERIFIED', cell: 'top_left',
                        source: '192.0.2.24', saved: true, steps: [],
                        windows: [], unlocked_windows: [],
                        regressed_windows: []};
    });

  await check('clearing a live window is a switch, not a local delete',
    async () => {
      await openLive();
      confirmCalls = 0;
      const before = requests.filter(r =>
        r.url.startsWith('/api/multiview/switch')).length;
      const clear = windowBoxes()[0].findAll(
        c => c._classes.has('mv-window-clear'))[0];
      assert(clear, 'the clear control is missing from an assigned window');
      clear.dispatch('click');
      await flush();
      assertEqual(confirmCalls, 0, 'clearing a live window opened a dialog');
      const switches = requests.filter(r =>
        r.url.startsWith('/api/multiview/switch'));
      assertEqual(switches.length - before, 1,
        'clearing a live window did not reach the decoder');
      assertEqual(switches[switches.length - 1].payload.source, '',
        'a clear was sent as a switch to something');
    });

  await check('Save on the live canvas does not promise to apply sources again',
    async () => {
      await openLive();
      assert(REGISTRY.get('mv_save').textContent !== 'Save Multiview',
        'the live Save button reads exactly like the preset one');
      await openInactive();
      assertEqual(REGISTRY.get('mv_save').textContent, 'Save Multiview',
                  'the inactive Save button was renamed');
    });

  // ---- Phase 7: source eligibility ---------------------------------------
  await check('an encoder that only needs configuring is shown, with the reason',
    async () => {
      await openInactive();
      const tiles = REGISTRY.get('mv_sources').children;
      const tile = tiles.find(t => t.deepText.includes('enc-unconfigured-01'));
      assert(tile, 'an encoder that could be made to work was hidden');
      assert(tile.deepText.includes('Multicast configuration required'),
        'the tile does not say what is wrong: ' + tile.deepText);
      assert(tile.deepText.includes('Session 2 has no multicast address'),
        'the tile does not say how to fix it: ' + tile.deepText);
      assert(tile._classes.has('needs-work'),
        'a source needing configuration looks identical to a ready one');
      assertEqual(tile.draggable, false,
        'a source that cannot work yet can still be dragged onto a window');
      assert(/multicast configuration required/i
        .test(tile.getAttribute('aria-label') || ''),
        'the reason is not announced: ' + tile.getAttribute('aria-label'));

      // draggable=false is the browser's guard; the handler carries its own,
      // because a page that relies on one line of defence has none.
      let carried = null;
      tile.dispatch('dragstart', {dataTransfer: {
        setData: (type, value) => { carried = value; }}});
      assertEqual(carried, null,
        'an unconfigured source handed its address to a drag');
      assert(!tile._classes.has('dragging'),
        'an unconfigured source started a drag');
    });

  await check('a source that needs configuring cannot be assigned by click either',
    async () => {
      // Click-to-pick then click-a-window is the keyboard-and-touch path to the
      // same place as a drag, so it has to refuse in the same place.
      const tile = REGISTRY.get('mv_sources').children
        .find(t => t.deepText.includes('enc-unconfigured-01'));
      tile.dispatch('click');
      await flush(2);
      const empty = windowBoxes().find(b => /drop a source/i.test(b.deepText));
      assert(empty, 'no empty window was available to attempt the assignment');
      empty.dispatch('click');
      await flush(2);
      const boxes = windowBoxes();
      assert(!boxes.some(b => b.deepText.includes('enc-unconfigured-01')),
        'an unconfigured source was placed in a window');
      assert(!boxes.some(b => b.deepText.includes('192.0.2.25')),
        'an unconfigured source was placed in a window by address');
    });

  await check('ready sources come first and are still draggable', () => {
    const tiles = REGISTRY.get('mv_sources').children;
    assertEqual(tiles[0].dataset.status, 'ready', 'ready sources are not first');
    assertEqual(tiles[0].draggable, true, 'a ready source cannot be dragged');
    assertEqual(tiles[tiles.length - 1].dataset.status, 'configuration_required',
                'the not-yet-usable source is not last');
  });

  await check('an offline encoder is not offered, but is still accounted for',
    () => {
      const tiles = REGISTRY.get('mv_sources').children.map(t => t.deepText);
      assert(!tiles.some(t => t.includes('enc-offline-01')),
        'an offline encoder was offered as a source');
      const excluded = REGISTRY.get('mv_sources_excluded').deepText;
      assert(excluded.includes('enc-offline-01'),
        'an offline encoder vanished without explanation');
      assert(excluded.includes('Offline'),
        'the excluded list does not say why: ' + excluded);
    });

  await check('the live canvas is distinguished by more than a class name', () => {
    // Adding the class and never styling it produces a page that passes every
    // behavioural check and looks identical to an inactive one.
    assert(/\.mv-canvas-mode\.live\{[^}]+\}/.test(style),
      'the LIVE banner has no styling of its own');
    assert(/\.mv-stage\.live\{[^}]+\}/.test(style),
      'the live canvas is not visually distinguished from a preset');
    assert(/\.mv-source\.needs-work\{[^}]+\}/.test(style),
      'a source needing configuration has no styling of its own');
    assert(/\.mv-window\.health-no-signal\{[^}]+\}/.test(style),
      'a subscribed window with no video has no styling of its own');
  });

  // ---- Phase 7A: source hover preview ------------------------------------
  // The preview exists so an operator can tell two encoders apart without
  // routing one to find out. Everything below is about it staying that: lazy,
  // read-only, and gone the moment it is not being looked at.

  const card = () => REGISTRY.get('mv_decoder') && sandboxCard();
  function sandboxCard() {
    return DOCUMENT.body.children.find(c => c._classes.has('mv-preview')) || null;
  }
  function sourceTile(hostname) {
    return REGISTRY.get('mv_sources').children
      .find(t => t.deepText.includes(hostname));
  }
  function hover(tile) { tile.dispatch('pointerenter', {pointerType: 'mouse'}); }
  function closeAnyPreview() {
    (DOCUMENT._listeners.keydown || []).forEach(fn => fn({key: 'Escape'}));
  }

  await check('no preview was fetched before a canvas or a hover existed', () => {
    // Recorded at the very first check, before any decoder was chosen. Window
    // previews are fetched for sources ON the canvas, which is lazy but not
    // zero, so the assertion belongs at page load rather than here.
    assertEqual(previewsAtPageLoad, 0,
      'opening the page fetched ' + previewsAtPageLoad + ' preview(s)');
    assert(!sandboxCard(), 'a preview card existed before any hover');
  });

  await check('a preview appears after the pointer rests on a tile', async () => {
    await openInactive();
    previewRequests.length = 0;
    const tile = sourceTile('enc-test-01');
    hover(tile);
    assertEqual(previewRequests.length, 0,
      'the preview was requested before the debounce elapsed');
    await flush();
    assertEqual(previewRequests.length, 1, 'preview request count');
    assert(previewRequests[0].includes('192.0.2.20'), previewRequests[0]);
    const box = sandboxCard();
    assert(box && !box.hidden, 'the preview card did not open');
    assert(box.deepText.includes('enc-test-01'), box.deepText);
    const image = box.findAll(c => c.tagName === 'IMG')[0];
    assert(image, 'no image in the preview');
    assert(image.src.includes('192.0.2.20/thumbnail/thumbnail1.jpg'),
      'the preview did not come from that encoder: ' + image.src);
  });

  await check('the preview names the source and stays out of engineering detail',
    () => {
      const box = sandboxCard();
      assert(box.deepText.includes('192.0.2.20'), 'the address is not shown');
      assert(box.deepText.includes('S1 ') && box.deepText.includes('S2 '),
        'the sessions are not shown: ' + box.deepText);
      assert(!/bitrate|scaler|ip_input|encoder 2/i.test(box.deepText),
        'engineering detail leaked into the preview: ' + box.deepText);
    });

  await check('leaving the tile closes the preview and drops the image',
    async () => {
      const box = sandboxCard();
      // Held from before the close: emptying the card removes it from the tree,
      // but the element itself is what owns the connection, and an <img> whose
      // src is still set is still a request the browser may be finishing.
      const image = box.findAll(c => c.tagName === 'IMG')[0];
      assert(image, 'no image was open to begin with');
      const tile = sourceTile('enc-test-01');
      tile.dispatch('pointerleave');
      await flush(2);
      assert(box.hidden, 'the preview stayed open after the pointer left');
      assertEqual(box.findAll(c => c.tagName === 'IMG').length, 0,
        'the image was left in the card');
      assert(!image.src, 'the image still had a src, and so a connection: '
                         + image.src);
    });

  await check('moving quickly across the list opens nothing', async () => {
    await openInactive();
    previewRequests.length = 0;
    REGISTRY.get('mv_sources').children.forEach((tile) => {
      hover(tile);
      tile.dispatch('pointerleave');       // gone before the debounce elapses
    });
    await flush();
    assertEqual(previewRequests, [],
      'sweeping the list opened preview connections');
    assert(!sandboxCard() || sandboxCard().hidden, 'a preview was left open');
  });

  await check('crossing tiles without leaving them opens only the last one',
    async () => {
      // A pointer moving between adjacent tiles can raise the next enter
      // before the previous leave, so scheduling a preview has to cancel any
      // debounce already pending rather than rely on the leave to do it.
      await openInactive();
      previewRequests.length = 0;
      const tiles = REGISTRY.get('mv_sources').children;
      tiles.forEach(tile => hover(tile));      // no pointerleave anywhere
      await flush();
      assertEqual(previewRequests.length, 1,
        'each tile crossed opened its own preview: ' + previewRequests);
      const last = tiles[tiles.length - 1];
      assert(previewRequests[0].includes(last.dataset.ip),
        'the preview that opened was not the tile the pointer ended on');
      closeAnyPreview();
      await flush(2);
    });

  await check('a preview that resolves after the pointer left is discarded',
    async () => {
      // The status request is not instant on real hardware. If the pointer has
      // moved on by the time it answers, the card must not appear over a tile
      // nobody is pointing at.
      await openInactive();
      let release = null;
      const held = new Promise(resolve => { release = resolve; });
      previewHold = held;
      const tile = sourceTile('enc-test-01');
      hover(tile);
      await flush(4);                      // debounce fires, request in flight
      tile.dispatch('pointerleave');       // and the pointer leaves
      release();
      previewHold = null;
      await flush();
      assert(!sandboxCard() || sandboxCard().hidden,
        'a stale preview opened after the pointer had gone');
    });

  await check('starting a drag dismisses the preview', async () => {
    await openInactive();
    const tile = sourceTile('enc-test-01');
    hover(tile);
    await flush();
    assert(!sandboxCard().hidden, 'the preview did not open');
    let carried = null;
    tile.dispatch('dragstart', {dataTransfer: {
      setData: (type, value) => { carried = value; },
      effectAllowed: ''}});
    assert(sandboxCard().hidden, 'the preview survived the start of a drag');
    assertEqual(carried, '192.0.2.20',
      'closing the preview broke the drag it was supposed to get out of');
  });

  await check('the preview never intercepts the pointer', () => {
    assert(/\.mv-preview\{[^}]*pointer-events:\s*none/.test(style),
      'the preview card can swallow a drop');
  });

  await check('a drag still lands on a window with a preview open', async () => {
    await openInactive();
    const tile = sourceTile('new-wp-01');
    hover(tile);
    await flush();
    const before = requests.length;
    windowBoxes()[0].dispatch('drop',
      {dataTransfer: {getData: () => '192.0.2.24'}});
    await flush();
    assert(windowBoxes()[0].deepText.includes('new-wp-01'),
      'the drop did not take while a preview was open');
    const wrote = requests.slice(before).filter(r => r.method !== 'GET'
      && !r.url.startsWith('/api/multiview/plan'));
    assertEqual(wrote, [], 'an inactive drop reached a device');
  });

  await check('a disabled preview says so, and says it differently', async () => {
    await openInactive();
    const tile = sourceTile('new-wp-01');
    hover(tile);
    await flush();
    const box = sandboxCard();
    assert(box.deepText.includes('Preview disabled'), box.deepText);
    assert(!box.deepText.includes('Preview unavailable'),
      'disabled and unavailable were collapsed into one message');
    assertEqual(box.findAll(c => c.tagName === 'IMG').length, 0,
      'a disabled preview still fetched an image');
  });

  await check('an unavailable preview says so, and does not disable routing',
    async () => {
      await openInactive();
      previewResponses['192.0.2.24'] = {ok: false, status: 'unavailable',
        reason: 'The encoder did not answer.'};
      const tile = sourceTile('new-wp-01');
      hover(tile);
      await flush();
      assert(sandboxCard().deepText.includes('Preview unavailable'),
        sandboxCard().deepText);
      // The whole point: a source you cannot look at is still a source.
      assertEqual(tile.draggable, true,
        'a failed preview made the source undraggable');
      assertEqual(tile.dataset.status, 'ready',
        'a failed preview changed the eligibility of the source');
      tile.dispatch('pointerleave');
      await flush(2);
    });

  await check('a source that only needs configuring can still be previewed',
    async () => {
      // This is the case where a preview helps most: the operator can see what
      // is on an encoder before deciding whether it is worth configuring.
      await openInactive();
      previewRequests.length = 0;
      const tile = sourceTile('enc-unconfigured-01');
      hover(tile);
      await flush();
      assertEqual(previewRequests.length, 1, 'it was not previewed at all');
      const box = sandboxCard();
      assert(box.findAll(c => c.tagName === 'IMG')[0], 'no image was shown');
      assert(box.deepText.includes('Multicast configuration required'),
        'the tile reason is not repeated on the card: ' + box.deepText);
      assertEqual(tile.draggable, false,
        'previewing it made it draggable');
    });

  await check('hovering performs no routing call of any kind', async () => {
    await openInactive();
    const before = requests.length;
    REGISTRY.get('mv_sources').children.forEach((tile) => {
      hover(tile);
    });
    await flush();
    const after = requests.slice(before).map(r => r.url.split('?')[0]);
    const routing = after.filter(u => !u.startsWith('/api/multiview/preview')
      && !u.startsWith('/api/multiview/plan'));
    assertEqual(routing, [], 'hovering reached a routing endpoint: ' + after);
    assert(!requests.slice(before).some(r => r.method === 'POST'
      && !r.url.startsWith('/api/multiview/plan')),
      'hovering sent a POST');
  });

  await check('Escape dismisses an open preview', async () => {
    await openInactive();
    const tile = sourceTile('enc-test-01');
    hover(tile);
    await flush();
    assert(!sandboxCard().hidden, 'the preview did not open');
    (DOCUMENT._listeners.keydown || []).forEach(fn => fn({key: 'Escape'}));
    assert(sandboxCard().hidden, 'Escape left the preview open');
  });

  await check('keyboard focus shows the same preview', async () => {
    await openInactive();
    previewRequests.length = 0;
    const tile = sourceTile('enc-test-01');
    tile.dispatch('focus');
    await flush();
    assertEqual(previewRequests.length, 1, 'focus did not open a preview');
    assert(!sandboxCard().hidden, 'focus did not show the card');
    tile.dispatch('blur');
    await flush(2);
    assert(sandboxCard().hidden, 'blur left the preview open');
  });

  await check('sixty seconds of sitting still makes no preview request',
    async () => {
      await openInactive();
      (DOCUMENT._listeners.keydown || []).forEach(fn => fn({key: 'Escape'}));
      await flush();
      previewRequests.length = 0;
      const before = requests.length;
      const timersBefore = timers.filter(t => !t.cancelled && !t.fired).length;

      // Sixty rounds of letting every pending timer run. A repeating timer
      // would re-arm itself and keep firing; a one-shot debounce that nothing
      // triggered has nothing to fire. Timers that have already run are NOT
      // reset -- a browser does not re-run them, and pretending otherwise
      // would just replay the hovers from the checks above.
      for (let tick = 0; tick < 60; tick += 1) await flush(2);

      assertEqual(previewRequests, [], 'the page polled for previews while idle');
      const wrote = requests.slice(before)
        .filter(r => r.url.startsWith('/api/multiview/preview'));
      assertEqual(wrote, [], 'idle preview traffic');
      const timersAfter = timers.filter(t => !t.cancelled && !t.fired).length;
      assert(timersAfter <= timersBefore,
        'the page armed new timers while nothing was happening');
    });

  await check('the preview card is positioned inside the viewport', async () => {
    await openInactive();
    const tile = sourceTile('enc-test-01');
    // A tile at the very bottom of the window must flip the card upward
    // rather than let it run off the screen.
    tile._rect = {top: 860, left: 40, right: 300, bottom: 890,
                  width: 260, height: 30};
    hover(tile);
    await flush();
    const box = sandboxCard();
    const size = box.getBoundingClientRect();
    const top = parseInt(box.style.top, 10);
    const left = parseInt(box.style.left, 10);
    assert(top >= 8, 'the card was positioned above the top of the window: ' + top);
    // The whole card has to fit, not just its top edge.
    assert(top + size.height <= sandbox.innerHeight - 8,
      'the card ran off the bottom: top ' + top + ' + ' + size.height
      + ' > ' + (sandbox.innerHeight - 8));
    assert(left >= 8, 'the card ran off the left: ' + left);
    assert(left + size.width <= sandbox.innerWidth - 8,
      'the card ran off the right: ' + left + ' + ' + size.width);
    delete tile._rect;
  });

  await check('the preview uses only semantic colour tokens', () => {
    const block = style.slice(style.indexOf('.mv-preview{'));
    const section = block.slice(0, block.indexOf('\n\n\n') + 1 || block.length);
    assert(!/#[0-9a-f]{3,8}\b/i.test(section),
      'the preview hard-codes a hex colour');
    assert(/var\(--card\)/.test(section) && /var\(--border\)/.test(section),
      'the preview does not use the shared surface tokens');
  });

  // ---- Phase 7B: the operator notice -------------------------------------
  // Multiview reconfigures equipment other people are using. The notice says
  // which equipment, once per release, and its suppression is scoped to that
  // release rather than being a permanent "never again".

  const notice = () => REGISTRY.get('mv_notice');
  // The page raises the notice from start(). A test that needs it on screen
  // again drives the same elements a fresh load would leave behind.
  function showNoticeFromTest() {
    REGISTRY.get('mv_notice').hidden = false;
  }
  const noticeText = () => REGISTRY.get('mv_notice_body').deepText;

  await check('the notice is shown when the page is newly opened', () => {
    // `start()` ran when the module was loaded, before any of these checks.
    assert(noticeShownAtStart, 'the notice did not appear on a fresh page');
  });

  await check('it says what Multiview may change, in an operator\'s terms', () => {
    const text = noticeText();
    [['Encoder 2', /Encoder 2/],
     ['the scaler', /scaler/i],
     ['Session 2', /Session 2/],
     ['SAP', /SAP/],
     ['subscriptions', /subscribed|subscription/i],
     ['the reserved inputs', /ip_input2.*ip_input8|ip_input2/],
     ['display audio', /sound|audio/i],
     ['Session 1 audio', /Session 1/],
     ['bit rates', /bit rate/i],
     ['immediate live changes', /immediately|while it is on screen/i],
     ['recall reconciliation', /recall/i],
    ].forEach(([what, pattern]) => assert(pattern.test(text),
      'the notice does not mention ' + what));
  });

  await check('and what it will never do on its own', () => {
    const text = noticeText();
    [['Video Wall', /Video Wall/],
     ['Fast Switching', /Fast Switching/],
     ['opening the page', /just because you opened/i],
     ['enabling preview', /preview or thumbnail generation/i],
    ].forEach(([what, pattern]) => assert(pattern.test(text),
      'the notice does not say it will not touch ' + what));
    assert(/never do these on its own/i.test(text),
      'the two lists are not distinguished');
  });

  await check('the notice costs nothing and touches no device', () => {
    // Measured at page load, which is when the notice is raised -- not across
    // the whole session, by which point the tests have saved and switched.
    assertEqual(writesAtPageLoad, 0,
      'the page wrote ' + writesAtPageLoad + ' time(s) before the notice');
    assertEqual(previewsAtPageLoad, 0,
      'the notice fetched device preview state');
    // And displaying it again costs nothing either.
    const before = requests.length;
    showNoticeFromTest();
    assertEqual(requests.length, before, 'showing the notice made a request');
  });

  await check('Copy to clipboard copies the whole notice as plain text',
    async () => {
      sandboxClipboard.length = 0;
      REGISTRY.get('mv_notice_copy').dispatch('click');
      await flush(3);
      assertEqual(sandboxClipboard.length, 1, 'nothing was copied');
      const copied = sandboxClipboard[0];
      assert(copied.includes('V1.0.7'), 'the version is missing: ' + copied.slice(0, 80));
      assert(copied.includes('Before you use Multiview'), 'the title is missing');
      assert(copied.includes('Encoder 2'), 'the body is missing');
      assert(copied.includes('Video Wall'), 'the will-not list is missing');
      assert(/\n  - /.test(copied), 'it is not readable plain text');
      ['password', 'token', 'secret', 'credential'].forEach(word =>
        assert(!copied.toLowerCase().includes(word),
          'the copied notice contains ' + word));
    });

  await check('Save to file writes a readable, versioned text file', async () => {
    sandbox.URL._created.length = 0;
    REGISTRY.get('mv_notice_save').dispatch('click');
    await flush(3);
    assertEqual(sandbox.URL._created.length, 1, 'no file was produced');
    const blob = sandbox.URL._created[0];
    assertEqual(blob.options.type, 'text/plain;charset=utf-8', 'not plain text');
    const text = (blob.parts || []).join('');
    assert(text.includes('V1.0.7'), 'the file does not carry the version');
    assert(text.includes('Before you use Multiview'), 'no title');
    assert(text.includes('Generated '), 'no generated date');
    assert(text.includes('Encoder 2') && text.includes('Video Wall'),
      'the file is not the complete notice');
    const link = DOCUMENT.body.children.find(c => c.tagName === 'A')
      || lastDownloadName;
    assert(String(lastDownloadName).includes('V1.0.7'),
      'the filename does not carry the version: ' + lastDownloadName);
    assert(String(lastDownloadName).endsWith('.txt'),
      'not a .txt file: ' + lastDownloadName);
  });

  await check('suppression is recorded against the version, not as a flag',
    async () => {
      REGISTRY.get('mv_notice_suppress').checked = true;
      REGISTRY.get('mv_notice_close').dispatch('click');
      await flush(2);
      assert(notice().hidden, 'the notice stayed open');
      const stored = sandbox.localStorage._store;
      assertEqual(stored.get('multiview_notice_acknowledged_version'), 'V1.0.7',
        'the acknowledgement is not the version: '
        + JSON.stringify(Array.from(stored.entries())));
      // The thing this must never be.
      assert(!Array.from(stored.keys()).some(k => /hidden|dismissed/i.test(k)),
        'a permanent suppression flag was written');
    });

  await check('the same version does not show it again', () => {
    // `noticeAcknowledged()` is what start() consults.
    assertEqual(sandbox.localStorage.getItem('multiview_notice_acknowledged_version'),
                'V1.0.7');
    assert(/localStorage\.getItem\(NOTICE_KEY\) === state\.version/
      .test(sourceWithoutComments),
      'suppression is not compared against the running version');
  });

  await check('a new version shows it again by itself', () => {
    // No code change, no migration: the stored value simply stops matching.
    sandbox.localStorage.setItem('multiview_notice_acknowledged_version', 'V1.0.6');
    assert(/if \(!noticeAcknowledged\(\)\) showNotice\(\)/
      .test(sourceWithoutComments),
      'the page does not consult the acknowledgement on load');
    sandbox.localStorage.setItem('multiview_notice_acknowledged_version', 'V1.0.7');
  });

  await check('it is not re-shown by ordinary re-rendering', () => {
    // It is raised from start(), once, and nowhere inside render().
    const renderBody = sourceWithoutComments.slice(
      sourceWithoutComments.indexOf('function render() {'),
      sourceWithoutComments.indexOf('function wire() {'));
    assert(!/showNotice\(/.test(renderBody),
      'render() can put the notice back in front of the operator');
  });

  // ---- Phase 7B: previews inside the windows -----------------------------

  await check('a window whose source has preview shows the real thumbnail',
    async () => {
      await openLive();
      await flush();
      const box = windowBoxes().find(b => b.findAll(
        c => c._classes.has('mv-window-shot')).length > 0);
      assert(box, 'no window is showing a thumbnail');
      const shot = box.findAll(c => c._classes.has('mv-window-shot'))[0];
      assert(shot.src.includes('/thumbnail/thumbnail1.jpg'),
        'the window is not showing the encoder thumbnail: ' + shot.src);
      assert(shot.src.includes('?t='), 'the frame is not cache-busted');
      assert(box._classes.has('has-shot'), 'the window is not marked as showing one');
    });

  await check('the source information stays readable over the picture', () => {
    const box = windowBoxes().find(b => b._classes.has('has-shot'));
    const overlay = box.findAll(c => c._classes.has('mv-window-overlay'))[0];
    assert(overlay, 'there is no overlay over the video');
    assert(overlay.deepText.includes('enc-test-01')
        || overlay.deepText.includes('192.0.2.20'),
      'the source is not named over the picture: ' + overlay.deepText);
    assert(overlay.deepText.includes('ip_input'),
      'the decoder input is not shown: ' + overlay.deepText);
    assert(/\.mv-window\.has-shot \.mv-window-overlay\{[^}]*background/.test(style),
      'the overlay has no scrim, so it will be unreadable over bright video');
  });

  await check('a disabled preview shows no image and says so', async () => {
    // 192.0.2.24 answers `disabled`. The device's placeholder JPEG is
    // deliberately not displayed: it looks like a picture and is not one.
    await openLive();
    await flush();
    const boxes = windowBoxes();
    const box = boxes.find(b => b.deepText.includes('new-wp-01'));
    assert(box, 'the disabled source is not on the canvas');
    assertEqual(box.findAll(c => c._classes.has('mv-window-shot')).length, 0,
      'a disabled preview still drew an image');
    assert(box.deepText.includes('Preview disabled'), box.deepText);
    assert(box.deepText.includes('new-wp-01'),
      'the source information was replaced by the preview state');
  });

  await check('a preview that is unavailable does not make the window unusable',
    async () => {
      previewResponses['192.0.2.21'] = {ok: false, status: 'unavailable',
                                        reason: 'The encoder did not answer.'};
      await openLive();
      await flush();
      const box = windowBoxes().find(b => b._classes.has('assigned'));
      assert(box, 'no assigned window');
      // Routing is unaffected: the window still takes a drop.
      const before = requests.filter(r =>
        r.url.startsWith('/api/multiview/switch')).length;
      box.dispatch('drop', {dataTransfer: {getData: () => '192.0.2.24'}});
      await flush();
      assert(requests.filter(r => r.url.startsWith('/api/multiview/switch')).length
             > before, 'a window with no preview refused a drop');
    });

  await check('one refresh timer exists while previews are on the canvas',
    async () => {
      await openLive();
      await flush();
      const live = intervals.filter(i => i.live);
      assertEqual(live.length, 1, 'there are ' + live.length + ' refresh timers');
      assertEqual(live[0].ms, 5000, 'the refresh is ' + live[0].ms + 'ms');
    });

  await check('each tick fetches one image per UNIQUE visible source',
    async () => {
      await openLive();
      await flush();
      imageLoads.length = 0;
      await tick(1);
      const sources = new Set(imageLoads.map(u => u.split('/')[2]));
      assertEqual(imageLoads.length, sources.size,
        'a source was fetched more than once in a tick: ' + imageLoads);
      assert(imageLoads.every(u => u.includes('?t=')),
        'a refreshed frame was not cache-busted');
    });

  await check('two windows on one source cost one image, not two', async () => {
    // The canvas is put into the duplicate state through the live-switch path,
    // which is how an operator would reach it.
    await openLive();
    await flush();
    const shots = windowBoxes().map(b =>
      (b.findAll(c => c._classes.has('mv-window-shot'))[0] || {}).dataset);
    const ips = shots.filter(Boolean).map(d => d && d.ip).filter(Boolean);
    imageLoads.length = 0;
    await tick(1);
    const unique = new Set(ips);
    assert(imageLoads.length <= unique.size,
      imageLoads.length + ' image requests for ' + unique.size + ' unique source(s)');
  });

  await check('the timer stops when the canvas is not on screen', async () => {
    await openLive();
    await flush();
    assert(intervals.some(i => i.live), 'no timer to stop');
    REGISTRY.get('mv_cancel').dispatch('click');
    await flush();
    assertEqual(intervals.filter(i => i.live).length, 0,
      'a refresh timer survived leaving the canvas');
  });

  await check('the timer stops when the page is hidden, and does not multiply',
    async () => {
      await openLive();
      await flush();
      const created = intervals.length;
      DOCUMENT.hidden = true;
      DOCUMENT.dispatch('visibilitychange');
      assertEqual(intervals.filter(i => i.live).length, 0,
        'the page kept refreshing while hidden');
      DOCUMENT.hidden = false;
      DOCUMENT.dispatch('visibilitychange');
      assertEqual(intervals.filter(i => i.live).length, 1,
        'it did not come back when the page became visible');
      // Re-rendering repeatedly must not add timers.
      for (let i = 0; i < 5; i += 1) await openLive();
      await flush();
      assertEqual(intervals.filter(i => i.live).length, 1,
        'repeated recalls multiplied the refresh timer');
    });

  await check('a Multiview this release cannot show does not offer Show',
    async () => {
      stateResponse = stateBody([Object.assign({}, EXISTING_VIEW, {
        selected_on_output: false, showable: false,
        not_showable_reason:
          'This is a 3840x2160 Multiview. This release shows 1920x1080 '
          + 'Multiviews only.'})]);
      await selectDecoder('192.0.2.10');
      REGISTRY.get('mv_target').value = 'multiview2x2';
      REGISTRY.get('mv_target').dispatch('change');
      await flush();
      const before = requests.filter(r =>
        r.url.startsWith('/api/multiview/show')).length;
      REGISTRY.get('mv_show').dispatch('click');
      await flush();
      assertEqual(requests.filter(r =>
        r.url.startsWith('/api/multiview/show')).length, before,
        'the page sent a Show it had been told would be refused');
      assert(/3840x2160/.test(REGISTRY.get('mv_status').textContent),
        'the operator was not told why: '
        + REGISTRY.get('mv_status').textContent);
      stateResponse = stateBody([EXISTING_VIEW]);
    });

  // ---- Phase 7C: the page remembers where the operator was ---------------
  // Only two identifiers are stored. Everything about the devices is still
  // read live when the page opens; the saved value is a bookmark, not a cache.

  const stored = () => sandbox.localStorage._store;

  await check('choosing a decoder is remembered', async () => {
    await selectDecoder('192.0.2.10');
    assertEqual(stored().get('multiview_selected_decoder'), '192.0.2.10',
      'the decoder was not remembered');
  });

  await check('choosing a Multiview is remembered too', async () => {
    stateResponse = stateBody([EXISTING_VIEW]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_target').value = 'multiview2x2';
    REGISTRY.get('mv_target').dispatch('change');
    await flush();
    assertEqual(stored().get('multiview_selected_target'), 'multiview2x2');
  });

  await check('changing decoder forgets the previous decoder\'s Multiview',
    async () => {
      // A Multiview name only means anything on the decoder that holds it.
      await selectDecoder('192.0.2.11');
      assertEqual(stored().get('multiview_selected_target'), undefined,
        'a Multiview name was carried to a different decoder');
    });

  await check('only identifiers are stored, never device state', () => {
    const keys = Array.from(stored().keys())
      .filter(k => k.startsWith('multiview_selected'));
    assert(keys.length <= 2, 'more than the selection is persisted: ' + keys);
    keys.forEach((key) => {
      const value = String(stored().get(key));
      assert(value.length < 120, key + ' looks like cached state: ' + value);
      assert(!/[{\[]/.test(value), key + ' holds a structure, not an id');
      ['password', 'token', 'secret', 'credential'].forEach(word =>
        assert(!value.toLowerCase().includes(word), key + ' holds a credential'));
    });
  });

  await check('restoring asks the devices for current state, not the store',
    () => {
      // The restore path calls loadDecoderState, which is the same live read a
      // manual selection performs. It must not populate anything from storage.
      // Bounded by code, not by a comment: comments are stripped from
      // `sourceWithoutComments`, so a comment marker finds nothing and the
      // slice silently becomes the rest of the file.
      const from = sourceWithoutComments.indexOf('async function restoreSelection()');
      const to = sourceWithoutComments.indexOf('const NOTICE_KEY');
      assert(from >= 0 && to > from, 'restoreSelection could not be isolated');
      const body = sourceWithoutComments.slice(from, to);
      assert(/loadDecoderState\(/.test(body),
        'the restore does not read live decoder state');
      assert(!/state\.decoderState\s*=/.test(body),
        'the restore writes device state from storage');
      assert(/selectableDecoder\(/.test(body),
        'the restore does not check the decoder is still usable');
    });

  await check('a remembered decoder that is offline is dropped', () => {
    // `selectableDecoder` is what the restore consults, and the decoder list
    // is the live answer. An unreachable or unsupported decoder is not one.
    const body = sourceWithoutComments.slice(
      sourceWithoutComments.indexOf('function selectableDecoder('),
      sourceWithoutComments.indexOf('async function restoreSelection()'));
    assert(/multiview_supported\s*!==\s*false/.test(body),
      'an unsupported decoder would still be restored');
    assert(/reachable\s*!==\s*false/.test(body),
      'an offline decoder would still be restored');
    assert(/rememberDecoder\(''\)/.test(sourceWithoutComments),
      'a stale decoder selection is never cleared');
  });

  await check('a deleted Multiview clears only the Multiview selection',
    async () => {
      stateResponse = stateBody([EXISTING_VIEW]);
      await selectDecoder('192.0.2.10');
      REGISTRY.get('mv_target').value = 'multiview2x2';
      REGISTRY.get('mv_target').dispatch('change');
      await flush();
      assertEqual(stored().get('multiview_selected_target'), 'multiview2x2');
      confirmAnswer = true;
      stateResponse = stateBody([]);
      REGISTRY.get('mv_delete').dispatch('click');
      await flush();
      assertEqual(stored().get('multiview_selected_target'), undefined,
        'the deleted Multiview is still remembered');
      assertEqual(stored().get('multiview_selected_decoder'), '192.0.2.10',
        'deleting a Multiview also forgot the decoder');
      stateResponse = stateBody([EXISTING_VIEW]);
    });

  // ---- Phase 7C: target vs actual ----------------------------------------

  await check('the table shows the layout target when the actual is below it',
    async () => {
      forcedPlan = (desired) => ({
        ok: true, object_name: 'multiview2x2', friendly_name: desired.name,
        layout: desired.layout, canvas: {width: 1920, height: 1088},
        requested_canvas: {width: 1920, height: 1080}, snapped: true,
        decoder: {ip: '192.0.2.10', hostname: 'dec-test-01'},
        windows: [
          {cell: 'left', width: 960, height: 544, encoder_index: 2,
           session: 'session2', scaler_format: '960x544',
           source: {ip: '192.0.2.20', hostname: 'enc-test-01'},
           ip_input: {ip_input: 'ip_input2'},
           bitrate: 200, bitrate_target: 200},
          {cell: 'right', width: 960, height: 544, encoder_index: 2,
           session: 'session2', scaler_format: '960x544',
           source: {ip: '192.0.2.24', hostname: 'new-wp-01'},
           ip_input: {ip_input: 'ip_input4'},
           bitrate: 150, bitrate_target: 200},
        ],
        bandwidth: {budget_source: 900, budget_decoder: 900,
                    targets: {equal: 200, main: 300, small: 150},
                    unique_streams: 2, window_count: 2, decoder_aggregate: 350,
                    allocations: [
                      {source_ip: '192.0.2.20', target: 200, bitrate: 200,
                       encoder1_bitrate: 700, headroom: 200, capped_by: '',
                       error: '', encoder1_target: null},
                      {source_ip: '192.0.2.24', target: 200, bitrate: 150,
                       encoder1_bitrate: 750, headroom: 150, error: '',
                       encoder1_target: null,
                       capped_by: 'Encoder 1 is at 750 Mb/s, leaving 150 of '
                                  + "the source's 900 Mb/s"},
                    ]},
        mutations: [], errors: [], conflicts: [],
        warnings: ['Encoder 2 on 192.0.2.24 is set to 150 Mb/s rather than the '
                   + '200 Mb/s target for this window: Encoder 1 is at 750 '
                   + "Mb/s, leaving 150 of the source's 900 Mb/s. Encoder 1 is "
                   + 'left alone — it carries the source\'s primary stream.'],
      });
      await selectDecoder('192.0.2.10');
      REGISTRY.get('mv_new').dispatch('click');
      await flush();
      windowBoxes()[0].dispatch('drop',
        {dataTransfer: {getData: () => '192.0.2.20'}});
      await flush();

      const plan = REGISTRY.get('mv_plan').deepText;
      assert(plan.includes('200 Mb/s'), '200 is missing: ' + plan);
      assert(plan.includes('150 Mb/s'), '150 is missing');
      assert(plan.includes('target 200 Mb/s'),
        'the window that fell short does not show its target: ' + plan);
      // And the old, meaningless explanation must be gone.
      assert(!/its window size would give it/.test(plan),
        'the old area-derived wording is still shown');
    });

  await check('the warning names the target and protects Encoder 1', () => {
    const notes = REGISTRY.get('mv_notes').deepText;
    assert(/200 Mb\/s target for this window/.test(notes),
      'the warning does not state the target: ' + notes);
    assert(/Encoder 1 is left alone/.test(notes),
      'the warning does not say Encoder 1 is preserved');
    assert(!/window size would give it/.test(notes),
      'the old wording survives in the warnings');
  });

  await check('engineering detail shows target, Encoder 1 and headroom', () => {
    const detail = REGISTRY.get('mv_engineering_body').deepText;
    assert(/layout target 200 Mb\/s/.test(detail), detail.slice(0, 200));
    assert(/Encoder 1 700 Mb\/s/.test(detail), 'Encoder 1 is not shown');
    assert(/headroom 150 Mb\/s/.test(detail), 'headroom is not shown');
    assert(/Encoder 1 is never reduced/.test(detail),
      'the policy is not stated anywhere');
    forcedPlan = null;
  });

  await check('Delete is kept well away from Save in the action bar', () => {
    const order = PAGE_HTML.slice(PAGE_HTML.indexOf('id="mv_actions"'));
    assert(order.indexOf('id="mv_save"') < order.indexOf('id="mv_delete"'),
      'Delete comes before Save');
    assert(/\.mv-actions \.btn\.danger\{[^}]*margin-left/.test(style),
      'Delete is not separated from the other actions');
    assert(/\.mv-actions\{[^}]*position:sticky/.test(style),
      'the action bar is not pinned, so a long plan can push it out of sight');
  });

  // =======================================================================
  // Phase 8B: the workflow
  // =======================================================================

  // ---- §1 what the decoder is showing outranks what we remembered --------
  await check('choosing a decoder selects the Multiview it is showing', async () => {
    stateResponse = stateBody([Object.assign({}, EXISTING_VIEW,
                                             {selected_on_output: true})]);
    await selectDecoder('192.0.2.10');
    assert(REGISTRY.get('mv_target').value === 'multiview2x2',
           'the active Multiview was not selected: '
           + REGISTRY.get('mv_target').value);
    assert(!hidden('mv_workspace'), 'the canvas was not populated');
  });

  await check('a remembered preset does not outrank the live one', async () => {
    sandbox.localStorage.setItem('multiview_selected_target', 'multiviewOther');
    stateResponse = stateBody([
      Object.assign({}, EXISTING_VIEW, {selected_on_output: true}),
      Object.assign({}, EXISTING_VIEW, {name: 'multiviewOther',
                                        selected_on_output: false}),
    ]);
    await selectDecoder('192.0.2.10');
    assert(REGISTRY.get('mv_target').value === 'multiview2x2',
           'the bookmark won over the live composition');
  });

  await check('with nothing active, the page waits for the operator', async () => {
    // Priority 3: no live composition to show and no bookmark being restored,
    // so nothing is chosen on the operator's behalf.
    stateResponse = stateBody([EXISTING_VIEW]);
    await selectDecoder('192.0.2.10');
    assert(REGISTRY.get('mv_target').value === '',
           'a preset was selected when none was active: '
           + REGISTRY.get('mv_target').value);
    assert(hidden('mv_workspace'),
           'the editor opened without the operator choosing anything');
  });

  // ---- §4 Save follows the dirty state ----------------------------------
  await check('Save is disabled while nothing has been changed', async () => {
    stateResponse = stateBody([EXISTING_VIEW]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_target').value = 'multiview2x2';
    REGISTRY.get('mv_target').dispatch('change');
    await flush();
    assert(REGISTRY.get('mv_save').disabled,
           'Save is offered on a preset nobody has edited');
    assert(/no changes/i.test(REGISTRY.get('mv_save_reason').textContent),
           'the reason is not stated: '
           + REGISTRY.get('mv_save_reason').textContent);
  });

  await check('the heading says the preset is saved', () => {
    assert(/Editing/.test(REGISTRY.get('mv_edit_heading').textContent),
           REGISTRY.get('mv_edit_heading').textContent);
    assert(REGISTRY.get('mv_edit_state').textContent === 'Saved',
           REGISTRY.get('mv_edit_state').textContent);
  });

  await check('changing the name enables Save', async () => {
    REGISTRY.get('mv_name').value = 'Renamed';
    REGISTRY.get('mv_name').dispatch('input');
    await flush();
    assert(!REGISTRY.get('mv_save').disabled, 'Save stayed disabled after a rename');
    assert(/Unsaved/.test(REGISTRY.get('mv_edit_state').textContent),
           REGISTRY.get('mv_edit_state').textContent);
  });

  await check('a live reading does not enable Save', async () => {
    stateResponse = stateBody([EXISTING_VIEW]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_target').value = 'multiview2x2';
    REGISTRY.get('mv_target').dispatch('change');
    await flush();
    assert(REGISTRY.get('mv_save').disabled, 'Save was enabled before the reread');
    // The same preset, read again with different packet counts and health.
    const moved = JSON.parse(JSON.stringify(EXISTING_VIEW));
    moved.subframes[0].packets = 99999;
    moved.subframes[0].health = 'stalled';
    moved.subframes[0].input_active = false;
    stateResponse = stateBody([moved]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_target').value = 'multiview2x2';
    REGISTRY.get('mv_target').dispatch('change');
    await flush();
    assert(REGISTRY.get('mv_save').disabled,
           'a packet counter and a health change lit up Save');
  });

  await check('a failed save keeps the edit and leaves Save enabled', async () => {
    stateResponse = stateBody([EXISTING_VIEW]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_target').value = 'multiview2x2';
    REGISTRY.get('mv_target').dispatch('change');
    await flush();
    REGISTRY.get('mv_name').value = 'Attempted';
    REGISTRY.get('mv_name').dispatch('input');
    await flush();
    const before = applyResponse;
    applyResponse = {ok: false, status: 'FAILED — ROLLED BACK',
                     error: 'the device refused'};
    REGISTRY.get('mv_save').dispatch('click');
    await flush();
    applyResponse = before;
    assert(REGISTRY.get('mv_name').value === 'Attempted',
           'a failed save discarded what the operator typed: '
           + REGISTRY.get('mv_name').value);
    assert(!REGISTRY.get('mv_save').disabled,
           'a failed save left Save disabled, as though there were nothing to save');
  });

  // ---- §2/§3 creating is not editing ------------------------------------
  await check('creating says so, and hides the preset picker', async () => {
    REGISTRY.get('mv_new').dispatch('click');
    await flush();
    assert(REGISTRY.get('mv_edit_heading').textContent === 'Create New Multiview',
           REGISTRY.get('mv_edit_heading').textContent);
    assert(hidden('mv_field_target'),
           'the preset picker is still shown while creating something new');
    assert(!hidden('mv_field_cancel_create'), 'no way to back out of creation');
    assert(/Not saved/.test(REGISTRY.get('mv_edit_state').textContent),
           REGISTRY.get('mv_edit_state').textContent);
  });

  await check('cancelling creation returns to the picker', async () => {
    REGISTRY.get('mv_cancel_create').dispatch('click');
    await flush();
    assert(!hidden('mv_field_target'), 'the preset picker did not come back');
  });

  // ---- §5/§24 the Save confirmation, scoped to the version ---------------
  const SUPPRESS_KEY = 'multiview_save_confirm_suppressed_version';

  async function saveWithConfirm(answer) {
    confirmAnswer = answer;
    confirmCalls = 0;
    stateResponse = stateBody([EXISTING_VIEW]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_target').value = 'multiview2x2';
    REGISTRY.get('mv_target').dispatch('change');
    await flush();
    REGISTRY.get('mv_name').value = 'Changed' + Math.random().toString(16).slice(2, 6);
    REGISTRY.get('mv_name').dispatch('input');
    await flush();
    REGISTRY.get('mv_save').dispatch('click');
    await flush();
    return confirmCalls;
  }

  await check('the first Save asks, and offers to stop asking', async () => {
    sandbox.localStorage.removeItem(SUPPRESS_KEY);
    const asked = await saveWithConfirm({ok: true, suppress: false});
    assert(asked === 1, 'the Save confirmation did not appear');
    assert(lastConfirmOptions.suppressLabel,
           'no "do not ask again" was offered');
    assert(/version/i.test(lastConfirmOptions.suppressLabel),
           'the opt-out does not say it is for this version: '
           + lastConfirmOptions.suppressLabel);
  });

  await check('an ordinary acknowledgement does not suppress anything',
              async () => {
    assert(sandbox.localStorage.getItem(SUPPRESS_KEY) === null,
           'pressing Save once stopped the warning for ever');
    const asked = await saveWithConfirm({ok: true, suppress: false});
    assert(asked === 1, 'the second Save was not confirmed');
  });

  await check('ticking the box stops it, and records the version', async () => {
    await saveWithConfirm({ok: true, suppress: true});
    assert(sandbox.localStorage.getItem(SUPPRESS_KEY) === 'V1.0.7',
           'the suppression did not record the version: '
           + sandbox.localStorage.getItem(SUPPRESS_KEY));
    const asked = await saveWithConfirm({ok: true, suppress: false});
    assert(asked === 0, 'the suppressed confirmation came back');
  });

  await check('a new application version asks once more', async () => {
    // Nothing is cleared by hand: the stored version simply stops matching.
    sandbox.localStorage.setItem(SUPPRESS_KEY, 'V1.0.6');
    const asked = await saveWithConfirm({ok: true, suppress: false});
    assert(asked === 1,
           'a version the operator never agreed to skipped the warning');
  });

  await check('suppressing again stores the new version', async () => {
    sandbox.localStorage.setItem(SUPPRESS_KEY, 'V1.0.6');
    await saveWithConfirm({ok: true, suppress: true});
    assert(sandbox.localStorage.getItem(SUPPRESS_KEY) === 'V1.0.7',
           sandbox.localStorage.getItem(SUPPRESS_KEY));
  });

  await check('unrelated warning preferences are left alone', async () => {
    sandbox.localStorage.setItem('matrix_multiview_exit_suppressed', 'true');
    sandbox.localStorage.setItem('multiview_notice_acknowledged_version', 'V1.0.7');
    sandbox.localStorage.setItem(SUPPRESS_KEY, 'V1.0.6');
    await saveWithConfirm({ok: true, suppress: true});
    assert(sandbox.localStorage.getItem('matrix_multiview_exit_suppressed') === 'true',
           'the A/V Matrix preference was reset');
    assert(sandbox.localStorage.getItem('multiview_notice_acknowledged_version') === 'V1.0.7',
           'the operator notice preference was reset');
  });

  // ---- §6 copy to another decoder ---------------------------------------
  await check('copy offers the other decoders and says it shows nothing',
              async () => {
    sandbox.localStorage.setItem(SUPPRESS_KEY, 'V1.0.7');
    stateResponse = stateBody([EXISTING_VIEW]);
    await selectDecoder('192.0.2.10');
    REGISTRY.get('mv_target').value = 'multiview2x2';
    REGISTRY.get('mv_target').dispatch('change');
    await flush();
    REGISTRY.get('mv_copy').dispatch('click');
    await flush();
    assert(!hidden('mv_copy_dialog'), 'the copy panel did not open');
    const options = REGISTRY.get('mv_copy_target').children.map(o => o.value);
    assert(!options.includes('192.0.2.10'),
           'the decoder it is already on was offered as a target');
    assert(options.length > 0, 'no target decoder was offered');
    const note = PAGE_HTML.slice(PAGE_HTML.indexOf('id="mv_copy_dialog"'));
    assert(/changes no display/.test(note),
           'the panel does not say that copying shows nothing');
  });

  await check('copying posts the definition and reports it as not shown',
              async () => {
    requests.length = 0;
    REGISTRY.get('mv_copy_go').dispatch('click');
    await flush();
    const call = requests.find(r => r.url === '/api/multiview/copy');
    assert(call, 'no copy request was made');
    assert(call.payload.source_decoder === '192.0.2.10', call.payload);
    assert(call.payload.name === 'multiview2x2', call.payload);
    assert(/unchanged/i.test(REGISTRY.get('mv_copy_status').textContent),
           REGISTRY.get('mv_copy_status').textContent);
  });

  await check('a name already in use asks instead of overwriting', async () => {
    copyResponse = {ok: false, status: 'NAME IN USE',
                    error: 'dec-test-03 already has a Multiview called that.',
                    suggested_name: 'multiview2x22', existing: []};
    confirmAnswer = {ok: false};
    confirmCalls = 0;
    requests.length = 0;
    REGISTRY.get('mv_copy_go').dispatch('click');
    await flush();
    assert(confirmCalls === 1, 'the operator was not asked');
    assert(/already has/.test(lastConfirmOptions.message), lastConfirmOptions.message);
    const copies = requests.filter(r => r.url === '/api/multiview/copy');
    assert(copies.length === 1,
           'declining still sent a second copy: ' + copies.length);
    copyResponse = {ok: true, status: 'VERIFIED', shown: false, warnings: [],
                    target: {decoder: '192.0.2.12', name: 'multiview2x2'}};
    REGISTRY.get('mv_copy_cancel').dispatch('click');
  });

  // ---- §9/§10 groups -----------------------------------------------------
  await check('the groups panel lists the saved groups', async () => {
    REGISTRY.get('mv_groups').dispatch('click');
    await flush();
    assert(!hidden('mv_groups_dialog'), 'the groups panel did not open');
    const options = REGISTRY.get('mv_group_select').children.map(o => o.value);
    assert(options.includes('g1'), 'the saved group was not listed');
  });

  await check('saving a group posts its members and touches no device',
              async () => {
    REGISTRY.get('mv_group_select').value = 'g1';
    REGISTRY.get('mv_group_select').dispatch('change');
    await flush();
    requests.length = 0;
    REGISTRY.get('mv_group_name').value = 'Main Bar';
    REGISTRY.get('mv_group_save').dispatch('click');
    await flush();
    const call = requests.find(r => r.url === '/api/multiview/groups/save');
    assert(call, 'no group save was sent');
    assert(call.payload.name === 'Main Bar', call.payload);
    assert(Array.isArray(call.payload.members), call.payload);
    assert(!requests.some(r => /apply|show|switch/.test(r.url)),
           'saving a group reached a device');
  });

  await check('Save to group is not Show on group', async () => {
    requests.length = 0;
    REGISTRY.get('mv_group_copy').dispatch('click');
    await flush();
    assert(requests.some(r => r.url === '/api/multiview/groups/copy'),
           'no group copy was sent');
    assert(!requests.some(r => r.url === '/api/multiview/groups/show'),
           'saving to the group also showed it');
  });

  await check('Show on group asks first, and names what changes', async () => {
    confirmAnswer = {ok: false};
    confirmCalls = 0;
    requests.length = 0;
    REGISTRY.get('mv_group_show').dispatch('click');
    await flush();
    assert(confirmCalls === 1, 'Show on group did not ask');
    assert(/pictures change/.test(lastConfirmOptions.message),
           lastConfirmOptions.message);
    assert(!requests.some(r => r.url === '/api/multiview/groups/show'),
           'cancelling still showed it on the group');
  });

  await check('a refused group operation shows the conflict it was refused for',
              async () => {
    groupShowResponse = {
      ok: false, status: 'REFUSED', writes: 0,
      error: 'refused',
      conflicts: [{source: 'enc-test-01',
                   detail: 'enc-test-01 can only send one picture size at a '
                     + 'time, and this group asks it for 1280x720 for '
                     + 'dec-test-01 main and 640x360 for dec-test-03 top left.'}],
      problems: [],
    };
    confirmAnswer = {ok: true};
    REGISTRY.get('mv_group_show').dispatch('click');
    await flush();
    const status = REGISTRY.get('mv_groups_status').textContent;
    assert(/one picture size/.test(status), status);
    assert(/enc-test-01/.test(status), status);
    assert(/dec-test-03/.test(status), status);
    groupShowResponse = {ok: true, status: 'VERIFIED', shown: [],
                         shared_sources: [],
                         group: {id: 'g1', name: 'Sports Bar'},
                         message: 'Every decoder in the group is showing it.'};
  });

  // ---- §16 group context -------------------------------------------------
  await check('showing on a group enters a visible group context', async () => {
    confirmAnswer = {ok: true};
    REGISTRY.get('mv_group_show').dispatch('click');
    await flush();
    assert(!hidden('mv_group_context'),
           'nothing says that changes now move several displays');
    assert(/decoders in/.test(REGISTRY.get('mv_group_context_detail').textContent),
           REGISTRY.get('mv_group_context_detail').textContent);
  });

  await check('in group context a source change goes to the whole group',
              async () => {
    REGISTRY.get('mv_groups_close').dispatch('click');
    // A live change is only live on the Multiview that is on the display.
    stateResponse = stateBody([LIVE_VIEW]);
    await selectDecoder('192.0.2.10');
    await flush();
    requests.length = 0;
    windowBoxes()[0].dispatch('drop',
      {dataTransfer: {getData: () => '192.0.2.24'}});
    await flush();
    const call = requests.find(r => r.url === '/api/multiview/groups/show');
    assert(call, 'the change was not applied to the group: '
           + requests.map(r => r.url).join(', '));
    assert(call.payload.cell === 'top_left', call.payload);
    assert(call.payload.source === '192.0.2.24', call.payload);
    assert(!requests.some(r => r.url === '/api/multiview/switch'),
           'the change was also applied to this decoder alone');
  });

  await check('leaving group context makes changes single-decoder again',
              async () => {
    REGISTRY.get('mv_group_leave').dispatch('click');
    await flush();
    assert(REGISTRY.get('mv_target').value === 'multiview2x2',
           'the live Multiview is no longer selected, so this proves nothing');
    assert(hidden('mv_group_context'), 'the group context banner stayed');
    requests.length = 0;
    windowBoxes()[0].dispatch('drop',
      {dataTransfer: {getData: () => '192.0.2.24'}});
    await flush();
    assert(requests.some(r => r.url === '/api/multiview/switch'),
           'the single-decoder path was not used after leaving group context');
    assert(!requests.some(r => r.url === '/api/multiview/groups/show'),
           'a single-decoder change still went to the group');
  });

  console.log(failures.length
    ? `\nmultiview UI test: ${failures.length} failure(s)`
    : '\nmultiview UI test: all checks passed');
  process.exit(failures.length ? 1 : 0);
})();

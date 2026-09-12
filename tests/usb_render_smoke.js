/*
 * Frontend smoke test for the USB Matrix render path.
 *
 * A syntax check cannot catch `ReferenceError: esc is not defined` -- that only
 * appears when the code actually runs. This loads ui/matrix/usb.js into a
 * minimal DOM stub and invokes render() with representative live API JSON, so an
 * undefined helper, a renamed function, or a missing global fails the build.
 *
 * Run: node tests/usb_render_smoke.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = path.join(__dirname, '..', 'ui', 'matrix', 'usb.js');
const failures = [];
const pendingChecks = [];
let checkQueue = Promise.resolve();
// Scenarios that render and click share one DOM and one request log, so they run
// strictly one after another rather than all starting at declaration time.
function checkSeq(name, fn) {
  checkQueue = checkQueue.then(async () => {
    try { await fn(); console.log(`  ok   ${name}`); }
    catch (err) { failures.push(`${name}: ${err && err.message}`); console.log(`  FAIL ${name}: ${err && err.message}`); }
  });
  pendingChecks.push(checkQueue);
}
function check(name, fn) {
  try {
    const result = fn();
    if (result && typeof result.then === 'function') {
      pendingChecks.push(result.then(
        () => console.log(`  ok   ${name}`),
        err => { failures.push(`${name}: ${err && err.message}`); console.log(`  FAIL ${name}: ${err && err.message}`); }));
      return;
    }
    console.log(`  ok   ${name}`);
  } catch (err) {
    failures.push(`${name}: ${err && err.message}`);
    console.log(`  FAIL ${name}: ${err && err.message}`);
  }
}

// ---- minimal DOM ----------------------------------------------------------
function makeElement(tag) {
  const el = {
    tagName: String(tag || 'div').toUpperCase(),
    children: [], attributes: {}, dataset: {}, style: {}, classList: null,
    _classes: new Set(), _html: '', textContent: '',
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; },
    removeAttribute(k) { delete this.attributes[k]; },
    appendChild(child) { this.children.push(child); return child; },
    _listeners: null,
    addEventListener(type, fn) {
      (this._listeners || (this._listeners = {}))[type] = (this._listeners[type] || []).concat(fn);
    },
    removeEventListener() {},
    // Dispatch synchronously and hand back the handler's promise, so a test can
    // await an async click handler to completion. The event bubbles to ancestors,
    // as a real one does: the Matrix binds one delegated handler on the table
    // rather than a handler per cell, and a stub that did not bubble would let a
    // per-cell regression pass.
    click() {
      let stopped = false;
      const event = {
        target: this,
        preventDefault() {},
        stopPropagation() { stopped = true; },
      };
      const results = [];
      let node = this;
      while (node) {
        const fns = (node._listeners && node._listeners.click) || [];
        fns.forEach(fn => results.push(fn(event)));
        if (stopped) break;
        node = node._parent || null;
      }
      return Promise.all(results);
    },
    // Ancestor lookup, so a delegated handler can find the cell that was hit.
    closest(sel) {
      const selector = String(sel);
      // Strip the :not(...) groups first, or their classes are read as required.
      const excluded = (selector.match(/:not\(\.([A-Za-z0-9_-]+)\)/g) || [])
        .map(part => part.slice(6, -1));
      const bare = selector.replace(/:not\([^)]*\)/g, '');
      const tag = (bare.match(/^[A-Za-z]+/) || [''])[0].toLowerCase();
      const required = (bare.match(/\.([A-Za-z0-9_-]+)/g) || []).map(c => c.slice(1));
      const matches = node => {
        if (tag && node.tagName && node.tagName.toLowerCase() !== tag) return false;
        const own = String(node.className || '').split(/\s+/).filter(Boolean);
        return required.every(c => own.includes(c)) && excluded.every(c => !own.includes(c));
      };
      let node = this;
      while (node) {
        if (matches(node)) return node;
        node = node._parent || null;
      }
      return null;
    },
    contains(node) {
      while (node) {
        if (node === this) return true;
        node = node._parent || null;
      }
      return false;
    },
    // Class selectors resolve to a stable stub, so code that renders markup and
    // then wires up its buttons finds the same element a test can drive.
    querySelector(sel) {
      const cache = this._q || (this._q = new Map());
      if (!cache.has(sel)) cache.set(sel, makeElement('div'));
      return cache.get(sel);
    },
    // Parse the cells this element last rendered so they can be clicked and
    // updated. Writes are serialised back into this element's HTML, so the
    // in-place refresh path usb.js uses is genuinely exercised.
    querySelectorAll(sel) {
      if (!/td\.cell/.test(String(sel))) return [];
      const wantEnabled = /:not\(\.disabled\)/.test(String(sel));
      const owner = this;
      if (owner._cellsFor !== owner._html) {
        const parts = [];
        const cells = [];
        const re = /<td\s([^>]*?)>([\s\S]*?)<\/td>/g;
        let last = 0, m;
        while ((m = re.exec(owner._html)) !== null) {
          parts.push(owner._html.slice(last, m.index));
          const attrs = new Map();
          for (const a of m[1].matchAll(/([\w-]+)="([^"]*)"/g)) attrs.set(a[1], a[2]);
          const slot = {attrs, inner: m[2]};
          parts.push(slot);
          cells.push(slot);
          last = re.lastIndex;
        }
        parts.push(owner._html.slice(last));
        const serialise = () => {
          owner._html = parts.map(part => typeof part === 'string' ? part
            : `<td ${[...part.attrs].map(([k, v]) => `${k}="${v}"`).join(' ')}>${part.inner}</td>`).join('');
          owner._cellsFor = owner._html;      // keep this parse; do not re-parse
        };
        owner._cellsFor = owner._html;
        owner._cells = cells.map(slot => {
          const el = makeElement('td');
          el.setAttribute = (k, v) => { slot.attrs.set(k, String(v)); serialise(); };
          el.getAttribute = k => (slot.attrs.has(k) ? slot.attrs.get(k) : null);
          el.removeAttribute = k => { slot.attrs.delete(k); serialise(); };
          Object.defineProperty(el, 'innerHTML', {
            get: () => slot.inner,
            set: v => { slot.inner = String(v); serialise(); },
          });
          Object.defineProperty(el, 'className', {
            get: () => slot.attrs.get('class') || '',
            set: v => { slot.attrs.set('class', String(v)); serialise(); },
          });
          el.classList = {
            add: (...c) => { el.className = [...new Set(el.className.split(/\s+/).filter(Boolean).concat(c))].join(' '); },
            remove: (...c) => { el.className = el.className.split(/\s+/).filter(x => x && !c.includes(x)).join(' '); },
            contains: c => el.className.split(/\s+/).includes(c),
            toggle: (c, on) => (on ? el.classList.add(c) : el.classList.remove(c)),
          };
          // Parented to the table, so a delegated handler on the table receives
          // this cell's click and can resolve it with closest()/contains().
          el._parent = owner;
          return el;
        }).filter(el => el.classList.contains('cell'));
      }
      return wantEnabled ? owner._cells.filter(el => !el.classList.contains('disabled')) : owner._cells;
    },
    getBoundingClientRect() { return {left: 0, top: 0, right: 40, bottom: 20, width: 40, height: 20}; },
    focus() {},
    // toast() schedules el.remove(); without it the harness dies on a timer.
    remove() {
      const kids = this.parentNode && this.parentNode.children;
      if (kids) { const i = kids.indexOf(this); if (i >= 0) kids.splice(i, 1); }
    },
  };
  // configurable: cell elements handed out by querySelectorAll redefine these
  // so that writes are serialised back into the owning table's HTML.
  Object.defineProperty(el, 'innerHTML', {
    configurable: true,
    get() { return this._html; },
    set(v) { this._html = String(v); },
  });
  Object.defineProperty(el, 'className', {
    configurable: true,
    get() { return [...this._classes].join(' '); },
    set(v) { this._classes = new Set(String(v).split(/\s+/).filter(Boolean)); },
  });
  el.classList = {
    add: (...c) => c.forEach(x => el._classes.add(x)),
    remove: (...c) => c.forEach(x => el._classes.delete(x)),
    toggle: (c, on) => (on === undefined ? (el._classes.has(c) ? el._classes.delete(c) : el._classes.add(c))
                                         : (on ? el._classes.add(c) : el._classes.delete(c))),
    contains: c => el._classes.has(c),
  };
  return el;
}

function buildDom() {
  const registry = new Map();
  ['usbMatrix', 'lexTbl', 'rexTbl', 'usb_loading_overlay', 'sticky_switch',
   'sticky_headers_toggle', 'sticky_headers_label', 'refreshBtn'].forEach(id => {
    const el = makeElement(id.includes('Tbl') || id === 'usbMatrix' ? 'table' : 'div');
    el.setAttribute('id', id);
    registry.set(id, el);
  });
  const body = makeElement('body');
  const document = {
    body,
    documentElement: makeElement('html'),
    // Unknown ids resolve to a throwaway element: module-level init code touches
    // several optional controls, and the point of this harness is to surface
    // undefined *helpers*, not to model every widget on the page.
    getElementById(id) {
      if (!registry.has(id)) registry.set(id, makeElement('div'));
      return registry.get(id);
    },
    querySelector(sel) {
      const id = String(sel).replace(/^#/, '');
      if (!/^[\w-]+$/.test(id)) return null;
      return document.getElementById(id);
    },
    querySelectorAll: () => [],
    createElement: makeElement,
    addEventListener() {},
    removeEventListener() {},
    get activeElement() { return null; },
  };
  return {document, registry};
}

// ---- representative live API payload --------------------------------------
const E4521 = {kind: 'integrated', usb_key: '192.168.100.141', usb_mac: 'B8:98:B0:07:85:ED', ip: '192.168.100.141',
               mac: 'B8:98:B0:07:85:ED', host: 'hw-omni-e4521-00002', usb_ip: '192.168.100.246',
               revision: '1.9.4', protocol: 'IP', type: 'LEX', host_port: 'FollowVideo', filter: 'Allow_All',
               classification: 'INTEGRATED', online: true};
const D4511 = {kind: 'integrated', usb_key: '192.168.100.151', usb_mac: 'B8:98:B0:07:85:87', ip: '192.168.100.151',
               mac: 'B8:98:B0:07:85:87', host: 'hw-omni-d4511-08586', usb_ip: '192.168.100.250',
               revision: '1.9.4', protocol: 'IP', type: 'REX', host_port: '', filter: 'Allow_All',
               classification: 'INTEGRATED', online: true};
const OMNI311 = {kind: 'standalone', usb_key: '00:1B:13:04:E9:6E', usb_mac: '00:1B:13:04:E9:6E', ip: '192.168.100.128',
                 mac: '00:1B:13:04:E9:6E', model: 'AT-OMNI-311', host: 'AT-OMNI-311',
                 usb_ip: '192.168.100.128', revision: '1.9.4', protocol: 'IP', type: 'LEX',
                 classification: 'STANDALONE', online: true, pairing_eligible: true,
                 pairing_state_fresh: true, paired_macs: ['00:1B:13:04:6A:EA'],
                 host_port: '', host_port_configurable: false, filter: '', filter_configurable: false};
const OMNI324 = {kind: 'standalone', usb_key: '00:1B:13:04:6A:EA', usb_mac: '00:1B:13:04:6A:EA', ip: '192.168.100.127',
                 mac: '00:1B:13:04:6A:EA', model: 'AT-OMNI-324', host: 'AT-OMNI-324',
                 usb_ip: '192.168.100.127', revision: '1.9.4', protocol: 'IP', type: 'REX',
                 classification: 'STANDALONE', online: true, pairing_eligible: true,
                 pairing_state_fresh: true, paired_macs: ['00:1B:13:04:E9:6E'],
                 host_port: '', host_port_configurable: false, filter: '', filter_configurable: false};

const cap = (state, enabled, path, label, detail, extra) => ({
  state, enabled, control_path: path, label, detail, note: detail,
  mixed: false, data_plane_verified: enabled, control_plane_verified: enabled, ...(extra || {})});
const ICRON = cap('SUPPORTED_USB_ICRON', true, 'usb_icron', 'Available', 'Route through the OmniStream USB pairing.');
const UDP = cap('SUPPORTED_STANDALONE_UDP', true, 'standalone_udp', 'Available', 'Route between standalone USB extenders.',
                {data_plane_verified: false});
// Routable so the operator can perform the physical USB test, but never
// presented as data-plane verified.
const MIXED = cap('SUPPORTED_MIXED_EXPERIMENTAL', true, 'standalone_udp',
                  'Mixed USB route — data transport not yet validated',
                  'Pairing control has been bench verified. Enable this route to perform USB peripheral validation.',
                  {mixed: true, data_plane_verified: false});
const BLOCKED = cap('UNSUPPORTED_MIXED', false, '', 'Not routable', 'This combination has not been validated.');

const inventoryRow = (u, extra) => ({
  kind: u.kind, usb_key: u.usb_key, usb_mac: u.usb_mac, mac: u.usb_mac,
  device_ip: u.ip, ip: u.ip, usb_ip: u.usb_ip, name: u.host, host: u.host,
  model: u.model || '', type: u.type, protocol: u.protocol || 'IP',
  firmware: u.revision || '1.9.4', revision: u.revision || '1.9.4',
  classification: u.classification, online: u.online,
  paired_macs: u.paired_macs || [], pairing_state_fresh: !!u.pairing_state_fresh,
  link_state: 'UNKNOWN', link_peer_macs: [], link_state_fresh: false, link_label: 'Unknown',
  ...extra,
});
const INTEGRATED_CAPS = {host_port: 'FollowVideo', host_port_configurable: true,
                         filter: 'Allow_All', filter_configurable: true, type_configurable: true};
const STANDALONE_CAPS = {host_port: 'Fixed / N/A', host_port_configurable: false,
                         filter: 'Not established', filter_configurable: false, type_configurable: false};

const STATE = {
  ok: true,
  inventory_lex: [inventoryRow(E4521, INTEGRATED_CAPS),
                  inventoryRow(OMNI311, {...STANDALONE_CAPS, link_state: 'LINKED',
                                         link_state_fresh: true, link_label: 'Linked'})],
  inventory_rex: [inventoryRow(D4511, {...INTEGRATED_CAPS, host_port: '', host_port_configurable: false}),
                  inventoryRow(OMNI324, STANDALONE_CAPS)],
  lex: [E4521], rex: [D4511],
  matrix_lex: [E4521, OMNI311], matrix_rex: [D4511, OMNI324],
  capabilities: {
    '192.168.100.151': {'192.168.100.141': ICRON, '00:1B:13:04:E9:6E': MIXED},
    '00:1B:13:04:6A:EA': {'192.168.100.141': MIXED, '00:1B:13:04:E9:6E': UDP},
  },
  standalone_routes: {'00:1B:13:04:6A:EA': {active: '00:1B:13:04:E9:6E', available: [], fresh: true, pairing_age: 3}},
  standalone_lex: [OMNI311], standalone_rex: [OMNI324],
  pairings: {'192.168.100.151': {active: '192.168.100.141', available: []}},
  pairings_rex_side: {}, pairings_lex_side: {}, pairing_conflicts: {},
};

// ---- load and exercise ----------------------------------------------------
const {document, registry} = buildDom();
const sandbox = {
  document,
  window: {innerWidth: 1440, innerHeight: 900, addEventListener() {}, removeEventListener() {}},
  localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
  console,
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: fn => setTimeout(fn, 0),
  fetch: async (url, options) => {
    sandbox.__requests.push({url, body: options && options.body ? JSON.parse(options.body) : null});
    const reply = sandbox.__reply || {ok: true, status: 'VERIFIED_SUCCESS'};
    const routing = String(url).includes('/api/usb_route/');
    // A test can hold a route request open to observe the in-flight rendering.
    if (routing && sandbox.__hold) await sandbox.__hold;
    // A state read can be held too, so a response can be made to arrive out of
    // order on purpose -- which is the whole point of the ordering guards.
    if (!routing && sandbox.__stateHold) await sandbox.__stateHold;
    const payload = routing ? reply : ((!routing && sandbox.__stateFactory) ? sandbox.__stateFactory() : STATE);
    return {ok: true, status: 200, json: async () => payload, text: async () => JSON.stringify(payload)};
  },
  Symbol, Promise, Date, Math, JSON, Number, String, Boolean, Array, Object, Set, Map, Error,
};
sandbox.__requests = [];
sandbox.globalThis = sandbox;
sandbox.window.document = document;

const code = fs.readFileSync(SOURCE, 'utf8');
const TOAST = path.join(__dirname, '..', 'ui', 'toast.js');
const context = vm.createContext(sandbox);

check('the shared toast module evaluates and defines toast()', () => {
  vm.runInContext(fs.readFileSync(TOAST, 'utf8'), context, {filename: 'toast.js'});
  // In a browser `window` IS the global object, so `window.toast = ...` defines
  // the bare name usb.js calls. This sandbox keeps them as two objects, so the
  // module's own export has to be mirrored across -- a property of the harness,
  // not of the module.
  if (typeof sandbox.window.toast !== 'function') throw new Error('toast() was not defined');
  sandbox.toast = sandbox.window.toast;
  sandbox.toastError = sandbox.window.toastError;
  sandbox.toastOk = sandbox.window.toastOk;
});

check('usb.js evaluates without throwing', () => {
  vm.runInContext(code, context, {filename: 'usb.js'});
});

check('render() runs with a mixed integrated/standalone payload', () => {
  // This is the case that produced "ReferenceError: esc is not defined".
  vm.runInContext('render(__STATE__)', Object.assign(context, {__STATE__: STATE}), {filename: 'render'});
});

check('matrix grid rendered both families', () => {
  const html = registry.get('usbMatrix').innerHTML;
  if (!html) throw new Error('matrix table is empty');
  for (const needle of ['192.168.100.141', '192.168.100.151', '00:1B:13:04:E9:6E', '00:1B:13:04:6A:EA']) {
    if (!html.includes(needle)) throw new Error(`axis entry ${needle} missing from grid`);
  }
});

check('cells carry concise tooltip data and no native title', () => {
  const html = registry.get('usbMatrix').innerHTML;
  if (!html.includes('data-tip=')) throw new Error('data-tip missing');
  if (/<td class="cell[^>]*\stitle=/.test(html)) throw new Error('native title attribute present on a cell');
  if (!html.includes('Mixed USB route')) throw new Error('mixed-cell label missing');
});

check('no backend enum names appear in tooltip text', () => {
  const html = registry.get('usbMatrix').innerHTML;
  const tips = [...html.matchAll(/data-tip(?:-detail)?="([^"]*)"/g)].map(m => m[1]);
  for (const t of tips) {
    if (/CONTROL_ONLY_VERIFIED|SUPPORTED_USB_ICRON|SUPPORTED_STANDALONE_UDP|UNSUPPORTED_MIXED|SUPPORTED_MIXED_EXPERIMENTAL/.test(t)) {
      throw new Error(`enum leaked into user-facing text: ${t}`);
    }
  }
  if (!tips.length) throw new Error('no tooltip text produced');
});

check('experimental mixed cell is routable and marked as such', () => {
  const html = registry.get('usbMatrix').innerHTML;
  const cell = html.match(/<td[^>]*data-lex="192\.168\.100\.141"[^>]*data-rex="00:1B:13:04:6A:EA"[^>]*>/)
            || html.match(/<td[^>]*data-rex="00:1B:13:04:6A:EA"[^>]*data-lex="192\.168\.100\.141"[^>]*>/);
  if (!cell) throw new Error('mixed cell not rendered');
  if (/class="cell[^"]*disabled/.test(cell[0])) throw new Error('mixed cell must be clickable so it can be tested');
  if (!/data-experimental="1"/.test(cell[0])) throw new Error('mixed cell must be marked experimental');
  if (!/data-tip-detail="[^"]*not yet validated/.test(cell[0])) {
    throw new Error('mixed cell must warn that data transport is unvalidated');
  }
});

check('a genuinely unsupported cell stays disabled', () => {
  // Same render path, with a capability the server refuses.
  const blocked = JSON.parse(JSON.stringify(STATE));
  blocked.capabilities['00:1B:13:04:6A:EA']['192.168.100.141'] = BLOCKED;
  vm.runInContext('render(__BLOCKED__)', Object.assign(context, {__BLOCKED__: blocked}), {filename: 'render'});
  const html = registry.get('usbMatrix').innerHTML;
  if (!/class="cell[^"]*disabled/.test(html)) throw new Error('unsupported cell must not be clickable');
  vm.runInContext('render(__STATE__)', Object.assign(context, {__STATE__: STATE}), {filename: 'render'});
});

check('no mixed-route confirmation step exists', () => {
  // Clicking a supported route is the operator's intent; the Matrix acts on it.
  // The route transaction still verifies and reports errors normally.
  for (const gone of ['usbConfirmMixedRoute', 'usbMixedAcknowledged', 'Mixed USB Route',
                      'Data transport not yet validated', 'Create Route']) {
    if (code.includes(gone)) throw new Error(`the confirmation survives: ${gone}`);
  }
});

check('no native dialog is used in the route path', () => {
  const block = code.slice(code.indexOf("if(controlPath === 'standalone_udp')"));
  if (/(window\.)?(confirm|alert|prompt)\s*\(/.test(block.slice(0, 3000))) {
    throw new Error('a native dialog is used in the route path');
  }
});

check('standalone route renders as connected', () => {
  const html = registry.get('usbMatrix').innerHTML;
  if (!/data-rex="00:1B:13:04:6A:EA" data-lex="00:1B:13:04:E9:6E"[^>]*data-active="true"/.test(html)) {
    throw new Error('311<->324 route not rendered active');
  }
});

check('LEX/REX inventory tables render both families', () => {
  const lex = registry.get('lexTbl').innerHTML, rex = registry.get('rexTbl').innerHTML;
  if (!lex.includes('192.168.100.141') || !lex.includes('00:1B:13:04:E9:6E')) throw new Error('LEX table incomplete');
  if (!rex.includes('192.168.100.151') || !rex.includes('00:1B:13:04:6A:EA')) throw new Error('REX table incomplete');
});

check('each physical endpoint appears exactly once in the inventory', () => {
  const count = (html, needle) => html.split(needle).length - 1;
  const lex = registry.get('lexTbl').innerHTML, rex = registry.get('rexTbl').innerHTML;
  const lexRows = (lex.match(/<tr>/g) || []).length - 1;   // minus the header row
  const rexRows = (rex.match(/<tr>/g) || []).length - 1;
  if (lexRows !== 2) throw new Error(`LEX inventory has ${lexRows} rows, expected 2`);
  if (rexRows !== 2) throw new Error(`REX inventory has ${rexRows} rows, expected 2`);
  if (count(lex, '00:1B:13:04:E9:6E') !== 1) throw new Error('the standalone 311 is duplicated');
  if (count(rex, '00:1B:13:04:6A:EA') !== 1) throw new Error('the standalone 324 is duplicated');
});

check('a standalone row does not inherit integrated-only controls', () => {
  const lex = registry.get('lexTbl').innerHTML;
  const rows = lex.split('<tr>').filter(r => r.includes('00:1B:13:04:E9:6E'));
  if (rows.length !== 1) throw new Error('expected exactly one standalone LEX row');
  const row = rows[0];
  if (/class="port-select"/.test(row)) throw new Error('standalone row offers a Host Port selector');
  if (/class="filter-select"/.test(row)) throw new Error('standalone row offers a Device Filtering selector');
  if (/class="type-select"/.test(row)) throw new Error('standalone row offers a Type selector');
  if (!row.includes('Fixed / N/A')) throw new Error('standalone Host Port must read Fixed / N/A');
  // Absence of evidence is not a capability statement: the hardware audit found
  // no filtering API on a standalone unit, which is not the same as the unit
  // being unable to filter.
  if (row.includes('Not supported')) throw new Error('standalone filtering must not claim Not supported');
  if (!row.includes('Not established')) throw new Error('standalone filtering must read Not established');
});

check('an integrated row keeps its established controls', () => {
  const lex = registry.get('lexTbl').innerHTML;
  const row = lex.split('<tr>').filter(r => r.includes('192.168.100.141'))[0] || '';
  if (!/class="port-select"/.test(row)) throw new Error('integrated LEX lost its Host Port selector');
  if (!/class="filter-select"/.test(row)) throw new Error('integrated LEX lost its Device Filtering selector');
  if (!/class="type-select"/.test(row)) throw new Error('integrated LEX lost its Type selector');
});

check('the inventory shows live firmware and a separate link column', () => {
  const lex = registry.get('lexTbl').innerHTML;
  if (!/<th>Firmware<\/th>/.test(lex)) throw new Error('Firmware column missing');
  if (!/<th>Link<\/th>/.test(lex)) throw new Error('Link column missing');
  if (/<th>Revision<\/th>/.test(lex)) throw new Error('Revision column should have been renamed');
  if (!lex.includes('1.9.4')) throw new Error('live firmware not rendered');
  const standalone = lex.split('<tr>').filter(r => r.includes('00:1B:13:04:E9:6E'))[0];
  if (!standalone.includes('Linked')) throw new Error('link state not rendered');
});

check('a state-only re-render does not reorder the axes', () => {
  const before = registry.get('usbMatrix').innerHTML;
  vm.runInContext('render(__STATE__)', context, {filename: 'render2'});
  const after = registry.get('usbMatrix').innerHTML;
  const keys = h => [...h.matchAll(/data-lex="([^"]+)"/g)].map(m => m[1]).join(',');
  if (keys(before) !== keys(after)) throw new Error('axis order changed on a state-only refresh');
});

check('render survives shuffled axis input (deterministic ordering)', () => {
  const shuffled = Object.assign({}, STATE, {
    matrix_lex: [OMNI311, E4521], matrix_rex: [OMNI324, D4511],
  });
  vm.runInContext('render(__SHUF__)', Object.assign(context, {__SHUF__: shuffled}), {filename: 'render3'});
  const html = registry.get('usbMatrix').innerHTML;
  const order = [...html.matchAll(/data-lex="([^"]+)"/g)].map(m => m[1]);
  if (order[0] !== '192.168.100.141') throw new Error(`integrated LEX must sort first, got ${order[0]}`);
});

check('every helper the render path references is defined', () => {
  for (const helper of ['render', 'sortMatrixAxis', 'matrixSignature', 'cellCapability',
                        'standaloneRow', 'bindUsbCellTips', 'usbTip', 'esc', 'ipNum',
                        'sortByIpAsc', 'usbLexPeerCounts', 'isDifferentSubnet']) {
    const kind = vm.runInContext(`typeof ${helper}`, context);
    if (kind === 'undefined') throw new Error(`${helper} is not defined`);
  }
});

// ---- mixed-cell click, on wholly synthetic identities ----------------------
// The defect this guards against: an integrated row's key is a control IP, and
// sending that as a MAC produced a plausible but fabricated identity.
const SYN_LEX = {kind: 'standalone', usb_key: 'AA:11:22:33:44:55', usb_mac: 'AA:11:22:33:44:55',
                 ip: '10.77.0.11', mac: 'AA:11:22:33:44:55', model: 'AT-OMNI-311', host: 'AT-OMNI-311',
                 usb_ip: '10.77.0.11', type: 'LEX', classification: 'STANDALONE', online: true,
                 pairing_eligible: true, pairing_state_fresh: true, paired_macs: [],
                 host_port: '', host_port_configurable: false, filter: '', filter_configurable: false};
// An integrated REX: display key is its control IP, identity is its Icron MAC.
const SYN_REX = {kind: 'integrated', usb_key: '10.77.0.32', usb_mac: 'CC:DD:EE:00:00:32',
                 ip: '10.77.0.32', mac: 'CC:DD:EE:00:00:32', host: 'syn-decoder-0032',
                 usb_ip: '10.77.0.132', type: 'REX', host_port: '', filter: '',
                 classification: 'INTEGRATED', online: true};

function synthState(overrides) {
  const rex = {...SYN_REX, ...(overrides || {})};
  return {
    ok: true, lex: [], rex: [],
    matrix_lex: [SYN_LEX], matrix_rex: [rex],
    capabilities: {[rex.usb_key]: {[SYN_LEX.usb_key]: MIXED}},
    standalone_routes: {}, standalone_lex: [SYN_LEX], standalone_rex: [],
    pairings: {}, pairings_rex_side: {}, pairings_lex_side: {}, pairing_conflicts: {},
  };
}

async function clickOnlyCell(state) {
  sandbox.__requests.length = 0;
  vm.runInContext('render(__SYN__)', Object.assign(context, {__SYN__: state}), {filename: 'render'});
  const cells = registry.get('usbMatrix').querySelectorAll('td.cell:not(.disabled)');
  if (!cells.length) return {cells, routeRequests: [], blocked: true};
  // A first mixed route asks for confirmation; approve it as a user would.
  const pending = cells[0].click();
  const modal = sandbox.document.getElementById('usb_mixed_route_modal');
  const go = modal && modal.querySelector('.usb-mixed-go');
  if (go && typeof go.onclick === 'function') go.onclick();
  await pending;
  return {cells, routeRequests: sandbox.__requests.filter(r => String(r.url).includes('/api/usb_route/')), blocked: false};
}

checkSeq('clicking a mixed cell posts canonical MACs, never an address', async () => {
  const {routeRequests} = await clickOnlyCell(synthState());
  if (routeRequests.length !== 1) throw new Error(`expected 1 route request, got ${routeRequests.length}`);
  const req = routeRequests[0];
  if (req.url !== '/api/usb_route/pair') throw new Error(`posted to ${req.url}`);
  if (req.body.lex_mac !== SYN_LEX.usb_mac) throw new Error(`lex_mac was ${req.body.lex_mac}`);
  if (req.body.rex_mac !== SYN_REX.usb_mac) throw new Error(`rex_mac was ${req.body.rex_mac}`);
  // The reported failure: an integrated row's control IP used as an identity.
  for (const [field, value] of Object.entries(req.body)) {
    if (/^(\d{1,3}\.){3}\d{1,3}$/.test(String(value))) {
      throw new Error(`${field} carried an IP address: ${value}`);
    }
    if (String(value).replace(/[^0-9a-fA-F]/g, '') === SYN_REX.usb_key.replace(/\./g, '')) {
      throw new Error(`${field} was synthesised from the display key: ${value}`);
    }
  }
  if (req.body.rex_mac === SYN_REX.usb_key) throw new Error('rex_mac was the display key');
  if (req.body.rex_mac === SYN_REX.usb_ip) throw new Error('rex_mac was the USB IP');
});

checkSeq('a supported mixed route dispatches with no confirmation', async () => {
  const {routeRequests} = await clickOnlyCell(synthState());
  if (routeRequests.length !== 1) {
    throw new Error(`expected the click alone to dispatch one request, got ${routeRequests.length}`);
  }
  if (routeRequests[0].url !== '/api/usb_route/pair') throw new Error(`posted to ${routeRequests[0].url}`);
});

checkSeq('a cell without a canonical identity sends nothing', async () => {
  // Capability still claims routable; the client must refuse on its own.
  const {routeRequests, cells} = await clickOnlyCell(synthState({usb_mac: '', mac: ''}));
  if (cells.length && routeRequests.length) {
    throw new Error('a route was requested without an endpoint identity');
  }
});

checkSeq('a mixed route into an integrated REX renders as connected', async () => {
  // usb_icron does not observe a UDP-created pairing, so the route state comes
  // from the UDP observation and is matched on canonical MAC, not on the key.
  const state = synthState();
  state.standalone_routes = {[SYN_REX.usb_key]: {active: SYN_LEX.usb_mac, available: [],
                                                 fresh: true, source: 'udp_advanced_query'}};
  vm.runInContext('render(__SYN2__)', Object.assign(context, {__SYN2__: state}), {filename: 'render'});
  const html = registry.get('usbMatrix').innerHTML;
  if (!/data-active="true"/.test(html)) throw new Error('mixed route not rendered active');
  if (!/data-paired="true"/.test(html)) throw new Error('mixed route not rendered paired');
  // Clicking a connected cell must remove the route, not create it again.
  sandbox.__requests.length = 0;
  const cells = registry.get('usbMatrix').querySelectorAll('td.cell:not(.disabled)');
  await cells[0].click();
  const routed = sandbox.__requests.filter(r => String(r.url).includes('/api/usb_route/'));
  if (!routed.length) throw new Error('no route request was sent');
  if (routed[0].url !== '/api/usb_route/unpair') throw new Error(`posted to ${routed[0].url}`);
  if (routed[0].body.rex_mac !== SYN_REX.usb_mac) throw new Error('unpair used the wrong identity');
});

// Async checks (a real cell click) finish before the verdict is reported.
checkSeq('a route change reads as pending while it is in flight', async () => {
  sandbox.__requests.length = 0;
  vm.runInContext('render(__SYN__)', Object.assign(context, {__SYN__: synthState()}), {filename: 'render'});
  const cell = registry.get('usbMatrix').querySelectorAll('td.cell:not(.disabled)')[0];
  if (!cell) throw new Error('no routable cell rendered');
  let release;
  sandbox.__hold = new Promise(resolve => { release = resolve; });
  const inFlight = cell.click();
  // The request is open: the cell must say so rather than showing an outcome.
  if (!cell.className.includes('pending')) throw new Error('cell is not marked pending');
  if (cell.getAttribute('aria-busy') !== 'true') throw new Error('cell is not aria-busy');
  release();
  sandbox.__hold = null;
  await inFlight;
  if (cell.className.includes('pending')) throw new Error('pending outlived the request');
  if (cell.getAttribute('aria-busy')) throw new Error('aria-busy outlived the request');
});

checkSeq('a failed route change clears pending too', async () => {
  sandbox.__requests.length = 0;
  sandbox.__reply = {ok: false, error: 'route refused'};
  vm.runInContext('render(__SYN__)', Object.assign(context, {__SYN__: synthState()}), {filename: 'render'});
  const cell = registry.get('usbMatrix').querySelectorAll('td.cell:not(.disabled)')[0];
  await cell.click();
  sandbox.__reply = null;
  if (cell.className.includes('pending')) throw new Error('a failure left the cell pending');
  if (cell.getAttribute('aria-busy')) throw new Error('a failure left the cell aria-busy');
});

// ---- convergence: every outcome must leave the cell settled ---------------
// The failure the operator reported was a cell stuck showing an in-flight
// marker until the page was reloaded. It does not matter which answer the
// backend gives -- success, refusal, a network mismatch, an offline endpoint,
// a rollback -- the cell must end up reflecting authoritative state and must
// never keep `pending` or `aria-busy`.
function settled(cell) {
  return !cell.className.includes('pending') && !cell.getAttribute('aria-busy');
}

const OUTCOMES = [
  ['a verified success', {ok: true, status: 'VERIFIED_SUCCESS'}],
  ['a refusal', {ok: false, error: 'route refused'}],
  ['a cross-subnet refusal', {ok: false, status: 'NETWORK_MISMATCH',
                              error: 'The USB endpoints are on different networks.'}],
  ['an endpoint that cannot be reached', {ok: false, status: 'OFFLINE',
                                          error: 'The endpoint did not answer.'}],
  ['a route moved from another host', {ok: true, status: 'VERIFIED_SUCCESS',
                                       reassigned_from: '00:1B:13:05:50:50'}],
  ['a release that could not be verified', {ok: false, status: 'REASSIGN_RELEASE_FAILED',
                                            error: 'The previous owner did not release it.'}],
  ['a rolled-back failure', {ok: false, status: 'FAILED_ROLLED_BACK',
                             error: 'The change was undone.'}],
  ['a rollback that could not be confirmed', {ok: false, status: 'FAILED_ROLLBACK_UNVERIFIED',
                                              error: 'The rollback could not be confirmed.'}],
  ['a transport that threw', new Error('the request never completed')],
];

OUTCOMES.forEach(([name, reply]) => {
  checkSeq(`the cell settles after ${name}`, async () => {
    sandbox.__requests.length = 0;
    sandbox.__reply = reply;
    vm.runInContext('render(__SYN__)', Object.assign(context, {__SYN__: synthState()}), {filename: 'render'});
    const cell = registry.get('usbMatrix').querySelectorAll('td.cell:not(.disabled)')[0];
    await cell.click();
    sandbox.__reply = null;
    if (!settled(cell)) throw new Error(`${name} left the cell pending/aria-busy`);
  });
});

checkSeq('rapid sequential actions all settle', async () => {
  // The per-cell handler used to be re-bound on every poll, so one click ran
  // hundreds of handlers and each cleared the marker belonging to another.
  vm.runInContext('render(__SYN__)', Object.assign(context, {__SYN__: synthState()}), {filename: 'render'});
  const cells = registry.get('usbMatrix').querySelectorAll('td.cell:not(.disabled)');
  sandbox.__reply = {ok: true, status: 'VERIFIED_SUCCESS'};
  for (let round = 0; round < 6; round += 1) {
    for (const cell of cells) await cell.click();
  }
  sandbox.__reply = null;
  const stuck = Array.from(cells).filter(cell => !settled(cell));
  if (stuck.length) throw new Error(`${stuck.length} cell(s) never settled`);
});

checkSeq('a render landing between the click and the reply still settles', async () => {
  vm.runInContext('render(__SYN__)', Object.assign(context, {__SYN__: synthState()}), {filename: 'render'});
  const cell = registry.get('usbMatrix').querySelectorAll('td.cell:not(.disabled)')[0];
  sandbox.__reply = {ok: true, status: 'VERIFIED_SUCCESS'};
  const inFlight = cell.click();
  vm.runInContext('render(__SYN__)', Object.assign(context, {__SYN__: synthState()}), {filename: 'render'});
  await inFlight;
  sandbox.__reply = null;
  const after = registry.get('usbMatrix').querySelectorAll('td.cell:not(.disabled)')[0];
  if (!settled(after)) throw new Error('a poll landing mid-request left the cell pending');
});

// ---- ordering: an older read must never overwrite a newer one --------------
function tagged(tag) {
  const copy = JSON.parse(JSON.stringify(STATE));
  copy.__tag = tag;
  return copy;
}
function currentTag() {
  return vm.runInContext('lastState && lastState.__tag', context, {filename: 'tag'});
}

checkSeq('a superseded state response is discarded', async () => {
  let release;
  sandbox.__stateHold = new Promise(resolve => { release = resolve; });
  sandbox.__stateFactory = () => tagged('older');
  const slow = vm.runInContext('refresh()', context, {filename: 'refresh'});

  // A second read is issued afterwards and, this time, answers immediately.
  sandbox.__stateHold = null;
  sandbox.__stateFactory = () => tagged('newer');
  await vm.runInContext('refresh()', context, {filename: 'refresh'});
  if (currentTag() !== 'newer') throw new Error(`expected the newer read, got ${currentTag()}`);

  release();
  await slow;
  if (currentTag() !== 'newer') throw new Error('an older response overwrote a newer one');
  sandbox.__stateFactory = null;
});

checkSeq('a state read that predates a route change is discarded', async () => {
  sandbox.__stateFactory = () => tagged('settled');
  await vm.runInContext('refresh()', context, {filename: 'refresh'});

  let release;
  sandbox.__stateHold = new Promise(resolve => { release = resolve; });
  sandbox.__stateFactory = () => tagged('beforeTheChange');
  const inFlight = vm.runInContext('refresh()', context, {filename: 'refresh'});

  // The operator changes a route while that read is still outstanding.
  vm.runInContext('noteUsbRouteMutation()', context, {filename: 'mutate'});
  release();
  await inFlight;
  if (currentTag() === 'beforeTheChange') {
    throw new Error('a read from before the route change was applied over it');
  }
  sandbox.__stateHold = null;
  sandbox.__stateFactory = null;
});

checkSeq('a completed route change reconciles without a page reload', async () => {
  sandbox.__requests.length = 0;
  vm.runInContext('render(__SYN__)', Object.assign(context, {__SYN__: synthState()}), {filename: 'render'});
  const before = vm.runInContext('usbStateEpoch', context, {filename: 'epoch'});
  const cell = registry.get('usbMatrix').querySelectorAll('td.cell:not(.disabled)')[0];
  await cell.click();

  const after = vm.runInContext('usbStateEpoch', context, {filename: 'epoch'});
  if (after <= before) throw new Error('the route change did not invalidate earlier reads');
  const urls = sandbox.__requests.map(r => String(r.url));
  const routeAt = urls.findIndex(u => u.includes('/api/usb_route/'));
  const stateAt = urls.findIndex((u, i) => i > routeAt && u.includes('/api/usb_state'));
  if (routeAt === -1) throw new Error('no route request was sent');
  if (stateAt === -1) throw new Error('the grid never re-read state after the change');
  if (cell.className.includes('pending')) throw new Error('the cell stayed pending');
  // Nothing may reload the page to make the grid agree.
  const source = fs.readFileSync(SOURCE, 'utf8');
  if (/location\.reload/.test(source)) throw new Error('usb.js reloads the page');
});

checkSeq('a pending cell survives a state render landing mid-request', async () => {
  sandbox.__requests.length = 0;
  const state = synthState();
  vm.runInContext('render(__SYN__)', Object.assign(context, {__SYN__: state}), {filename: 'render'});
  const cell = registry.get('usbMatrix').querySelectorAll('td.cell:not(.disabled)')[0];
  let release;
  sandbox.__hold = new Promise(resolve => { release = resolve; });
  const inFlight = cell.click();
  // A poll lands while the request is still open.
  vm.runInContext('render(__SYN__)', context, {filename: 'render'});
  const live = registry.get('usbMatrix').querySelectorAll('td.cell')[0];
  if (!live.className.includes('pending')) throw new Error('a re-render lost the pending marker');
  release();
  sandbox.__hold = null;
  await inFlight;
  if (live.className.includes('pending')) throw new Error('pending outlived the request');
});

Promise.all(pendingChecks).then(() => {
  console.log('');
  if (failures.length) {
    console.error(`FAILED: ${failures.length} check(s)`);
    failures.forEach(f => console.error('  - ' + f));
    process.exit(1);
  }
  console.log('usb.js render smoke test: all checks passed');
  process.exit(0);   // usb.js installs a refresh interval that would keep node alive
});

// The restricted User's USB Matrix, executed.
//
// Two layers. The module on its own with scripted dependencies (what it draws, what a dot sends, what it
// refuses to send, what happens to a cell while a request is in flight), then the real page script with the
// real module against a scripted server: choosing USB reads the projection, a dot dispatches the request the
// server's route table serves, and the state is read again whatever the answer was.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {El} = require('./_fakedom');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const HTML = read('ui', 'user', 'index.html');
const USER_JS = read('ui', 'user', 'user.js');
const MODULE_JS = read('ui', 'user', 'usb-matrix.js');
const ADMIN_JS = read('ui', 'matrix', 'usb.js');
const PREVIEW_JS = read('ui', 'user', 'preview-refresh.js');
const MATRIX_JS = read('ui', 'user', 'matrix.js');
const DRAG_JS = read('ui', 'user', 'dragdrop.js');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]); });
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
function eq(a, b, m) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m || 'not equal'}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); }
const settle = () => new Promise((r) => setTimeout(r, 40));
const strip = (s) => s.split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');

// Synthetic identities: nothing here is a lab MAC or name.
const C1 = '0a:1b:2c:a0:00:09', C2 = '0a:1b:2c:a0:00:0a';
const D1 = '0a:1b:2c:b0:00:09', D2 = '0a:1b:2c:b0:00:0b', D3 = '0a:1b:2c:b0:00:0c';
const cell = (state, extra) => Object.assign({
  state, routed: state === 'routed', active: false, enabled: state !== 'blocked',
  reason: '', label: state === 'blocked' ? 'Different USB subnet' : (state === 'routed' ? 'Connected' : 'Available'),
  detail: state === 'blocked' ? 'A USB route is not carried across a router.' : 'Click to create this route.',
  stale: false}, extra || {});

function dataset(over) {
  return Object.assign({
    ok: true,
    computers: [{id: C1, name: 'Teacher PC', online: true}, {id: C2, name: 'Lectern PC', online: true}],
    peripherals: [{id: D1, name: 'Keyboard', online: true, attached_to: {mode: 'none', computer_id: null, computer_name: null}},
                  {id: D2, name: 'Camera', online: true, attached_to: {mode: 'computer', computer_id: C1, computer_name: 'Teacher PC'}},
                  {id: D3, name: 'Far Mouse', online: true, attached_to: {mode: 'none', computer_id: null, computer_name: null}}],
    cells: {
      [D1]: {[C1]: cell('available'), [C2]: cell('available')},
      [D2]: {[C1]: cell('routed', {active: true, label: 'Connected', detail: 'Click to remove this route.'}), [C2]: cell('available')},
      [D3]: {[C1]: cell('blocked', {reason: 'network_mismatch'}), [C2]: cell('blocked', {reason: 'network_mismatch'})},
    },
  }, over || {});
}

function harness(over) {
  const doc = {createElement: (t) => new El(t), body: new El('body'), documentElement: new El('html')};
  const wrap = new El('div'); wrap.hidden = true;
  wrap.style = {setProperty() {}};
  const table = new El('table'); table.id = 'usbMatrix';
  wrap.appendChild(table);
  const empty = new El('div'); empty.hidden = true;
  const status = new El('p');
  const legend = new El('div'); legend.hidden = true;
  const requests = [], toasts = [];
  let reloads = 0;
  let answer = () => ({status: 200, body: {ok: true, status: 'verified', changed: true}});
  const timers = [];
  const sandbox = {window: {innerWidth: 1280, innerHeight: 800, addEventListener() {}}, Promise, Array, Object, JSON,
                   String, Number, Set, Map, Date, console,
                   setTimeout: (fn, ms) => { timers.push({fn, ms}); return timers.length; }, clearTimeout() {}};
  vm.createContext(sandbox);
  vm.runInContext(MODULE_JS, sandbox);
  const holds = [];
  const module = sandbox.window.OmniUserUsbMatrix.create(Object.assign({
    document: doc, wrap, table, empty, status, legend,
    request: async (computer, peripheral, detach) => {
      requests.push({computer, peripheral, detach});
      const gate = holds.shift();
      if (gate) await gate;
      return answer(computer, peripheral, detach);
    },
    reload: async () => { reloads++; },
    toast: (text, kind) => toasts.push([text, kind]),
    failureText: (body, status) => body.error || ('HTTP ' + status),
  }, over || {}));
  const dots = () => table.querySelectorAll('td');
  const at = (device, computer) => dots().find((td) => td.getAttribute('data-rex') === device && td.getAttribute('data-lex') === computer);
  return {module, table, wrap, empty, status, legend, requests, toasts, timers, holds, doc, dots, at,
          setAnswer: (fn) => { answer = fn; }, reloads: () => reloads,
          click: (node) => table.fire('click', {target: node, preventDefault() { node.prevented = true; }})};
}

const suite = [];

// ------------------------------------------------------------------ drawing
suite.push(() => check('rows are USB devices and columns are computers, as on the Admin matrix', async () => {
  const h = harness();
  h.module.render(dataset());
  const rows = h.table.children;
  eq(rows.length, 4, 'a header row and one row per device');
  const header = rows[0].children;
  assert(header[0].className.split(' ').includes('row-head'), 'the corner');
  eq(header.slice(1).map((th) => th.className), ['enc-head', 'enc-head'], 'one column per computer');
  eq(header.slice(1).map((th) => th.children[0].children[0].textContent), ['Teacher PC', 'Lectern PC']);
  eq(rows.slice(1).map((r) => r.children[0].children[0].textContent), ['Keyboard', 'Camera', 'Far Mouse']);
  eq(h.dots().length, 6);
  assert(h.wrap.hidden === false && h.empty.hidden === true && h.legend.hidden === false);
}));

suite.push(() => check('each cell is the Admin cell: td.cell > span.radio-wrap > input[radio] + span.dot', async () => {
  const h = harness();
  h.module.render(dataset());
  const td = h.at(D1, C1);
  assert(td.tagName === 'TD' && td.className.split(' ').includes('cell'));
  const wrap = td.children[0];
  assert(wrap.className === 'radio-wrap');
  assert(wrap.children[0].tagName === 'INPUT' && wrap.children[0].type === 'radio');
  assert(wrap.children[1].className.split(' ').includes('dot'));
}));

suite.push(() => check('routed, active and blocked states are drawn from the server and from nothing else', async () => {
  const h = harness();
  h.module.render(dataset());
  const routed = h.at(D2, C1), free = h.at(D1, C1), blocked = h.at(D3, C1);
  assert(routed.getAttribute('data-paired') === 'true' && routed.getAttribute('data-active') === 'true');
  assert(routed.children[0].children[0].checked === true);
  assert(routed.children[0].children[1].className.includes('audio-on'), 'the inner dot marks an active link');
  assert(free.getAttribute('data-paired') === 'false' && free.children[0].children[0].checked === false);
  assert(blocked.className.split(' ').includes('disabled') && blocked.children[0].children[0].disabled === true);
  assert(!free.className.split(' ').includes('disabled'));
  assert(blocked.getAttribute('data-tip') === 'Different USB subnet');
  assert(blocked.getAttribute('data-tip-detail'));
  eq(h.table.querySelectorAll('small').map((s) => s.textContent),
     ['Not connected', 'Connected to Teacher PC', 'Not connected'], 'a device says what it is attached to');
}));

suite.push(() => check('a cell the server did not describe is a blocked cell, never an open one', async () => {
  const h = harness();
  const d = dataset();
  delete d.cells[D1][C2];
  h.module.render(d);
  const td = h.at(D1, C2);
  assert(td.className.split(' ').includes('disabled') && td.children[0].children[0].disabled === true);
}));

suite.push(() => check('no computer or no device draws an explanation instead of an empty grid', async () => {
  const h = harness();
  h.module.render(dataset({computers: []}));
  assert(h.wrap.hidden === true && h.empty.hidden === false && /computers/.test(h.empty.textContent));
  h.module.render(dataset({peripherals: []}));
  assert(/USB devices/.test(h.empty.textContent));
  h.module.render(dataset({computers: [], peripherals: []}));
  assert(/computers or USB devices/.test(h.empty.textContent));
  h.module.render(dataset());
  assert(h.wrap.hidden === false && h.empty.hidden === true);
}));

suite.push(() => check('a state-only refresh updates the cells in place; membership changing rebuilds', async () => {
  const h = harness();
  h.module.render(dataset());
  const before = h.at(D1, C1);
  const d = dataset();
  d.cells[D1][C1] = cell('routed', {active: true});
  h.module.render(d);
  assert(h.at(D1, C1) === before, 'the node under the pointer was replaced by a state-only refresh');
  assert(before.getAttribute('data-paired') === 'true' && before.children[0].children[0].checked === true);
  const grown = dataset();
  grown.peripherals.push({id: '0a:1b:2c:b0:00:0d', name: 'Headset', online: true,
                          attached_to: {mode: 'none', computer_id: null, computer_name: null}});
  grown.cells['0a:1b:2c:b0:00:0d'] = {[C1]: cell('available'), [C2]: cell('available')};
  h.module.render(grown);
  eq(h.dots().length, 8);
  assert(h.at(D1, C1) !== before, 'a new row rebuilds the grid');
}));

suite.push(() => check('names are text, the module builds nodes, and nothing is written to storage or polled', async () => {
  const code = strip(MODULE_JS);
  assert(!/innerHTML|insertAdjacentHTML|outerHTML/.test(code), 'built from nodes, so nothing needs escaping');
  assert(!/setInterval/.test(code), 'no timer');
  assert(!/localStorage|sessionStorage|document\.cookie/.test(code));
  assert(!/\.title\s*=|setAttribute\('title'/.test(code), 'a native title would be one very wide tooltip');
  const h = harness();
  const d = dataset();
  d.computers[0].name = '<img src=x onerror=alert(1)>';
  h.module.render(d);
  assert(h.table.children[0].children[1].children[0].children[0].textContent === '<img src=x onerror=alert(1)>');
}));

// -------------------------------------------------------------- interaction
suite.push(() => check('a dot on a free cell sends exactly the two identities and "connect"', async () => {
  const h = harness();
  h.module.render(dataset());
  const input = h.at(D1, C1).children[0].children[0];
  await h.click(input);
  eq(h.requests, [{computer: C1, peripheral: D1, detach: false}]);
  assert(input.prevented === true, 'the radio is not allowed to check itself: the cell is a request, not an outcome');
}));

suite.push(() => check('a dot on a routed cell sends "disconnect"', async () => {
  const h = harness();
  h.module.render(dataset());
  await h.click(h.at(D2, C1));
  eq(h.requests, [{computer: C1, peripheral: D2, detach: true}]);
}));

suite.push(() => check('a blocked cell sends nothing, whichever node of it is hit', async () => {
  const h = harness();
  h.module.render(dataset());
  const td = h.at(D3, C1);
  await h.click(td);
  await h.click(td.children[0]);
  await h.click(td.children[0].children[0]);
  eq(h.requests, [], 'a prohibited cell reached the request path');
}));

suite.push(() => check('a cell is pending while its request is in flight and the state is read again after', async () => {
  const h = harness();
  h.module.render(dataset());
  let release;
  h.holds.push(new Promise((r) => { release = r; }));
  const td = h.at(D1, C1);
  const done = h.click(td);
  await settle();
  assert(td.className.split(' ').includes('pending') && td.getAttribute('aria-busy') === 'true', 'pending is shown');
  assert(td.getAttribute('data-paired') === 'false', 'never drawn as routed before a read says so');
  assert(/Connecting/.test(h.status.textContent));
  assert(h.reloads() === 0);
  release();
  await done;
  assert(!td.className.split(' ').includes('pending'), 'pending ends');
  eq(h.reloads(), 1, 'the state is read again after the request');
  eq(h.status.getAttribute('data-kind'), 'ok');
  assert(/created and verified/.test(h.status.textContent));
  eq(h.toasts[0][1], 'ok');
}));

suite.push(() => check('a second dot on the same device waits; a dot on another device does not', async () => {
  const h = harness();
  h.module.render(dataset());
  let release;
  h.holds.push(new Promise((r) => { release = r; }));
  const first = h.click(h.at(D1, C1));
  await settle();
  await h.click(h.at(D1, C2));
  eq(h.requests.length, 1, 'one device cannot be in two requests at once');
  const other = h.click(h.at(D2, C2));
  await settle();
  eq(h.requests.length, 2, 'a different device is independent');
  release();
  await Promise.all([first, other]);
}));

suite.push(() => check('a refusal is said in the server\'s words, the state is read again and the page stays usable', async () => {
  const h = harness();
  h.module.render(dataset());
  h.setAnswer(() => ({status: 409, body: {ok: false, status: 'refused', reason: 'peer_limit',
                                           error: 'Peer limit reached. That computer already has its maximum of 5 peripherals.'}}));
  const td = h.at(D1, C1);
  await h.click(td);
  eq(h.status.getAttribute('data-kind'), 'error');
  assert(/Peer limit reached/.test(h.status.textContent));
  eq(h.toasts[0][1], 'error');
  eq(h.reloads(), 1);
  assert(!td.className.split(' ').includes('pending'));
  h.setAnswer(() => ({status: 200, body: {ok: true, status: 'verified', changed: true}}));
  await h.click(td);
  eq(h.requests.length, 2, 'the same device accepts the next request after a failure');
}));

suite.push(() => check('accepted-but-unverified is a failure, not a success', async () => {
  const h = harness();
  h.module.render(dataset());
  h.setAnswer(() => ({status: 502, body: {ok: false, status: 'unverified', changed: false,
                                           error: 'The device accepted the command but a fresh read did not confirm it.'}}));
  await h.click(h.at(D1, C1));
  eq(h.status.getAttribute('data-kind'), 'error');
  assert(/did not confirm/.test(h.status.textContent));
  eq(h.toasts[0][1], 'error');
}));

suite.push(() => check('a request that throws still ends pending and still reads again', async () => {
  const h = harness();
  h.module.render(dataset());
  h.setAnswer(() => { throw new Error('network down'); });
  const td = h.at(D1, C1);
  await h.click(td);
  assert(!td.className.split(' ').includes('pending'));
  eq(h.reloads(), 1);
  assert(/Could not reach/.test(h.status.textContent));
}));

suite.push(() => check('the explanation is a bounded styled tooltip on hover and focus, never a native title', async () => {
  const h = harness();
  h.module.render(dataset());
  const td = h.at(D3, C1);
  await h.table.fire('mouseover', {target: td.children[0].children[0]});
  const tip = h.doc.body.children.find((n) => n.className === 'usb-cell-tip');
  assert(tip, 'a tooltip was made');
  assert(tip.style.display === 'block' && /Different USB subnet/.test(tip.textContent));
  assert(/max-width:320px/.test(tip.style.cssText) && /position:fixed/.test(tip.style.cssText));
  await h.table.fire('mouseout', {target: td});
  h.timers[h.timers.length - 1].fn();
  assert(tip.style.display === 'none');
  await h.table.fire('focusin', {target: td.children[0].children[0]});
  assert(tip.style.display === 'block', 'keyboard focus shows it too');
}));

// ----------------------------------------------------------------- the page
function world(options) {
  options = options || {};
  const byId = new Map();
  [...HTML.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)].forEach((m) => {
    const n = new El('div'); n.id = m[1];
    n.hidden = /\shidden(\s|>|=|\/)/.test(m[0]);
    byId.set(m[1], n);
  });
  byId.get('usb_wrap').appendChild(byId.get('usbMatrix'));
  byId.get('usbMatrix').tagName = 'TABLE';
  byId.get('usb_wrap').style = {setProperty() {}};
  const listeners = {};
  const body = new El('body');
  const document = {
    getElementById: (id) => byId.get(id) || null,
    querySelectorAll: () => [],
    createElement: (tag) => new El(tag),
    createTextNode: (t) => Object.assign(new El('#text'), {_text: String(t)}),
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    hidden: false, body, documentElement: new El('html'),
  };
  const calls = [], toasts = [];
  const live = {usb: options.usb || dataset(), usbStatus: 200, routeAnswer: options.routeAnswer};
  const sandbox = {
    document,
    window: {toast: (m, kind) => toasts.push([m, kind]), omniConfirm: async () => true, innerWidth: 1280,
             innerHeight: 800, addEventListener() {}, setInterval() { return 0; }, clearInterval() {},
             setTimeout, clearTimeout,
             omniRemember: {get: () => null, set() {}}},
    location: {pathname: '/user', replace() {}, reload() {}},
    sessionStorage: {getItem: () => 'tok', setItem() {}, removeItem() {}},
    fetch: async (url, opts) => {
      calls.push({url, method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null});
      if (calls.length > 80) throw new Error('runaway requests');
      let out = {body: {ok: true}};
      if (url === '/api/user/state') out = {body: options.state};
      else if (url === '/api/user/revision') out = {body: {ok: true, revision: 'r1'}};
      else if (url === '/api/auth/session') out = {body: {csrf_token: 'tok', method: 'password'}};
      else if (url === '/api/user/usb_state') out = {status: live.usbStatus, body: live.usb};
      else if (url === '/api/user/usb_route') out = live.routeAnswer || {body: {ok: true, status: 'verified', changed: true}};
      return {ok: (out.status || 200) < 400, status: out.status || 200,
              headers: {get: () => 'application/json'}, json: async () => out.body || {}};
    },
    URL: {createObjectURL() { return ''; }, revokeObjectURL() {}},
    Image: function () {},
    Date, JSON, Object, Array, Set, Map, String, Number, Promise, console, encodeURIComponent,
    setTimeout, clearTimeout, setInterval() { return 0; },
  };
  vm.createContext(sandbox);
  [PREVIEW_JS, MATRIX_JS, DRAG_JS, MODULE_JS, USER_JS].forEach((src) => vm.runInContext(src, sandbox));
  return {byId, calls, toasts, live, document, sandbox};
}

const PAGE_STATE = (features) => ({
  ok: true, role: 'user', username: 'teacher', features, interfaces: ['standard'],
  sources: [{id: 's1', name: 'Teacher PC', online: true}],
  destinations: [{id: 'd1', name: 'Front', online: true, showing: {mode: 'none'}}], groups: [], revision: 'r1',
});

suite.push(() => check('the page reads the projection when USB is shown and draws the Admin dot grid', async () => {
  const w = world({state: PAGE_STATE(['av', 'usb'])});
  await settle();
  const before = w.calls.filter((c) => c.url === '/api/user/usb_state').length;
  assert(before >= 1, 'granted USB is read');
  const tab = w.byId.get('tabs').children.find((b) => b.textContent === 'USB');
  await tab.fire('click');
  await settle();
  assert(w.calls.filter((c) => c.url === '/api/user/usb_state').length > before, 'every visit reads again');
  assert(w.byId.get('panel_usb').hidden === false);
  eq(w.byId.get('usbMatrix').querySelectorAll('td').length, 6);
  assert(w.byId.get('action_bar').hidden === true, 'USB has no Connect button: a dot is the request');
}));

suite.push(() => check('a dot dispatches POST /api/user/usb_route with only the identities and the direction', async () => {
  const w = world({state: PAGE_STATE(['av', 'usb'])});
  await settle();
  await w.byId.get('tabs').children.find((b) => b.textContent === 'USB').fire('click');
  await settle();
  const td = w.byId.get('usbMatrix').querySelectorAll('td')
    .find((t) => t.getAttribute('data-rex') === D1 && t.getAttribute('data-lex') === C1);
  await w.byId.get('usbMatrix').fire('click', {target: td.children[0].children[0], preventDefault() {}});
  await settle();
  const posts = w.calls.filter((c) => c.url === '/api/user/usb_route');
  eq(posts.length, 1);
  eq(posts[0].method, 'POST');
  eq(posts[0].body, {computer: C1, peripherals: D1, detach: false});
  // The state is read again afterwards, whatever the answer.
  const lastRead = w.calls.map((c) => c.url).lastIndexOf('/api/user/usb_state');
  assert(lastRead > w.calls.indexOf(posts[0]), 'the projection is read again after the route');
  assert(w.toasts.some(([m, k]) => k === 'ok' && /verified/.test(m)));
}));

suite.push(() => check('the page script no longer carries the Connect/Disconnect card flow', async () => {
  const code = strip(USER_JS);
  assert(!/function routeUsb/.test(code) && !/usb_computers|usb_peripherals/.test(code + HTML),
    'the card pickers are gone');
  assert(/OmniUserUsbMatrix/.test(code));
  assert(/\/ui\/user\/usb-matrix\.js/.test(HTML) && /\/ui\/matrix\/usb-grid\.css/.test(HTML)
    && /\/ui\/matrix\/usb-matrix\.css/.test(HTML), 'the shared stylesheets and the module are loaded');
  const order = [...HTML.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]);
  assert(order.indexOf('/ui/user/usb-matrix.js') < order.indexOf('/ui/user/user.js'), 'the module loads first');
  assert(order.includes('/ui/mirror-scroll.js'), 'the shared top-and-bottom scrolling is on the grid');
  assert(/id="usb_wrap"[^>]*data-mirror-scroll/.test(HTML));
}));

suite.push(() => check('the user grid has the same structure vocabulary as the Admin template', async () => {
  // Both draw td.cell > span.radio-wrap > input[radio] + span.dot, th.row-head, th.enc-head and the
  // vertical column header. A class dropped from one and not the other is how two grids drift.
  const tokens = ['cell', 'radio-wrap', 'dot', 'audio-on', 'row-head', 'rex-label', 'enc-head', 'col-header',
                  'lex-label', 'enc-host'];
  const mod = strip(MODULE_JS);
  tokens.forEach((token) => {
    if (token === 'enc-host') return;     // the Admin page names the host under each column; the user page names no host
    assert(ADMIN_JS.includes(token), 'the Admin template no longer has ' + token);
    assert(mod.includes(token), 'the user module lacks ' + token);
  });
  assert(/type="radio"/.test(ADMIN_JS) && /input\.type = 'radio'/.test(mod));
  assert(/data-active/.test(ADMIN_JS) && /data-active/.test(mod));
  assert(/data-paired/.test(ADMIN_JS) && /data-paired/.test(mod));
  assert(/data-tip-detail/.test(ADMIN_JS) && /data-tip-detail/.test(mod));
  assert(/class="dot\$\{innerCls\}"|class="dot/.test(ADMIN_JS));
}));

(async () => {
  for (const s of suite) await s();
  results.forEach(([tag, text]) => console.log(`${tag} ${text}`));
  const total = results.length;
  console.log(failures ? `\n${failures} FAILED of ${total}` : `\n${total}/${total} checks passed`);
  process.exit(failures ? 1 : 0);
})();

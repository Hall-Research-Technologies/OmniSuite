// The restricted User's Multiview page, executed.
//
// Two layers. The module on its own with scripted dependencies (what it draws, what Show sends, what it refuses
// to send while a show is in flight, what an operator is told), then the real shell script with the real module
// against a scripted server: the tab is offered only to an account that holds the capability, opening it reads the
// projection once, and a real click on Show dispatches the request the server's route table serves.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {El} = require('./_fakedom');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const HTML = read('ui', 'user', 'index.html');
const USER_JS = read('ui', 'user', 'user.js');
const MODULE_JS = read('ui', 'user', 'multiview.js');
const ACCESS_PY = read('omni_access.py');
const CSS = read('ui', 'remote.css');
const PREVIEW_JS = read('ui', 'user', 'preview-refresh.js');
const MATRIX_JS = read('ui', 'user', 'matrix.js');
const DRAG_JS = read('ui', 'user', 'dragdrop.js');
const USB_JS = read('ui', 'user', 'usb-matrix.js');

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
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map((l) => l.replace(/^\s*\/\/.*$/, '')).join('\n');

// Synthetic identities: nothing here is a lab MAC, a lab name or a real address.
const DEC1 = '0a:1b:2c:d0:00:01', DEC2 = '0a:1b:2c:d0:00:02';
const L1 = DEC1 + '/mv_00000000000000a1', L2 = DEC1 + '/mv_00000000000000a2', L3 = DEC2 + '/mv_00000000000000b1';

const FOUR = [
  {cell: 'main', x: 0, y: 0, width: 1440, height: 810},
  {cell: 'a', x: 1440, y: 0, width: 480, height: 270},
  {cell: 'b', x: 1440, y: 270, width: 480, height: 270},
  {cell: 'c', x: 1440, y: 540, width: 480, height: 270},
];
const layout = (id, name, extra) => Object.assign({
  id, name, layout_label: 'Main and three', windows: FOUR, canvas: {width: 1920, height: 1080},
  showing: false, available: true, reason: '',
}, extra || {});
function dataset(over) {
  return Object.assign({ok: true, displays: [
    {id: DEC1, name: 'Front Display', layouts: [layout(L1, 'Lesson', {showing: true}), layout(L2, 'Assembly')]},
    {id: DEC2, name: 'Back Display', layouts: [layout(L3, 'Quad', {layout_label: '2 x 2', available: false,
      reason: 'Video Wall is on for this display.'})]},
  ]}, over || {});
}

function all(node, out) {
  out = out || [];
  node.children.forEach((c) => { out.push(c); all(c, out); });
  return out;
}
const byClass = (root, cls) => all(root).filter((n) => n.className.split(' ').includes(cls));
const textOf = (root) => all(root).map((n) => n._text || '').join(' ');

function harness(over) {
  over = over || {};
  const doc = {createElement: (t) => new El(t), body: new El('body')};
  const host = new El('div');
  const status = new El('p');
  const loads = [], shows = [], toasts = [], gates = [];
  let state = over.state || dataset();
  let loadStatus = 200;
  let answer = over.answer || (() => ({status: 200, body: {ok: true, status: 'VERIFIED'}}));
  const sandbox = {window: {}, Promise, Array, Object, JSON, String, Number, Set, Map, Date, console};
  vm.createContext(sandbox);
  vm.runInContext(MODULE_JS, sandbox);
  const module = sandbox.window.OmniUserMultiview.create({
    document: doc, host, status,
    load: async () => {
      loads.push(1);
      if (loadStatus === 0) throw new Error('network');
      return {status: loadStatus, body: JSON.parse(JSON.stringify(state))};
    },
    show: async (id) => {
      shows.push(id);
      const gate = gates.shift();
      if (gate) await gate;
      return answer(id);
    },
    toast: (text, kind) => toasts.push([text, kind]),
    failureText: (body) => body.error || 'That did not work.',
  });
  const card = (id) => byClass(host, 'mv-card').find((c) => c.getAttribute('data-layout') === id);
  const button = (id) => byClass(card(id), 'mv-show')[0];
  return {module, host, status, loads, shows, toasts, gates, card, button, doc,
          setState: (s) => { state = s; }, setLoadStatus: (s) => { loadStatus = s; },
          setAnswer: (fn) => { answer = fn; }};
}

const suite = [];

// ------------------------------------------------------------------ drawing
suite.push(() => check('one heading per display and one card per assigned layout, nothing else', async () => {
  const h = harness();
  await h.module.refresh();
  eq(byClass(h.host, 'mv-display-name').map((n) => n.textContent), ['Front Display', 'Back Display']);
  eq(byClass(h.host, 'mv-card').map((c) => c.getAttribute('data-layout')), [L1, L2, L3]);
  eq(byClass(h.card(L2), 'mv-name').map((n) => n.textContent), ['Assembly']);
  eq(byClass(h.card(L2), 'mv-meta').map((n) => n.textContent), ['Main and three']);
}));

suite.push(() => check('the schematic is drawn in proportion from the server geometry', async () => {
  const h = harness();
  await h.module.refresh();
  const windows = byClass(h.card(L1), 'mv-window');
  eq(windows.length, 4);
  eq([windows[0].style.left, windows[0].style.top, windows[0].style.width, windows[0].style.height],
     ['0%', '0%', '75%', '75%']);
  eq([windows[1].style.left, windows[1].style.width, windows[1].style.height], ['75%', '25%', '25%']);
  const art = byClass(h.card(L1), 'mv-schematic')[0];
  assert(art.getAttribute('role') === 'img' && /4 windows/.test(art.getAttribute('aria-label')), 'described for a screen reader');
}));

suite.push(() => check('the layout on the display is marked in words, and only that one', async () => {
  const h = harness();
  await h.module.refresh();
  const marker = (id) => byClass(h.card(id), 'mv-showing')[0];
  assert(marker(L1).hidden === false && /Showing/.test(marker(L1).textContent));
  assert(marker(L2).hidden === true, 'a layout that is not on the display carries no marker');
  assert(h.card(L1).classList.contains('showing') && !h.card(L2).classList.contains('showing'));
}));

suite.push(() => check('an unavailable layout is disabled and says why, and clicking it sends nothing', async () => {
  const h = harness();
  await h.module.refresh();
  assert(h.button(L3).disabled === true);
  const reason = byClass(h.card(L3), 'mv-reason')[0];
  assert(reason.hidden === false && reason.textContent === 'Video Wall is on for this display.', reason.textContent);
  await h.button(L3).fire('click');
  await settle();
  eq(h.shows, [], 'nothing was sent');
}));

suite.push(() => check('a display that is not answering is named with its message and has no cards', async () => {
  const h = harness({state: {ok: true, displays: [{id: DEC1, name: 'Front Display', layouts: [],
    unavailable: 'This display is not answering.'}]}});
  await h.module.refresh();
  assert(/Front Display/.test(h.host.textContent) && /This display is not answering\./.test(h.host.textContent));
  eq(byClass(h.host, 'mv-card').length, 0);
  assert(!/No Multiview layouts/.test(h.host.textContent), 'not presented as empty');
}));

// ------------------------------------------------------ the three non-states
suite.push(() => check('empty, loading and failed to load are three different pages', async () => {
  const empty = harness({state: {ok: true, displays: []}});
  await empty.module.refresh();
  eq(empty.host.textContent, 'No Multiview layouts have been given to your account.');

  const failed = harness();
  failed.setLoadStatus(500);
  await failed.module.refresh();
  assert(!/No Multiview layouts/.test(failed.host.textContent), 'a failed read is not "none"');
  assert(/could not be loaded/.test(failed.host.textContent));
  const retry = byClass(failed.host, 'mv-retry')[0];
  assert(retry, 'there is a way to try again');
  failed.setLoadStatus(200);
  await retry.fire('click');
  await settle();
  eq(byClass(failed.host, 'mv-card').length, 3, 'trying again loads');

  const slow = harness();
  slow.setAnswer(() => null);
  const pending = slow.module.refresh();
  assert(/Loading/.test(slow.host.textContent) && !/No Multiview layouts/.test(slow.host.textContent),
    'while waiting it says it is loading, not that there is nothing');
  await pending;
}));

suite.push(() => check('a network failure is a load failure too, not a signed-out page', async () => {
  const h = harness();
  h.setLoadStatus(0);
  await h.module.refresh();
  assert(/could not be loaded/.test(h.host.textContent));
}));

// --------------------------------------------------------------------- Show
suite.push(() => check('Show hands the assignment id, exactly as the server gave it, to the request', async () => {
  const h = harness();
  await h.module.refresh();
  await h.button(L2).fire('click');
  await settle();
  eq(h.shows, [L2]);
  eq(h.module.pendingCount(), 0);
}));

suite.push(() => check('a second click while a show is in flight sends nothing, and the display is locked', async () => {
  const h = harness();
  await h.module.refresh();
  let release;
  h.gates.push(new Promise((r) => { release = r; }));
  const first = h.button(L2).fire('click');
  await settle();
  assert(h.button(L2).disabled === true && h.button(L2).textContent === 'Working…', 'pending state is visible');
  assert(h.card(L2).getAttribute('aria-busy') === 'true');
  assert(h.button(L1).disabled === true, 'the other layout on that display waits for it');
  assert(h.button(L3).disabled === true);          // unavailable anyway
  await h.button(L2).fire('click');
  await h.button(L1).fire('click');
  await settle();
  eq(h.shows, [L2], 'only the first click reached the server');
  release();
  await first;
  await settle();
  assert(h.button(L2).disabled === false && h.button(L1).disabled === false, 'released afterwards');
  assert(h.button(L2).textContent === 'Show');
}));

suite.push(() => check('a verified show says so, then reads the state once and moves the marker', async () => {
  const h = harness();
  await h.module.refresh();
  const before = h.loads.length;
  h.setState(dataset({displays: [
    {id: DEC1, name: 'Front Display', layouts: [layout(L1, 'Lesson'), layout(L2, 'Assembly', {showing: true})]},
    dataset().displays[1]]}));
  await h.button(L2).fire('click');
  await settle();
  eq(h.loads.length - before, 1, 'one read after the show, not a poll');
  assert(byClass(h.card(L2), 'mv-showing')[0].hidden === false);
  assert(byClass(h.card(L1), 'mv-showing')[0].hidden === true);
  assert(h.toasts.length === 1 && h.toasts[0][1] === 'ok' && /Assembly/.test(h.toasts[0][0]), JSON.stringify(h.toasts));
  eq(h.status.textContent, '', 'the progress line is cleared once the outcome is on the card');
}));

suite.push(() => check('a refusal shows the server sentence, never a status word or a code', async () => {
  const h = harness();
  await h.module.refresh();
  h.setAnswer(() => ({status: 409, body: {ok: false, status: 'NO ACTIVE VIDEO — ROLLED BACK',
    reason: 'refused', error: 'That layout could not be shown: a source is not producing a picture.'}}));
  await h.button(L2).fire('click');
  await settle();
  const line = byClass(h.card(L2), 'mv-result')[0];
  assert(line.hidden === false && line.className.includes('error'));
  eq(line.textContent, 'That layout could not be shown: a source is not producing a picture.');
  eq(h.toasts, [['That layout could not be shown: a source is not producing a picture.', 'error']]);
  assert(!/409|refused|ROLLED BACK/.test(textOf(h.host) + h.toasts.join()), 'no internal vocabulary');
  assert(h.button(L2).disabled === false, 'it can be tried again');
}));

suite.push(() => check('the server sentence is shown as given, only its first letter capitalised', async () => {
  const h = harness();
  await h.module.refresh();
  h.setAnswer(() => ({status: 502, body: {ok: false, error: 'a unit did not answer after 3 attempts. No device was changed.'}}));
  await h.button(L2).fire('click');
  await settle();
  eq(byClass(h.card(L2), 'mv-result')[0].textContent, 'A unit did not answer after 3 attempts. No device was changed.');
}));

suite.push(() => check('a show whose request fails outright is reported plainly and releases the lock', async () => {
  const h = harness();
  await h.module.refresh();
  h.setAnswer(() => { throw new Error('network down'); });
  await h.button(L2).fire('click');
  await settle();
  assert(/could not be reached/.test(h.toasts[0][0]) && h.toasts[0][1] === 'error', JSON.stringify(h.toasts));
  assert(h.module.pendingCount() === 0 && h.button(L2).disabled === false);
}));

suite.push(() => check('a show that worked but left a window dark is a warning, not a plain success', async () => {
  const h = harness();
  await h.module.refresh();
  h.setAnswer(() => ({status: 200, body: {ok: true, status: 'VERIFIED', unlocked: 1,
    message: 'Shown, but 1 window did not show a picture.'}}));
  await h.button(L2).fire('click');
  await settle();
  eq(h.toasts, [['Shown, but 1 window did not show a picture.', 'warn']]);
}));

// ------------------------------------------------------- structure vs state
suite.push(() => check('a state-only refresh updates the cards in place and rebuilds nothing', async () => {
  const h = harness();
  await h.module.refresh();
  const nodes = byClass(h.host, 'mv-card');
  h.setState(dataset({displays: [
    {id: DEC1, name: 'Front Display', layouts: [layout(L1, 'Lesson'), layout(L2, 'Assembly', {showing: true})]},
    dataset().displays[1]]}));
  await h.module.refresh();
  const after = byClass(h.host, 'mv-card');
  assert(nodes.every((n, i) => n === after[i]), 'the same nodes');
  assert(byClass(after[1], 'mv-showing')[0].hidden === false);
  h.setState(dataset({displays: [dataset().displays[0]]}));
  await h.module.refresh();
  eq(byClass(h.host, 'mv-card').length, 2, 'membership changed, so it was rebuilt');
}));

// -------------------------------------------------------------- what is not here
suite.push(() => check('no source, address, MAC or engineering text is drawn', async () => {
  const h = harness();
  await h.module.refresh();
  const text = textOf(h.host);
  assert(!/\b\d{1,3}(\.\d{1,3}){3}\b/.test(text), 'no IPv4 address: ' + text);
  assert(!/\b[0-9a-f]{2}(:[0-9a-f]{2}){5}\b/i.test(text), 'no MAC');
  ['mv_', 'ip_input', 'Encoder', 'Session', 'multicast', 'bitrate', 'scaler'].forEach((term) => {
    assert(!text.includes(term), term + ' reached the page');
  });
  const inputs = all(h.host).filter((n) => ['INPUT', 'SELECT', 'TEXTAREA'].includes(n.tagName));
  eq(inputs.length, 0, 'no field of any kind');
  const buttons = byClass(h.host, 'mv-show').length;
  eq(all(h.host).filter((n) => n.tagName === 'BUTTON').length, buttons, 'the only controls are Show buttons');
}));

suite.push(() => check('the module never uses native dialogs, innerHTML or a timer', () => {
  const code = strip(MODULE_JS);
  assert(!/\b(alert|confirm|prompt)\s*\(/.test(code), 'native dialog');
  assert(!/innerHTML|insertAdjacentHTML|document\.write/.test(code), 'markup injection');
  assert(!/setInterval|setTimeout/.test(code), 'a timer means polling');
  assert(!/title\s*=|setAttribute\('title'/.test(code), 'no native title tooltip');
}));

suite.push(() => check('every control has an accessible name and the stylesheet defines the classes it uses', () => {
  assert(/setAttribute\('aria-label', 'Show '/.test(MODULE_JS), 'the Show button names its layout and display');
  const css = strip(CSS);
  ['.mv-card', '.mv-schematic', '.mv-window', '.mv-showing', '.mv-card.showing', '.mv-reason'].forEach((sel) => {
    assert(css.includes(sel), sel + ' has no rule');
  });
  // Every colour comes from a token, so every preset and both appearances reach it.
  const block = css.slice(css.indexOf('.mv-display {'), css.indexOf('.assign-group-head'));
  assert(block.length > 200 && !/#[0-9a-f]{3,8}\b|rgba?\(/i.test(block), 'a colour literal in the Multiview styles');
}));

// ------------------------------------------------------- the real shell page
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
  const listeners = {}, winListeners = {};
  const document = {
    getElementById: (id) => byId.get(id) || null,
    querySelectorAll: () => [],
    createElement: (tag) => new El(tag),
    createTextNode: (t) => Object.assign(new El('#text'), {_text: String(t)}),
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    hidden: false, body: new El('body'), documentElement: new El('html'),
  };
  const calls = [], toasts = [];
  const live = {mv: options.mv || dataset(), mvStatus: 200, show: options.show, revision: 'r1'};
  const sandbox = {
    document,
    window: {toast: (m, kind) => toasts.push([m, kind]), omniConfirm: async () => true, innerWidth: 1280,
             innerHeight: 800, addEventListener: (type, fn) => { (winListeners[type] = winListeners[type] || []).push(fn); },
             setInterval() { return 0; }, clearInterval() {},
             setTimeout, clearTimeout, omniRemember: {get: () => null, set() {}}},
    location: {pathname: '/user', replace() {}, reload() {}},
    sessionStorage: {getItem: () => 'tok', setItem() {}, removeItem() {}},
    fetch: async (url, opts) => {
      calls.push({url, method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null,
                  csrf: opts && opts.headers && opts.headers['X-OmniSuite-CSRF']});
      if (calls.length > 80) throw new Error('runaway requests');
      let out = {body: {ok: true}};
      if (url === '/api/user/state') out = {body: options.state};
      else if (url === '/api/user/revision') out = {body: {ok: true, revision: live.revision}};
      else if (url === '/api/auth/session') out = {body: {csrf_token: 'tok', method: 'password'}};
      else if (url === '/api/user/usb_state') out = {body: {ok: true, computers: [], peripherals: [], cells: {}}};
      else if (url === '/api/user/multiview_state') out = {status: live.mvStatus, body: live.mv};
      else if (url === '/api/user/multiview_show') out = live.show ? live.show(calls[calls.length - 1]) : {body: {ok: true, status: 'VERIFIED'}};
      return {ok: (out.status || 200) < 400, status: out.status || 200,
              headers: {get: () => 'application/json'}, json: async () => out.body || {}};
    },
    URL: {createObjectURL() { return ''; }, revokeObjectURL() {}},
    Image: function () {},
    Date, JSON, Object, Array, Set, Map, String, Number, Promise, console, encodeURIComponent,
    setTimeout, clearTimeout, setInterval() { return 0; },
  };
  vm.createContext(sandbox);
  [PREVIEW_JS, MATRIX_JS, DRAG_JS, USB_JS, MODULE_JS, USER_JS].forEach((src) => vm.runInContext(src, sandbox));
  return {byId, calls, toasts, live, sandbox, winListeners};
}

const PAGE_STATE = (features) => ({
  ok: true, role: 'user', username: 'teacher', features, interfaces: ['standard'],
  sources: [{id: 's1', name: 'Teacher PC', online: true}],
  destinations: [{id: 'd1', name: 'Front', online: true, showing: {mode: 'none'}}], groups: [], revision: 'r1',
});
const mvReads = (w) => w.calls.filter((c) => c.url === '/api/user/multiview_state').length;

suite.push(() => check('an account with only Multiview gets the page without a tab bar, read once', async () => {
  const w = world({state: PAGE_STATE(['multiview'])});
  await settle();
  assert(w.byId.get('panel_multiview').hidden === false && w.byId.get('panel_av').hidden === true);
  assert(w.byId.get('tabs').hidden === true, 'one feature needs no navigation');
  eq(mvReads(w), 1, 'opened once, read once');
  eq(byClass(w.byId.get('mv_host'), 'mv-card').length, 3);
  assert(w.byId.get('action_bar').hidden === true, 'no routing bar on this page');
}));

suite.push(() => check('an account without the capability is offered no tab and makes no Multiview request', async () => {
  const w = world({state: PAGE_STATE(['av', 'usb'])});
  await settle();
  eq(w.byId.get('tabs').children.map((b) => b.textContent), ['A/V', 'USB']);
  eq(mvReads(w), 0);
  assert(w.byId.get('panel_multiview').hidden === true);
}));

suite.push(() => check('with A/V as well it is a second tab that reads when it is opened', async () => {
  const w = world({state: PAGE_STATE(['av', 'multiview'])});
  await settle();
  eq(w.byId.get('tabs').children.map((b) => b.textContent), ['A/V', 'Multiview']);
  eq(mvReads(w), 0, 'not read until the tab is opened');
  await w.byId.get('tabs').children.find((b) => b.textContent === 'Multiview').fire('click');
  await settle();
  eq(mvReads(w), 1);
  assert(w.byId.get('panel_multiview').hidden === false && w.byId.get('panel_av').hidden === true);
}));

suite.push(() => check('a real click on Show dispatches POST /api/user/multiview_show with only the layout id', async () => {
  const w = world({state: PAGE_STATE(['multiview'])});
  await settle();
  const button = byClass(w.byId.get('mv_host'), 'mv-card').find((c) => c.getAttribute('data-layout') === L2)
    .children.find((n) => n.className.includes('mv-show'));
  await button.fire('click');
  await settle();
  const posts = w.calls.filter((c) => c.url === '/api/user/multiview_show');
  eq(posts.length, 1);
  eq(posts[0].method, 'POST');
  eq(posts[0].body, {layout: L2});
  eq(posts[0].csrf, 'tok', 'the session token travels with it');
  eq(mvReads(w), 2, 'one read on opening and one after the show');
}));

suite.push(() => check('a refused show in the real page puts the server sentence on the card', async () => {
  const w = world({state: PAGE_STATE(['multiview']), show: () => ({status: 409,
    body: {ok: false, reason: 'not_showable', error: 'That layout cannot be shown right now.'}})});
  await settle();
  const card = byClass(w.byId.get('mv_host'), 'mv-card').find((c) => c.getAttribute('data-layout') === L1);
  await card.children.find((n) => n.className.includes('mv-show')).fire('click');
  await settle();
  assert(/That layout cannot be shown right now\./.test(w.byId.get('mv_host').textContent));
  eq(w.toasts[w.toasts.length - 1], ['That layout cannot be shown right now.', 'error']);
}));

suite.push(() => check('opening the tab again by a deliberate click reads at most once', async () => {
  const w = world({state: PAGE_STATE(['av', 'multiview'])});
  await settle();
  const tab = w.byId.get('tabs').children.find((b) => b.textContent === 'Multiview');
  await tab.fire('click');
  await settle();
  const reads = mvReads(w);
  await w.byId.get('tabs').children.find((b) => b.textContent === 'Multiview').fire('click');
  await settle();
  assert(mvReads(w) <= reads + 1, 'a deliberate click is at most one read');
}));

suite.push(() => check('the shell re-reading itself after a permission change does not re-ask every display', async () => {
  const w = world({state: PAGE_STATE(['av', 'multiview'])});
  await settle();
  await w.byId.get('tabs').children.find((b) => b.textContent === 'Multiview').fire('click');
  await settle();
  const reads = mvReads(w);
  const stateReads = () => w.calls.filter((c) => c.url === '/api/user/state').length;
  const before = stateReads();
  w.live.revision = 'r2';                       // an administrator changed something
  for (const fn of w.winListeners.focus || []) await fn();
  await settle();
  assert(stateReads() > before, 'the precondition: the shell did read its state again');
  eq(mvReads(w), reads, 'and did not read every display again');
}));

// -------------------------------------------------- pinned against the server
suite.push(() => check('the endpoint paths are the ones the server serves, to the roles that may use them', () => {
  const user = strip(USER_JS);
  const paths = [...user.matchAll(/'(\/api\/user\/multiview_[a-z]+)'/g)].map((m) => m[1]).sort();
  eq(paths, ['/api/user/multiview_compose', '/api/user/multiview_layout', '/api/user/multiview_show', '/api/user/multiview_state']);
  paths.forEach((p) => {
    const row = new RegExp('"' + p + '":\\s*\\(Access\\.USER,\\s*FEATURE_MULTIVIEW\\)');
    assert(row.test(ACCESS_PY), p + ' is not a USER route behind the multiview feature in omni_access.ROUTES');
  });
  assert(/FEATURE_MULTIVIEW\s*=\s*"multiview"/.test(ACCESS_PY), 'the feature id the tab is keyed on');
  assert(/\['multiview',\s*'Multiview'\]/.test(user), 'the shell keys its tab on that same id');
}));

suite.push(() => check('the shell file holds no Multiview rule of its own beyond routing the two requests', () => {
  const user = strip(USER_JS);
  assert(!/layout_label|windows|canvas/.test(user), 'drawing belongs to the module');
}));

(async function run() {
  // The suites share a rendered DOM and a request log, so they run one after another.
  for (const test of suite) await test();
  results.forEach(([flag, line]) => console.log(`${flag} ${line}`));
  const passed = results.filter((r) => r[0] === 'ok  ').length;
  console.log(`\n${passed}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
})();

// Routing latency as the restricted page experiences it. Not a test: run it directly.
//
//   node tests/perf_ui_routing.js                       # the page in this tree
//   OMNI_USER_JS=... OMNI_MATRIX_JS=... node tests/perf_ui_routing.js   # another build's scripts
//
// The server is a scripted fetch with MODELLED latencies, because what is being measured
// is what the PAGE adds around them: ROUTE_MS for a verified route answer, STATE_MS for
// the read of the whole page, MATRIX_MS for the read of the grid (all overridable). The
// harness behaves like a person: it picks the next source and display and presses Route
// again as soon as the page will take it, retrying every 10 ms, and records when a second
// request actually leaves. No claim is made about hardware time.
//
// Per scenario it reports:
//   submit_ms   click until the request left the page
//   accept_ms   click until the page took (sent) the NEXT route
//   verified_ms click until the route's verified answer reached the page
//   state_ms    click until the page's own state refresh finished and was drawn
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {El} = require('./_fakedom');

const ROOT = path.join(__dirname, '..');
const readFile = (p) => fs.readFileSync(p, 'utf8').split('\r\n').join('\n');
const read = (...p) => readFile(path.join(ROOT, ...p));
const HTML = read('ui', 'user', 'index.html');
const USER_JS = process.env.OMNI_USER_JS ? readFile(process.env.OMNI_USER_JS) : read('ui', 'user', 'user.js');
const MATRIX_JS = process.env.OMNI_MATRIX_JS ? readFile(process.env.OMNI_MATRIX_JS) : read('ui', 'user', 'matrix.js');
const PREVIEW_JS = read('ui', 'user', 'preview-refresh.js');
const ROUTE_MS = Number(process.env.OMNI_PERF_ROUTE_MS || 500);
const STATE_MS = Number(process.env.OMNI_PERF_STATE_MS || 200);
const MATRIX_MS = Number(process.env.OMNI_PERF_MATRIX_MS || 200);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function pageState(interfaces) {
  return {
    ok: true, role: 'user', username: 'teacher', features: ['av'], interfaces,
    sources: [{id: 'src1', name: 'Teacher PC', online: true}, {id: 'src2', name: 'Camera', online: true}],
    destinations: [1, 2, 3, 4, 5, 6].map((n) => ({id: 'dst' + n, name: 'Display ' + n, online: true, showing: {mode: 'none'}})),
    groups: [{id: 'grp_a', name: 'Room A', displays: 2}, {id: 'grp_b', name: 'Room B', displays: 2}],
  };
}

function plane() { return {mode: 'none'}; }
function matrixData() {
  return {ok: true, interfaces: ['matrix'], revision: 'r1',
    sources: [{id: 'src1', name: 'Teacher PC', online: true}, {id: 'src2', name: 'Camera', online: true}],
    rows: [1, 2, 3, 4, 5, 6].map((n) => ({kind: 'display', id: 'dst' + n, name: 'Display ' + n, online: true,
                                          video: plane(), audio: plane()}))};
}

function world(interfaces) {
  const log = {posts: [], answers: [], stateDone: []};
  const byId = new Map();
  [...HTML.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)].forEach((m) => {
    const n = new El('div'); n.id = m[1];
    n.hidden = /\shidden(\s|>|=|\/)/.test(m[0]);
    byId.set(m[1], n);
  });
  byId.get('matrix_scroll').appendChild(byId.get('matrix_grid'));
  const listeners = {};
  const document = {
    getElementById: (id) => byId.get(id) || null,
    querySelectorAll: (sel) => {
      const root = new El('div');
      root.children = [...byId.get('sources').children, ...byId.get('destinations').children];
      return root.querySelectorAll(sel);
    },
    createElement: (tag) => new El(tag),
    createTextNode: (t) => Object.assign(new El('#text'), {_text: String(t)}),
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    hidden: false, body: new El('body'),
  };
  const remembered = {};
  const sandbox = {
    document,
    window: {toast() {}, omniConfirm: async () => true, addEventListener() {},
             setInterval() { return 0; }, clearInterval() {}, setTimeout, clearTimeout,
             omniRemember: {get: (k) => (k in remembered ? remembered[k] : null),
                            set: (k, v) => { if (v) remembered[k] = String(v); else delete remembered[k]; }}},
    location: {pathname: '/user', replace() {}, reload() {}},
    sessionStorage: {getItem: () => 'tok', setItem() {}, removeItem() {}},
    fetch: async (url, opts) => {
      const method = (opts && opts.method) || 'GET';
      const body = opts && opts.body ? JSON.parse(opts.body) : null;
      let out = {body: {ok: true}};
      if (url === '/api/user/state') {
        await sleep(STATE_MS); out = {body: pageState(interfaces)};
        log.stateDone.push(Date.now());
      } else if (url === '/api/user/revision') out = {body: {ok: true, revision: ''}};
      else if (url === '/api/auth/session') out = {body: {csrf_token: 'tok', method: 'password'}};
      else if (url === '/api/user/matrix_state') {
        await sleep(MATRIX_MS); out = {body: matrixData()};
        log.stateDone.push(Date.now());
      } else if (/^\/api\/user\/route/.test(url)) {
        log.posts.push({at: Date.now(), url, body});
        await sleep(ROUTE_MS);
        out = {body: {ok: true, status: 'VERIFIED', routed: 2, total: 2, results: []}};
        log.answers.push(Date.now());
      }
      return {ok: true, status: 200, headers: {get: () => 'application/json'}, json: async () => out.body || {}};
    },
    URL: {createObjectURL() { return ''; }, revokeObjectURL() {}},
    Image: function () {},
    Date, JSON, Object, Array, Set, Map, String, Number, Promise, console, encodeURIComponent,
    setTimeout, clearTimeout, setInterval() { return 0; },
  };
  vm.createContext(sandbox);
  vm.runInContext(PREVIEW_JS, sandbox);
  vm.runInContext(MATRIX_JS, sandbox);
  vm.runInContext(USER_JS, sandbox);
  const click = (node) => { for (const fn of listeners.click || []) fn({target: node, preventDefault() {}}); };
  const cards = () => [...byId.get('sources').children, ...byId.get('destinations').children];
  const card = (kind, id) => cards().find((c) => c.dataset.kind === kind && c.dataset.id === id);
  return {byId, log, click, card, listeners};
}

async function ready(w) { await sleep(STATE_MS + 150); }

// Press Route for source -> target the way a person would: choose, press, and if the page
// has not sent a request within 10 ms, choose and press again.
async function route(w, source, kind, id, timeoutMs = 20000) {
  const before = w.log.posts.length;
  const began = Date.now();
  while (Date.now() - began < timeoutMs) {
    // Choose only what is not already chosen: tapping the chosen card again clears it.
    [['source', source], [kind, id]].forEach(([k, i]) => {
      const c = w.card(k, i);
      if (c && c.getAttribute('aria-pressed') !== 'true') w.click(c);
    });
    w.byId.get('route_btn').fire('click');
    await sleep(10);
    if (w.log.posts.length > before) return Date.now();
  }
  throw new Error('the page never took the route to ' + id);
}

async function matrixRoute(w, source, rowId, timeoutMs = 20000) {
  const before = w.log.posts.length;
  const began = Date.now();
  while (Date.now() - began < timeoutMs) {
    const cell = w.byId.get('matrix_grid').querySelectorAll('.mx-cell')
      .find((c) => c.dataset.source === source && c.dataset.row === rowId);
    if (cell && !cell.disabled) w.byId.get('matrix_scroll').fire('click', {target: cell});
    await sleep(10);
    if (w.log.posts.length > before) return Date.now();
  }
  throw new Error('the matrix never took the route to ' + rowId);
}

async function scenario(name, interfaces, run) {
  const w = world(interfaces);
  await ready(w);
  const stateBefore = w.log.stateDone.length;
  const t0 = Date.now();
  const sent = await run(w, t0);
  // Wait for every route to be verified and for the page to have been read after the last one.
  const waitUntil = Date.now() + 15000;
  while (Date.now() < waitUntil) {
    if (w.log.answers.length >= w.log.posts.length && w.log.stateDone.length > stateBefore
        && Date.now() - Math.max(...w.log.answers) > STATE_MS + 800) break;
    await sleep(25);
  }
  const lastAnswer = Math.max(...w.log.answers);
  const stateAfter = w.log.stateDone.filter((t) => t >= lastAnswer);
  return {
    scenario: name,
    requests: w.log.posts.length,
    first_submit_ms: w.log.posts[0].at - t0,
    last_submit_ms: Math.max(...w.log.posts.map((p) => p.at)) - t0,
    second_accepted_ms: w.log.posts.length > 1 ? w.log.posts[1].at - t0 : null,
    all_verified_ms: lastAnswer - t0,
    state_refreshes: w.log.stateDone.length - stateBefore,
    verified_state_drawn_ms: stateAfter.length ? stateAfter[0] - t0 : null,
    sent,
  };
}

(async () => {
  const out = [];
  out.push(await scenario('single route', ['standard'], async (w) => { await route(w, 'src1', 'destination', 'dst1'); return 1; }));
  out.push(await scenario('five consecutive routes', ['standard'], async (w) => {
    for (const n of [1, 2, 3, 4, 5]) await route(w, n % 2 ? 'src1' : 'src2', 'destination', 'dst' + n);
    return 5;
  }));
  out.push(await scenario('one source to 3 displays (3 routes)', ['standard'], async (w) => {
    for (const n of [1, 2, 3]) await route(w, 'src1', 'destination', 'dst' + n);
    return 3;
  }));
  out.push(await scenario('one source to 2 groups (2 routes)', ['standard'], async (w) => {
    for (const g of ['grp_a', 'grp_b']) await route(w, 'src1', 'group', g);
    return 2;
  }));
  if (/multi_toggle/.test(USER_JS)) {
    out.push(await scenario('Select several: 3 displays, one action', ['standard'], async (w) => {
      w.click(w.card('source', 'src1'));
      w.byId.get('multi_toggle').fire('click');
      ['dst1', 'dst2', 'dst3'].forEach((id) => w.click(w.card('destination', id)));
      w.byId.get('route_btn').fire('click');
      await sleep(20);
      return 1;
    }));
    out.push(await scenario('Select several: 2 groups, one action', ['standard'], async (w) => {
      w.click(w.card('source', 'src1'));
      w.byId.get('multi_toggle').fire('click');
      ['grp_a', 'grp_b'].forEach((id) => w.click(w.card('group', id)));
      w.byId.get('route_btn').fire('click');
      await sleep(20);
      return 1;
    }));
    out.push(await scenario('Select several: 2 displays + 1 group, one action', ['standard'], async (w) => {
      w.click(w.card('source', 'src1'));
      w.byId.get('multi_toggle').fire('click');
      w.click(w.card('destination', 'dst1')); w.click(w.card('destination', 'dst2')); w.click(w.card('group', 'grp_a'));
      w.byId.get('route_btn').fire('click');
      await sleep(20);
      return 1;
    }));
  }
  out.push(await scenario('Matrix: five consecutive routes (different displays)', ['matrix'], async (w) => {
    for (const n of [1, 2, 3, 4, 5]) await matrixRoute(w, n % 2 ? 'src1' : 'src2', 'dst' + n);
    return 5;
  }));
  const rows = out.map((r) => ({scenario: r.scenario, requests: r.requests,
    second_accepted_ms: r.second_accepted_ms, last_submit_ms: r.last_submit_ms,
    all_verified_ms: r.all_verified_ms, state_refreshes: r.state_refreshes,
    verified_state_drawn_ms: r.verified_state_drawn_ms}));
  console.log('modelled latencies: route=%dms state=%dms matrix=%dms', ROUTE_MS, STATE_MS, MATRIX_MS);
  console.log(JSON.stringify(rows, null, 1));
  process.exit(0);
})().catch((err) => { console.error(err); process.exit(1); });

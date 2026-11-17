// Routing does not hold the page: executed against a scripted server.
//
// The reported defect was that a Standard or Matrix user could route one thing at a
// time -- the page waited for the route, then for a full re-read of the whole page, before
// it would take the next. These drive the real script against a fetch whose answers are
// held open on request, so "the second route went out while the first was still being
// verified" is a measurement of requests and not a claim about source text.
//
// Also here: "Select several" sends one request naming every display and group, a failed
// route is kept on its card instead of being lost to the next toast, and a verified
// success does not make the page read everything again once per route.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {El} = require('./_fakedom');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const HTML = read('ui', 'user', 'index.html');
const JS = process.env.OMNI_USER_JS ? fs.readFileSync(process.env.OMNI_USER_JS, 'utf8').split('\r\n').join('\n')
                                    : read('ui', 'user', 'user.js');
const PREVIEW_JS = read('ui', 'user', 'preview-refresh.js');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]); });
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }

const STATE = () => ({
  ok: true, role: 'user', username: 'teacher', features: ['av'],
  sources: [{id: 'src1', name: 'Teacher PC', online: true}, {id: 'src2', name: 'Camera', online: true}],
  destinations: [
    {id: 'dst1', name: 'Front', online: true, showing: {mode: 'none'}},
    {id: 'dst2', name: 'Side', online: true, showing: {mode: 'none'}},
    {id: 'dst3', name: 'Back', online: true, showing: {mode: 'none'}},
  ],
  groups: [{id: 'grp_a', name: 'Rooms', displays: 2}],
});

function world(options) {
  options = options || {};
  const state = STATE();
  const byId = new Map();
  [...HTML.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)].forEach((m) => {
    const n = new El('div'); n.id = m[1];
    n.hidden = /\shidden(\s|>|=|\/)/.test(m[0]);
    byId.set(m[1], n);
  });
  const listeners = {click: []};
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
    body: new El('body'),
  };
  const calls = [];
  const toasts = [];
  const holds = [];            // routes held open: {url, body, release(answer)}
  const sandbox = {
    document,
    window: {toast: (m, kind) => toasts.push([m, kind]), omniConfirm: async () => true,
             addEventListener() {}, setInterval() { return 0; }, clearInterval() {},
             setTimeout, clearTimeout},
    location: {pathname: '/user', replace() {}, reload() {}},
    sessionStorage: {getItem: () => 'tok', setItem() {}, removeItem() {}},
    fetch: async (url, opts) => {
      const call = {url, method: (opts && opts.method) || 'GET', at: Date.now(),
                    body: opts && opts.body ? JSON.parse(opts.body) : null};
      calls.push(call);
      let out = {body: {ok: true}};
      if (url === '/api/user/state') out = {body: state};
      else if (url === '/api/user/revision') out = {body: {ok: true, revision: ''}};
      else if (url === '/api/auth/session') out = {body: {csrf_token: 'tok', method: 'password'}};
      else if (/^\/api\/user\/route/.test(url)) {
        if (options.hold) {
          out = await new Promise((resolve) => holds.push({url, body: call.body, release: resolve}));
        } else {
          out = options.answer ? options.answer(call) : {body: {ok: true, status: 'VERIFIED'}};
        }
      }
      return {ok: (out.status || 200) < 400, status: out.status || 200,
              headers: {get: () => 'application/json'}, json: async () => out.body || {}};
    },
    URL: {createObjectURL() { return ''; }, revokeObjectURL() {}},
    Image: function () {},
    Date, JSON, Object, Array, Set, Map, String, Number, Promise, console, encodeURIComponent,
    setTimeout, clearTimeout, setInterval() { return 0; },
  };
  vm.createContext(sandbox);
  vm.runInContext(PREVIEW_JS, sandbox);
  vm.runInContext(JS, sandbox);
  const click = async (node) => {
    for (const fn of listeners.click) await fn({target: node, preventDefault() {}});
  };
  const cardsOf = () => [...byId.get('sources').children, ...byId.get('destinations').children];
  const card = (kind, id) => cardsOf().find((c) => c.dataset.kind === kind && c.dataset.id === id);
  return {byId, calls, toasts, holds, click, card, state, document};
}
const settle = () => new Promise((r) => setTimeout(r, 40));
const settleRoute = () => new Promise((r) => setTimeout(r, 600));
const posts = (w) => w.calls.filter((c) => c.method === 'POST');
const stateReads = (w) => w.calls.filter((c) => c.url === '/api/user/state').length;

const suite = [];

suite.push(() => check('a second route is sent while the first is still being verified', async () => {
  const w = world({hold: true});
  await settle();
  await w.click(w.card('source', 'src1'));
  await w.click(w.card('destination', 'dst1'));
  const first = w.byId.get('route_btn').fire('click');
  await settle();
  assert(posts(w).length === 1, 'the first route is out: ' + posts(w).length);
  // Nothing has answered yet. The page must take the next choice and send it.
  await w.click(w.card('source', 'src2'));
  await w.click(w.card('destination', 'dst2'));
  assert(w.byId.get('route_btn').disabled === false,
    'Route must be available while another route is being verified');
  const second = w.byId.get('route_btn').fire('click');
  await settle();
  assert(posts(w).length === 2, 'the second route waited for the first: ' + posts(w).length);
  assert(posts(w)[0].body.destination === 'dst1' && posts(w)[1].body.destination === 'dst2');
  w.holds.forEach((h) => h.release({body: {ok: true, status: 'VERIFIED'}}));
  await first; await second;
  await settleRoute();
}));

suite.push(() => check('the display being routed says so, and only that display', async () => {
  const w = world({hold: true});
  await settle();
  await w.click(w.card('source', 'src1'));
  await w.click(w.card('destination', 'dst1'));
  w.byId.get('route_btn').fire('click');
  await settle();
  const front = w.card('destination', 'dst1');
  const side = w.card('destination', 'dst2');
  assert(front.classList.contains('routing') && /Routing/.test(front.textContent),
    'the display in flight is marked: ' + front.className);
  assert(!side.classList.contains('routing'), 'a different display is untouched');
  assert(front.getAttribute('aria-busy') === 'true', 'announced as well as painted');
  const before = posts(w).length;
  await w.click(front);
  assert(posts(w).length === before, 'a tap on the display in flight sends nothing');
  w.holds.forEach((h) => h.release({body: {ok: true}}));
  await settleRoute();
  assert(!w.card('destination', 'dst1').classList.contains('routing'), 'cleared once settled');
}));

suite.push(() => check('five consecutive routes cause one read of the page, not five', async () => {
  const w = world({});
  await settle();
  const baseline = stateReads(w);
  await w.click(w.card('source', 'src1'));
  for (const id of ['dst1', 'dst2', 'dst3', 'dst1', 'dst2']) {
    await w.click(w.card('destination', id));
    await w.byId.get('route_btn').fire('click');
  }
  await settleRoute();
  assert(posts(w).length === 5, 'five routes: ' + posts(w).length);
  assert(stateReads(w) - baseline === 1,
    'the page was read ' + (stateReads(w) - baseline) + ' times after five routes');
}));

suite.push(() => check('the page is not read while any route is still in flight', async () => {
  const w = world({hold: true});
  await settle();
  const baseline = stateReads(w);
  await w.click(w.card('source', 'src1'));
  await w.click(w.card('destination', 'dst1'));
  const first = w.byId.get('route_btn').fire('click');
  await w.click(w.card('destination', 'dst2'));
  const second = w.byId.get('route_btn').fire('click');
  await settle();
  w.holds[0].release({body: {ok: true}});          // the first is verified; the second is not
  await settleRoute();
  assert(stateReads(w) === baseline, 'the page was rebuilt underneath a route still in flight');
  w.holds[1].release({body: {ok: true}});
  await first; await second;
  await settleRoute();
  assert(stateReads(w) - baseline === 1, 'one read once everything has settled');
}));

suite.push(() => check('a failed route stays on its card and is not lost to a later success', async () => {
  const w = world({answer: (call) => call.body.destination === 'dst1'
    ? {body: {ok: false, status: 'FAILED', error: 'The display did not confirm the video subscription.'}}
    : {body: {ok: true, status: 'VERIFIED'}}});
  await settle();
  await w.click(w.card('source', 'src1'));
  await w.click(w.card('destination', 'dst1'));
  await w.byId.get('route_btn').fire('click');
  await w.click(w.card('destination', 'dst2'));
  await w.byId.get('route_btn').fire('click');
  await settleRoute();
  const front = w.card('destination', 'dst1');
  assert(front.classList.contains('route-failed'), 'the failure is on the card: ' + front.className);
  assert(/Not routed/.test(front.textContent), front.textContent);
  assert(!w.card('destination', 'dst2').classList.contains('route-failed'));
  // The last toast was the success; the failure is still visible.
  assert(w.toasts[w.toasts.length - 1][1] === 'ok');
}));

suite.push(() => check('a verified failure is not drawn while the route is only pending', async () => {
  const w = world({hold: true});
  await settle();
  await w.click(w.card('source', 'src1'));
  await w.click(w.card('destination', 'dst1'));
  w.byId.get('route_btn').fire('click');
  await settle();
  const front = w.card('destination', 'dst1');
  assert(front.classList.contains('routing') && !front.classList.contains('route-failed'),
    'pending must not look like failure: ' + front.className);
  w.holds[0].release({body: {ok: false, error: 'x'}});
  await settle();
  assert(front.classList.contains('route-failed') && !front.classList.contains('routing'),
    'the verified failure replaces pending: ' + front.className);
  await settleRoute();
}));

suite.push(() => check('Select several sends one request naming every display and group', async () => {
  const w = world({});
  await settle();
  await w.click(w.card('source', 'src1'));
  await w.byId.get('multi_toggle').fire('click');
  await w.click(w.card('destination', 'dst1'));
  await w.click(w.card('destination', 'dst2'));
  await w.click(w.card('group', 'grp_a'));
  assert(w.card('destination', 'dst1').getAttribute('aria-pressed') === 'true');
  assert(w.card('group', 'grp_a').getAttribute('aria-pressed') === 'true',
    'choosing a group must not clear the displays');
  assert(/2 displays and 1 group/.test(w.byId.get('summary').textContent), w.byId.get('summary').textContent);
  await w.byId.get('route_btn').fire('click');
  await settleRoute();
  assert(posts(w).length === 1, 'one request: ' + posts(w).length);
  const sent = posts(w)[0];
  assert(sent.url === '/api/user/route_many', sent.url);
  assert(JSON.stringify(sent.body) === JSON.stringify({
    source: 'src1', destinations: ['dst1', 'dst2'], groups: ['grp_a'], mode: 'av', interface: 'standard'}),
    JSON.stringify(sent.body));
  // The selection is free for the next route at once.
  assert(w.card('destination', 'dst1').getAttribute('aria-pressed') === 'false');
}));

suite.push(() => check('Select several reports N of M and names what did not route', async () => {
  const w = world({answer: () => ({body: {ok: false, routed: 2, total: 3,
    results: [{ok: true, name: 'Front'}, {ok: false, name: 'Side'}, {ok: true, name: 'Rooms A'}]}})});
  await settle();
  await w.click(w.card('source', 'src1'));
  await w.byId.get('multi_toggle').fire('click');
  await w.click(w.card('destination', 'dst1'));
  await w.click(w.card('destination', 'dst2'));
  await w.byId.get('route_btn').fire('click');
  await settleRoute();
  assert(w.toasts.some((t) => /2 of 3 displays routed.*Not routed: Side/.test(t[0]) && t[1] === 'error'),
    JSON.stringify(w.toasts));
}));

suite.push(() => check('one display is still /api/user/route and one group still /api/user/route_group', async () => {
  const w = world({});
  await settle();
  await w.click(w.card('source', 'src1'));
  await w.click(w.card('destination', 'dst1'));
  await w.byId.get('route_btn').fire('click');
  await w.click(w.card('group', 'grp_a'));
  await w.byId.get('route_btn').fire('click');
  await settleRoute();
  assert(posts(w).map((p) => p.url).join() === '/api/user/route,/api/user/route_group',
    posts(w).map((p) => p.url).join());
}));

suite.push(() => check('turning Select several off returns to choosing one', async () => {
  const w = world({});
  await settle();
  await w.click(w.card('source', 'src1'));
  await w.byId.get('multi_toggle').fire('click');
  await w.click(w.card('destination', 'dst1'));
  await w.click(w.card('destination', 'dst2'));
  await w.byId.get('multi_toggle').fire('click');
  assert(w.byId.get('multi_toggle').getAttribute('aria-pressed') === 'false');
  await w.click(w.card('destination', 'dst3'));
  assert(w.card('destination', 'dst3').getAttribute('aria-pressed') === 'true');
  assert(w.card('destination', 'dst1').getAttribute('aria-pressed') === 'false');
  await w.byId.get('route_btn').fire('click');
  await settleRoute();
  assert(posts(w).length === 1 && posts(w)[0].url === '/api/user/route' && posts(w)[0].body.destination === 'dst3');
}));

(async () => {
  for (const run of suite) await run();
  results.forEach(([status, line]) => console.log(status + ' ' + line));
  console.log(`\n${results.length - failures}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
})();

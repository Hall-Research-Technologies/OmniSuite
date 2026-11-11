// The restricted shell with Display Groups: executed against a scripted server.
//
// A group card sits in the same selection area as the displays, choosing one
// clears the other, and Route sends exactly one request -- to the group endpoint
// with the source and the group -- then reports "N of M displays routed". No
// member is ever named by address, and Multiview has no tab for a User.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {El} = require('./_fakedom');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const HTML = read('ui', 'user', 'index.html');
const JS = read('ui', 'user', 'user.js');
const PREVIEW_JS = read('ui', 'user', 'preview-refresh.js');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]); });
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }

const STATE = {
  ok: true, role: 'user', username: 'teacher', features: ['av'],
  sources: [{id: 'src1', name: 'Teacher PC', online: true}, {id: 'src2', name: 'Document Camera', online: true}],
  destinations: [{id: 'dst1', name: 'Front Display', online: true, showing: {mode: 'none'}}],
  groups: [{id: 'grp_aaaa', name: 'Front rooms', displays: 4}],
};

function world(state, routeAnswer, live) {
  live = live || {state, revision: (state && state.revision) || ''};
  const byId = new Map();
  // Each element starts as the markup has it, `hidden` included: a page that never reveals a control it
  // ships hidden must be seen not to, which a model that starts everything visible cannot show.
  [...HTML.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)].forEach((m) => {
    const n = new El('div'); n.id = m[1];
    n.hidden = /\shidden(\s|>|=|\/)/.test(m[0]);
    byId.set(m[1], n);
  });
  const listeners = {click: []};
  const body = new El('body');
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
    body,
  };
  const calls = [];
  const toasts = [];
  const sandbox = {
    document,
    window: {toast: (m, kind) => toasts.push([m, kind]), omniConfirm: async () => true,
             addEventListener() {}, setInterval() { return 0; }, clearInterval() {},
             setTimeout, clearTimeout},
    location: {pathname: '/user', replace() {}, reload() {}},
    sessionStorage: {getItem: () => 'tok', setItem() {}, removeItem() {}},
    fetch: async (url, opts) => {
      const call = {url, method: (opts && opts.method) || 'GET',
                    body: opts && opts.body ? JSON.parse(opts.body) : null};
      calls.push(call);
      let out = {body: {ok: true}};
      if (url === '/api/user/state') out = {body: live.state};
      else if (url === '/api/user/revision') out = {body: {ok: true, revision: live.revision}};
      else if (url === '/api/auth/session') out = {body: {csrf_token: 'tok', method: 'password'}};
      else if (url === '/api/user/route_group') out = routeAnswer || {body: {ok: true, routed: 4, total: 4, results: []}};
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
  return {byId, calls, toasts, click, listeners, live, document};
}
const settle = () => new Promise((r) => setTimeout(r, 40));
const cards = (w, id) => w.byId.get(id).children;
const label = (card) => card.textContent;

const suite = [];

suite.push(() => check('a group card appears beside the displays, with its size', async () => {
  const w = world(STATE);
  await settle();
  const list = cards(w, 'destinations');
  assert(list.length === 2, 'one display and one group: ' + list.length);
  const group = list.find((c) => c.dataset.kind === 'group');
  assert(group, 'no group card');
  assert(/Front rooms/.test(label(group)) && /4 displays/.test(label(group)), label(group));
  assert(w.byId.get('destinations_empty').hidden === true);
}));

suite.push(() => check('a completed route asks for the pictures on screen again, once per source', async () => {
  const w = world(STATE);
  await settle();
  const pictures = () => w.calls.filter((c) => /\/api\/user\/source_preview/.test(c.url)).length;
  const before = pictures();
  assert(before === 2, 'the first load of each source card: ' + before);
  await w.click(cards(w, 'sources')[0]);
  await w.click(cards(w, 'destinations').find((c) => c.dataset.kind === 'group'));
  await w.byId.get('route_btn').fire('click');
  await settle();
  const after = pictures();
  assert(after - before === 2, 'the rebuilt cards load once each and the route adds nothing on top: ' + (after - before));
}));

suite.push(() => check('a group card carries no picture and no identity beyond its opaque id', async () => {
  const w = world(STATE);
  await settle();
  const group = cards(w, 'destinations').find((c) => c.dataset.kind === 'group');
  assert(!group.querySelector('.pick-shot'), 'a composite thumbnail would be invented');
  assert(group.dataset.id === 'grp_aaaa');
}));

suite.push(() => check('Route stays disabled until a source and a group or display are chosen', async () => {
  const w = world(STATE);
  await settle();
  assert(w.byId.get('route_btn').disabled === true);
  await w.click(cards(w, 'sources')[0]);
  assert(w.byId.get('route_btn').disabled === true);
  const group = cards(w, 'destinations').find((c) => c.dataset.kind === 'group');
  await w.click(group);
  assert(w.byId.get('route_btn').disabled === false);
  assert(/Front rooms/.test(w.byId.get('summary').textContent)
    && /4 displays/.test(w.byId.get('summary').textContent), w.byId.get('summary').textContent);
}));

suite.push(() => check('choosing a group clears a chosen display, and the reverse', async () => {
  const w = world(STATE);
  await settle();
  await w.click(cards(w, 'sources')[0]);
  const display = cards(w, 'destinations').find((c) => c.dataset.kind === 'destination');
  const group = cards(w, 'destinations').find((c) => c.dataset.kind === 'group');
  await w.click(display);
  assert(display.getAttribute('aria-pressed') === 'true');
  await w.click(group);
  assert(group.getAttribute('aria-pressed') === 'true' && display.getAttribute('aria-pressed') === 'false',
    'a route goes to one target');
  await w.click(display);
  assert(display.getAttribute('aria-pressed') === 'true' && group.getAttribute('aria-pressed') === 'false');
}));

suite.push(() => check('Route to a group makes exactly one request, to the group endpoint', async () => {
  const w = world(STATE);
  await settle();
  await w.click(cards(w, 'sources')[0]);
  await w.click(cards(w, 'destinations').find((c) => c.dataset.kind === 'group'));
  w.calls.length = 0;
  await w.byId.get('route_btn').fire('click');
  await settle();
  const posts = w.calls.filter((c) => c.method === 'POST');
  assert(posts.length === 1, JSON.stringify(posts));
  assert(posts[0].url === '/api/user/route_group', posts[0].url);
  assert(posts[0].body.source === 'src1' && posts[0].body.group === 'grp_aaaa', JSON.stringify(posts[0].body));
  assert(!('destination' in posts[0].body) && !('exit_multiview' in posts[0].body));
}));

suite.push(() => check('the aggregate result is reported as N of M displays routed', async () => {
  const w = world(STATE, {body: {ok: false, routed: 3, total: 4, group: 'Front rooms',
    results: [{name: 'A', ok: true}, {name: 'B', ok: true}, {name: 'C', ok: true},
              {name: 'Back Display', ok: false, result: 'failed'}]}});
  await settle();
  await w.click(cards(w, 'sources')[0]);
  await w.click(cards(w, 'destinations').find((c) => c.dataset.kind === 'group'));
  await w.byId.get('route_btn').fire('click');
  await settle();
  const [message, kind] = w.toasts[w.toasts.length - 1];
  assert(/3 of 4 displays routed/.test(message), message);
  assert(/Not routed: Back Display/.test(message), 'the displays that did not take it are named: ' + message);
  assert(kind === 'error', 'a partial result is not shown as a success');
}));

suite.push(() => check('a complete result is a success', async () => {
  const w = world(STATE);
  await settle();
  await w.click(cards(w, 'sources')[0]);
  await w.click(cards(w, 'destinations').find((c) => c.dataset.kind === 'group'));
  await w.byId.get('route_btn').fire('click');
  await settle();
  const [message, kind] = w.toasts[w.toasts.length - 1];
  assert(/4 of 4 displays routed/.test(message) && kind === 'ok', message + ' / ' + kind);
}));

suite.push(() => check('a refusal is worded, not shown as a count', async () => {
  const w = world(STATE, {status: 403, body: {ok: false, error: 'You do not have access to that display group.',
                                              reason: 'group_not_permitted'}});
  await settle();
  await w.click(cards(w, 'sources')[0]);
  await w.click(cards(w, 'destinations').find((c) => c.dataset.kind === 'group'));
  await w.byId.get('route_btn').fire('click');
  await settle();
  const [message, kind] = w.toasts[w.toasts.length - 1];
  assert(/display group/.test(message) && kind === 'error', message);
}));

suite.push(() => check('a single display still routes through the ordinary endpoint', async () => {
  const w = world(STATE);
  await settle();
  await w.click(cards(w, 'sources')[1]);
  await w.click(cards(w, 'destinations').find((c) => c.dataset.kind === 'destination'));
  w.calls.length = 0;
  await w.byId.get('route_btn').fire('click');
  await settle();
  const post = w.calls.find((c) => c.method === 'POST');
  assert(post.url === '/api/user/route' && post.body.destination === 'dst1' && post.body.mode === 'av');
}));

suite.push(() => check('Unroute is not offered for a group', async () => {
  const w = world(STATE);
  await settle();
  await w.click(cards(w, 'sources')[0]);
  await w.click(cards(w, 'destinations').find((c) => c.dataset.kind === 'group'));
  assert(w.byId.get('unroute_btn').hidden === true,
    'one tap must not stop a room full of displays');
}));

suite.push(() => check('a group the server stops listing is dropped from the selection', async () => {
  const w = world(STATE);
  await settle();
  await w.click(cards(w, 'sources')[0]);
  await w.click(cards(w, 'destinations').find((c) => c.dataset.kind === 'group'));
  // The next state has no groups: the selection must not outlive it.
  STATE.groups = [];
  try {
    await w.byId.get('route_btn').fire('click');
    await settle();
  } finally {
    STATE.groups = [{id: 'grp_aaaa', name: 'Front rooms', displays: 4}];
  }
  assert(w.byId.get('route_btn').disabled === true);
}));

suite.push(() => check('a User never gets a Multiview tab in this release', async () => {
  const w = world(Object.assign({}, STATE, {features: ['av', 'usb']}));
  await settle();
  const labels = cards(w, 'tabs').map((t) => t.textContent);
  assert(labels.join('|') === 'A/V|USB', labels.join('|'));
}));

suite.push(() => check('the Route bar is shown once the page has rendered, though the markup ships it hidden', async () => {
  assert(/<div class="action-bar" id="action_bar" hidden>/.test(HTML), 'the markup ships the bar hidden, which is what this guards');
  const w = world(STATE);
  assert(w.byId.get('action_bar').hidden === true, 'the model starts it as the markup has it');
  await settle();
  assert(w.byId.get('action_bar').hidden === false, 'a restricted account was never shown the Route button');
  // and it is not withdrawn by choosing things
  await w.click(cards(w, 'sources')[0]);
  assert(w.byId.get('action_bar').hidden === false);
}));

suite.push(() => check('Route is withheld, and says which unit, when a chosen unit goes offline', async () => {
  const live = {state: Object.assign({}, STATE, {revision: 'r1'}), revision: 'r1'};
  const w = world(live.state, null, live);
  await settle();
  await w.click(cards(w, 'sources')[0]);
  await w.click(cards(w, 'destinations').find((c) => c.dataset.kind === 'destination'));
  assert(w.byId.get('route_btn').disabled === false, 'a chosen source and display can be routed');
  // The source stops answering after it was chosen; the page learns it the way it learns anything else.
  live.state = Object.assign({}, STATE, {revision: 'r2',
    sources: [Object.assign({}, STATE.sources[0], {online: false}), STATE.sources[1]]});
  live.revision = 'r2';
  for (const fn of w.listeners.visibilitychange || []) await fn({});
  await settle();
  assert(w.byId.get('route_btn').disabled === true, 'Route stays enabled for a unit that is offline');
  assert(/Teacher PC is offline/.test(w.byId.get('summary').textContent), w.byId.get('summary').textContent);
  // And the display, the other end.
  live.state = Object.assign({}, STATE, {revision: 'r3', destinations: [Object.assign({}, STATE.destinations[0], {online: false})]});
  live.revision = 'r3';
  for (const fn of w.listeners.visibilitychange || []) await fn({});
  await settle();
  assert(w.byId.get('route_btn').disabled === true, 'Route stays enabled for a display that is offline');
}));

suite.push(() => check('a permission change reaches an open page without a reload', async () => {
  const live = {state: Object.assign({}, STATE, {revision: 'r1'}), revision: 'r1'};
  const w = world(live.state, null, live);
  await settle();
  await w.click(cards(w, 'sources')[0]);
  const before = w.calls.filter((c) => c.url === '/api/user/state').length;
  // Nothing changed: the digest matches, so the full state is not read again.
  for (const fn of w.listeners.visibilitychange || []) await fn({});
  await settle();
  assert(w.calls.filter((c) => c.url === '/api/user/state').length === before,
    'an unchanged digest caused a full refresh');
  assert(w.calls.some((c) => c.url === '/api/user/revision'), 'the digest was never asked for');
  // The administrator removes a source and the display; the digest moves.
  live.state = Object.assign({}, STATE, {revision: 'r2', sources: [STATE.sources[1]], destinations: []});
  live.revision = 'r2';
  for (const fn of w.listeners.visibilitychange || []) await fn({});
  await settle();
  assert(w.calls.filter((c) => c.url === '/api/user/state').length === before + 1,
    'a changed digest did not refresh');
  const names = cards(w, 'sources').map(label).join('|');
  assert(!/Teacher PC/.test(names) && /Document Camera/.test(names), names);
  assert(cards(w, 'destinations').filter((c) => c.dataset.kind !== 'group').length === 0,
    'a removed display is still offered');
  assert(w.byId.get('route_btn').disabled === true, 'Route stayed enabled with nothing selected');
}));

suite.push(() => check('a hidden page makes no permission request', async () => {
  const live = {state: Object.assign({}, STATE, {revision: 'r1'}), revision: 'r1'};
  const w = world(live.state, null, live);
  await settle();
  const before = w.calls.length;
  w.document.hidden = true;                  // the tab is in the background
  for (const fn of w.listeners.visibilitychange || []) await fn({});
  await settle();
  assert(w.calls.length === before, 'a hidden page still talked to the server');
}));

(async () => {
  for (const run of suite) await run();
  results.forEach(([s, l]) => console.log(`${s} ${l}`));
  console.log(`${suite.length - failures}/${suite.length} checks passed`);
  process.exit(failures ? 1 : 0);
})();

// The account editor's Multiview assignment, executed against a scripted server.
//
// The real ui/admin/users.js runs in a fake DOM built from the real ui/admin/users.html. What is measured: the
// capability is a checkbox like the other features, the layouts are read lazily and never polled, they are grouped
// under their display and given one at a time (there is no "all"), the exact body that is saved carries every
// layout the account holds, an assignment the displays no longer list is shown and kept until it is removed, and a
// layout on a display that cannot take it now is not newly assignable.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {El} = require('./_fakedom');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const HTML = read('ui', 'admin', 'users.html');
const JS = read('ui', 'admin', 'users.js');
const ACCESS_PY = read('omni_access.py');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]); });
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
function eq(a, b, m) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${m || 'not equal'}: ${JSON.stringify(a)} !== ${JSON.stringify(b)}`); }
const settle = () => new Promise((r) => setTimeout(r, 30));
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map((l) => l.replace(/^\s*\/\/.*$/, '')).join('\n');

// Synthetic identities only.
const D1 = '0a:1b:2c:d0:00:01', D2 = '0a:1b:2c:d0:00:02', D3 = '0a:1b:2c:d0:00:03';
const K1 = D1 + '/mv_00000000000000a1', K2 = D1 + '/mv_00000000000000a2';
const K3 = D2 + '/mv_00000000000000b1', K4 = D3 + '/mv_00000000000000c1';
const GONE = D1 + '/mv_00000000000000ff';

const CATALOGUE = [
  {id: D1, name: 'Front Display', eligible: true, status: 'ok', layouts: [
    {key: K1, name: 'Lesson', layout: 'main_3', layout_label: 'Main and three'},
    {key: K2, name: 'Assembly', layout: 'quad', layout_label: '2 x 2'}]},
  {id: D2, name: 'Back Display', eligible: false, status: 'unreachable', layouts: [
    {key: K3, name: 'Overflow', layout: 'quad', layout_label: '2 x 2'}]},
  {id: D3, name: 'Old Display', eligible: false, status: 'unsupported', layouts: [
    {key: K4, name: 'Legacy', layout: 'quad', layout_label: '2 x 2'}]},
];
const USERS = [
  {id: 'u1', username: 'root', role: 'admin', enabled: true, permissions: {features: [], sources: [], destinations: [], groups: []}},
  {id: 'u2', username: 'teacher', role: 'user', enabled: true,
   permissions: {features: ['av'], sources: ['s1'], destinations: ['d1'], groups: [], multiviews: []}},
  {id: 'u3', username: 'presenter', role: 'user', enabled: true,
   permissions: {features: ['multiview'], sources: [], destinations: [], groups: [], interfaces: ['standard'],
                 multiviews: [K1, K3, GONE]}},
];

function buildWorld(fetchImpl) {
  const byId = new Map();
  [...HTML.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)].forEach((m) => {
    const node = new El('div');
    node.id = m[1];
    node.hidden = /\shidden(\s|>|=|\/)/.test(m[0]);
    byId.set(m[1], node);
  });
  // The feature checkboxes come from the markup itself, so the page really offers the one under test.
  const features = [...HTML.matchAll(/<input type="checkbox" value="([a-z]+)" class="f-feature">/g)].map((m) => {
    const n = new El('input'); n.type = 'checkbox'; n.value = m[1]; n.className = 'f-feature'; return n;
  });
  const interfaces = ['standard', 'matrix'].map((v) => {
    const n = new El('input'); n.type = 'checkbox'; n.value = v; n.className = 'f-interface'; return n;
  });
  const tabsNav = byId.get('admin_tabs');
  const tabs = ['accounts', 'names', 'groups', 'audit'].map((p) => {
    const n = new El('button'); n.className = 'tab'; n.dataset.panel = p; tabsNav.appendChild(n); return n;
  });
  const document = {
    getElementById: (id) => byId.get(id) || null,
    querySelectorAll: (sel) => {
      if (sel === '.f-feature') return features;
      if (sel === '.f-interface') return interfaces;
      return [];
    },
    createElement: (tag) => new El(tag),
    createTextNode: (text) => Object.assign(new El('#text'), {_text: String(text)}),
    addEventListener() {},
    activeElement: null,
  };
  const calls = [], toasts = [];
  const sandbox = {
    document,
    window: {toast: (m, kind) => toasts.push([m, kind]), omniConfirm: async () => true},
    location: {pathname: '/admin/users', replace() {}},
    sessionStorage: {getItem: () => 'tok'},
    navigator: {clipboard: {writeText: async () => {}}},
    fetch: async (url, opts) => {
      const call = {url, method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null};
      calls.push(call);
      const out = await fetchImpl(call);
      return {status: out.status || 200, json: async () => out.body || {}};
    },
    URLSearchParams, JSON, Object, Array, Set, Map, Date, String, Number, Promise, console, encodeURIComponent,
    setTimeout, clearTimeout,
  };
  vm.createContext(sandbox);
  vm.runInContext(JS, sandbox);
  const feature = (value) => features.find((f) => f.value === value);
  return {byId, calls, toasts, features, feature, interfaces, sandbox};
}

function server(over) {
  over = over || {};
  return (call) => {
    if (over.handler) { const r = over.handler(call); if (r) return r; }
    if (call.url === '/api/admin/users' && call.method === 'GET') return {body: {ok: true, users: USERS}};
    if (call.url === '/api/admin/devices') return {body: {ok: true, devices: []}};
    if (call.url === '/api/admin/groups' && call.method === 'GET') return {body: {ok: true, groups: []}};
    if (call.url === '/api/admin/multiviews') return over.catalogue || {body: {ok: true, decoders: JSON.parse(JSON.stringify(CATALOGUE))}};
    if (call.url.indexOf('/direct_access') !== -1) return {body: {ok: true, direct_access: {enabled: false}}};
    return {body: {ok: true}};
  };
}

const all = (node, out) => { out = out || []; node.children.forEach((c) => { out.push(c); all(c, out); }); return out; };
const byClass = (root, cls) => all(root).filter((n) => n.className.split(' ').includes(cls));
const rowFor = (w, username) => w.byId.get('users_body').children.find((tr) => /./.test(tr.children[0].textContent)
  && tr.children[0].textContent.includes(username));
async function openEditor(w, username) {
  const row = rowFor(w, username);
  const edit = row.children[row.children.length - 1].children[0].children[0];
  await edit.fire('click');
  await settle();
}
const mvCalls = (w) => w.calls.filter((c) => c.url === '/api/admin/multiviews');
const boxes = (w) => all(w.byId.get('mv_list')).filter((n) => n.tagName === 'INPUT');
const boxFor = (w, key) => boxes(w).find((b) => b.value === key);
const perms = (w) => {
  const posts = w.calls.filter((c) => c.method === 'POST' && c.body && c.body.action === 'set_permissions');
  return posts.length ? posts[posts.length - 1].body.permissions : null;
};

const suite = [];

suite.push(() => check('Multiview is a capability checkbox beside A/V and USB, and the section starts hidden', async () => {
  const w = buildWorld(server());
  await settle();
  eq(w.features.map((f) => f.value), ['av', 'usb', 'multiview']);
  assert(/value="multiview" class="f-feature"> Multiview<\/label>/.test(HTML));
  assert(w.byId.get('mv_section').hidden === true);
  await openEditor(w, 'teacher');
  assert(w.byId.get('mv_section').hidden === true, 'an account without the capability shows no layouts');
  eq(mvCalls(w).length, 0, 'and reads nothing from the displays');
}));

suite.push(() => check('ticking the capability reads the layouts once, on demand, and lists them grouped by display', async () => {
  const w = buildWorld(server());
  await settle();
  eq(mvCalls(w).length, 0, 'the page itself does not read them');
  await openEditor(w, 'teacher');
  w.feature('multiview').checked = true;
  await w.feature('multiview').fire('change');
  assert(w.byId.get('mv_section').hidden === false);
  await settle();
  eq(mvCalls(w).length, 1);
  const heads = byClass(w.byId.get('mv_list'), 'assign-group-head').map((n) => n.textContent);
  eq(heads.map((h) => h.split(' · ')[0]), ['Front Display', 'Back Display', 'Old Display']);
  eq(boxes(w).map((b) => b.value), [K1, K2, K3, K4]);
  // Unticking and ticking again does not ask the displays a second time.
  w.feature('multiview').checked = false; await w.feature('multiview').fire('change');
  w.feature('multiview').checked = true; await w.feature('multiview').fire('change');
  await settle();
  eq(mvCalls(w).length, 1, 'once per opening of the editor');
}));

suite.push(() => check('while the displays are read it says so, and a failed read is not "no layouts"', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const w = buildWorld(server({catalogue: null, handler: (call) => (call.url === '/api/admin/multiviews'
    ? gate.then(() => ({status: 500, body: {ok: false}})) : null)}));
  await settle();
  await openEditor(w, 'presenter');
  assert(/Reading the displays/.test(w.byId.get('mv_list').textContent), w.byId.get('mv_list').textContent);
  release();
  await settle();
  const text = w.byId.get('mv_list').textContent;
  assert(/could not be loaded/.test(text) && !/No Multiview layouts have been saved/.test(text), text);
  assert(w.byId.get('mv_retry').hidden === false, 'there is a way to try again');
  // Saving after a failed read must not drop what the account already holds.
  await w.byId.get('editor_save').fire('click');
  await settle();
  eq(perms(w).multiviews, [K1, K3, GONE].sort(), 'layouts kept when the catalogue could not be read');
}));

suite.push(() => check('layouts are labelled by display and layout name; no identity is shown', async () => {
  const w = buildWorld(server());
  await settle();
  await openEditor(w, 'presenter');
  const text = w.byId.get('mv_list').textContent;
  assert(/Lesson/.test(text) && /Main and three/.test(text) && /Front Display/.test(text), text);
  assert(!/mv_[0-9a-f]{16}/.test(text), 'no layout id: ' + text);
  assert(!/[0-9a-f]{2}(:[0-9a-f]{2}){5}/i.test(text), 'no MAC: ' + text);
}));

suite.push(() => check('there is no "all" control: layouts are given one at a time', () => {
  const section = HTML.slice(HTML.indexOf('id="mv_section"'), HTML.indexOf('id="mv_list"')).replace(/\s+/g, ' ');
  assert(!/data-all|data-none/.test(section), 'no All / None buttons in the Multiview section');
  assert(/given separately from displays and sources/.test(section), 'the helper line');
  assert(/without the account being able to route to the displays or sources inside it/.test(section));
}));

suite.push(() => check('ticking a layout changes exactly what is saved, and the body carries every list', async () => {
  const w = buildWorld(server());
  await settle();
  await openEditor(w, 'presenter');
  const box = boxFor(w, K2);
  box.checked = true;
  await box.fire('change');
  assert(/4 selected/.test(w.byId.get('mv_count').textContent) || /\d+ selected/.test(w.byId.get('mv_count').textContent));
  w.calls.length = 0;
  await w.byId.get('editor_save').fire('click');
  await settle();
  const post = w.calls.find((c) => c.method === 'POST' && c.body && c.body.action === 'set_permissions');
  assert(post && post.url === '/api/admin/users/u3', JSON.stringify(post));
  eq(post.body, {action: 'set_permissions', permissions: {
    features: ['multiview'], sources: [], destinations: [], groups: [],
    multiviews: [K1, K2, K3, GONE].sort(), interfaces: ['standard']}});
}));

suite.push(() => check('unticking a layout removes just that layout from what is saved', async () => {
  const w = buildWorld(server());
  await settle();
  await openEditor(w, 'presenter');
  const box = boxFor(w, K1);
  box.checked = false;
  await box.fire('change');
  await w.byId.get('editor_save').fire('click');
  await settle();
  eq(perms(w).multiviews, [K3, GONE].sort());
}));

suite.push(() => check('creating an account sends the layouts chosen, in the create body', async () => {
  const w = buildWorld(server());
  await settle();
  await w.byId.get('new_user').fire('click');
  await settle();
  w.byId.get('f_username').value = 'newuser';
  w.byId.get('f_password').value = 'long enough pw';
  w.feature('multiview').checked = true;
  await w.feature('multiview').fire('change');
  await settle();
  const box = boxFor(w, K2);
  box.checked = true; await box.fire('change');
  w.calls.length = 0;
  await w.byId.get('editor_save').fire('click');
  await settle();
  const post = w.calls.find((c) => c.url === '/api/admin/users' && c.method === 'POST');
  assert(post, 'a create request');
  eq(post.body.permissions.features, ['multiview']);
  eq(post.body.permissions.multiviews, [K2]);
}));

suite.push(() => check('an assignment the displays no longer list is shown, kept on an unrelated save, and removable', async () => {
  const w = buildWorld(server());
  await settle();
  await openEditor(w, 'presenter');
  const lostHead = byClass(w.byId.get('mv_list'), 'assign-group-head').find((n) => /Unavailable/.test(n.textContent));
  assert(lostHead && /Unavailable — kept/.test(lostHead.textContent), 'shown as unavailable and kept');
  assert(!/mv_[0-9a-f]{16}|[0-9a-f]{2}(:[0-9a-f]{2}){5}/i.test(lostHead.parentNode ? lostHead.parentNode.textContent : ''), 'without its identity');
  // An unrelated change: the role label does not matter, only that the layouts survive a save.
  w.calls.length = 0;
  await w.byId.get('editor_save').fire('click');
  await settle();
  assert(perms(w).multiviews.includes(GONE), 'kept on an unrelated save: ' + JSON.stringify(perms(w)));
  // Open again and remove it.
  await openEditor(w, 'presenter');
  const remove = all(w.byId.get('mv_list')).find((n) => n.tagName === 'BUTTON' && n.textContent === 'Remove');
  assert(remove && /Remove unavailable layout/.test(remove.getAttribute('aria-label')), 'a Remove control with a name');
  await remove.fire('click');
  assert(!all(w.byId.get('mv_list')).some((n) => n.tagName === 'BUTTON' && n.textContent === 'Remove'), 'gone from the list');
  w.calls.length = 0;
  await w.byId.get('editor_save').fire('click');
  await settle();
  eq(perms(w).multiviews, [K1, K3].sort(), 'only the removed one is gone');
}));

suite.push(() => check('layouts on a display that cannot take them are not newly assignable, and say why in words', async () => {
  const w = buildWorld(server());
  await settle();
  await openEditor(w, 'presenter');
  // K3 is on a display that is not answering and is already held: it can be unticked. K4 is on a display
  // without Multiview and is not held: it cannot be ticked.
  assert(boxFor(w, K3).disabled === false && boxFor(w, K3).checked === true, 'a held layout stays removable');
  assert(boxFor(w, K4).disabled === true, 'an unheld layout on an ineligible display is not assignable');
  assert(boxFor(w, K1).disabled === false && boxFor(w, K2).disabled === false);
  const heads = byClass(w.byId.get('mv_list'), 'assign-group-head').map((n) => n.textContent).join(' | ');
  assert(/Back Display · Not answering right now/.test(heads), heads);
  assert(/Old Display · Multiview is not available on this display/.test(heads), heads);
  assert(!/unreachable|unsupported|eligible/.test(w.byId.get('mv_list').textContent), 'no internal state names');
}));

suite.push(() => check('a layout the display no longer offers is removed from the list but never dropped by saving', async () => {
  const w = buildWorld(server({catalogue: {body: {ok: true, decoders: [CATALOGUE[0]]}}}));
  await settle();
  await openEditor(w, 'presenter');
  await w.byId.get('editor_save').fire('click');
  await settle();
  eq(perms(w).multiviews, [K1, K3, GONE].sort());
}));

suite.push(() => check('a server refusal of a new assignment is shown in the editor, which stays open', async () => {
  const w = buildWorld(server({handler: (call) => (call.method === 'POST' && call.body && call.body.action === 'set_permissions'
    ? {status: 400, body: {ok: false, error: 'That display cannot be given Multiview layouts right now.'}} : null)}));
  await settle();
  await openEditor(w, 'presenter');
  await w.byId.get('editor_save').fire('click');
  await settle();
  const line = w.byId.get('editor_error');
  assert(/cannot be given Multiview layouts/.test(line.textContent) && line.style.display === 'block');
  assert(w.byId.get('editor_backdrop').style.display === 'flex', 'still open');
}));

suite.push(() => check('closing the editor before the answer arrives discards it', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  const w = buildWorld(server({handler: (call) => (call.url === '/api/admin/multiviews'
    ? gate.then(() => ({body: {ok: true, decoders: CATALOGUE}})) : null)}));
  await settle();
  await openEditor(w, 'presenter');
  await w.byId.get('editor_cancel').fire('click');
  release();
  await settle();
  assert(w.byId.get('mv_list').textContent.includes('Reading the displays'), 'the stale answer was not applied');
}));

suite.push(() => check('the account row names Multiview with its layout count', async () => {
  const w = buildWorld(server());
  await settle();
  const presenter = rowFor(w, 'presenter').children.map((td) => td.textContent).join(' | ');
  assert(/Multiview · 3 layouts/.test(presenter), presenter);
  assert(!/0 sources/.test(presenter), 'a Multiview-only account is not described by sources it cannot use: ' + presenter);
  const teacher = rowFor(w, 'teacher').children.map((td) => td.textContent).join(' | ');
  assert(/A\/V · 1 source, 1 display/.test(teacher) && !/Multiview/.test(teacher), teacher);
}));

suite.push(() => check('the endpoint path matches the server route table, as an administrator-only read', () => {
  const paths = [...strip(JS).matchAll(/'(\/api\/admin\/multiviews)'/g)].map((m) => m[1]);
  eq(paths, ['/api/admin/multiviews']);
  assert(/"\/api\/admin\/multiviews":\s*\(Access\.ADMIN,\s*None\)/.test(ACCESS_PY), 'the route is not admin-only in omni_access.ROUTES');
}));

suite.push(() => check('the editor script uses no native dialogs and puts no layout id in a title', () => {
  const code = strip(JS);
  assert(!/\b(?:alert|prompt)\s*\(/.test(code) && !/[^.\w]confirm\s*\(/.test(code), 'native dialog');
  assert(!/\.title\s*=|setAttribute\('title'/.test(code.slice(code.indexOf('Multiview layouts'), code.indexOf('display groups'))), 'native title tooltip');
}));

(async function run() {
  for (const test of suite) await test();
  results.forEach(([flag, line]) => console.log(`${flag} ${line}`));
  const passed = results.filter((r) => r[0] === 'ok  ').length;
  console.log(`\n${passed}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
})();

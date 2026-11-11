// The Remote Access administration page, executed against a scripted server.
//
// A syntax check cannot catch an undefined helper, and a source assertion cannot
// show that "Cancel makes no request". These load the real ui/admin/users.js
// into a small fake DOM built from the real ui/admin/users.html and drive it.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const HTML = read('ui', 'admin', 'users.html');
const JS = read('ui', 'admin', 'users.js');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]); });
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }

const {El} = require('./_fakedom');

function buildWorld(fetchImpl, options) {
  options = options || {};
  const byId = new Map();
  [...HTML.matchAll(/\bid="([^"]+)"/g)].forEach((m) => {
    const node = new El('div');
    node.id = m[1];
    byId.set(m[1], node);
  });
  // The pieces the script finds by class rather than by id.
  const features = ['av', 'usb'].map((v) => {
    const n = new El('input'); n.type = 'checkbox'; n.value = v; n.className = 'f-feature'; return n;
  });
  const interfaces = ['standard', 'matrix'].map((v) => {
    const n = new El('input'); n.type = 'checkbox'; n.value = v; n.className = 'f-interface'; return n;
  });
  const dataAll = ['sources', 'destinations', 'groups'].map((k) => {
    const n = new El('button'); n.dataset.all = k; return n;
  });
  const dataNone = ['sources', 'destinations', 'groups'].map((k) => {
    const n = new El('button'); n.dataset.none = k; return n;
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
      if (sel === '[data-all]') return dataAll;
      if (sel === '[data-none]') return dataNone;
      if (sel === '[data-assign-id]') {
        return byId.get('alias_body').children
          .map((row) => row.children.find((c) => c.dataset && c.dataset.assignId))
          .filter(Boolean);
      }
      return [];
    },
    createElement: (tag) => new El(tag),
    createTextNode: (text) => Object.assign(new El('#text'), {_text: String(text)}),
    addEventListener() {},
    activeElement: null,
  };
  const calls = [];
  const confirms = [];
  const toasts = [];
  const sandbox = {
    document,
    window: {
      toast: (m, kind) => toasts.push([m, kind]),
      omniConfirm: async (opts) => { confirms.push(opts); return options.confirm !== false; },
    },
    location: {pathname: '/admin/users', replace() {}},
    sessionStorage: {getItem: () => 'tok'},
    navigator: {clipboard: {writeText: async () => {}}},
    fetch: async (url, opts) => {
      const call = {url, method: (opts && opts.method) || 'GET',
                    body: opts && opts.body ? JSON.parse(opts.body) : null};
      calls.push(call);
      const out = await fetchImpl(call);
      return {status: out.status || 200, json: async () => out.body || {}};
    },
    URLSearchParams, JSON, Object, Array, Set, Map, Date, String, Number, Promise, console, encodeURIComponent,
    setTimeout, clearTimeout,
  };
  vm.createContext(sandbox);
  vm.runInContext(JS, sandbox);
  return {byId, document, calls, confirms, toasts, tabs, features, interfaces, dataAll, dataNone, sandbox};
}

const DEVICES = [
  {id: 'b8:98:b0:00:00:01', name: 'Teacher PC', role: 'encoder', model: 'hw-omni-e4521'},
  {id: 'b8:98:b0:00:00:11', name: 'Front Display', role: 'decoder', model: 'hw-omni-d4511'},
  {id: 'b8:98:b0:00:00:12', name: 'Back Display', role: 'decoder', model: 'hw-omni-d4511'},
];
const GROUPS = [{id: 'grp_aaaa', name: 'Front rooms',
                 members: [{id: 'b8:98:b0:00:00:11', name: 'Front Display', known: true}],
                 granted_to: ['teacher']}];
const USERS = [
  {id: 'u1', username: 'root', role: 'admin', enabled: true,
   permissions: {features: [], sources: [], destinations: [], groups: []}},
  {id: 'u2', username: 'teacher', role: 'user', enabled: true,
   permissions: {features: ['av'], sources: ['b8:98:b0:00:00:01'],
                 destinations: ['b8:98:b0:00:00:11'], groups: ['grp_aaaa'],
                 multiviews: ['b8:98:b0:00:00:11/Quad']}},
];

function server(extra) {
  return (call) => {
    if (extra) { const r = extra(call); if (r) return r; }
    if (call.url === '/api/admin/users' && call.method === 'GET') return {body: {ok: true, users: USERS}};
    if (call.url === '/api/admin/devices') return {body: {ok: true, devices: DEVICES}};
    if (call.url === '/api/admin/groups' && call.method === 'GET') return {body: {ok: true, groups: GROUPS}};
    if (call.url.indexOf('/direct_access') !== -1) return {body: {ok: true, direct_access: {enabled: false}}};
    return {body: {ok: true}};
  };
}
const settle = () => new Promise((r) => setTimeout(r, 30));
const rowsOf = (world, id) => world.byId.get(id).children;
const bodyTexts = (world, id) => rowsOf(world, id).map((tr) => tr.children.map((td) => td.textContent).join(' | '));

const suite = [];

suite.push(() => check('the page loads and renders accounts and groups', async () => {
  const w = buildWorld(server());
  await settle();
  const accounts = bodyTexts(w, 'users_body');
  assert(accounts.length === 2 && /teacher/.test(accounts[1]), accounts.join('\n'));
  const groups = bodyTexts(w, 'groups_body');
  assert(groups.length === 1 && /Front rooms/.test(groups[0]) && /1 display/.test(groups[0])
    && /teacher/.test(groups[0]), groups.join('\n'));
  assert(w.calls.map((c) => c.url).includes('/api/admin/groups'));
  assert(w.byId.get('loading').hidden === true, 'loading still shown');
  assert(w.byId.get('admin_tabs').hidden === false, 'tabs still hidden');
}));

suite.push(() => check('an account row summarises groups and never mentions Multiview', async () => {
  const w = buildWorld(server());
  await settle();
  const teacher = bodyTexts(w, 'users_body')[1];
  assert(/1 group/.test(teacher), teacher);
  assert(!/multiview/i.test(teacher), 'a stored grant must not be shown: ' + teacher);
}));

suite.push(() => check('the Display groups tab shows its panel and hides the others', async () => {
  const w = buildWorld(server());
  await settle();
  const tab = w.tabs.find((t) => t.dataset.panel === 'groups');
  await w.byId.get('admin_tabs').fire('click', {target: Object.assign(tab, {closest: () => tab})});
  assert(w.byId.get('panel_groups').hidden === false);
  assert(w.byId.get('panel_accounts').hidden === true && w.byId.get('panel_audit').hidden === true);
  assert(w.byId.get('panel_names').hidden === true);
}));

suite.push(() => check('creating a group posts its name and the chosen displays by identity', async () => {
  const w = buildWorld(server());
  await settle();
  await w.byId.get('new_group').fire('click');
  assert(w.byId.get('group_backdrop').style.display === 'flex');
  const rows = rowsOf(w, 'g_list');
  assert(rows.length === 2, 'only displays are offered, not sources: ' + rows.length);
  w.byId.get('g_name').value = '  Back rooms ';
  const box = rows[1].children[0];
  box.checked = true;
  await box.fire('change');
  w.calls.length = 0;
  await w.byId.get('group_save').fire('click');
  await settle();
  const post = w.calls.find((c) => c.method === 'POST' && c.url === '/api/admin/groups');
  assert(post, 'no create request was made');
  assert(post.body.name === 'Back rooms', JSON.stringify(post.body));
  assert(JSON.stringify(post.body.members) === JSON.stringify(['b8:98:b0:00:00:12']), JSON.stringify(post.body));
}));

suite.push(() => check('a group with no name or no displays is refused without a request', async () => {
  const w = buildWorld(server());
  await settle();
  await w.byId.get('new_group').fire('click');
  w.calls.length = 0;
  await w.byId.get('group_save').fire('click');
  assert(w.calls.length === 0 && /needs a name/.test(w.byId.get('group_error').textContent));
  w.byId.get('g_name').value = 'X';
  await w.byId.get('group_save').fire('click');
  assert(w.calls.length === 0 && /at least one display/.test(w.byId.get('group_error').textContent));
}));

suite.push(() => check('editing a group renames and sets members as two explicit actions', async () => {
  const w = buildWorld(server());
  await settle();
  const edit = rowsOf(w, 'groups_body')[0].children[3].children[0].children[0];
  await edit.fire('click');
  assert(w.byId.get('g_name').value === 'Front rooms');
  const preselected = rowsOf(w, 'g_list')[0].children[0];
  assert(preselected.checked === true, 'the current member should start ticked');
  w.byId.get('g_name').value = 'Front';
  w.calls.length = 0;
  await w.byId.get('group_save').fire('click');
  await settle();
  const posts = w.calls.filter((c) => c.method === 'POST');
  assert(posts.length === 2 && posts[0].body.action === 'rename' && posts[1].body.action === 'set_members',
    JSON.stringify(posts));
  assert(posts.every((p) => p.url === '/api/admin/groups/grp_aaaa'));
}));

suite.push(() => check('deleting a group asks first and Cancel makes no request', async () => {
  const w = buildWorld(server(), {confirm: false});
  await settle();
  const del = rowsOf(w, 'groups_body')[0].children[3].children[0].children[1];
  w.calls.length = 0;
  await del.fire('click');
  assert(w.confirms.length === 1 && /teacher/.test(w.confirms[0].message), 'the holders are named');
  assert(w.confirms[0].danger === true);
  assert(w.calls.length === 0, 'Cancel must make no request');
}));

suite.push(() => check('confirming a delete posts the delete action', async () => {
  const w = buildWorld(server());
  await settle();
  const del = rowsOf(w, 'groups_body')[0].children[3].children[0].children[1];
  w.calls.length = 0;
  await del.fire('click');
  await settle();
  const post = w.calls.find((c) => c.method === 'POST');
  assert(post && post.url === '/api/admin/groups/grp_aaaa' && post.body.action === 'delete');
}));

suite.push(() => check('saving an account sends groups and never sends multiviews', async () => {
  const w = buildWorld(server());
  await settle();
  const edit = rowsOf(w, 'users_body')[1].children[4].children[0].children[0];
  await edit.fire('click');
  w.calls.length = 0;
  await w.byId.get('editor_save').fire('click');
  await settle();
  const perms = w.calls.filter((c) => c.method === 'POST' && c.body && c.body.action === 'set_permissions');
  assert(perms.length === 1, 'one permissions write');
  const sent = perms[0].body.permissions;
  assert(JSON.stringify(sent.groups) === JSON.stringify(['grp_aaaa']), JSON.stringify(sent));
  assert(!('multiviews' in sent), 'a retired setting must not be written back: ' + JSON.stringify(sent));
  assert(!sent.features.includes('multiview'));
  assert(JSON.stringify(sent.sources) === JSON.stringify(['b8:98:b0:00:00:01']));
}));

const usersWith = (permissions) => server((call) => (call.url === '/api/admin/users' && call.method === 'GET'
  ? {body: {ok: true, users: [USERS[0], Object.assign({}, USERS[1], {permissions})]}} : null));
const teacherEdit = async (w) => {
  await settle();
  await rowsOf(w, 'users_body')[1].children[4].children[0].children[0].fire('click');
  w.calls.length = 0;
};

suite.push(() => check('a new account is offered both interfaces and starts on Standard', async () => {
  const w = buildWorld(server());
  await settle();
  await w.byId.get('new_user').fire('click');
  assert(w.interfaces.map((b) => b.value).join() === 'standard,matrix');
  assert(w.interfaces[0].checked === true && w.interfaces[1].checked === false,
    'the interface every account had before there was a choice');
}));

suite.push(() => check('an account stored before the choice existed shows Standard, one with both shows both', async () => {
  const old = buildWorld(server());
  await teacherEdit(old);
  assert(old.interfaces[0].checked === true && old.interfaces[1].checked === false);
  const both = buildWorld(usersWith({features: ['av'], sources: [], destinations: [], groups: [],
                                     interfaces: ['matrix', 'standard']}));
  await teacherEdit(both);
  assert(both.interfaces[0].checked === true && both.interfaces[1].checked === true);
  const matrixOnly = buildWorld(usersWith({features: ['av'], sources: [], destinations: [], groups: [],
                                           interfaces: ['matrix']}));
  await teacherEdit(matrixOnly);
  assert(matrixOnly.interfaces[0].checked === false && matrixOnly.interfaces[1].checked === true);
}));

suite.push(() => check('saving sends the routing interfaces the administrator chose', async () => {
  const w = buildWorld(server());
  await teacherEdit(w);
  w.interfaces[1].checked = true;
  await w.byId.get('editor_save').fire('click');
  await settle();
  const write = w.calls.find((c) => c.method === 'POST' && c.body && c.body.action === 'set_permissions');
  assert(write && JSON.stringify(write.body.permissions.interfaces) === JSON.stringify(['standard', 'matrix']),
    JSON.stringify(write && write.body));
}));

suite.push(() => check('an account that may route cannot be saved with neither interface', async () => {
  const w = buildWorld(server());
  await teacherEdit(w);
  w.interfaces.forEach((b) => { b.checked = false; });
  await w.byId.get('editor_save').fire('click');
  await settle();
  assert(!w.calls.some((c) => c.method === 'POST'), 'a request was sent: ' + JSON.stringify(w.calls));
  assert(/routing interface/i.test(w.byId.get('editor_error').textContent));
  assert(w.byId.get('editor_error').style.display === 'block');
}));

suite.push(() => check('the interface row belongs to A/V: shown with it, put away without it, choice kept', async () => {
  const w = buildWorld(server());
  await teacherEdit(w);
  assert(w.byId.get('interface_row').hidden === false, 'A/V is ticked');
  w.interfaces[1].checked = true;
  w.features[0].checked = false;
  await w.features[0].fire('change');
  assert(w.byId.get('interface_row').hidden === true);
  assert(w.interfaces[1].checked === true, 'unticking A/V must not forget the choice');
  w.features[0].checked = true;
  await w.features[0].fire('change');
  assert(w.byId.get('interface_row').hidden === false);
  // With A/V off there is nothing to route, so no interface is demanded.
  w.features[0].checked = false;
  w.interfaces.forEach((b) => { b.checked = false; });
  await w.byId.get('editor_save').fire('click');
  await settle();
  assert(w.calls.some((c) => c.method === 'POST' && c.body.action === 'set_permissions'),
    'USB-only needs no routing interface');
}));

suite.push(() => check('a new account carries its group grants', async () => {
  const w = buildWorld(server());
  await settle();
  await w.byId.get('new_user').fire('click');
  w.byId.get('f_username').value = 'newcomer';
  w.byId.get('f_password').value = 'long enough password';
  const groupBox = rowsOf(w, 'grp_list')[0].children[0];
  groupBox.checked = true;
  await groupBox.fire('change');
  w.calls.length = 0;
  await w.byId.get('editor_save').fire('click');
  await settle();
  const post = w.calls.find((c) => c.method === 'POST' && c.url === '/api/admin/users');
  assert(post, 'no create request');
  assert(JSON.stringify(post.body.permissions.groups) === JSON.stringify(['grp_aaaa']), JSON.stringify(post.body));
}));

suite.push(() => check('All and None on the groups list work', async () => {
  const w = buildWorld(server());
  await settle();
  await w.byId.get('new_user').fire('click');
  const all = w.dataAll.find((b) => b.dataset.all === 'groups');
  const none = w.dataNone.find((b) => b.dataset.none === 'groups');
  await all.fire('click');
  assert(/1 selected/.test(w.byId.get('grp_count').textContent), w.byId.get('grp_count').textContent);
  await none.fire('click');
  assert(/0 selected/.test(w.byId.get('grp_count').textContent));
}));

suite.push(() => check('the editor registers each handler once', async () => {
  const w = buildWorld(server());
  await settle();
  ['new_user', 'editor_save', 'editor_cancel', 'new_group', 'group_save'].forEach((id) => {
    const n = (w.byId.get(id)._listeners.click || []).length;
    assert(n === 1, id + ' has ' + n + ' click handlers (a duplicated block once registered them twice)');
  });
}));

suite.push(() => check('retention: loading Activity fills the limits and Save posts them', async () => {
  const w = buildWorld(server((call) => {
    if (call.url.indexOf('/api/admin/audit?') === 0) {
      return {body: {ok: true, entries: [], summary: {total: 3, events: [], accounts: [], retention: {
        days: 30, max_megabytes: 5, used_bytes: 1048576, entries: 3,
        defaults: {days: 30, max_megabytes: 5}}}}};
    }
    if (call.url === '/api/admin/audit/retention') return {body: {ok: true, retention: {
      days: 14, max_megabytes: 2, used_bytes: 0, entries: 0, defaults: {days: 30, max_megabytes: 5}}}};
    return null;
  }));
  await settle();
  const tab = w.tabs.find((t) => t.dataset.panel === 'audit');
  await w.byId.get('admin_tabs').fire('click', {target: Object.assign(tab, {closest: () => tab})});
  await settle();
  assert(w.byId.get('ret_days').value === 30 || w.byId.get('ret_days').value === '30', 'days ' + w.byId.get('ret_days').value);
  assert(/Default is 30 days and 5 MB/.test(w.byId.get('ret_note').textContent), w.byId.get('ret_note').textContent);
  w.byId.get('ret_days').value = '14';
  w.byId.get('ret_mb').value = '2';
  w.calls.length = 0;
  await w.byId.get('ret_save').fire('click');
  await settle();
  const post = w.calls.find((c) => c.url === '/api/admin/audit/retention');
  assert(post && post.method === 'POST' && post.body.days === 14 && post.body.max_megabytes === 2, JSON.stringify(post));
}));

suite.push(() => check('the Activity list labels the new events in English', async () => {
  const w = buildWorld(server((call) => {
    if (call.url.indexOf('/api/admin/audit?') === 0) {
      return {body: {ok: true, summary: {total: 3, events: [], accounts: []}, entries: [
        {at: 1, event: 'route_group', actor: 'teacher', detail: {group_name: 'Front rooms', routed: 3, total: 4}},
        {at: 2, event: 'group.created', actor: 'root', detail: {name: 'Front rooms', displays: 4}},
        {at: 3, event: 'activity.retention_changed', actor: 'root', detail: {days: 14, max_megabytes: 2}},
      ]}};
    }
    return null;
  }));
  await settle();
  const tab = w.tabs.find((t) => t.dataset.panel === 'audit');
  await w.byId.get('admin_tabs').fire('click', {target: Object.assign(tab, {closest: () => tab})});
  await settle();
  const rows = bodyTexts(w, 'audit_body');
  assert(/Routed to a group/.test(rows[0]) && /3 of 4 displays routed/.test(rows[0]), rows[0]);
  assert(/Group created/.test(rows[1]), rows[1]);
  assert(/Activity retention changed/.test(rows[2]) && /14 days, 2 MB/.test(rows[2]), rows[2]);
  rows.forEach((r) => assert(!/route_group|group\.created|activity\./.test(r), 'an internal name reached the operator: ' + r));
}));

// ---- what refers to each device, on the Device names page ----------------
const ASSIGNMENTS = {ok: true, remote_access: true, units: [
  {id: 'b8:98:b0:00:00:01', assigned: true, count: 2, summary: '2 Users',
   users: ['teacher', 'assistant'], direct_access: ['assistant'], groups: []},
  {id: 'b8:98:b0:00:00:11', assigned: true, count: 2, summary: '1 User · 1 Group',
   users: ['teacher'], direct_access: [], groups: ['Front rooms']},
  {id: 'b8:98:b0:00:00:12', assigned: false, count: 0, summary: 'Unassigned',
   users: [], direct_access: [], groups: []},
]};
const assignmentServer = (current) => server((call) =>
  call.url === '/api/admin/inventory/assignments' ? {body: current()} : null);
const nameRows = (w) => rowsOf(w, 'alias_body');
const assignCell = (w, index) => nameRows(w)[index].children[nameRows(w)[index].children.length - 1];

suite.push(() => check('each device shows what refers to it, and Unassigned when nothing does', async () => {
  const w = buildWorld(assignmentServer(() => ASSIGNMENTS));
  await settle();
  assert(nameRows(w).length === 3, 'rows');
  assert(assignCell(w, 0).children[0].textContent === '2 Users', assignCell(w, 0).textContent);
  assert(assignCell(w, 1).children[0].textContent === '1 User · 1 Group', assignCell(w, 1).textContent);
  assert(assignCell(w, 2).textContent === 'Unassigned', assignCell(w, 2).textContent);
  assert(assignCell(w, 2).children.length === 0, 'an unassigned unit has nothing to expand');
}));

suite.push(() => check('the summary expands to the names, marks Direct Access, and collapses', async () => {
  const w = buildWorld(assignmentServer(() => ASSIGNMENTS));
  await settle();
  const toggle = assignCell(w, 0).children[0];
  assert(toggle.getAttribute('aria-expanded') === 'false', 'starts collapsed');
  await toggle.fire('click');
  const text = assignCell(w, 0).textContent;
  assert(/Users: teacher, assistant \(Direct Access\)/.test(text), text);
  assert(assignCell(w, 0).children[0].getAttribute('aria-expanded') === 'true', 'announces the state');
  await assignCell(w, 1).children[0].fire('click');
  assert(/Groups: Front rooms/.test(assignCell(w, 1).textContent), assignCell(w, 1).textContent);
  await assignCell(w, 0).children[0].fire('click');
  assert(!/Users:/.test(assignCell(w, 0).textContent), 'collapses again');
}));

suite.push(() => check('opening the tab re-reads the assignments and repaints in place', async () => {
  let current = ASSIGNMENTS;
  const w = buildWorld(assignmentServer(() => current));
  await settle();
  const row = nameRows(w)[2];
  const typing = row.children[0].children[0];            // the friendly-name box
  typing.value = 'half typed';
  current = {ok: true, remote_access: true, units: ASSIGNMENTS.units.map((u) =>
    u.id.endsWith(':12') ? Object.assign({}, u, {assigned: true, count: 1, summary: '1 User',
                                                  users: ['visitor']}) : u)};
  w.calls.length = 0;
  const tab = w.tabs.find((t) => t.dataset.panel === 'names');
  await w.byId.get('admin_tabs').fire('click', {target: Object.assign(tab, {closest: () => tab})});
  await settle();
  assert(w.calls.some((c) => c.url === '/api/admin/inventory/assignments'), 'no re-read');
  assert(assignCell(w, 2).children[0].textContent === '1 User', assignCell(w, 2).textContent);
  assert(nameRows(w)[2] === row, 'the row was rebuilt, which would lose what is being typed');
  assert(typing.value === 'half typed', 'the typed name was disturbed');
}));

suite.push(() => check('an unreadable answer leaves a dash, never "Unassigned"', async () => {
  const w = buildWorld(server((call) =>
    call.url === '/api/admin/inventory/assignments' ? {status: 503, body: {ok: false}} : null));
  await settle();
  const cell = assignCell(w, 0);
  assert(cell.textContent === '—', cell.textContent);
  assert(!/Unassigned/.test(cell.textContent), 'not knowing is not the same as nothing assigned');
}));

suite.push(() => check('the audit list labels the two clear events in English', async () => {
  const w = buildWorld(server((call) => {
    if (call.url.indexOf('/api/admin/audit?') === 0) {
      return {body: {ok: true, summary: {total: 2, events: [], accounts: []}, entries: [
        {at: 1, event: 'inventory.cleared_unassigned', actor: 'root', detail: {devices: 3, kept: 5}},
        {at: 2, event: 'inventory.cleared', actor: 'root', detail: {devices: 8, kept: 0}},
      ]}};
    }
    return null;
  }));
  await settle();
  const tab = w.tabs.find((t) => t.dataset.panel === 'audit');
  await w.byId.get('admin_tabs').fire('click', {target: Object.assign(tab, {closest: () => tab})});
  await settle();
  const rows = bodyTexts(w, 'audit_body');
  assert(/Unassigned units cleared/.test(rows[0]), rows[0]);
  assert(/Discovered units cleared/.test(rows[1]), rows[1]);
  rows.forEach((r) => assert(!/inventory\./.test(r), 'an internal name reached the operator: ' + r));
}));

(async () => {
  for (const run of suite) await run();
  results.forEach(([s, l]) => console.log(`${s} ${l}`));
  console.log(`${suite.length - failures}/${suite.length} checks passed`);
  process.exit(failures ? 1 : 0);
})();

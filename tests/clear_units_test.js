/*
 * ui/clear-units.js: the Clear Units confirmation and View Assignments.
 *
 * Runs the real module against a scripted document and a recording fetch, so
 * "Cancel makes no request" and "an unreadable answer refuses" are measurements
 * rather than assertions about source text.
 *
 * Pinned here:
 *  - Remote Access off keeps the page's own confirmation and shows no dialog;
 *  - Remote Access on offers exactly Cancel / View Assignments / Clear
 *    Unassigned Units, and Cancel holds the focus;
 *  - Cancel, Escape and the backdrop all answer no, and nothing else is asked;
 *  - View Assignments lists assigned units only, with who holds them, returns
 *    to the confirmation on Close, and does not answer it;
 *  - nothing to clear leaves the destructive button disabled;
 *  - an unreadable answer refuses and says so; it is never read as "nothing is
 *    assigned";
 *  - a second request while one is open does not stack another dialog;
 *  - names are text, never markup;
 *  - there is no "disable Remote Access and clear" shortcut, and no native
 *    dialog is ever used.
 */
'use strict';

const path = require('path');
const fs = require('fs');
const {El} = require('./_fakedom.js');

let failures = 0;
// Strictly in order: scenarios share Node.focused and each loads its own copy of
// the module, so an async check whose setup ran at declaration time would be
// trampled by the next one.
let queue = Promise.resolve();
const pending = [];
// A scenario that never settles must fail, not vanish: a promise that is never
// resolved lets Node exit with status 0 and nothing printed, which reads exactly
// like a pass. Each scenario is given a short deadline, and the summary line is
// checked for by the runner as well.
function check(name, fn) {
  queue = queue.then(() => {
    let timer;
    const deadline = new Promise((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error('did not finish: a promise was never settled')), 4000);
    });
    // The timer is ref'd on purpose, so Node cannot exit while a scenario hangs,
    // and cleared as soon as the scenario settles so a pass costs nothing.
    return Promise.race([Promise.resolve().then(fn), deadline])
      .finally(() => clearTimeout(timer));
  }).then(
    () => console.log(`  ok   ${name}`),
    (err) => { failures += 1; console.log(`  FAIL ${name}: ${err.message}`); });
  pending.push(queue);
}
function assert(cond, message) { if (!cond) throw new Error(message); }
function equal(a, b, what) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw new Error(`${what}: expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
  }
}

class Node extends El {
  removeChild(child) {
    this.children = this.children.filter((c) => c !== child);
    child.parentNode = null;
    return child;
  }
  focus() { Node.focused = this; }
}
Node.focused = null;

function makeDoc() {
  const listeners = {};
  const doc = {
    body: new Node('body'),
    activeElement: null,
    createElement: (tag) => new Node(tag),
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener: (type, fn) => {
      listeners[type] = (listeners[type] || []).filter((f) => f !== fn);
    },
    key(key) { (listeners.keydown || []).slice().forEach((fn) => fn({key, stopPropagation() {}})); },
    listenerCount: (type) => (listeners[type] || []).length,
  };
  return doc;
}

function makeStack() {
  const open = [];
  return {
    raise: (el) => { open.push(el); },
    release: (el) => { const i = open.indexOf(el); if (i >= 0) open.splice(i, 1); },
    top: () => open[open.length - 1] || null,
    depth: () => open.length,
  };
}

const REPORT_ON = {
  ok: true, remote_access: true, total: 4, assigned: 2, protected: 2, clearable: 2,
  units: [
    {id: 'b8:98:b0:00:00:01', type: 'Encoder', name: 'Lectern PC', friendly_name: 'Lectern PC',
     device_name: 'enc-teacher', model: 'hw-omni-e4521', users: ['teacher', 'assistant'],
     direct_access: ['assistant'], groups: [], count: 2, summary: '2 Users', assigned: true},
    {id: 'b8:98:b0:00:00:31', type: 'Decoder', name: 'dec-a', friendly_name: '',
     device_name: 'dec-a', model: 'hw-omni-d4511', users: [], direct_access: [],
     groups: ['Front rooms'], count: 1, summary: '1 Group', assigned: true},
    {id: 'b8:98:b0:00:00:09', type: 'Encoder', name: 'enc-other', friendly_name: '',
     device_name: 'enc-other', model: 'hw-omni-e4111', users: [], direct_access: [],
     groups: [], count: 0, summary: 'Unassigned', assigned: false},
    {id: 'b8:98:b0:00:00:19', type: 'Decoder', name: 'dec-other', friendly_name: '',
     device_name: 'dec-other', model: 'hw-omni-d4511', users: [], direct_access: [],
     groups: [], count: 0, summary: 'Unassigned', assigned: false},
  ],
};

function recordingFetch(answer) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({url, method: (init && init.method) || 'GET'});
    const next = typeof answer === 'function' ? answer() : answer;
    if (next instanceof Error) throw next;
    return {ok: next.status === undefined || next.status === 200, status: next.status || 200,
            json: async () => next.body};
  };
  fn.calls = calls;
  return fn;
}

function setup(answer, extra) {
  delete require.cache[require.resolve(path.join(__dirname, '..', 'ui', 'clear-units.js'))];
  const mod = require(path.join(__dirname, '..', 'ui', 'clear-units.js'));
  mod._reset();
  const doc = makeDoc();
  const stack = makeStack();
  const fetch = recordingFetch(answer);
  const deps = Object.assign({
    doc, fetch, modalStack: stack,
    legacyConfirm: async () => { deps.legacyCalls += 1; return deps.legacyAnswer; },
    onError: (m) => { deps.errors.push(m); },
  }, extra || {});
  deps.legacyCalls = 0;
  deps.legacyAnswer = true;
  deps.errors = [];
  return {mod, doc, stack, fetch, deps};
}

const tick = () => new Promise((r) => setImmediate(r));

function visibleDialogs(doc) {
  return doc.body.children.filter((c) => c.style.display === 'flex');
}
function buttons(shell) { return shell.querySelectorAll('button'); }
function button(shell, label) {
  const found = buttons(shell).find((b) => b.textContent === label);
  assert(found, `no "${label}" button in ${buttons(shell).map((b) => b.textContent)}`);
  return found;
}

check('Remote Access off uses the page confirmation and shows no dialog', async () => {
  const {mod, doc, deps} = setup({body: {ok: true, remote_access: false, units: []}});
  deps.legacyAnswer = true;
  equal(await mod.confirmClear(deps), true, 'answer');
  equal(deps.legacyCalls, 1, 'legacy confirmation asked once');
  equal(visibleDialogs(doc).length, 0, 'dialogs shown');
});

check('Remote Access off: the page confirmation can still say no', async () => {
  const {mod, deps} = setup({body: {ok: true, remote_access: false, units: []}});
  deps.legacyAnswer = false;
  equal(await mod.confirmClear(deps), false, 'answer');
});

check('Remote Access on offers exactly Cancel, View Assignments, Clear Unassigned Units', async () => {
  const {mod, doc, deps} = setup({body: REPORT_ON});
  const asked = mod.confirmClear(deps);
  await tick();
  const [dialog] = visibleDialogs(doc);
  assert(dialog, 'no dialog');
  equal(buttons(dialog).map((b) => b.textContent),
        ['Cancel', 'View Assignments', 'Clear Unassigned Units'], 'buttons');
  equal(deps.legacyCalls, 0, 'the plain confirmation must not also be asked');
  const text = dialog.textContent;
  assert(text.includes('Clear discovered units?'), 'title');
  assert(text.includes('Remote Access is enabled.'), 'states Remote Access is on');
  assert(text.includes('will be kept'), 'says assigned units are kept');
  assert(text.includes('disable Remote Access in Settings first'),
         'names the way to remove assigned units');
  assert(Node.focused === button(dialog, 'Cancel'), 'Cancel holds the focus');
  await button(dialog, 'Cancel').fire('click');
  equal(await asked, false, 'answer');
});

check('Cancel changes nothing: one read, no other request', async () => {
  const {mod, doc, fetch, deps} = setup({body: REPORT_ON});
  const asked = mod.confirmClear(deps);
  await tick();
  await button(visibleDialogs(doc)[0], 'Cancel').fire('click');
  equal(await asked, false, 'answer');
  equal(fetch.calls.map((c) => c.method + ' ' + c.url),
        ['GET /api/admin/inventory/assignments'], 'requests made');
  equal(visibleDialogs(doc).length, 0, 'dialog left open');
  equal(doc.listenerCount('keydown'), 0, 'keyboard listener leaked');
});

check('Escape and the backdrop both answer no', async () => {
  for (const how of ['escape', 'backdrop']) {
    const {mod, doc, deps} = setup({body: REPORT_ON});
    const asked = mod.confirmClear(deps);
    await tick();
    const [dialog] = visibleDialogs(doc);
    if (how === 'escape') doc.key('Escape');
    else await dialog.fire('click', {target: dialog});
    equal(await asked, false, how);
  }
});

check('Clear Unassigned Units answers yes', async () => {
  const {mod, doc, deps} = setup({body: REPORT_ON});
  const asked = mod.confirmClear(deps);
  await tick();
  await button(visibleDialogs(doc)[0], 'Clear Unassigned Units').fire('click');
  equal(await asked, true, 'answer');
});

check('View Assignments lists assigned units only, with who holds them', async () => {
  const {mod, doc, deps} = setup({body: REPORT_ON});
  const asked = mod.confirmClear(deps);
  await tick();
  const [dialog] = visibleDialogs(doc);
  button(dialog, 'View Assignments').fire('click');          // resolves on Close
  await tick();
  const open = visibleDialogs(doc);
  equal(open.length, 2, 'the confirmation stays open underneath');
  const list = open.find((d) => d.id === 'assignments_backdrop');
  assert(list, 'no assignments dialog');
  const text = list.textContent;
  for (const needle of ['hw-omni-e4521', 'Lectern PC', 'enc-teacher', 'teacher, assistant (Direct Access)',
                        'Front rooms', 'hw-omni-d4511', 'dec-a']) {
    assert(text.includes(needle), `missing ${needle}`);
  }
  for (const absent of ['enc-other', 'dec-other']) {
    assert(!text.includes(absent), `unassigned unit ${absent} must not be listed`);
  }
  assert(text.includes('2 unassigned units are not listed'), 'says how many are not listed');
  const headers = list.querySelectorAll('th').map((h) => h.textContent);
  equal(headers, ['Device', 'Type', 'Friendly name', 'Device name', 'Assignments', 'Users',
                  'Display Groups'], 'columns');
  await button(list, 'Close').fire('click');
  await tick();
  equal(visibleDialogs(doc).map((d) => d.id), ['clear_units_backdrop'],
        'closing the list returns to the confirmation, still unanswered');
  await button(dialog, 'Cancel').fire('click');
  equal(await asked, false, 'answer');
});

check('Escape closes only the top dialog', async () => {
  const {mod, doc, deps} = setup({body: REPORT_ON});
  const asked = mod.confirmClear(deps);
  await tick();
  button(visibleDialogs(doc)[0], 'View Assignments').fire('click');
  await tick();
  doc.key('Escape');
  await tick();
  equal(visibleDialogs(doc).map((d) => d.id), ['clear_units_backdrop'], 'after the first Escape');
  doc.key('Escape');
  equal(await asked, false, 'second Escape answers no');
});

check('nothing to clear leaves the destructive button disabled', async () => {
  const allAssigned = Object.assign({}, REPORT_ON, {
    clearable: 0, protected: 4,
    units: REPORT_ON.units.map((u) => Object.assign({}, u, {assigned: true, count: 1})),
  });
  const {mod, doc, deps} = setup({body: allAssigned});
  const asked = mod.confirmClear(deps);
  await tick();
  const [dialog] = visibleDialogs(doc);
  const go = button(dialog, 'Clear Unassigned Units');
  assert(go.disabled === true, 'must be disabled');
  assert(dialog.textContent.includes('nothing to clear'), 'says why');
  await go.fire('click');
  equal(visibleDialogs(doc).length, 1, 'a disabled button must not answer');
  await button(dialog, 'Cancel').fire('click');
  equal(await asked, false, 'answer');
});

check('an unreadable answer refuses and says so', async () => {
  const cases = {
    'network failure': new Error('offline'),
    'server refusal': {status: 503, body: {ok: false}},
    'garbage': {body: null},
  };
  for (const [name, answer] of Object.entries(cases)) {
    const {mod, doc, deps} = setup(answer);
    equal(await mod.confirmClear(deps), false, name);
    equal(deps.legacyCalls, 0, `${name}: must not fall back to the plain confirmation`);
    equal(visibleDialogs(doc).length, 0, `${name}: dialog`);
    equal(deps.errors.length, 1, `${name}: the operator was told`);
  }
});

check('a second request while one is open does not stack another dialog', async () => {
  const {mod, doc, fetch, deps} = setup({body: REPORT_ON});
  const first = mod.confirmClear(deps);
  const second = await mod.confirmClear(deps);
  equal(second, false, 'second answer');
  await tick();
  equal(visibleDialogs(doc).length, 1, 'dialogs open');
  equal(fetch.calls.length, 1, 'requests made');
  await button(visibleDialogs(doc)[0], 'Cancel').fire('click');
  equal(await first, false, 'first answer');
  // and the lock is released, so the next press works.
  const third = mod.confirmClear(deps);
  await tick();
  equal(visibleDialogs(doc).length, 1, 'reopens after closing');
  await button(visibleDialogs(doc)[0], 'Cancel').fire('click');
  await third;
});

check('the guard is released when the read throws', async () => {
  const {mod, deps} = setup(new Error('offline'));
  await mod.confirmClear(deps);
  const again = setup({body: REPORT_ON});
  again.mod._reset();
  equal(typeof (await mod.confirmClear(deps)), 'boolean', 'a later call still answers');
});

check('the guard is released when the page confirmation itself fails', async () => {
  const {mod, deps} = setup({body: {ok: true, remote_access: false, units: []}});
  deps.legacyConfirm = async () => { throw new Error('dialog failed'); };
  let threw = false;
  try { await mod.confirmClear(deps); } catch (err) { threw = true; }
  assert(threw, 'the failure should reach the caller, not be swallowed');
  deps.legacyConfirm = async () => true;
  equal(await mod.confirmClear(deps), true, 'a later press still works');
});

check('names are text, never markup', async () => {
  const hostile = JSON.parse(JSON.stringify(REPORT_ON));
  hostile.units[0].name = '<img src=x onerror=alert(1)>';
  hostile.units[0].friendly_name = '<img src=x onerror=alert(1)>';
  hostile.units[0].users = ['<b>teacher</b>'];
  hostile.units[0].direct_access = [];
  const {mod, doc, deps} = setup({body: hostile});
  const asked = mod.confirmClear(deps);
  await tick();
  const [dialog] = visibleDialogs(doc);
  button(dialog, 'View Assignments').fire('click');
  await tick();
  const list = visibleDialogs(doc).find((d) => d.id === 'assignments_backdrop');
  assert(list.textContent.includes('<img src=x onerror=alert(1)>'), 'shown literally');
  assert(list.textContent.includes('<b>teacher</b>'), 'user name shown literally');
  doc.key('Escape'); doc.key('Escape');
  await asked;
});

check('the outcome message comes from the server answer', async () => {
  const {mod} = setup({body: REPORT_ON});
  equal(mod.outcomeMessage({ok: true, mode: 'unassigned', cleared: 2, kept: 2}),
        'Cleared 2 unassigned units. 2 assigned units were kept.', 'plural');
  equal(mod.outcomeMessage({ok: true, mode: 'unassigned', cleared: 1, kept: 1}),
        'Cleared 1 unassigned unit. 1 assigned unit was kept.', 'singular');
  equal(mod.outcomeMessage({ok: true, mode: 'full', cleared: 4, kept: 0}), '', 'full clear');
  equal(mod.outcomeMessage({ok: false}), '', 'failure');
});

check('no shortcut to disable Remote Access, and no native dialog', async () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'ui', 'clear-units.js'), 'utf8');
  const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert(!/\/api\/remote/.test(code), 'the module must not reach the Remote Access endpoints');
  assert(!/\bdisable\b[^'"\n]*clear/i.test(code.replace(/disable Remote Access in Settings first/g, '')),
         'no "disable and clear" action');
  for (const native of ['alert(', 'prompt(', 'window.confirm', ' confirm(']) {
    assert(!code.includes(native), `native dialog ${native}`);
  }
  assert(!/innerHTML/.test(code), 'no innerHTML');
  const labels = (code.match(/make\(doc, 'button', 'btn[^']*', '([^']+)'\)/g) || [])
    .map((m) => /'([^']+)'\)$/.exec(m)[1]);
  equal(labels.sort(), ['Cancel', 'Clear Unassigned Units', 'Close', 'View Assignments'].sort(),
        'the only buttons the module can make');
});

Promise.all(pending).then(() => {
  console.log(failures ? `\n${failures} FAILED` : '\nall passed');
  process.exit(failures ? 1 : 0);
});

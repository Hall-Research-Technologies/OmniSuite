// The configuration backup flow, and the dialog option it depends on.
//
// Two things are worth asserting in a browser rather than in Python. The
// first is that the passphrase never reaches a native prompt() or a log --
// the whole flow is built around a credential the operator types. The second
// is that the two `omniConfirm` implementations agree about the `field`
// option: index.html has carried its own dialog since before there was a
// shared one, and two implementations of one control drift.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');

function read(...parts) {
  return fs.readFileSync(path.join(ROOT, ...parts), 'utf8')
    .split('\r\n').join('\n');
}

const SETTINGS_JS = read('ui', 'settings.js');
const CONFIRM_JS = read('ui', 'confirm.js');
const INDEX_HTML = read('ui', 'index.html');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => {
      failures++;
      results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]);
    });
}
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}
function code(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((l) => l.split('//')[0]).join('\n');
}

// Lift one function out of settings.js and run it against a scripted world.
function lift(signature, extras) {
  const start = SETTINGS_JS.indexOf(signature);
  assert(start >= 0, `${signature} is not where this expects it`);
  const end = SETTINGS_JS.indexOf('\n}\n', start);
  assert(end > start, `could not find the end of ${signature}`);
  const body = SETTINGS_JS.slice(start, end + 3);
  const name = signature.replace(/^(async )?function /, '').split('(')[0];
  const sandbox = Object.assign(
    {Promise, JSON, Object, console, FormData: FakeFormData, URL: FakeURL},
    extras);
  sandbox.window = sandbox.window || {};
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(`${body}\nglobalThis.__lifted = ${name};`, sandbox);
  return sandbox.__lifted;
}

class FakeFormData {
  constructor() { this.entries = []; }
  append(k, v, n) { this.entries.push([k, v, n]); }
}
const FakeURL = {
  createObjectURL: () => 'blob:fake',
  revokeObjectURL: () => {},
};

const suite = [];

// ---------------------------------------------------------------- the flow

suite.push(() => check('the passphrase is asked for in the shared dialog, never a prompt', () => {
  const settings = code(SETTINGS_JS);
  const flow = settings.slice(settings.indexOf('async function askForPassphrase'));
  assert(/omniConfirm/.test(flow), 'the flow does not use the shared dialog');
  // The two native dialogs that would show or fail to take a credential.
  assert(!/\bprompt\s*\(/.test(flow), 'the flow can reach window.prompt');
  assert(!/window\.prompt/.test(settings), 'settings.js reaches for prompt');
}));

suite.push(() => check('the passphrase is never logged or stored', () => {
  const settings = code(SETTINGS_JS);
  const flow = settings.slice(settings.indexOf('async function askForPassphrase'));
  for (const forbidden of ['console.log', 'localStorage', 'sessionStorage']) {
    assert(!flow.includes(forbidden),
      `the backup flow reaches for ${forbidden}`);
  }
  // And the VALUE is never put on the page. Matching the word would catch
  // "Enter the backup passphrase", which is the label and belongs there; what
  // must not appear is the variable holding what was typed.
  assert(!/backupStatus\([^)]*\bpassphrase\b\s*[,+)]/.test(flow),
    'the passphrase value is shown in the status line');
  assert(!/textContent\s*=\s*passphrase/.test(flow));
}));

suite.push(() => check('cancelling the passphrase dialog sends nothing', async () => {
  const posted = [];
  const ask = lift('async function askForPassphrase(', {
    document: {getElementById: () => null},
    window: {omniConfirm: () => Promise.resolve(false)},
  });
  const answer = await ask({title: 't', message: 'm', confirmText: 'c'});
  assert(answer === null, `cancelling returned ${JSON.stringify(answer)}`);
  assert(posted.length === 0);
}));

suite.push(() => check('a missing dialog refuses rather than degrading', async () => {
  // The message explains what the passphrase is for. A browser box cannot
  // carry it, and there is no safe way to ask without it.
  let said = null;
  const ask = lift('async function askForPassphrase(', {
    backupStatus: (text) => { said = text; },
    window: {},
  });
  const answer = await ask({title: 't', message: 'm'});
  assert(answer === null, 'it carried on without a dialog');
  assert(said && /passphrase/i.test(said),
    `it refused without saying why: ${said}`);
}));

suite.push(() => check('export posts the passphrase and downloads the file', async () => {
  const calls = [];
  const clicked = [];
  const ask = {value: 'a-long-enough-backup-passphrase'};
  const fakeLink = {click() { clicked.push(this.download); }, remove() {}};
  const exportFn = lift('async function exportConfiguration(', {
    askForPassphrase: () => Promise.resolve(ask.value),
    BACKUP_MIN_PASSPHRASE: 8,
    setTimeout: () => {},
    backupStatus: () => {},
    filenameFrom: () => 'OmniSuite-V1.2.0-backup-20260101-000000.omnibak',
    fetch: (url, opts) => {
      calls.push({url, opts});
      return Promise.resolve({
        ok: true,
        headers: {get: () => 'attachment; filename="x.omnibak"'},
        blob: () => Promise.resolve('BYTES'),
        json: () => Promise.resolve({}),
      });
    },
    document: {
      createElement: () => fakeLink,
      body: {appendChild: () => {}},
    },
  });
  await exportFn();
  assert(calls.length === 1, `made ${calls.length} request(s)`);
  assert(calls[0].url === '/api/backup/export', calls[0].url);
  const body = JSON.parse(calls[0].opts.body);
  assert(body.passphrase === ask.value, 'the passphrase was not sent');
  assert(clicked.length === 1, 'nothing was downloaded');
  assert(/\.omnibak$/.test(clicked[0]), `downloaded ${clicked[0]}`);
}));

suite.push(() => check('a refused export reports the reason and downloads nothing', async () => {
  let said = null;
  const clicked = [];
  const exportFn = lift('async function exportConfiguration(', {
    askForPassphrase: () => Promise.resolve('short'),
    // Long enough to reach the server in this scenario: what is under test
    // here is how a refusal is reported, not the length check.
    BACKUP_MIN_PASSPHRASE: 1,
    setTimeout: () => {},
    backupStatus: (text) => { said = text; },
    filenameFrom: () => '',
    fetch: () => Promise.resolve({
      ok: false,
      json: () => Promise.resolve({ok: false, reason: 'weak_passphrase',
                                   error: 'Choose a longer passphrase.'}),
    }),
    document: {createElement: () => ({click() { clicked.push(1); },
                                      remove() {}}),
               body: {appendChild: () => {}}},
  });
  await exportFn();
  assert(said === 'Choose a longer passphrase.', `it said ${said}`);
  assert(clicked.length === 0, 'it downloaded something anyway');
}));

suite.push(() => check('restore inspects before it asks, and stops on a refusal', async () => {
  const calls = [];
  const asked = [];
  const restoreFn = lift('async function restoreConfiguration(', {
    chooseBackupFile: () => Promise.resolve({name: 'b.omnibak'}),
    askForPassphrase: () => Promise.resolve('a-long-enough-backup-passphrase'),
    backupStatus: () => {},
    backupSectionLabel: (n) => n,
    renderRestoreResult: () => {},
    postBackup: (path) => {
      calls.push(path);
      return Promise.resolve({ok: false, error: 'That backup could not be opened.'});
    },
    window: {omniConfirm: (o) => { asked.push(o.title); return Promise.resolve(true); }},
  });
  await restoreFn();
  assert(calls.length === 1, `made ${calls.length} request(s)`);
  assert(calls[0] === '/api/backup/inspect', calls[0]);
  assert(asked.length === 0,
    'it asked to replace the configuration before the backup even opened');
}));

suite.push(() => check('saying no to the restore confirmation changes nothing', async () => {
  const calls = [];
  const restoreFn = lift('async function restoreConfiguration(', {
    chooseBackupFile: () => Promise.resolve({name: 'b.omnibak'}),
    askForPassphrase: () => Promise.resolve('a-long-enough-backup-passphrase'),
    backupStatus: () => {},
    backupSectionLabel: (n) => n,
    renderRestoreResult: () => {},
    postBackup: (path) => {
      calls.push(path);
      return Promise.resolve({ok: true, summary: {sections: [], notes: []}});
    },
    window: {omniConfirm: () => Promise.resolve(false)},
  });
  await restoreFn();
  assert(calls.length === 1,
    `it restored anyway: ${JSON.stringify(calls)}`);
  assert(!calls.includes('/api/backup/restore'));
}));

suite.push(() => check('the restore confirmation is the dangerous kind', () => {
  const settings = code(SETTINGS_JS);
  const flow = settings.slice(settings.indexOf('async function restoreConfiguration'));
  const block = flow.slice(flow.indexOf('Replace this configuration?'));
  assert(/danger:\s*true/.test(block.slice(0, 600)),
    'replacing every account is not marked as destructive');
}));

// -------------------------------------------------- the two dialogs agree

suite.push(() => check('both omniConfirm implementations support the field option', () => {
  // index.html carries its own dialog and ui/confirm.js is the shared one.
  // The backup flow runs on whichever the page has, so both must honour the
  // same option -- and this is what stops them drifting apart again.
  for (const [name, source] of [['ui/confirm.js', CONFIRM_JS],
                                ['ui/index.html', INDEX_HTML]]) {
    const body = code(source);
    assert(/options\.field/.test(body) || /const field = options\.field/.test(body),
      `${name} ignores the field option`);
    assert(/confirm-field-input|confirm_field_input/.test(body),
      `${name} has no field markup`);
    assert(/field\.onValue/.test(body),
      `${name} never returns what was typed`);
    assert(/fieldInput\.value = ''/.test(body),
      `${name} leaves the passphrase in the DOM`);
  }
}));

suite.push(() => check('neither dialog degrades a field request to a browser box', () => {
  // window.confirm() takes no input and prompt() shows a passphrase in the
  // clear. A dialog that cannot be built must refuse.
  const inline = code(INDEX_HTML);
  assert(/options\.requireDialog \|\| options\.field/.test(inline),
    'the inline dialog can fall back to window.confirm with a field');
  const shared = code(CONFIRM_JS);
  assert(!/window\.confirm\(/.test(shared),
    'the shared dialog can fall back to window.confirm');
}));

suite.push(() => check('both dialogs offer one button for an acknowledgement', () => {
  for (const [name, source] of [['ui/confirm.js', CONFIRM_JS],
                                ['ui/index.html', INDEX_HTML]]) {
    assert(/options\.acknowledge/.test(code(source)),
      `${name} shows Cancel on a message with no second answer`);
  }
}));

suite.push(() => check('the field is styled wherever the dialog lives', () => {
  // Markup shared between pages renders unstyled everywhere except the page
  // whose stylesheet it came from.
  assert(/\.confirm-field-input\{/.test(CONFIRM_JS.replace(/\s/g, '')
    .replace(/'/g, '').replace(/\+/g, ''))
    || /confirm-field-input\{/.test(CONFIRM_JS),
    'the shared dialog does not style its own field');
  assert(/\.confirm-field-input\{/.test(INDEX_HTML),
    'the inline dialog does not style its own field');
}));

// ------------------------------------------------------------------ markup

suite.push(() => check('both buttons exist and say what they do', () => {
  assert(/id="cfg_backup_export"/.test(SETTINGS_JS), 'no export button');
  assert(/id="cfg_backup_restore"/.test(SETTINGS_JS), 'no restore button');
  assert(/Export Configuration/.test(SETTINGS_JS));
  assert(/Restore Configuration/.test(SETTINGS_JS));
  assert(/somewhere other than this computer/.test(SETTINGS_JS),
    'nothing tells the operator where to keep the backup');
}));

suite.push(() => check('the buttons are bound, not merely drawn', () => {
  const settings = code(SETTINGS_JS);
  assert(/cfg_backup_export[\s\S]{0,200}addEventListener/.test(settings),
    'the export button is not wired up');
  assert(/cfg_backup_restore[\s\S]{0,200}addEventListener/.test(settings),
    'the restore button is not wired up');
}));


// ------------------------------------------------- local access and the UI

suite.push(() => check('the backup controls are hidden until the server says local', async () => {
  // Not worked out from the address bar: a hostname is not an authority.
  // 127.0.0.1 can be forwarded and a machine can be reached by its own LAN
  // address; what decides is the socket the request arrived on, which only
  // the server can see.
  const settings = code(SETTINGS_JS);
  const fn = settings.slice(settings.indexOf('async function applyBackupLocality'));
  const body = fn.slice(0, fn.indexOf('\n}'));
  assert(/omniIsLocal/.test(body),
    'the page decides locality for itself');
  assert(!/location\.hostname|127\.0\.0\.1['"]|localhost['"]/.test(body),
    'the page trusts the address bar');
}));

suite.push(() => check('they start hidden, so nothing flashes up remotely', () => {
  // Revealing on a positive answer is the safe direction. Hiding on a
  // negative one shows them first and thinks better of it.
  const markup = SETTINGS_JS.slice(SETTINGS_JS.indexOf('id="cfg_backup_section"'));
  const block = markup.slice(0, markup.indexOf('cfg_backup_status'));
  for (const id of ['cfg_backup_export', 'cfg_backup_restore']) {
    const tag = block.slice(block.indexOf('id="' + id + '"'));
    const end = tag.indexOf('>');
    assert(/hidden/.test(tag.slice(0, end)), `${id} is not hidden by default`);
    assert(/disabled/.test(tag.slice(0, end)), `${id} is not disabled by default`);
  }
}));

suite.push(() => check('a remote page says what is needed instead', () => {
  assert(/id="cfg_backup_remote"/.test(SETTINGS_JS), 'no notice element');
  assert(/require local access to OmniSuite\s*\n?\s*through 127\.0\.0\.1/
    .test(SETTINGS_JS.replace(/\s+/g, ' ')) ||
    /require local access to OmniSuite through 127\.0\.0\.1/
      .test(SETTINGS_JS.replace(/\s+/g, ' ')),
    'the notice does not say what is required');
}));

suite.push(() => check('a failure to ask is treated as remote', async () => {
  // The safe direction: a hidden control on the OmniSuite computer is a
  // nuisance; an offered one elsewhere is a button that answers 404 and a
  // description of where the credentials live.
  const nodes = {};
  const make = () => ({hidden: false, disabled: false});
  ['cfg_backup_export', 'cfg_backup_restore', 'cfg_backup_remote',
   'cfg_backup_blurb'].forEach((id) => { nodes[id] = make(); });
  const apply = lift('async function applyBackupLocality(', {
    document: {getElementById: (id) => nodes[id] || null},
    window: {omniIsLocal: () => Promise.reject(new Error('offline'))},
    backupStatus: () => {},
  });
  await apply();
  assert(nodes.cfg_backup_export.hidden === true,
    'the export button was offered when locality could not be established');
  assert(nodes.cfg_backup_export.disabled === true);
  assert(nodes.cfg_backup_remote.hidden === false, 'nothing explained why');
  assert(nodes.cfg_backup_blurb.hidden === true,
    'what the backup contains was described to a remote browser');
}));

suite.push(() => check('a local answer reveals them', async () => {
  const nodes = {};
  ['cfg_backup_export', 'cfg_backup_restore', 'cfg_backup_remote',
   'cfg_backup_blurb'].forEach((id) => {
    nodes[id] = {hidden: true, disabled: true};
  });
  const apply = lift('async function applyBackupLocality(', {
    document: {getElementById: (id) => nodes[id] || null},
    window: {omniIsLocal: () => Promise.resolve(true)},
    backupStatus: () => {},
  });
  await apply();
  assert(nodes.cfg_backup_export.hidden === false, 'still hidden locally');
  assert(nodes.cfg_backup_export.disabled === false);
  assert(nodes.cfg_backup_remote.hidden === true,
    'the local operator is told they need local access');
}));

suite.push(() => check('the download is not cancelled before it starts', () => {
  // revokeObjectURL on the same tick can abort the download the click just
  // began, which looks exactly like a button that did nothing.
  const settings = code(SETTINGS_JS);
  const fn = settings.slice(settings.indexOf('async function exportConfiguration'));
  const body = fn.slice(0, fn.indexOf('\n}'));
  assert(/setTimeout\(\(\) => URL\.revokeObjectURL\(url\)/.test(body),
    'the object URL is revoked on the same tick as the click');
  const click = body.indexOf('link.click()');
  const revoke = body.indexOf('revokeObjectURL');
  assert(click < revoke, 'it revokes before it clicks');
}));

// ----------------------------------------------------- clear units prompts

suite.push(() => check('Clear Units asks the server what it would do', () => {
  const page = code(INDEX_HTML);
  const handler = page.slice(page.indexOf("$('#clear_units_btn')"));
  const body = handler.slice(0, handler.indexOf('\n});'));
  assert(/\/api\/clear_units\/preview/.test(body),
    'the confirmation is built from what the page remembers');
  // And the preview comes before anything is sent.
  assert(body.indexOf('/api/clear_units/preview')
         < body.indexOf("method: 'POST'"),
    'it clears before it checks');
}));

suite.push(() => check('while Remote Access is on it always confirms', () => {
  const page = code(INDEX_HTML);
  const handler = page.slice(page.indexOf("$('#clear_units_btn')"));
  const body = handler.slice(0, handler.indexOf('\n});'));
  assert(/if \(preview && preview\.remote_access\)/.test(body),
    'the prompt depends on something other than Remote Access being on');
  // Not on whether anything is assigned -- clearing changes what every
  // User's controls are built from either way.
  assert(!/if \(assigned\.length\)\s*\{[\s\S]{0,80}omniConfirm/.test(body),
    'it only asks when something is assigned');
  assert(/requireDialog: true/.test(body),
    'the confirmation can degrade to a browser box');
}));

suite.push(() => check('the confirmation says what it affects, in words', () => {
  const page = INDEX_HTML;
  const handler = page.slice(page.indexOf("$('#clear_units_btn')"));
  const body = handler.slice(0, handler.indexOf('\n});'));
  assert(/Run a Scan afterwards/.test(body), 'no mention of rescanning');
  assert(/controls your Users see/.test(body),
    'nothing about the effect on User control');
  assert(/Removing assigned units may affect User control/.test(body));
  // Named equipment and named people, not identities.
  assert(/row\.name/.test(body) && /row\.users/.test(body),
    'the confirmation does not name the units and the accounts');
  assert(!/row\.mac|identity/.test(body),
    'a raw identity reaches the confirmation');
}));

suite.push(() => check('Cancel sends nothing at all', async () => {
  const requests = [];
  const handler = extractClearHandler();
  const sandbox = makeClearWorld(requests, {remote_access: true,
                                            assigned: [], would_clear: 3},
                                 () => Promise.resolve(false));
  await runClearHandler(handler, sandbox);
  assert(requests.filter((r) => r.method === 'POST').length === 0,
    `Cancel still sent: ${JSON.stringify(requests)}`);
  assert(requests.some((r) => r.url.indexOf('/preview') >= 0),
    'it never even asked');
}));

suite.push(() => check('the override is off unless it is ticked', async () => {
  const requests = [];
  const handler = extractClearHandler();
  const sandbox = makeClearWorld(
    requests,
    {remote_access: true, would_clear: 3,
     assigned: [{name: 'Hall Display', address: '198.51.100.11',
                 users: ['teacher']}]},
    () => Promise.resolve(true));            // confirm, but never tick the box
  await runClearHandler(handler, sandbox);
  const post = requests.filter((r) => r.method === 'POST').pop();
  assert(post, 'nothing was sent');
  assert(post.body === '{}',
    `the safe default was not what was sent: ${post.body}`);
}));

suite.push(() => check('ticking it sends the override', async () => {
  const requests = [];
  const handler = extractClearHandler();
  const sandbox = makeClearWorld(
    requests,
    {remote_access: true, would_clear: 3,
     assigned: [{name: 'Hall Display', address: '198.51.100.11',
                 users: ['teacher']}]},
    (options) => {
      if (options.checkbox) options.checkbox.onValue(true);
      return Promise.resolve(true);
    });
  await runClearHandler(handler, sandbox);
  const post = requests.filter((r) => r.method === 'POST').pop();
  assert(post && JSON.parse(post.body).override === true,
    `the override was not sent: ${post && post.body}`);
}));

suite.push(() => check('a preview that cannot be read stops the clear', async () => {
  // Fail closed: an operator can look again, they cannot un-clear.
  const requests = [];
  const handler = extractClearHandler();
  const sandbox = makeClearWorld(requests,
    {ok: false, error: 'could not read the assignments'},
    () => Promise.resolve(true));
  await runClearHandler(handler, sandbox);
  assert(requests.filter((r) => r.method === 'POST').length === 0,
    'it cleared without knowing what it would affect');
}));

suite.push(() => check('Remote Access off keeps the old behaviour', async () => {
  const requests = [];
  const asked = [];
  const handler = extractClearHandler();
  const sandbox = makeClearWorld(requests,
    {remote_access: false, assigned: [], would_clear: 0},
    (o) => { asked.push(o.title); return Promise.resolve(true); });
  await runClearHandler(handler, sandbox);
  assert(asked.length === 0,
    'a standalone installation was made to argue with dormant records');
  const post = requests.filter((r) => r.method === 'POST').pop();
  assert(post, 'the ordinary clear did not happen');
  assert(post.body === '{}');
}));

// -------------------------------------------- both dialogs know the option

suite.push(() => check('both dialogs support the checkbox option', () => {
  // The override needs somewhere of its own. Overloading Cancel to mean a
  // second choice is the thing this project does not do.
  for (const [name, source] of [['ui/confirm.js', CONFIRM_JS],
                                ['ui/index.html', INDEX_HTML]]) {
    const body = code(source);
    assert(/options\.checkbox/.test(body), `${name} ignores the checkbox`);
    assert(/confirm-checkbox-input|confirm_checkbox_input/.test(body),
      `${name} has no checkbox markup`);
    assert(/checkbox\.onValue/.test(body),
      `${name} never reports what was ticked`);
    assert(/boxInput\.checked = false/.test(body),
      `${name} does not reset it, so the destructive answer could stick`);
  }
}));

// --- helpers for driving the clear handler -------------------------------

function extractClearHandler() {
  const start = INDEX_HTML.indexOf("$('#clear_units_btn').addEventListener");
  assert(start >= 0, 'the clear handler is not where this expects it');
  const end = INDEX_HTML.indexOf('\n});', start);
  assert(end > start, 'could not find the end of the clear handler');
  // Just the async function body, so it can be called directly.
  const whole = INDEX_HTML.slice(start, end + 4);
  const bodyStart = whole.indexOf('async () => {') + 'async () => {'.length;
  return whole.slice(bodyStart, whole.lastIndexOf('});'));
}

function makeClearWorld(requests, previewBody, confirmAnswer) {
  return {
    Promise, JSON, Object, console,
    fetch: (url, init) => {
      const method = ((init && init.method) || 'GET').toUpperCase();
      requests.push({url, method, body: init && init.body});
      const payload = url.indexOf('/preview') >= 0 ? previewBody : {ok: true};
      return Promise.resolve({json: () => Promise.resolve(payload)});
    },
    omniConfirm: confirmAnswer,
    toast: () => {},
    loadUnits: () => Promise.resolve(),
    refreshUsbExtenderCount: () => Promise.resolve(),
    stopPolling: () => {},
    startPolling: () => {},
    pollInterval: null,
  };
}

async function runClearHandler(body, sandbox) {
  vm.createContext(sandbox);
  vm.runInContext(
    `globalThis.__run = async () => {${body}};`, sandbox);
  await sandbox.__run();
}


// ------------------------------------------------------- the modal stack

const MODAL_STACK_JS = read('ui', 'modal-stack.js');

function loadStack() {
  const sandbox = {window: {}, Object, String, console};
  sandbox.window.window = sandbox.window;
  vm.createContext(sandbox);
  vm.runInContext(MODAL_STACK_JS, sandbox);
  return sandbox.window.omniModalStack;
}

function fakeModal(name) {
  return {name: name, style: {zIndex: ''}};
}

suite.push(() => check('the most recently opened dialog is the one on top', () => {
  // The defect: every dialog was `.modal-backdrop` at one z-index, so two
  // open at once were ordered by document order. Settings is injected at the
  // end of <body> and the confirmation dialog is static markup further up, so
  // Settings painted over the thing it had opened.
  const stack = loadStack();
  const settings = fakeModal('settings');
  const dialog = fakeModal('dialog');

  stack.raise(settings);
  stack.raise(dialog);

  const a = Number(settings.style.zIndex);
  const b = Number(dialog.style.zIndex);
  assert(a > 0 && b > 0, `nothing was assigned: ${a}, ${b}`);
  assert(b > a, `the dialog opened from Settings is underneath it: ${b} <= ${a}`);
  assert(stack.top() === dialog, 'the stack disagrees about what is on top');
}));

suite.push(() => check('closing the top one gives the level back', () => {
  const stack = loadStack();
  const settings = fakeModal('settings');
  const dialog = fakeModal('dialog');
  stack.raise(settings);
  stack.raise(dialog);
  stack.release(dialog);
  assert(dialog.style.zIndex === '',
    `a closed dialog is still claiming ${dialog.style.zIndex}`);
  assert(stack.top() === settings, 'Settings did not come back to the top');
  assert(stack.depth() === 1, `depth is ${stack.depth()}`);
}));

suite.push(() => check('closing out of order does not strand a level', () => {
  // A counter would hand the next dialog a level that is already taken.
  const stack = loadStack();
  const first = fakeModal('first');
  const second = fakeModal('second');
  const third = fakeModal('third');
  stack.raise(first);
  stack.raise(second);
  stack.raise(third);
  stack.release(second);                    // the middle one goes first
  assert(Number(third.style.zIndex) > Number(first.style.zIndex),
    'the top dialog fell behind one underneath it');
  const fourth = fakeModal('fourth');
  stack.raise(fourth);
  assert(Number(fourth.style.zIndex) > Number(third.style.zIndex),
    'a new dialog opened underneath an existing one');
}));

suite.push(() => check('re-opening the same dialog does not stack it twice', () => {
  const stack = loadStack();
  const dialog = fakeModal('dialog');
  stack.raise(dialog);
  stack.raise(dialog);
  stack.raise(dialog);
  assert(stack.depth() === 1, `depth is ${stack.depth()}`);
  stack.release(dialog);
  assert(stack.depth() === 0, 'a repeat open left a level behind');
  assert(dialog.style.zIndex === '', 'and it is still claiming one');
}));

suite.push(() => check('releasing something that was never raised is harmless', () => {
  const stack = loadStack();
  stack.release(fakeModal('never-opened'));
  stack.release(null);
  assert(stack.depth() === 0);
}));

suite.push(() => check('it sits above the modal layer and below the loading overlay', () => {
  // A dialog must clear `.modal-backdrop` (20000) and the notice layer
  // (20010); the loading overlay (30000) is deliberately in front of
  // everything, because it means the page is not ready to be used.
  const stack = loadStack();
  const dialog = fakeModal('dialog');
  stack.raise(dialog);
  const z = Number(dialog.style.zIndex);
  assert(z > 20010, `${z} does not clear the notice layer`);
  assert(z < 30000, `${z} is in front of the loading overlay`);

  const settingsCss = read('ui', 'settings.css');
  assert(/\.modal-backdrop\{[^}]*z-index:20000/.test(settingsCss),
    'the base modal layer moved; the stack base needs revisiting');
  assert(/\.notice-backdrop\{z-index:20010\}/.test(settingsCss));
}));

suite.push(() => check('twenty dialogs deep is still ordered', () => {
  const stack = loadStack();
  const modals = [];
  for (let i = 0; i < 20; i++) {
    const m = fakeModal('m' + i);
    stack.raise(m);
    modals.push(m);
  }
  for (let i = 1; i < modals.length; i++) {
    assert(Number(modals[i].style.zIndex) > Number(modals[i - 1].style.zIndex),
      `dialog ${i} is not above ${i - 1}`);
  }
  assert(Number(modals[19].style.zIndex) < 30000,
    'a deep stack climbs over the loading overlay');
}));

// ------------------------------------------ the pages actually use it

suite.push(() => check('Settings joins the stack when it opens and leaves when it closes', () => {
  const code_ = code(SETTINGS_JS);
  assert(/omniModalStack\.raise\(backdrop\)/.test(code_),
    'Settings does not join the stack');
  assert(/omniModalStack\.release\(backdrop\)/.test(code_),
    'Settings never leaves the stack, so it keeps its level forever');
  // Raised after it is shown, released when it is hidden.
  const open = code_.indexOf("backdrop.style.display = 'flex'");
  const raise = code_.indexOf('omniModalStack.raise(backdrop)');
  assert(open < raise && raise - open < 600,
    'the raise is not beside the show');
}));

suite.push(() => check('both dialog implementations join the stack', () => {
  for (const [name, source] of [['ui/confirm.js', CONFIRM_JS],
                                ['ui/index.html', INDEX_HTML]]) {
    const body = code(source);
    assert(/omniModalStack\.raise\(backdrop\)/.test(body),
      `${name} does not join the modal stack`);
    assert(/omniModalStack\.release\(backdrop\)/.test(body),
      `${name} never leaves it`);
  }
}));

suite.push(() => check('nobody reached for a giant z-index instead', () => {
  // The fix is a stack, not a bigger number. A five- or six-figure literal
  // parked in a dialog is the thing this replaced.
  const inline = code(INDEX_HTML);
  const dialog = inline.slice(inline.indexOf('function omniConfirm('));
  const body = dialog.slice(0, dialog.indexOf('\n}'));
  assert(!/z-?[Ii]ndex\s*[:=]\s*['"]?\d{4,}/.test(body),
    'a hard-coded z-index was added to the dialog');
}));

suite.push(() => check('every page with modals loads the stack, before it needs it', () => {
  const pages = ['ui/index.html', 'ui/admin/users.html', 'ui/matrix/index.html',
                 'ui/matrix/configure.html', 'ui/matrix/usb.html',
                 'ui/matrix/multiview.html', 'ui/user/index.html'];
  for (const page of pages) {
    const html = read(...page.split('/'));
    assert(/modal-stack\.js/.test(html), `${page} does not load the stack`);
    const stackAt = html.indexOf('modal-stack.js');
    for (const user of ['settings.js', 'confirm.js']) {
      const at = html.indexOf('/ui/' + user);
      if (at >= 0) {
        assert(stackAt < at,
          `${page} loads ${user} before the stack it uses`);
      }
    }
    // csrf.js stays first: a script before it would hold the unwrapped fetch.
    const csrfAt = html.indexOf('/ui/csrf.js');
    if (csrfAt >= 0) {
      assert(csrfAt < stackAt, `${page} loads the stack before csrf.js`);
    }
  }
}));

suite.push(() => check('a missing stack does not break a dialog', () => {
  // Every call is guarded, so a page that somehow has no stack still shows
  // its dialogs -- just without the ordering.
  for (const [name, source] of [['ui/confirm.js', CONFIRM_JS],
                                ['ui/index.html', INDEX_HTML],
                                ['ui/settings.js', SETTINGS_JS]]) {
    const body = code(source);
    const calls = body.match(/omniModalStack\.\w+\(/g) || [];
    const guards = body.match(/window\.omniModalStack\s*\)/g) || [];
    assert(guards.length >= calls.length / 2,
      `${name} calls the stack without checking it is there`);
  }
}));

// --------------------------------------------------- passphrase minimum

suite.push(() => check('the client and the server agree the minimum is eight', () => {
  assert(/const BACKUP_MIN_PASSPHRASE = 8;/.test(SETTINGS_JS),
    'the page does not know the minimum');
  const server = read('omni_backup.py');
  assert(/^MIN_PASSPHRASE = 8$/m.test(server),
    'the server minimum is not 8');
  // And what the operator is shown says the same number.
  assert(/At least 8 characters/.test(SETTINGS_JS),
    'the placeholder still says something else');
  assert(/at least 8 /i.test(SETTINGS_JS),
    'the explanation still says something else');
  assert(!/12 characters/.test(SETTINGS_JS),
    'the old minimum is still on screen somewhere');
}));

suite.push(() => check('a short passphrase is refused without a round trip', async () => {
  const calls = [];
  let said = null;
  const exportFn = lift('async function exportConfiguration(', {
    askForPassphrase: () => Promise.resolve('short'),
    backupStatus: (text, tone) => { said = {text, tone}; },
    filenameFrom: () => '',
    BACKUP_MIN_PASSPHRASE: 8,
    fetch: (url) => { calls.push(url); return Promise.resolve({ok: true}); },
    document: {createElement: () => ({click() {}, remove() {}}),
               body: {appendChild: () => {}}},
  });
  await exportFn();
  assert(calls.length === 0, `it asked the server anyway: ${calls}`);
  assert(said && said.tone === 'error', 'nothing was said');
  assert(/at least 8/i.test(said.text), `it said: ${said.text}`);
}));

suite.push(() => check('exactly eight is allowed through', async () => {
  const calls = [];
  const exportFn = lift('async function exportConfiguration(', {
    askForPassphrase: () => Promise.resolve('12345678'),
    backupStatus: () => {},
    filenameFrom: () => 'x.omnibak',
    BACKUP_MIN_PASSPHRASE: 8,
    fetch: (url, opts) => {
      calls.push({url, body: opts && opts.body});
      return Promise.resolve({
        ok: true,
        headers: {get: () => 'attachment; filename="x.omnibak"'},
        blob: () => Promise.resolve('BYTES'),
        json: () => Promise.resolve({}),
      });
    },
    setTimeout: () => {},
    document: {createElement: () => ({click() {}, remove() {}}),
               body: {appendChild: () => {}}},
  });
  await exportFn();
  assert(calls.length === 1, `made ${calls.length} request(s)`);
  assert(JSON.parse(calls[0].body).passphrase === '12345678');
}));

suite.push(() => check('no shape is demanded of a passphrase', () => {
  // No mandatory capital, digit or symbol anywhere in the flow.
  const flow = code(SETTINGS_JS).slice(
    code(SETTINGS_JS).indexOf('async function exportConfiguration'));
  const body = flow.slice(0, flow.indexOf('\n}'));
  // A rule would be a test against the value, not a word in a sentence --
  // "letters, numbers, symbols and spaces are all fine" says the opposite of
  // what matching the word "symbol" would suggest.
  assert(!/passphrase\s*\.\s*match\(/.test(body),
    'the page runs a pattern over the passphrase');
  assert(!/\/\[.*\]\/\s*\.\s*test\(\s*passphrase/.test(body),
    'the page tests the passphrase against a character class');
  assert(!/must contain|at least one/i.test(body),
    'the page demands particular characters');
  // The only thing it may check is how long it is.
  const checks = body.match(/if \(passphrase[^)]*\)/g) || [];
  checks.forEach((check) => {
    assert(/length|=== null/.test(check),
      `the page checks something other than length: ${check}`);
  });
}));

(async function run() {
  for (const scenario of suite) await scenario();
  results.forEach(([status, name]) => console.log(`${status} ${name}`));
  console.log(`\n${results.length - failures}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
}());

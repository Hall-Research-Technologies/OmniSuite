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

(async function run() {
  for (const scenario of suite) await scenario();
  results.forEach(([status, name]) => console.log(`${status} ${name}`));
  console.log(`\n${results.length - failures}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
}());

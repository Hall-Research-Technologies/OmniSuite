// Where the keyboard is when the Restore confirmation opens.
//
// Found by packaged acceptance: the confirmation that replaces the accounts and
// permissions on this computer opened with the focus on its red Restore button,
// so a single Enter confirmed it. The project's rule is that when Yes starts
// something large or irreversible, Cancel takes the focus.
//
// The cause was not the shared dialog in ui/confirm.js, which focuses Cancel for
// any `danger` dialog. Settings is opened from the A/V Matrix page, and that page
// carries its own copy of the dialog (ui/index.html), whose Cancel default is
// opt-in through `defaultAction: 'cancel'` -- which the Restore confirmation
// did not pass. `danger: true` there only changes the colour.
//
// This lifts the page's own dialog and Settings' own restore flow, joins them
// through the passphrase step as a person would, and reads where the focus ends
// up. Nothing is matched in source text.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {El} = require('./_fakedom.js');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const SETTINGS_JS = read('ui', 'settings.js');
const INDEX_HTML = read('ui', 'index.html');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message || err}`]); });
}
function assert(condition, message) { if (!condition) throw new Error(message || 'assertion failed'); }

function liftFrom(source, signature, extras) {
  const start = source.indexOf(signature);
  assert(start >= 0, `${signature} is not where this expects it`);
  const end = source.indexOf('\n}\n', start);
  assert(end > start, `could not find the end of ${signature}`);
  const name = signature.replace(/^(async )?function /, '').split('(')[0];
  const sandbox = Object.assign({Promise, JSON, Object, Array, String, console}, extras);
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(`${source.slice(start, end + 3)}\nglobalThis.__lifted = ${name};`, sandbox);
  return sandbox.__lifted;
}

// A fake element that also knows where the focus is and what is listening.
function world() {
  const document = {activeElement: null, _listeners: {}};
  class Node extends El {
    constructor(tag) { super(tag); this.isConnected = true; }
    focus() { document.activeElement = this; }
    removeEventListener(type, fn) {
      this._listeners[type] = (this._listeners[type] || []).filter((f) => f !== fn);
    }
  }
  document.addEventListener = (type, fn) => { (document._listeners[type] = document._listeners[type] || []).push(fn); };
  document.removeEventListener = (type, fn) => {
    document._listeners[type] = (document._listeners[type] || []).filter((f) => f !== fn);
  };
  const ids = {};
  ['confirm_backdrop', 'confirm_title', 'confirm_message', 'confirm_summary', 'confirm_list',
   'confirm_ok', 'confirm_cancel', 'confirm_checkbox', 'confirm_checkbox_input',
   'confirm_checkbox_label', 'confirm_field', 'confirm_field_input', 'confirm_field_label']
    .forEach((id) => { ids[id] = Object.assign(new Node('div'), {id}); });
  ids.confirm_ok.className = 'btn';
  ids.confirm_cancel.className = 'btn secondary';
  // The Settings "Restore" button that started everything: it holds the focus
  // when the flow begins, exactly as it does after being clicked.
  const opener = Object.assign(new Node('button'), {id: 'cfg_backup_restore', className: 'btn secondary'});
  opener.focus();
  return {document, ids, opener, $: (selector) => ids[selector.replace(/^#/, '')] || null};
}

// Run the real restore flow against the page's real dialog.
async function openRestoreConfirmation() {
  const w = world();
  const requests = [];
  const status = [];
  const omniConfirm = liftFrom(INDEX_HTML, 'function omniConfirm(', {
    $: w.$, document: w.document, escapeAttr: (v) => String(v), window: {},
  });
  const ask = liftFrom(SETTINGS_JS, 'async function askForPassphrase(', {
    window: {omniConfirm}, backupStatus: (t) => status.push(t),
    BACKUP_MIN_PASSPHRASE: 8,
  });
  const restore = liftFrom(SETTINGS_JS, 'async function restoreConfiguration(', {
    chooseBackupFile: () => Promise.resolve({name: 'b.omnibak'}),
    askForPassphrase: ask,
    backupStatus: (t) => status.push(t),
    backupSectionLabel: (n) => n,
    accountImpactLines: () => [],
    renderRestoreResult: () => {},
    postBackup: (p) => {
      requests.push(p);
      return Promise.resolve(p === '/api/backup/inspect'
        ? {ok: true, summary: {sections: [{name: 'accounts', detail: '1 account(s)'}], notes: []}}
        : {ok: true});
    },
    window: {omniConfirm},
  });
  const done = restore();
  const settle = () => new Promise((resolve) => setImmediate(resolve));

  // The passphrase dialog: type, press Enter in the field, as a person does.
  while (w.ids.confirm_backdrop.style.display !== 'flex') await settle();
  assert(w.ids.confirm_title.textContent.indexOf('Restore from') === 0,
    'the first dialog was not the passphrase step: ' + w.ids.confirm_title.textContent);
  w.ids.confirm_field_input.value = 'a-long-enough-backup-passphrase';
  for (const fn of (w.document._listeners.keydown || []).slice()) {
    fn({key: 'Enter', target: w.ids.confirm_field_input, preventDefault() {}});
  }

  // Wait for the confirmation itself.
  for (let i = 0; i < 200 && w.ids.confirm_title.textContent !== 'Replace this configuration?'; i++) {
    await settle();
  }
  assert(w.ids.confirm_title.textContent === 'Replace this configuration?',
    'the confirmation never opened: ' + w.ids.confirm_title.textContent);
  return {w, requests, status, done};
}

const suite = [];

suite.push(() => check('the Restore confirmation opens with the focus on Cancel', async () => {
  const {w, requests, done} = await openRestoreConfirmation();
  const active = w.document.activeElement;
  assert(active === w.ids.confirm_cancel,
    'focus is on ' + (active && active.id) + ' (' + (active && active.className) + '), so Enter would restore');
  assert(active !== w.ids.confirm_ok);
  // Cancel is the default, so it carries the primary treatment and Restore stays red.
  assert(!w.ids.confirm_cancel.classList.contains('secondary'), 'Cancel still looks secondary');
  assert(w.ids.confirm_ok.classList.contains('danger'), 'Restore lost its danger treatment');
  await w.ids.confirm_cancel.fire('click');
  await done;
  assert(requests.indexOf('/api/backup/restore') < 0, 'Cancel still restored: ' + JSON.stringify(requests));
}));

suite.push(() => check('Cancel makes no restore request and says nothing was changed', async () => {
  const {w, requests, status, done} = await openRestoreConfirmation();
  await w.ids.confirm_cancel.fire('click');
  await done;
  // Only the read-only inspection happened.
  assert(JSON.stringify(requests) === JSON.stringify(['/api/backup/inspect']), JSON.stringify(requests));
  assert(status.indexOf('Nothing was changed.') >= 0, JSON.stringify(status));
}));

suite.push(() => check('pressing Restore still restores', async () => {
  const {w, requests, done} = await openRestoreConfirmation();
  await w.ids.confirm_ok.fire('click');
  await done;
  assert(requests.indexOf('/api/backup/restore') >= 0, JSON.stringify(requests));
}));

suite.push(() => check('the dialog gives the next caller the arrangement in the markup', async () => {
  const {w, done} = await openRestoreConfirmation();
  await w.ids.confirm_cancel.fire('click');
  await done;
  assert(!w.ids.confirm_ok.classList.contains('secondary'));
  assert(w.ids.confirm_cancel.classList.contains('secondary'));
}));

(async function run() {
  for (const scenario of suite) await scenario();
  results.forEach(([status, name]) => console.log(`${status} ${name}`));
  console.log(`\n${results.length - failures}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
}());

// What the restore confirmation tells the person about to press Restore.
//
// "Replaces the accounts" is true and says nothing about who is about to be
// signed out. The server works out which accounts on this computer a backup
// would remove or replace, and the dialog has to say so in words: how many, by
// name, and what happens to anyone signed in as them. The function that builds
// the sentences is lifted out of settings.js and run, so what is asserted is
// the text a person reads, not the text of the source.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const SETTINGS_JS = fs.readFileSync(path.join(ROOT, 'ui', 'settings.js'), 'utf8')
  .split('\r\n').join('\n');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message || err}`]); });
}
function assert(condition, message) { if (!condition) throw new Error(message || 'assertion failed'); }

function lift(signature, extras) {
  const start = SETTINGS_JS.indexOf(signature);
  assert(start >= 0, `${signature} is not where this expects it`);
  const end = SETTINGS_JS.indexOf('\n}\n', start);
  assert(end > start, `could not find the end of ${signature}`);
  const body = SETTINGS_JS.slice(start, end + 3);
  const name = signature.replace(/^(async )?function /, '').split('(')[0];
  const sandbox = Object.assign({Promise, JSON, Object, Array, String, console}, extras);
  sandbox.window = sandbox.window || {};
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(`${body}\nglobalThis.__lifted = ${name};`, sandbox);
  return sandbox.__lifted;
}

const lines = lift('function accountImpactLines(', {});
const text = (impact) => lines(impact).join('\n');

const suite = [];

suite.push(() => check('a backup that changes no account says nothing', () => {
  assert(lines({applies: true, known: true, current: 2, in_backup: 2,
                removed: [], replaced: [], added: []}).length === 0);
}));

suite.push(() => check('a backup that is silent about accounts says nothing', () => {
  assert(lines({applies: false}).length === 0);
  assert(lines(null).length === 0);
  assert(lines(undefined).length === 0);
}));

suite.push(() => check('removed accounts are counted, named, and their sign-out is stated', () => {
  const said = text({applies: true, known: true, current: 3, in_backup: 1,
                     removed: ['alice', 'bob'], replaced: [], added: []});
  assert(/2 account\(s\) on this computer are not in the backup and will be removed: alice, bob/.test(said), said);
  assert(/signed out at once/.test(said), said);
  assert(/Direct Access link stops working/.test(said), said);
}));

suite.push(() => check('a backup with no accounts says every administrator goes, and how to recover', () => {
  const said = text({applies: true, known: true, current: 2, in_backup: 0,
                     removed: ['root', 'bob'], replaced: [], added: []});
  assert(/no accounts/.test(said), said);
  assert(/including every administrator/.test(said), said);
  assert(/create a new administrator from this computer/.test(said), said);
}));

suite.push(() => check('replaced accounts are named and only they lose their sign-in', () => {
  const said = text({applies: true, known: true, current: 3, in_backup: 3,
                     removed: [], replaced: ['carol'], added: []});
  assert(/1 account\(s\) come back with a different password, link, role or permissions \(carol\)/.test(said), said);
  assert(/stay signed in/.test(said), said);
  assert(!/will be removed/.test(said), said);
}));

suite.push(() => check('a long list is shortened, and says how many it left out', () => {
  const names = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'];
  const said = text({applies: true, known: true, current: 8, in_backup: 0 + 1,
                     removed: names, replaced: [], added: []});
  assert(/a, b, c, d, e, f and 2 more/.test(said), said);
  assert(!/, g/.test(said), 'it listed past the limit');
}));

suite.push(() => check('when the server could not work it out, the dialog does not claim nothing happens', () => {
  const said = text({applies: true, known: false, removed: [], replaced: []});
  assert(/Accounts on this computer will be replaced/.test(said), said);
  assert(/may be signed out/.test(said), said);
}));

suite.push(() => check('the sentences carry no internal name', () => {
  const said = text({applies: true, known: true, current: 3, in_backup: 1,
                     removed: ['alice'], replaced: ['bob'], added: []});
  for (const forbidden of ['applies', 'in_backup', 'impact', 'fingerprint', 'sessions_ended']) {
    assert(!said.includes(forbidden), `${forbidden} reached the operator`);
  }
}));

// The dialog is built from them: lifted and driven, not matched in source.
suite.push(() => check('the confirmation shows the account sentences before the section list', async () => {
  const asked = [];
  const accountImpactLines = lines;
  const restore = lift('async function restoreConfiguration(', {
    accountImpactLines,
    chooseBackupFile: () => Promise.resolve({name: 'b.omnibak'}),
    askForPassphrase: () => Promise.resolve('a-long-enough-backup-passphrase'),
    backupStatus: () => {},
    backupSectionLabel: (n) => n,
    renderRestoreResult: () => {},
    postBackup: (p) => Promise.resolve(p === '/api/backup/inspect'
      ? {ok: true,
         summary: {sections: [{name: 'accounts', detail: '1 account(s)'}], notes: []},
         account_impact: {applies: true, known: true, current: 3, in_backup: 1,
                          removed: ['alice', 'bob'], replaced: [], added: []}}
      : {ok: true}),
    window: {omniConfirm: (o) => { asked.push(o); return Promise.resolve(false); }},
  });
  await restore();
  assert(asked.length === 1, 'the confirmation was not shown');
  const message = asked[0].message;
  const warning = message.indexOf('will be removed: alice, bob');
  const section = message.indexOf('• accounts');
  assert(warning > 0, message);
  assert(section > warning, 'the account warning is buried under the section list');
  assert(asked[0].danger === true, 'it is not the dangerous kind');
}));

suite.push(() => check('a result that ended sign-ins says how many', () => {
  const shown = [];
  const render = lift('function renderRestoreResult(', {
    backupStatus: (t) => shown.push(t),
    backupSectionLabel: (n) => n,
    window: {omniConfirm: () => Promise.resolve(true)},
  });
  render({ok: true, applied: {written: ['users.json']}, sessions_ended: 3});
  assert(shown.some((t) => /3 sign-in\(s\) ended/.test(t)), JSON.stringify(shown));
  const none = [];
  lift('function renderRestoreResult(', {
    backupStatus: (t) => none.push(t), backupSectionLabel: (n) => n,
    window: {omniConfirm: () => Promise.resolve(true)},
  })({ok: true, applied: {written: ['users.json']}, sessions_ended: 0});
  assert(!none.some((t) => /sign-in/.test(t)), 'it mentioned sign-ins when none ended');
}));

suite.push(() => check('an accounts-empty restore tells the operator what to do, not who came with it', () => {
  const shown = [];
  const render = lift('function renderRestoreResult(', {
    backupStatus: () => {},
    backupSectionLabel: (n) => n,
    window: {omniConfirm: (o) => { shown.push(o.message); return Promise.resolve(true); }},
  });
  const note = 'The backup contained no administrator account, so nobody can sign in as one now.';
  render({ok: true, applied: {written: ['users.json']}, sessions_ended: 0,
          admin_recovery: {administrators: [], note}});
  assert(shown.length === 1 && shown[0].indexOf(note) >= 0, JSON.stringify(shown));
  assert(shown[0].indexOf('Administrator account(s) from the backup') < 0,
         'it named administrators that do not exist');
}));

(async function run() {
  for (const scenario of suite) await scenario();
  results.forEach(([status, name]) => console.log(`${status} ${name}`));
  console.log(`\n${results.length - failures}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
}());

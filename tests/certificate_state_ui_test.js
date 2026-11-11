// What Settings says about the HTTPS certificate, executed rather than read.
//
// The wording follows the certificate that actually exists, never merely that
// HTTPS was chosen:
//
//   generated, usable   HTTPS - encrypted . certificate active
//   supplied, usable    HTTPS - encrypted . custom certificate active
//   anything unusable   HTTPS - certificate required
//
// and nothing here may call a certificate "trusted": OmniSuite cannot know
// whether a browser trusts whoever issued it, and a self-signed one is exactly
// the case where it does not.
//
// The helpers are lifted out of ui/settings.js between their own markers and
// run against certificate descriptions shaped like the server's.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {El} = require('./_fakedom.js');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const SETTINGS = read('ui', 'settings.js');

let failures = 0, total = 0;
const results = [];
function check(name, fn) {
  total += 1;
  try { fn(); results.push(['ok  ', name]); }
  catch (err) { failures += 1; results.push(['FAIL', name + ': ' + (err && err.message || err)]); }
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
function eq(a, b, m) { assert(a === b, (m || 'differ') + ': ' + JSON.stringify(a) + ' !== ' + JSON.stringify(b)); }

const begin = SETTINGS.indexOf('// BEGIN certificate wording');
const end = SETTINGS.indexOf('// END certificate wording');
assert(begin > 0 && end > begin, 'the certificate wording markers are missing');

function load(select, win) {
  const document = {getElementById: (id) => (id === 'cfg_remote_transport' ? select : null)};
  const sandbox = {document, Array, console, window: win || {}};
  vm.createContext(sandbox);
  vm.runInContext(SETTINGS.slice(begin, end) +
    '\nglobalThis.api = {certificateKind, httpsLabel, certificateWords, certificateDetail, describeHttpsOption, certificateFor, confirmCertificateReplacement};',
    sandbox);
  return sandbox.api;
}

const FP = 'A1B2C3D4E5F60718293A4B5C6D7E8F90';
const generated = {present: true, readable: true, generated: true, usable: true,
  self_signed: true, subject: '10.20.30.40', san: ['10.20.30.40'], days_remaining: 700,
  fingerprint: FP, for_address: '10.20.30.40', matches_address: true, not_after: 'Nov 5 2028'};
const custom = Object.assign({}, generated, {generated: false, self_signed: false,
  subject: 'omni.example.test', san: ['omni.example.test'], days_remaining: 90});
const GUIDE = 'HTTPS certificate is missing or invalid. Turn off Remote Access, then create or install a certificate for this network before enabling HTTPS.';

const api = load(new El('select'));
const label = (c, state) => api.httpsLabel(api.certificateKind(c, state));

// ---- 1 generated active
check('a generated certificate in use is "certificate active", self-signed, with its facts', () => {
  eq(label(generated), 'HTTPS — encrypted · certificate active');
  const words = api.certificateWords(generated, {});
  eq(words, 'Self-signed certificate active for 10.20.30.40 (700 days left). Your browser may warn because this certificate is self-signed. Fingerprint: ' + FP);
});

// ---- 2 custom active
check('a supplied certificate in use is "custom certificate active"', () => {
  eq(label(custom), 'HTTPS — encrypted · custom certificate active');
  const words = api.certificateWords(custom, {});
  assert(words.indexOf('Custom certificate active for') === 0, words);
  assert(words.indexOf('(90 days left)') > 0 && words.indexOf(FP) > 0, words);
  assert(words.indexOf('self-signed') < 0, 'a supplied certificate is not described as self-signed');
});

// ---- 3 missing
check('no certificate is "certificate required", with the instruction', () => {
  eq(label({present: false}), 'HTTPS — certificate required');
  eq(api.certificateWords({present: false}, {}), GUIDE);
  eq(label(undefined), 'HTTPS — certificate required');
  eq(label(null), 'HTTPS — certificate required');
});

// ---- 4 expired
check('an expired certificate is "certificate required" and says so', () => {
  const cert = Object.assign({}, generated, {expired: true, days_remaining: -3});
  eq(label(cert), 'HTTPS — certificate required');
  eq(api.certificateWords(cert, {}), 'The certificate has expired. ' + GUIDE);
  eq(api.certificateDetail(cert, 'required'), 'Expired Nov 5 2028');
  eq(label(Object.assign({}, custom, {expired: true})), 'HTTPS — certificate required');
});

// ---- 5 unreadable
check('an unreadable certificate is "certificate required"', () => {
  const cert = {present: true, readable: false, error: 'Not a readable PEM certificate (ValueError).'};
  eq(label(cert), 'HTTPS — certificate required');
  eq(api.certificateWords(cert, {}), 'Not a readable PEM certificate (ValueError). ' + GUIDE);
});

check('a certificate that cannot actually be loaded (its key) is "certificate required"', () => {
  const cert = Object.assign({}, generated, {usable: false,
    unusable_reason: 'That private key does not match that certificate.'});
  eq(label(cert), 'HTTPS — certificate required');
  eq(api.certificateWords(cert, {}), 'That private key does not match that certificate. ' + GUIDE);
  eq(label(Object.assign({}, custom, {usable: false})), 'HTTPS — certificate required');
});

// ---- 6 wrong network
check('a certificate OmniSuite created for another network is "certificate required"', () => {
  const cert = Object.assign({}, generated, {for_address: '10.99.99.9', matches_address: false});
  eq(label(cert), 'HTTPS — certificate required');
  const words = api.certificateWords(cert, {});
  assert(words.indexOf('The certificate OmniSuite created is for 10.20.30.40, not for 10.99.99.9. ') === 0, words);
  assert(words.endsWith(GUIDE));
  assert(/Create one for this network/.test(api.certificateDetail(cert, 'required')));
});

check('with no address to judge against, a generated certificate is not called wrong-network', () => {
  const cert = Object.assign({}, generated, {for_address: '', matches_address: false});
  eq(label(cert), 'HTTPS — encrypted · certificate active');
});

check('a supplied certificate is never judged against the adapter', () => {
  const cert = Object.assign({}, custom, {for_address: '10.99.99.9', matches_address: false});
  eq(label(cert), 'HTTPS — encrypted · custom certificate active');
  const detail = api.certificateDetail(cert, 'custom');
  assert(/will not alter a certificate you supplied/.test(detail), detail);
});

// ---- 7, 8, 9, 10 the label follows the certificate, not the selection
check('the label is the same whether HTTP or HTTPS is selected, and enabled or not', () => {
  const select = new El('select');
  const option = Object.assign(new El('option'), {value: 'https'});
  select.options = [Object.assign(new El('option'), {value: 'http'}), option];
  const lifted = load(select);
  [['http', false], ['https', false], ['http', true], ['https', true]].forEach(([choice, enabled]) => {
    select.value = choice;
    lifted.describeHttpsOption({transport: choice, enabled, certificate: generated});
    eq(option.textContent, 'HTTPS — encrypted · certificate active',
       'selection ' + choice + ', enabled ' + enabled);
  });
  lifted.describeHttpsOption({transport: 'https', enabled: true, certificate: {present: false}});
  eq(option.textContent, 'HTTPS — certificate required', 'choosing HTTPS must not make a missing certificate look active');
  lifted.describeHttpsOption({transport: 'http', certificate: custom});
  eq(option.textContent, 'HTTPS — encrypted · custom certificate active');
});

check('the statuses come from the certificate state, not from a transport field', () => {
  const body = SETTINGS.slice(begin, end);
  const code = body.replace(/\/\/.*$/gm, '');
  const kind = /function certificateKind[\s\S]*?\n  }\n/.exec(code)[0];
  assert(!/transport/.test(kind), 'certificateKind reads the selection');
  assert(!/enabled/.test(kind), 'certificateKind reads whether Remote Access is on');
});

// ---- 11 never "trusted"
check('nothing OmniSuite says about a certificate calls it trusted', () => {
  const outputs = [];
  [generated, custom, {present: false}, {present: true, readable: false},
   Object.assign({}, generated, {expired: true}),
   Object.assign({}, generated, {for_address: 'x', matches_address: false})].forEach((cert) => {
    const kind = api.certificateKind(cert, {});
    outputs.push(api.httpsLabel(kind), api.certificateWords(cert, {}), api.certificateDetail(cert, kind));
  });
  outputs.forEach((o) => assert(!/trust/i.test(o), 'the word trust appears: ' + o));
  const code = SETTINGS.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map((l) => l.split('//')[0]).join('\n');
  const mentions = code.split('\n').filter((l) => /trusted|should connect without warning/i.test(l));
  assert(mentions.length === 0, 'Settings claims trust: ' + mentions.join(' | '));
});

check('the self-signed notice states a limit, not an endorsement', () => {
  const warning = /browsers will warn the first[\s\S]{0,200}/i.exec(SETTINGS);
  assert(warning, 'the self-signed explanation is gone');
  assert(/Whether browsers accept[\s\S]{0,80}depends on who issued it/.test(SETTINGS));
});

// ---- 12 the static markup claims nothing before the state loads
check('the HTTPS entry does not claim a certificate before the state is read', () => {
  assert(SETTINGS.indexOf('needs a certificate') < 0);
  assert(SETTINGS.indexOf('HTTPS &mdash; checking certificate') > 0);
});

check('the label is refreshed before any early return in the renderer', () => {
  const at = SETTINGS.indexOf('describeHttpsOption(remoteState);');
  const early = SETTINGS.indexOf('if (!remoteState.enabled && remoteState.configured)');
  assert(at > 0 && early > at, 'the option would stay stale when Remote Access is configured but unavailable');
});

// ---- certificate identity: the common name is the machine name
const machineNamed = Object.assign({}, generated, {subject: 'STUDIO-B', organization: 'Hall Research',
  organizational_unit: 'OmniSuite', addresses: ['10.20.30.40'], san: ['STUDIO-B', '10.20.30.40']});

check('a certificate named for the computer is described by its addresses, not its machine name', () => {
  eq(api.certificateFor(machineNamed), '10.20.30.40');
  const words = api.certificateWords(Object.assign({}, machineNamed, {for_address: ''}), {});
  assert(words.indexOf('active for 10.20.30.40') > 0 && words.indexOf('STUDIO-B') < 0, words);
  eq(label(machineNamed), 'HTTPS — encrypted · certificate active');
});

check('a certificate with no addresses is still described by its subject', () => {
  eq(api.certificateFor({subject: 'omni.example.test'}), 'omni.example.test');
  eq(api.certificateFor({}), '');
});

check('a machine-named certificate for another network says which address it is for', () => {
  const other = Object.assign({}, machineNamed, {for_address: '10.20.30.99', matches_address: false});
  const words = api.certificateWords(other, {});
  assert(words.indexOf('is for 10.20.30.40, not for 10.20.30.99') > 0, words);
  eq(label(other), 'HTTPS — certificate required');
});

// ---- replacing an existing certificate is asked, with Cancel focused
async function replacement(existing, answer, hasDialog) {
  const calls = [];
  const win = hasDialog === false ? {} : {omniConfirm: (o) => { calls.push(o); return Promise.resolve(answer); }};
  const result = await load(new El('select'), win).confirmCertificateReplacement(existing, '10.20.30.40');
  return {result, calls};
}
const later = [];
later.push(async () => {
  const none = await replacement({present: false}, true);
  assert(none.result === true && none.calls.length === 0, 'asked when there was nothing to replace');
  const own = await replacement(generated, true);
  assert(own.result === true && own.calls.length === 1, 'not asked before replacing');
  eq(own.calls[0].danger, true, 'Cancel must take the focus (danger treatment)');
  assert(/fingerprint/i.test(own.calls[0].message) && /three years/.test(own.calls[0].message), own.calls[0].message);
  const no = await replacement(generated, false);
  assert(no.result === false, 'Cancel still replaced');
  const supplied = await replacement({present: true, path_configured: true}, true);
  assert(/files are left where they are/.test(supplied.calls[0].message), 'a supplied certificate must say its files are untouched');
  const noDialog = await replacement(generated, true, false);
  assert(noDialog.result === false, 'without the shared dialog the replacement must be refused');
});
check('the replacement flow source awaits the confirmation before any request', () => {
  const flow = SETTINGS.slice(SETTINGS.indexOf("certGenerate.addEventListener('click'"));
  const bounded = flow.slice(0, flow.indexOf("if (certSave) {"));
  assert(bounded.indexOf('await confirmCertificateReplacement(') > 0
    && bounded.indexOf('await confirmCertificateReplacement(') < bounded.indexOf("fetch('/api/admin/certificate'"),
    'the request can precede the confirmation');
  assert(bounded.indexOf("dataset.busy = '1'") < bounded.indexOf('await confirmCertificateReplacement('),
    'single-flight must be taken before the dialog opens');
  // The refusal must sit between the answer and the request, comments removed
  // so a sentence naming it cannot satisfy this with the code gone.
  const code = bounded.split('\n').map((l) => l.split('//')[0]).join('\n');
  const answered = code.indexOf('await confirmCertificateReplacement(');
  const refused = code.indexOf('if (!go) return;', answered);
  const requested = code.indexOf("fetch('/api/admin/certificate'", answered);
  assert(refused > answered && refused < requested, 'Cancel does not stop the request');
});

(async () => {
  for (const t of later) {
    try { await t(); results.push(['ok  ', 'replacement confirmation behaviour']); total += 1; }
    catch (err) { failures += 1; total += 1; results.push(['FAIL', 'replacement confirmation behaviour: ' + (err && err.message || err)]); }
  }
  results.forEach(([s, n]) => console.log(s + ' ' + n));
  console.log((total - failures) + '/' + total + ' checks passed');
  process.exit(failures ? 1 : 0);
})();

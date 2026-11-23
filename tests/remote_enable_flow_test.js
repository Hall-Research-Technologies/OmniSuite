// Turning on Remote Access over HTTPS: the certificate decision and what follows it.
//
// The flow is executed, not read. The certificate section of ui/settings.js is lifted out
// whole -- the toggle's handler, the decision, the creation flow and the wording rules --
// and driven with a scripted dialog, a recording fetch and a state that follows the
// requests it receives. What is asserted is what the operator is asked and what the
// server is sent, in order.
//
// The defects this fences:
//   * Create / Regenerate Certificate was followed by a SECOND confirmation of the same
//     action ("Replace this certificate?"), so an approved decision was asked twice.
//   * An OmniSuite certificate for another network was offered "Create Certificate"
//     rather than a regeneration that says what it changes.
//   * A SUPPLIED certificate was offered a replacement. It belongs to the site.
//   * Nothing may turn Remote Access on merely because a certificate was made: the last
//     question, "Turn on remote access?", is always asked and always last.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const SETTINGS = read('ui', 'settings.js');

let failures = 0;
const results = [];
async function check(name, fn) {
  try { await fn(); results.push(['ok  ', name]); }
  catch (err) { failures++; results.push(['FAIL', name + ': ' + (err && err.message || err)]); }
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }

const wordingStart = SETTINGS.indexOf('  // BEGIN certificate wording');
const wordingEnd = SETTINGS.indexOf('  // END certificate wording');
const flowStart = SETTINGS.indexOf('  // ---- certificate ---');
const flowEnd = SETTINGS.indexOf('  if (cfgTsExportBtn) {');
assert(wordingStart > 0 && wordingEnd > wordingStart && flowStart > wordingEnd && flowEnd > flowStart,
  'the certificate section markers moved; this test lifts it whole');
const LIFTED = SETTINGS.slice(wordingStart, wordingEnd) + '\n' + SETTINGS.slice(flowStart, flowEnd);

const ADDRESS = '192.0.2.10';
const OTHER = '198.51.100.7';
const cert = (over) => Object.assign({
  present: true, readable: true, generated: true, usable: true, expired: false,
  san: [ADDRESS], addresses: [ADDRESS], subject: 'test-host',
  fingerprint: 'AA11BB22CC33DD44EE55FF6677889900',
  for_address: ADDRESS, matches_address: true, days_remaining: 700,
}, over || {});
const KINDS = {
  valid: () => cert(),
  none: () => ({present: false, for_address: ADDRESS, matches_address: false, generated: true}),
  stale: () => cert({san: [OTHER], addresses: [OTHER], matches_address: false}),
  damaged: () => cert({readable: false, usable: false, error: 'Not a readable PEM certificate (ValueError).'}),
  suppliedBad: () => cert({generated: false, usable: false,
    unusable_reason: 'The private key does not match the certificate.', path_configured: true}),
  suppliedOtherName: () => cert({generated: false, san: ['site.example.test'],
    matches_address: false, path_configured: true}),
};

function stub(id) {
  const listeners = {};
  return {id, value: '', hidden: false, disabled: false, textContent: '', dataset: {}, style: {},
    children: [], listeners,
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    dispatchEvent() {}, setAttribute() {}, removeAttribute() {},
    selectedOptions: [], classList: {toggle() {}, add() {}, remove() {}}};
}

function scenario(kind, answers, opts) {
  opts = opts || {};
  const els = {};
  const getEl = (id) => (els[id] = els[id] || stub(id));
  const log = {dialogs: [], fetches: [], toasts: [], order: []};
  const queue = answers.slice();
  const sandbox = {
    Event: function Event(t) { this.type = t; },
    JSON, Array, Object, Promise, console,
    document: {getElementById: getEl, addEventListener() {}},
    window: {
      toast: (m, k) => log.toasts.push([m, k]),
      omniConfirm: opts.noDialog ? undefined : async (o) => {
        log.dialogs.push(o); log.order.push('dialog:' + o.title);
        return queue.length ? queue.shift() : false;
      },
    },
    __made: false,
    fetch: async (url, init) => {
      const body = JSON.parse((init && init.body) || '{}');
      log.fetches.push([url, body]);
      log.order.push('fetch:' + url + ':' + (body.action || (body.enabled ? 'on' : 'off')));
      if (url === '/api/admin/certificate' && body.action === 'generate') sandbox.__made = true;
      return {ok: true, json: async () => ({ok: true})};
    },
  };
  const adapter = getEl('cfg_remote_adapter');
  adapter.selectedOptions = [{value: 'adapter-1', dataset: {address: ADDRESS},
    textContent: 'Test network (' + ADDRESS + ')'}];
  getEl('cfg_remote_transport').value = 'https';
  vm.createContext(sandbox);
  // remoteState and loadRemoteAccess are the page's own free variables. The reload
  // after a request is modelled by the state following what was sent: a generated
  // certificate is a valid one.
  sandbox.__kind = KINDS[kind]();
  sandbox.__valid = KINDS.valid();
  vm.runInContext(
    'var remoteState = {enabled: false, transport: "https", certificate: __kind};\n' +
    'async function loadRemoteAccess() { if (__made) remoteState = Object.assign({}, remoteState, {certificate: __valid}); }\n' +
    LIFTED, sandbox);
  return {sandbox, log, getEl,
    toggle: async () => { for (const fn of getEl('cfg_remote_toggle').listeners.click) await fn({}); },
    create: async () => {
      for (const fn of getEl('cfg_cert_generate').listeners.click) await fn({});
      await new Promise((resolve) => setTimeout(resolve, 25));
    }};
}

const TURN_ON = 'Turn on remote access?';
const titles = (s) => s.log.dialogs.map((d) => d.title);
const requests = (s) => s.log.fetches.map(([u, b]) => u + ':' + (b.action || (b.enabled ? 'on' : 'off')));

(async () => {
  await check('a moved address is worded plainly, only while on, and never otherwise', async () => {
    const s = scenario('valid', []);
    const words = vm.runInContext('addressMovedStatus', s.sandbox);
    const moved = {from: '198.51.100.10', to: '198.51.100.77', message: "This computer's address changed from "
      + '198.51.100.10 to 198.51.100.77. Remote Access is still listening on 198.51.100.10. Turn Remote Access '
      + 'off and on to move it.'};
    assert(words({enabled: true, address_moved: moved}) === 'ON \u2014 ' + moved.message);
    assert(words({enabled: false, address_moved: moved}) === '', 'nothing is said while it is off');
    assert(words({enabled: true, address_moved: null}) === '');
    assert(words(null) === '');
    const src = SETTINGS.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map((l) => l.split('//')[0]).join('\n');
    assert(/const movedWords = addressMovedStatus\(remoteState\);\s*if \(remoteState\.enabled && movedWords\)/.test(src),
      'the status renderer must use it before it claims ON');
  });

  await check('a valid certificate goes straight to the one confirmation', async () => {
    const s = scenario('valid', [true]);
    await s.toggle();
    assert(titles(s).join('|') === TURN_ON, titles(s).join('|'));
    assert(requests(s).join() === '/api/remote_access:on', requests(s).join());
  });

  await check('no certificate: Create Certificate makes it and continues, asking only the last question', async () => {
    const s = scenario('none', [true, true]);
    await s.toggle();
    assert(titles(s).length === 2, 'asked ' + titles(s).join(' | '));
    const d = s.log.dialogs[0];
    assert(d.confirmText === 'Create Certificate' && d.alternateText === 'Change to HTTP', JSON.stringify(d));
    assert(d.danger === true, 'Cancel must take the focus');
    // Device Info's own dialog reads only defaultAction; every other page reads danger. Both, or the
    // costly button holds the focus on one page (measured in a browser).
    assert(d.defaultAction === 'cancel', 'Device Info’s dialog focuses Cancel only for defaultAction');
    assert(s.log.dialogs[1].title === TURN_ON, 'the last question is the meaningful one');
    assert(requests(s).join() === '/api/admin/certificate:generate,/api/remote_access:on', requests(s).join());
    assert(s.log.dialogs[1].summary.map((r) => r.label).join() === 'Network,Security',
      'the last question shows Network and Security');
    // Row for row the same wording as LumaSuite's, so the question reads the same wherever it is asked.
    assert(s.log.dialogs[1].summary[1].value === 'HTTPS (encrypted)', JSON.stringify(s.log.dialogs[1].summary));
    assert(s.log.dialogs[1].summary[0].value === 'Test network (' + ADDRESS + ')', JSON.stringify(s.log.dialogs[1].summary));
    assert(s.log.dialogs[1].message === 'Other devices on this network will be able to reach OmniSuite and sign in.');
  });

  await check('a certificate made for another network is regenerated from the decision, with no second confirmation', async () => {
    const s = scenario('stale', [true, true]);
    await s.toggle();
    assert(titles(s).length === 2, 'asked ' + titles(s).join(' | '));
    const d = s.log.dialogs[0];
    assert(d.confirmText === 'Regenerate Certificate' && d.alternateText === 'Change to HTTP', JSON.stringify(d));
    assert(d.message.indexOf(OTHER) > 0 && d.message.indexOf(ADDRESS) > 0, 'names both addresses: ' + d.message);
    assert(/fingerprint/i.test(d.message), 'says what regenerating changes');
    assert(requests(s).join() === '/api/admin/certificate:generate,/api/remote_access:on', requests(s).join());
    assert(!titles(s).includes('Replace this certificate?'), 'the approved action was confirmed twice');
  });

  await check('an unusable certificate OmniSuite made is regenerated the same way', async () => {
    const s = scenario('damaged', [true, true]);
    await s.toggle();
    assert(s.log.dialogs[0].confirmText === 'Regenerate Certificate', JSON.stringify(s.log.dialogs[0]));
    assert(s.log.dialogs[0].message.indexOf('Not a readable PEM') >= 0, 'the reason is shown');
    assert(titles(s).length === 2 && requests(s).length === 2, requests(s).join());
  });

  await check('creating a certificate never turns Remote Access on by itself', async () => {
    const s = scenario('none', [true, false]);
    await s.toggle();
    assert(requests(s).join() === '/api/admin/certificate:generate', requests(s).join());
    assert(s.log.order[s.log.order.length - 1] === 'dialog:' + TURN_ON,
      'the last thing asked is the enable question');
  });

  await check('every decision that can cost something puts the focus on Cancel on every page', async () => {
    for (const kind of ['none', 'stale', 'damaged', 'suppliedBad']) {
      const s = scenario(kind, [false]);
      await s.toggle();
      assert(s.log.dialogs[0].danger === true && s.log.dialogs[0].defaultAction === 'cancel', kind);
    }
    const ok = scenario('valid', [false]);
    await ok.toggle();
    assert(ok.log.dialogs[0].defaultAction === undefined, 'an encrypted turn-on is not the costly answer');
  });

  await check('Cancel at the decision changes nothing at all', async () => {
    for (const kind of ['none', 'stale', 'damaged', 'suppliedBad']) {
      const s = scenario(kind, [false]);
      await s.toggle();
      assert(s.log.fetches.length === 0, kind + ' sent ' + requests(s).join());
      assert(s.getEl('cfg_remote_transport').value === 'https', kind + ' changed the transport');
      assert(titles(s).length === 1, kind + ' went on to ask more');
    }
  });

  await check('a SUPPLIED certificate is never offered a replacement', async () => {
    const s = scenario('suppliedBad', [true, true]);
    await s.toggle();
    const d = s.log.dialogs[0];
    assert(!/Create|Regenerate/.test(d.confirmText) && !d.alternateText, JSON.stringify(d));
    assert(/never replaces a certificate you supplied/.test(d.message), d.message);
    assert(!requests(s).some((r) => /generate/.test(r)), 'a certificate was generated over a supplied one');
  });

  await check('a supplied certificate: Change to HTTP is explicit, then the plaintext confirmation follows', async () => {
    const s = scenario('suppliedBad', [true, true]);
    await s.toggle();
    assert(s.getEl('cfg_remote_transport').value === 'http', 'transport not changed');
    assert(s.log.dialogs[1].title === TURN_ON && s.log.dialogs[1].danger === true,
      'plaintext asks, with Cancel focused');
    assert(s.log.dialogs[1].defaultAction === 'cancel');
    assert(s.log.dialogs[1].summary[1].value === 'HTTP (not encrypted)');
    assert(/not encrypted/i.test(JSON.stringify(s.log.dialogs[1].summary)), JSON.stringify(s.log.dialogs[1].summary));
    assert(s.log.fetches[0][1].accept_insecure === true && s.log.fetches[0][1].transport === 'http');
  });

  await check('a supplied certificate carrying another name is not blocked and not questioned', async () => {
    const s = scenario('suppliedOtherName', [true]);
    await s.toggle();
    assert(titles(s).join('|') === TURN_ON, titles(s).join('|'));
    assert(requests(s).join() === '/api/remote_access:on');
  });

  await check('Change to HTTP: HTTP is selected, nothing is generated, and the plaintext question follows', async () => {
    const s = scenario('none', ['alternate', true]);
    await s.toggle();
    assert(s.getEl('cfg_remote_transport').value === 'http');
    assert(requests(s).join() === '/api/remote_access:on', requests(s).join());
    assert(s.log.dialogs[1].danger === true);
    assert(s.log.fetches[0][1].transport === 'http' && s.log.fetches[0][1].accept_insecure === true);
  });

  await check('the Create button next to the certificate keeps its own replacement question', async () => {
    const s = scenario('stale', [true]);
    await s.create();
    assert(titles(s).join('|') === 'Replace this certificate?', 'titles=' + titles(s).join('|'));
    assert(requests(s).join() === '/api/admin/certificate:generate', 'req=' + requests(s).join());
    const none = scenario('none', []);
    await none.create();
    assert(titles(none).length === 0 && requests(none).join() === '/api/admin/certificate:generate',
      'nothing to replace, nothing to ask');
  });

  await check('the Create button refuses to touch a supplied certificate and asks nothing', async () => {
    const s = scenario('suppliedBad', [true]);
    await s.create();
    assert(s.log.fetches.length === 0 && s.log.dialogs.length === 0, requests(s).join());
    assert(s.log.toasts.length === 1 && /supplied/.test(s.log.toasts[0][0]), JSON.stringify(s.log.toasts));
  });

  await check('without the shared dialog the answer is refusal: no native dialog, no silent choice', async () => {
    const s = scenario('none', [true], {noDialog: true});
    await s.toggle();
    assert(s.log.fetches.length === 0, requests(s).join());
    assert(s.getEl('cfg_remote_transport').value === 'https');
  });

  results.forEach(([st, n]) => console.log(st + ' ' + n));
  console.log((results.length - failures) + '/' + results.length + ' checks passed');
  process.exit(failures ? 1 : 0);
})();

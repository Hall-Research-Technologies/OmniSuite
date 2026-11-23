// Remote Access switched on or off while a page is open: the bar follows it, and
// HTTPS without a certificate is a decision rather than a dead end.
//
// Two defects, both found on hardware with the shipped build:
//
//  1. The Remote Access tab is drawn from what the page was SERVED with, and
//     nothing told it when the setting changed, so it appeared or vanished only
//     after the browser was refreshed. adminnav.js now follows an announced
//     change in place.
//  2. Turning Remote Access on over HTTPS with no usable certificate gave a
//     toast and nothing to press. The operator now chooses: Create Certificate,
//     Change to HTTP, or Cancel -- and Cancel changes nothing.
//
// The module is executed, not read: adminnav.js runs in a sandbox with a
// listener registry, and the decision function is lifted out of settings.js and
// driven with a scripted dialog and a recording fetch.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const NAV_JS = read('ui', 'adminnav.js');
const SETTINGS_JS = read('ui', 'settings.js');
const CONFIRM_JS = read('ui', 'confirm.js');
const INDEX_HTML = read('ui', 'index.html');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]); });
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
function code(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((l) => l.split('//')[0]).join('\n');
}

// ------------------------------------------------------------ the nav ---
function element(tag) {
  const node = {
    tagName: tag, id: '', href: '', className: '', textContent: '', style: {},
    dataset: {}, children: [], parentNode: null, _attrs: {},
    appendChild(child) { child.parentNode = node; node.children.push(child); return child; },
    insertBefore(child, before) {
      child.parentNode = node;
      const at = node.children.indexOf(before);
      node.children.splice(at < 0 ? node.children.length : at, 0, child);
      return child;
    },
    remove() {
      if (node.parentNode) {
        node.parentNode.children = node.parentNode.children.filter((c) => c !== node);
        node.parentNode = null;
      }
    },
    addEventListener() {},
    getAttribute(name) { return name === 'href' ? node.href : (node._attrs[name] ?? null); },
    setAttribute(name, value) { node._attrs[name] = value; },
    getBoundingClientRect() { return {width: 100, height: 20}; },
    querySelector(sel) {
      const wanted = /a\[data-nav="remote"\]/.test(sel);
      return wanted ? (node.children.find((c) => c.dataset && c.dataset.nav === 'remote') || null) : null;
    },
  };
  return node;
}

// A page served with Remote Access in `served` state, `current` meaning the
// page IS the Remote Access page.
async function openPage({served, current, status}) {
  const tabs = element('div');
  ['Device Info', 'Configure', 'A/V Matrix', 'USB Matrix', 'Multiview'].forEach((t, i) => {
    const a = element('a'); a.textContent = t; a.href = '/x' + i; tabs.appendChild(a);
  });
  if (served && current) {
    const a = element('a');
    a.textContent = 'Remote Access'; a.href = '/admin/users'; a.dataset.nav = 'remote';
    a.setAttribute('aria-current', 'page');
    tabs.appendChild(a);
  }
  const meta = {_c: served ? '1' : '0', getAttribute() { return this._c; },
                setAttribute(n, v) { if (n === 'content') this._c = v; }};
  const fetches = [];
  const navigations = [];
  const listeners = {};
  const document = {
    head: element('head'), documentElement: element('html'), body: element('body'),
    readyState: 'complete', createElement: element, getElementById: () => null,
    querySelector: (sel) => (/omni-remote-access/.test(sel) ? meta
      : (/\.tabs/.test(sel) ? tabs : null)),
    querySelectorAll: () => [],
    addEventListener(type, fn) { listeners['document:' + type] = fn; },
    visibilityState: 'visible',
  };
  const channels = [];
  class FakeChannel {
    constructor(name) { this.name = name; this.sent = []; channels.push(this); }
    postMessage(m) { this.sent.push(m); }
  }
  const window = {addEventListener(type, fn) { listeners[type] = fn; }};
  const sandbox = {
    window, document, sessionStorage: {removeItem() {}},
    location: {replace() {}, assign(u) { navigations.push(u); }},
    BroadcastChannel: FakeChannel,
    fetch: async (url) => {
      fetches.push(url);
      if (status && status.fail) throw new Error('network');
      if (status && url === '/api/remote_access/status') {
        return {ok: status.ok !== false, json: async () => ({ok: true, enabled: status.enabled})};
      }
      return {ok: true, json: async () => ({})};
    },
    Date, Promise, JSON, Object, Array, console,
  };
  vm.createContext(sandbox);
  vm.runInContext(NAV_JS, sandbox);
  await new Promise((r) => setTimeout(r, 15));
  const announce = (detail) => listeners['omni:remote-access-changed']({detail});
  const names = () => tabs.children.map((c) => c.textContent);
  return {tabs, names, meta, fetches, navigations, announce, channels, listeners};
}

const suite = [];
suite.push(() => check('OFF -> ON: the tab appears at once, with no reload and no request', async () => {
  const p = await openPage({served: false});
  assert(!p.names().includes('Remote Access'));
  p.announce({enabled: true, switched: true, leave: true});
  assert(p.names().filter((n) => n === 'Remote Access').length === 1, p.names().join('|'));
  assert(p.names()[p.names().length - 1] === 'Remote Access', 'it stays last');
  assert(p.fetches.length === 0, 'following a change must not cost a request');
  assert(p.navigations.length === 0, 'turning it on never moves the operator');
  assert(p.meta.getAttribute() === '1', 'the page copy of the answer follows too');
}));
suite.push(() => check('ON -> OFF: the tab disappears at once, and the operator stays put', async () => {
  const p = await openPage({served: true});
  assert(p.names().includes('Remote Access'));
  p.announce({enabled: false, switched: true, leave: true});
  assert(!p.names().includes('Remote Access'), p.names().join('|'));
  assert(p.navigations.length === 0, 'not on that page, so nobody is moved');
  assert(p.meta.getAttribute() === '0');
}));
suite.push(() => check('ON -> OFF while on a Remote Access page: moved to the application', async () => {
  const p = await openPage({served: true, current: true});
  p.announce({enabled: false, switched: true, leave: true});
  assert(!p.names().includes('Remote Access'));
  assert(p.navigations.join() === '/', 'expected the application page, got ' + p.navigations);
}));
suite.push(() => check('a state merely re-read never moves anyone off the page they are on', async () => {
  const p = await openPage({served: true, current: true});
  p.announce({enabled: false, switched: false, leave: false});
  assert(p.names().includes('Remote Access'), 'the page that IS the entry keeps it');
  assert(p.navigations.length === 0);
}));
suite.push(() => check('repeating the same answer neither duplicates nor flickers the entry', async () => {
  const p = await openPage({served: false});
  p.announce({enabled: true});
  const first = p.tabs.children[5];
  p.announce({enabled: true});
  p.announce({enabled: true});
  assert(p.names().filter((n) => n === 'Remote Access').length === 1);
  assert(p.tabs.children[5] === first, 'the same element is kept, not rebuilt');
}));
suite.push(() => check('a malformed announcement changes nothing', async () => {
  const p = await openPage({served: true});
  p.announce({});
  p.announce({enabled: 'yes'});
  assert(p.names().includes('Remote Access'));
}));
suite.push(() => check('a switch is told to other windows; a re-read is not', async () => {
  const p = await openPage({served: false});
  const channel = p.channels[0];
  assert(channel && channel.name === 'omni-remote-access');
  p.announce({enabled: true, switched: false});
  assert(channel.sent.length === 0, 'a re-read is private to this window');
  p.announce({enabled: true, switched: true, leave: true});
  assert(channel.sent.length === 1 && channel.sent[0].enabled === true);
  channel.onmessage({data: {enabled: false, leave: false}});
  assert(!p.names().includes('Remote Access'), 'another window turned it off');
}));
// ---- a page that was not on screen when the setting changed ------------------------------------
suite.push(() => check('a page that missed the change catches up when it is shown again', async () => {
  const p = await openPage({served: false, status: {enabled: true}});
  assert(!p.names().includes('Remote Access'));
  p.listeners['window:focus'] ? 0 : 0;
  await p.listeners.focus({});
  await new Promise((r) => setTimeout(r, 20));
  assert(p.names().includes('Remote Access'), 'the tab never followed: ' + p.names().join('|'));
  assert(p.meta.getAttribute() === '1');
  assert(p.fetches.join() === '/api/remote_access/status',
    'the reconcile must use the cheap status read, never the adapter-enumerating one: ' + p.fetches);
  assert(p.navigations.length === 0);
}));
suite.push(() => check('coming back to a page that still shows an entry Remote Access no longer has removes it', async () => {
  const p = await openPage({served: true, status: {enabled: false}});
  await p.listeners['document:visibilitychange']({});
  await new Promise((r) => setTimeout(r, 20));
  assert(!p.names().includes('Remote Access'), p.names().join('|'));
  assert(p.navigations.length === 0, 'a reconcile never moves anyone');
}));
suite.push(() => check('the page that IS the entry keeps it through a reconcile, and nobody is moved', async () => {
  const p = await openPage({served: true, current: true, status: {enabled: false}});
  await p.listeners.focus({});
  await new Promise((r) => setTimeout(r, 20));
  assert(p.names().includes('Remote Access'));
  assert(p.navigations.length === 0);
}));
suite.push(() => check('repeated focus events cost one request, not one each', async () => {
  const p = await openPage({served: false, status: {enabled: true}});
  await p.listeners.focus({});
  await new Promise((r) => setTimeout(r, 20));              // the first has finished
  await p.listeners.focus({});
  await p.listeners['document:visibilitychange']({});
  await new Promise((r) => setTimeout(r, 20));
  assert(p.fetches.filter((u) => u === '/api/remote_access/status').length === 1, p.fetches.join());
}));
suite.push(() => check('a failed or refused read leaves the bar exactly as it was', async () => {
  for (const status of [{fail: true}, {ok: false, enabled: false}]) {
    const p = await openPage({served: true, status});
    await p.listeners.focus({});
    await new Promise((r) => setTimeout(r, 20));
    assert(p.names().includes('Remote Access'), 'unknown must not remove the entry');
  }
}));
suite.push(() => check('Settings says it is working the moment turning on starts, before the slow request', () => {
  const src = code(SETTINGS_JS);
  const at = src.indexOf("'Turning on");
  assert(at > 0, 'no pending label');
  assert(/remoteToggle\.textContent = turningOn \? 'Turning on/.test(src),
    'the control itself must say it is working, not only the status line');
  const post = src.indexOf("fetch('/api/remote_access', {", at);
  assert(post > at, 'the pending state must be shown before the POST is awaited');
  assert(/aria-busy/.test(src), 'announced as well as painted');
  assert(/several seconds/.test(src), 'the wait is explained');
}));
suite.push(() => check('settings announces what the server verified, after reading it back', () => {
  const src = code(SETTINGS_JS);
  assert(/new CustomEvent\('omni:remote-access-changed'/.test(src));
  const load = src.slice(src.indexOf('async function loadRemoteAccess('));
  const body = load.slice(0, load.indexOf('\n  }\n'));
  assert(body.indexOf('/api/remote_access') > -1 && body.indexOf('announceRemoteState(') > body.indexOf('/api/remote_access'),
    'the announcement must follow the read-back, not the request');
  assert(/loadRemoteAccess\(switched\)/.test(src), 'a successful toggle must say it switched');
  assert(/switched = true;/.test(src));
}));

// -------------------------------------------- HTTPS needs a certificate ---
function liftDecision() {
  const start = SETTINGS_JS.indexOf('  async function askAboutMissingCertificate()');
  assert(start > 0, 'decision function not found');
  const end = SETTINGS_JS.indexOf('\n  }\n', start) + 4;
  return SETTINGS_JS.slice(start, end);
}
async function decide(answer, extra) {
  const log = {dialogs: [], created: 0, changes: 0};
  const select = {value: 'https', dispatchEvent() { log.changes++; }};
  const sandbox = {
    window: {omniConfirm: extra && extra.noDialog ? undefined : async (o) => {
      log.dialogs.push(o); return answer;
    }},
    transportSelect: select,
    createCertificateFlow: async () => { log.created++; return !(extra && extra.createFails); },
    Event: function Event(t) { this.type = t; },
  };
  vm.createContext(sandbox);
  // The decision reads the certificate's description, so the wording rules that
  // classify it are lifted with it, and the page's state is a missing certificate.
  const wording = SETTINGS_JS.slice(SETTINGS_JS.indexOf('  // BEGIN certificate wording'),
    SETTINGS_JS.indexOf('  // END certificate wording'));
  vm.runInContext('var remoteState = {certificate: {present: false}};\n' + wording + liftDecision()
    + '\nthis.fn = askAboutMissingCertificate;', sandbox);
  const result = await sandbox.fn();
  return {result, log, select};
}
suite.push(() => check('the decision offers Create Certificate, Change to HTTP, and Cancel', async () => {
  const r = await decide(false);
  const o = r.log.dialogs[0];
  assert(o.confirmText === 'Create Certificate' && o.alternateText === 'Change to HTTP', JSON.stringify(o));
  assert(o.danger === true, 'Cancel takes the focus: neither costly answer is the default');
}));
suite.push(() => check('Create Certificate enters the existing creation flow and reports it', async () => {
  const r = await decide(true);
  assert(r.result === 'created' && r.log.created === 1 && r.select.value === 'https');
  const failed = await decide(true, {createFails: true});
  assert(failed.result === 'cancel', 'a certificate that was not made is not a go-ahead');
}));
suite.push(() => check('Change to HTTP selects HTTP, creates nothing, and lets the operator continue', async () => {
  const r = await decide('alternate');
  assert(r.result === 'http' && r.select.value === 'http');
  assert(r.log.created === 0 && r.log.changes === 1);
}));
suite.push(() => check('Cancel (and Escape) change nothing at all', async () => {
  const r = await decide(false);
  assert(r.result === 'cancel' && r.select.value === 'https' && r.log.created === 0 && r.log.changes === 0);
}));
suite.push(() => check('without the shared dialog the answer is refusal, never a silent choice', async () => {
  const r = await decide(true, {noDialog: true});
  assert(r.result === 'cancel' && r.log.created === 0 && r.select.value === 'https');
}));
suite.push(() => check('the decision runs before the confirmation and before any request', () => {
  const src = code(SETTINGS_JS);
  const toggle = src.slice(src.indexOf("remoteToggle.addEventListener('click'"));
  const decision = toggle.indexOf('askAboutMissingCertificate()');
  const confirmAt = toggle.indexOf('Turn on remote access?');
  const request = toggle.indexOf("fetch('/api/remote_access'");
  assert(decision > 0 && decision < confirmAt && confirmAt < request,
    `order ${decision}/${confirmAt}/${request}`);
  assert(/certificateKind\(\(remoteState \|\| \{\}\)\.certificate, remoteState\) === 'required'/.test(toggle));
  assert(/turningOn && transport === 'https'/.test(toggle), 'only when turning on over HTTPS');
}));
suite.push(() => check('Create Certificate and the Create button are one implementation', () => {
  const src = code(SETTINGS_JS);
  assert(/certGenerate\.addEventListener\('click', \(\) => \{ createCertificateFlow\(\); \}\)/.test(src));
  assert((src.match(/action: 'generate'/g) || []).length === 1, 'one place makes certificates');
}));

// --------------------------------------------- the third answer, shared ---
suite.push(() => check('both dialogs offer the same third answer and Cancel never means it', () => {
  const shared = code(CONFIRM_JS);
  assert(/alternateText/.test(shared) && /cleanup\('alternate'\)/.test(shared));
  const page = code(INDEX_HTML);
  assert(/alternateText/.test(page) && /cleanup\('alternate'\)/.test(page));
  assert(/id="confirm_alt"[^>]*hidden/.test(INDEX_HTML), 'hidden unless asked for');
  assert(!/onCancel = \(\) => cleanup\('alternate'\)/.test(page));
}));
suite.push(() => check('a caller that never asks for it sees only true or false', () => {
  const shared = code(CONFIRM_JS);
  // The button is hidden and has no listener unless alternateText was passed.
  assert(/if \(altBtn && options\.alternateText\) altBtn\.addEventListener/.test(shared));
}));
suite.push(() => check('the script versions were bumped so a cached page cannot keep the old behaviour', () => {
  for (const page of ['index.html', 'admin/users.html', 'matrix/index.html', 'matrix/configure.html',
                      'matrix/usb.html', 'matrix/multiview.html']) {
    const html = read('ui', ...page.split('/'));
    assert(/\/ui\/adminnav\.js\?v=20261117-remotenav2/.test(html), page + ' adminnav');
    assert(/\/ui\/settings\.js\?v=20261120-settings3/.test(html), page + ' settings');
  }
}));

(async () => {
  for (const run of suite) await run();
  results.forEach(([s, l]) => console.log(`${s} ${l}`));
  console.log(`${suite.length - failures}/${suite.length} checks passed`);
  process.exit(failures ? 1 : 0);
})();

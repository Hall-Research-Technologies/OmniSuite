/*
 * ui/scan-notice.js: the macOS Local Network notice.
 *
 * Runs the real module against a DOM stub small enough to make the questions
 * answerable without a browser. The stub has NO innerHTML: any attempt to use it
 * throws, which is how the "data, not markup" rule is enforced rather than
 * merely asserted.
 *
 * Pinned here:
 *  - the notice appears for a Local Network verdict and for nothing else -- not
 *    an empty scan, not another blocked cause, not garbage;
 *  - it names the product, gives the Settings path, and offers Retry Scan;
 *  - Open System Settings is offered only for a Mac server viewed from that Mac;
 *  - the detailed diagnostics are kept;
 *  - a hostile product name is text, not markup;
 *  - Retry hides the notice and runs one scan.
 */
'use strict';

const path = require('path');
let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures += 1; console.log(`  FAIL ${name}: ${err.message}`); }
}
function assert(cond, message) { if (!cond) throw new Error(message); }
function equal(a, b, what) { if (a !== b) throw new Error(`${what}: expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`); }

// --- the smallest DOM the module can run against -----------------------------
class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attrs = {};
    this.listeners = {};
    this.hidden = false;
    this.className = '';
    this._text = '';
  }
  set innerHTML(_v) { throw new Error('innerHTML must not be used: the product name is data, not markup'); }
  get innerHTML() { throw new Error('innerHTML must not be used'); }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(' '); }
  get firstChild() { return this.children[0] || null; }
  appendChild(c) { this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  click() { (this.listeners.click || []).forEach((fn) => fn({})); }
  find(pred, out = []) {
    if (pred(this)) out.push(this);
    this.children.forEach((c) => c.find(pred, out));
    return out;
  }
  byTag(tag) { return this.find((e) => e.tagName === tag.toUpperCase()); }
  byLabel(label) { return this.byTag('button').filter((b) => b.textContent === label); }
}
const doc = { createElement: (tag) => new El(tag) };

const notice = require(path.join(__dirname, '..', 'ui', 'scan-notice.js'));

function body(overrides) {
  return Object.assign({
    ok: false, reason: 'scan_blocked', likely_cause: 'local_network_permission',
    app_name: 'OmniSuite', error: 'The scan could not reach the network.',
    diagnostics: { platform: { system: 'Darwin', machine: 'arm64' },
                   probe_outcomes: { EHOSTUNREACH: 254 }, duration_s: 0.05 },
  }, overrides || {});
}

function setup(extra) {
  const container = new El('div');
  const calls = { retry: 0, navigated: [] };
  notice.attach(Object.assign({
    container, document: doc,
    onRetry: () => { calls.retry += 1; },
    navigate: (url) => calls.navigated.push(url),
    env: { hostname: '127.0.0.1', platform: 'MacIntel' },
  }, extra || {}));
  return { container, calls };
}

// --- when it appears -----------------------------------------------------------
check('a Local Network verdict is classified', () => {
  equal(notice.classify(body()), 'local_network_permission', 'classify');
});

check('nothing else is ever classified as a permission problem', () => {
  const not = [
    null, undefined, 'x', 7, [], {},
    { ok: true, reachable: 0, tested: 254 },                       // an ordinary empty scan
    body({ ok: true }),                                            // a success carrying the same fields
    body({ reason: 'no_valid_targets' }),
    body({ likely_cause: 'descriptor_exhaustion' }),
    body({ likely_cause: 'blocked_by_firewall_or_policy' }),
    body({ likely_cause: 'no_route' }),
    body({ likely_cause: undefined }),
    body({ reason: undefined }),
  ];
  not.forEach((b, i) => equal(notice.classify(b), null, `case ${i}`));
});

check('an ordinary zero-device scan shows nothing and clears nothing it did not own', () => {
  const { container } = setup();
  equal(notice.show({ ok: true, reachable: 0, tested: 254, units: [] }), false, 'show');
  assert(container.children.length === 0, 'rendered something for a successful scan');
});

check('show returns false, without throwing, when there is nowhere to render', () => {
  notice.attach({ container: null, document: doc });
  equal(notice.show(body()), false, 'show');
  notice.hide();                                                   // and hide is harmless
});

// --- what it says ---------------------------------------------------------------
check('it names the product and gives the full Settings path', () => {
  const { container } = setup();
  equal(notice.show(body()), true, 'show');
  const text = container.textContent;
  assert(text.includes('macOS is blocking OmniSuite from your local network'), 'heading');
  assert(text.includes('System Settings'), 'System Settings');
  assert(text.includes('Privacy & Security'), 'Privacy & Security');
  assert(text.includes('Local Network'), 'Local Network');
  assert(text.includes('Switch on OmniSuite'), 'step to switch it on');
  assert(text.includes('Retry Scan'), 'step to retry');
  assert(text.includes('not because there are none'), 'explains it is not an empty network');
  equal(container.hidden, false, 'visible');
  equal(container.attrs.role, 'status', 'announced politely');
});

check('OmniSuite Plus is named as OmniSuite Plus', () => {
  const { container } = setup();
  notice.show(body({ app_name: 'OmniSuite Plus' }));
  const text = container.textContent;
  assert(text.includes('blocking OmniSuite Plus from'), 'heading');
  assert(text.includes('Switch on OmniSuite Plus.'), 'step');
});

check('the product name falls back to OmniSuite when the server sent none', () => {
  const { container } = setup();
  notice.show(body({ app_name: undefined }));
  assert(container.textContent.includes('Switch on OmniSuite.'), 'fallback');
});

check('a hostile product name is text, never markup', () => {
  const { container } = setup();
  // The stub throws if innerHTML is touched, so reaching here proves it was not.
  notice.show(body({ app_name: '<img src=x onerror=alert(1)>' }));
  assert(container.textContent.includes('<img src=x onerror=alert(1)>'), 'shown literally');
  equal(container.byTag('img').length, 0, 'no element was created from it');
});

check('the technical details are kept', () => {
  const { container } = setup();
  notice.show(body());
  const details = container.byTag('details');
  equal(details.length, 1, 'details element');
  const pre = container.byTag('pre')[0].textContent;
  assert(pre.includes('The scan could not reach the network.'), 'the server sentence');
  assert(pre.includes('EHOSTUNREACH'), 'the outcomes');
  assert(pre.includes('"probe_outcomes"'), 'the diagnostics object');
  assert(pre.includes('local_network_permission'), 'the cause');
});

// --- actions ---------------------------------------------------------------------
check('Retry Scan hides the notice and runs exactly one scan', () => {
  const { container, calls } = setup();
  notice.show(body());
  const retry = container.byLabel('Retry Scan');
  equal(retry.length, 1, 'one Retry Scan button');
  retry[0].click();
  equal(calls.retry, 1, 'scans run');
  equal(container.hidden, true, 'notice hidden while the retry runs');
  equal(container.children.length, 0, 'content cleared');
});

check('without a retry handler no dead button is drawn', () => {
  const { container } = setup({ onRetry: null });
  notice.show(body());
  equal(container.byLabel('Retry Scan').length, 0, 'Retry Scan');
});

check('Open System Settings is offered for a Mac server viewed from that Mac', () => {
  const { container, calls } = setup();
  notice.show(body());
  const open = container.byLabel('Open System Settings');
  equal(open.length, 1, 'button');
  open[0].click();
  equal(calls.navigated.length, 1, 'navigations');
  equal(calls.navigated[0], 'x-apple.systempreferences:com.apple.preference.security?Privacy_LocalNetwork', 'url');
});

check('Open System Settings is withheld wherever it cannot be right', () => {
  const cases = [
    ['a remote browser', { hostname: '192.0.2.10', platform: 'MacIntel' }, body()],
    ['a Windows browser on the Mac\'s own host', { hostname: '127.0.0.1', platform: 'Win32' }, body()],
    ['a Linux browser', { hostname: 'localhost', platform: 'Linux x86_64' }, body()],
    ['a non-Mac server', { hostname: '127.0.0.1', platform: 'MacIntel' },
      body({ diagnostics: { platform: { system: 'Windows' } } })],
    ['a server that did not say', { hostname: '127.0.0.1', platform: 'MacIntel' }, body({ diagnostics: {} })],
    ['no environment at all', {}, body()],
  ];
  cases.forEach(([label, env, b]) => {
    const { container } = setup({ env });
    notice.show(b);
    equal(container.byLabel('Open System Settings').length, 0, label);
    assert(container.textContent.includes('Privacy & Security'), label + ': the manual path must always be there');
    equal(container.byLabel('Retry Scan').length, 1, label + ': retry is always offered');
  });
});

check('every local spelling of the host counts as local', () => {
  ['127.0.0.1', 'localhost', '::1', '[::1]'].forEach((hostname) => {
    equal(notice.canOpenSettings(body(), { hostname, platform: 'MacIntel' }), true, hostname);
  });
});

// --- lifetime --------------------------------------------------------------------
check('showing twice replaces the notice instead of stacking a second one', () => {
  const { container } = setup();
  notice.show(body());
  notice.show(body());
  equal(container.byLabel('Retry Scan').length, 1, 'Retry buttons');
  equal(container.byTag('details').length, 1, 'details blocks');
});

check('hide clears the notice and hides the element', () => {
  const { container } = setup();
  notice.show(body());
  notice.hide();
  equal(container.hidden, true, 'hidden');
  equal(container.children.length, 0, 'cleared');
});

check('a later non-permission failure does not leave the old notice on screen', () => {
  const { container } = setup();
  notice.show(body());
  // The page hides the notice when a scan starts; a different failure then shows none.
  notice.hide();
  equal(notice.show(body({ likely_cause: 'no_route' })), false, 'show');
  equal(container.children.length, 0, 'nothing rendered');
});

if (failures) {
  console.log(`\nscan-notice test: ${failures} failure(s)`);
  process.exit(1);
}
console.log('\nscan-notice test: all checks passed');

// Firmware availability in the browser: the module executed, not read.
//
// The module decorates the Version cell by data attribute (handlers elsewhere
// rewrite that cell's text, and an attribute survives that where a child element
// would not), summarises per model with the most pressing state winning, and says
// "Up to Date", "Available" or "Unknown" -- never "N/A".
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const SOURCE = fs.readFileSync(path.join(ROOT, 'ui', 'firmware-status.js'), 'utf8');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]); });
}
function assert(condition, message) { if (!condition) throw new Error(message || 'assertion failed'); }

function cell() {
  const attrs = {};
  return {
    dataset: {}, attrs,
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return attrs[k]; },
  };
}
function row(attr, value, versionCell) {
  const cells = [];
  for (let i = 0; i < 18; i += 1) cells.push(i === 11 ? versionCell : cell());
  return {cells, getAttribute: (k) => (k === attr ? value : null)};
}

function load(fetchImpl) {
  const rows = {'tr[data-ip]': [], 'tr[data-extender-ip]': []};
  const sandbox = {
    window: {},
    document: {querySelectorAll: (sel) => rows[sel] || []},
    sessionStorage: {getItem: () => 'token-123'},
    fetch: fetchImpl || (async () => ({ok: false})),
    setTimeout: (fn) => { fn(); return 0; },
    Promise, JSON, Object, Array, Set, Map, Date, String, console,
  };
  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);
  return {api: sandbox.window.omniFirmware, rows};
}

const DATA = {
  ok: true,
  check: {state: 'ready', last_success: 1_700_000_000, stale: false, running: false},
  units: {
    '192.0.2.1': {state: 'up_to_date', label: 'Up to Date', model: 'hw-omni-d4511',
                  installed: '2.1.2', latest: '2.1.2', reason: ''},
    '192.0.2.2': {state: 'update_available', label: 'Available', model: 'hw-omni-d4511',
                  installed: '2.0.0', latest: '2.1.2', reason: ''},
    '192.0.2.3': {state: 'unknown', label: 'Unknown', model: 'hw-omni-d4111-wp',
                  installed: '2.1.2', latest: null, reason: 'Hall Research has no product page for this model.'},
  },
  extenders: {
    '192.0.2.9': {state: 'unknown', label: 'Unknown', model: 'HW-OMNI-324',
                  installed: '2.0.9', latest: null,
                  reason: 'Hall Research publishes no firmware for this model.'},
  },
};

const suite = [];

suite.push(() => check('the module installs itself once', () => {
  const {api} = load();
  assert(api && typeof api.applyToTable === 'function');
  assert(api.STATES.update_available === 'Available');
}));

suite.push(() => check('rows are decorated by data attribute, in the operator\'s words', () => {
  const {api, rows} = load();
  const a = cell(), b = cell(), c = cell(), x = cell();
  rows['tr[data-ip]'] = [row('data-ip', '192.0.2.1', a), row('data-ip', '192.0.2.2', b),
                         row('data-ip', '192.0.2.3', c)];
  rows['tr[data-extender-ip]'] = [row('data-extender-ip', '192.0.2.9', x)];
  const painted = api.applyToTable(DATA);
  assert(painted === 4, 'painted ' + painted);
  assert(a.dataset.fw === 'up_to_date' && a.dataset.fwLabel === 'Up to Date');
  assert(b.dataset.fw === 'update_available' && b.dataset.fwLabel === 'Available');
  assert(c.dataset.fw === 'unknown' && c.dataset.fwLabel === 'Unknown');
  assert(x.dataset.fw === 'unknown', 'a 311/324 row carries a status too');
  [a, b, c, x].forEach((n) => assert(!/N\/A/.test(JSON.stringify(n.dataset)), 'N/A shown'));
}));

suite.push(() => check('the status is data, so rewriting the cell text cannot remove it', () => {
  const {api, rows} = load();
  const v = cell();
  v.textContent = '2.0.0';
  rows['tr[data-ip]'] = [row('data-ip', '192.0.2.2', v)];
  api.applyToTable(DATA);
  v.textContent = '2.0.0 (changed by a poll)';
  assert(v.dataset.fw === 'update_available');
}));

suite.push(() => check('a row with no status is cleared, not left stale', () => {
  const {api, rows} = load();
  const v = cell();
  v.dataset.fw = 'up_to_date';
  v.dataset.fwLabel = 'Up to Date';
  rows['tr[data-ip]'] = [row('data-ip', '192.0.2.77', v)];
  api.applyToTable(DATA);
  assert(v.dataset.fw === undefined && v.dataset.fwLabel === undefined);
}));

suite.push(() => check('a missing answer decorates nothing and does not throw', () => {
  const {api, rows} = load();
  const v = cell();
  rows['tr[data-ip]'] = [row('data-ip', '192.0.2.1', v)];
  assert(api.applyToTable(null) === 0);
  assert(v.dataset.fw === undefined);
}));

suite.push(() => check('an unrecognised state reads as Unknown, never as itself', () => {
  const {api, rows} = load();
  const v = cell();
  rows['tr[data-ip]'] = [row('data-ip', '192.0.2.1', v)];
  api.applyToTable({units: {'192.0.2.1': {state: 'UPDATE_NOW', label: 'x'}}});
  assert(v.dataset.fw === 'unknown' && v.dataset.fwLabel === 'Unknown');
}));

suite.push(() => check('per model, the most pressing state wins', () => {
  const {api} = load();
  const models = api.perModel(DATA);
  const d4511 = models.find((m) => m.model === 'HW-OMNI-D4511');
  assert(d4511.units === 2, 'two units');
  assert(d4511.state === 'update_available', 'one that can be updated is not hidden');
  assert(d4511.installed === '2.0.0, 2.1.2', d4511.installed);
  const ext = models.find((m) => m.model === 'HW-OMNI-324');
  assert(ext.state === 'unknown' && /no firmware/i.test(ext.reason));
  assert(models.map((m) => m.model).join() === [...models.map((m) => m.model)].sort().join(),
    'a stable order');
}));

suite.push(() => check('the summary says what is true', () => {
  const {api} = load();
  const s = api.summarize(DATA);
  assert(/1 model has newer firmware available/.test(s.text), s.text);
  assert(s.tone === 'warn');
  assert(/Last checked/.test(s.detail));
  const clean = JSON.parse(JSON.stringify(DATA));
  delete clean.units['192.0.2.2'];
  assert(api.summarize(clean).tone === 'ok');
}));

suite.push(() => check('a stale result is said to be the last known one', () => {
  const {api} = load();
  const stale = JSON.parse(JSON.stringify(DATA));
  stale.check.stale = true;
  assert(/last known result/.test(api.summarize(stale).detail));
}));

suite.push(() => check('before any success it does not claim everything is current', () => {
  const {api} = load();
  const none = JSON.parse(JSON.stringify(DATA));
  none.check = {state: 'pending', last_success: null};
  const s = api.summarize(none);
  assert(!/up to date/i.test(s.text), s.text);
  none.check.state = 'unavailable';
  assert(/Could not reach Hall Research/.test(api.summarize(none).text));
}));

suite.push(() => check('no data at all is stated, not drawn as "up to date"', () => {
  const {api} = load();
  assert(/unavailable/i.test(api.summarize(null).text));
}));

suite.push(() => check('fetchStatus survives a failing server', async () => {
  const bad = load(async () => { throw new Error('offline'); });
  assert((await bad.api.fetchStatus()) === null);
  const notOk = load(async () => ({ok: false, status: 500}));
  assert((await notOk.api.fetchStatus()) === null);
  const refused = load(async () => ({ok: true, json: async () => ({ok: false})}));
  assert((await refused.api.fetchStatus()) === null);
}));

suite.push(() => check('the manual check posts with the CSRF header and polls local status', async () => {
  const calls = [];
  const {api} = load(async (url, opts) => {
    calls.push([url, opts && opts.method, opts && opts.headers]);
    if (url === '/api/firmware_status/check') return {status: 202, ok: true};
    return {ok: true, json: async () => DATA};
  });
  const data = await api.check();
  assert(data && data.ok);
  assert(calls[0][0] === '/api/firmware_status/check' && calls[0][1] === 'POST');
  assert(calls[0][2]['X-OmniSuite-CSRF'] === 'token-123');
  assert(calls.slice(1).every((c) => c[0] === '/api/firmware_status'),
    'after starting, only the local status is read');
}));

suite.push(() => check('a refused manual check reports null and does not poll', async () => {
  let polled = 0;
  const {api} = load(async (url) => {
    if (url === '/api/firmware_status') polled += 1;
    return {status: 409, ok: false};
  });
  assert((await api.check()) === null);
  assert(polled === 0);
}));

suite.push(() => check('nothing in the module reads a download link or touches storage beyond the token', () => {
  const code = SOURCE.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map((l) => l.split('//')[0]).join('\n');
  assert(!/firmware_link|\.url\b|location\.|window\.open|download/i.test(code));
  assert(!/localStorage/.test(code));
}));

(async () => {
  for (const run of suite) await run();
  results.forEach(([status, line]) => console.log(`${status} ${line}`));
  console.log(`${suite.length - failures}/${suite.length} checks passed`);
  process.exit(failures ? 1 : 0);
})();

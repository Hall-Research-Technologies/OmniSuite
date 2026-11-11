/*
 * The default network adapter is a default: it never replaces a choice.
 *
 * Both pages start on the adapter the server ranked best (a physical Ethernet port
 * before Wi-Fi, before anything virtual) -- but only when nothing was saved or
 * configured. The saved scan adapter (Device Info) and the configured Remote Access
 * adapter (Settings) win over the ranking even when the saved one is a virtual
 * adapter, because the operator chose it.
 *
 * This lifts the real functions out of ui/index.html and ui/settings.js and runs
 * them against a small select, rather than reading the source text: the order of
 * two strings in a function proves the text, not the behaviour.
 *
 * Run: node tests/adapter_default_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const INDEX = fs.readFileSync(path.join(__dirname, '..', 'ui', 'index.html'), 'utf8');
const SETTINGS = fs.readFileSync(path.join(__dirname, '..', 'ui', 'settings.js'), 'utf8');

const failures = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => console.log(`  ok   ${name}`),
    (err) => { failures.push(`${name}: ${err.message}`); console.log(`  FAIL ${name}: ${err.message}`); });
}
function eq(actual, expected, label) {
  if (actual !== expected) throw new Error(`${label || ''} expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

function lift(source, name) {
  const m = new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(').exec(source);
  if (!m) throw new Error('function not found: ' + name);
  let depth = 0, i = source.indexOf('{', m.index);
  for (; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}' && --depth === 0) return source.slice(m.index, i + 1);
  }
  throw new Error('unbalanced: ' + name);
}

// Just enough of a <select>: options, value, selectedIndex.
class Option {
  constructor() { this.value = ''; this.dataset = {}; this.textContent = ''; }
}
class Select {
  constructor(options) { this._options = options || []; this.selectedIndex = this._options.length ? 0 : -1; }
  get options() { return this._options; }
  get value() { return this._options[this.selectedIndex] ? this._options[this.selectedIndex].value : ''; }
  set value(v) { const i = this._options.findIndex((o) => o.value === v); this.selectedIndex = i; }
  replaceChildren() { this._options = []; this.selectedIndex = -1; }
  appendChild(o) { o.index = this._options.length; this._options.push(o); if (this.selectedIndex < 0) this.selectedIndex = 0; }
}
const documentStub = { createElement: () => new Option() };

function scanSelect(entries) {
  return new Select(entries.map(([ip, preferred]) => {
    const o = new Option();
    o.value = 'scan:' + ip;
    o.dataset.ip = ip;
    if (preferred) o.dataset.preferred = '1';
    return o;
  }));
}

const WIFI = '192.0.2.10', ETH = '192.0.2.20', VETH = '198.51.100.30';

function scanContext(saved) {
  const ctx = vm.createContext({
    fetch: async () => ({ json: async () => ({ interface_ip: saved }) }),
    console: { warn() {}, error() {}, log() {} },
    state: { adapters: [] },
  });
  vm.runInContext(lift(INDEX, 'restoreScanAdapter'), ctx);
  return ctx;
}

(async () => {
  console.log('Device Info: scan adapter');
  const rows = () => scanSelect([[WIFI, false], [ETH, true], [VETH, false]]);

  await check('nothing saved: starts on the ranked-best adapter', async () => {
    const sel = rows();
    await scanContext('').restoreScanAdapter(sel);
    eq(sel.selectedIndex, 1);
  });
  await check('a saved physical adapter that is not the best is kept', async () => {
    const sel = rows();
    await scanContext(WIFI).restoreScanAdapter(sel);
    eq(sel.selectedIndex, 0);
  });
  await check('a saved VIRTUAL adapter is kept over the ranked-best one', async () => {
    const sel = rows();
    await scanContext(VETH).restoreScanAdapter(sel);
    eq(sel.selectedIndex, 2, 'the operator chose it');
  });
  await check('a saved adapter that has gone falls back to the ranked-best one', async () => {
    const sel = rows();
    await scanContext('203.0.113.99').restoreScanAdapter(sel);
    eq(sel.selectedIndex, 1);
  });
  await check('nothing saved and nothing ranked: the first entry, unchanged', async () => {
    const sel = scanSelect([[WIFI, false], [ETH, false]]);
    await scanContext('').restoreScanAdapter(sel);
    eq(sel.selectedIndex, 0);
  });

  console.log('Settings: Remote Access adapter');
  const ctx = vm.createContext({ document: documentStub, console });
  vm.runInContext(lift(SETTINGS, 'fillAdapterList'), ctx);
  const adapters = [
    { id: 'wifi', address: WIFI, name: 'Wi-Fi' },
    { id: 'eth', address: ETH, name: 'Ethernet', preferred: true },
    { id: 'veth', address: VETH, name: 'vEthernet' },
  ];
  const fill = (state, previous) => {
    const sel = new Select([]);
    if (previous) { const o = new Option(); o.value = previous; sel._options = [o]; sel.selectedIndex = 0; }
    ctx.fillAdapterList(sel, Object.assign({ adapters: adapters.map((a) => Object.assign({}, a)) }, state));
    return sel;
  };

  await check('nothing chosen or configured: starts on the ranked-best adapter', () => {
    eq(fill({}).value, 'eth');
  });
  await check('a configured adapter wins over the ranking', () => {
    eq(fill({ configured_adapter: 'wifi' }).value, 'wifi');
  });
  await check('a configured VIRTUAL adapter wins over the ranking', () => {
    eq(fill({ configured_adapter: 'veth' }).value, 'veth');
  });
  await check('what the operator just picked wins over the configured one and the ranking', () => {
    eq(fill({ configured_adapter: 'wifi' }, 'veth').value, 'veth');
  });
  await check('a configured address finds its adapter', () => {
    eq(fill({ configured_address: VETH }).value, 'veth');
  });
  await check('nothing ranked: the first entry', () => {
    const sel = new Select([]);
    ctx.fillAdapterList(sel, { adapters: adapters.map((a) => ({ id: a.id, address: a.address, name: a.name })) });
    eq(sel.selectedIndex, 0);
  });

  if (failures.length) { console.log('\n' + failures.length + ' failed'); process.exit(1); }
  console.log('\nall adapter-default checks passed');
})();

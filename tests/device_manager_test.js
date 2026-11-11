/*
 * Device Info: the compact uptime and the row a standalone USB extender gets.
 *
 * Lifts the real functions out of ui/index.html and runs them. Uptime is shown as
 * 3m, 2h 14m, 1d 2h 49m, 73d 23h 33m -- never with a leading zero unit -- and a dash
 * when the unit gave none. An extender row offers no selection and carries no data-ip,
 * because polling and every per-unit action find a row by that attribute.
 *
 * Run: node tests/device_manager_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const INDEX = fs.readFileSync(path.join(__dirname, '..', 'ui', 'index.html'), 'utf8');
const failures = [];
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures.push(`${name}: ${err.message}`); console.log(`  FAIL ${name}: ${err.message}`); }
}
function eq(actual, expected, label) {
  if (actual !== expected) throw new Error(`${label || ''} expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

function lift(name) {
  const start = INDEX.search(new RegExp('\\bfunction\\s+' + name + '\\s*\\('));
  if (start < 0) throw new Error('function not found: ' + name);
  let depth = 0, i = INDEX.indexOf('{', start);
  for (; i < INDEX.length; i++) {
    if (INDEX[i] === '{') depth++;
    else if (INDEX[i] === '}' && --depth === 0) return INDEX.slice(start, i + 1);
  }
  throw new Error('unbalanced: ' + name);
}

// A document that is just enough for buildExtenderRow: createElement returns a row that
// records its attributes and keeps the markup it was given.
class Row {
  constructor() { this.attrs = {}; this.className = ''; this.innerHTML = ''; }
  setAttribute(k, v) { this.attrs[k] = v; }
  hasAttribute(k) { return k in this.attrs; }
}
const ctx = vm.createContext({ document: { createElement: () => new Row() } });
vm.runInContext(['escapeHtml', 'escapeAttr', 'formatUptime', 'buildExtenderRow'].map(lift).join('\n'), ctx);

const DAY = 86400, HOUR = 3600;
const cases = [
  [0, '<1m'], [1, '<1m'], [59, '<1m'], [60, '1m'], [180, '3m'], [3599, '59m'],
  [3600, '1h 0m'], [2 * HOUR + 14 * 60, '2h 14m'],
  [DAY, '1d 0h 0m'], [DAY + 2 * HOUR + 49 * 60, '1d 2h 49m'],
  [73 * DAY + 23 * HOUR + 33 * 60, '73d 23h 33m'], [6396067, '74d 0h 41m'],
  [59.9, '<1m'], [3 * 60 + 0.5, '3m'],
];
for (const [seconds, text] of cases) {
  check(`${seconds} s reads ${text}`, () => eq(ctx.formatUptime(seconds), text));
}
check('no reading is a dash and never a zero', () => {
  for (const bad of [null, undefined, NaN, Infinity, -1, '123', '', {}, [], true]) {
    eq(ctx.formatUptime(bad), '—', JSON.stringify(bad));
  }
});
check('a leading zero unit is never shown', () => {
  for (let s = 0; s < 3 * DAY; s += 997) {
    const text = ctx.formatUptime(s);
    if (/^0[dh]/.test(text)) throw new Error(`${s} s -> ${text}`);
  }
});

const row = ctx.buildExtenderRow({ ip: '192.0.2.138', mac: '00:1B:13:FA:CE:11', model: 'HW-OMNI-324', version: '1.9.4', type: 'USB Extender', online: true });
check('an extender row is not found by data-ip', () => {
  eq(row.hasAttribute('data-ip'), false);
  eq(row.attrs['data-extender-ip'], '192.0.2.138');
});
check('an extender row offers no selection, blink, preview or log', () => {
  if (/row-select|blink-btn|log-btn|preview-checkbox|type="checkbox"/.test(row.innerHTML)) throw new Error(row.innerHTML);
});
check('an extender row is eighteen cells, like every other row', () => {
  eq((row.innerHTML.match(/<td/g) || []).length, 18);
});
check('an extender row shows what the extender reported and a dash elsewhere', () => {
  for (const t of ['192.0.2.138', '00:1B:13:FA:CE:11', 'HW-OMNI-324', '1.9.4', 'USB Extender', 'Online']) {
    if (!row.innerHTML.includes(t)) throw new Error('missing ' + t);
  }
  if ((row.innerHTML.match(/—/g) || []).length < 9) throw new Error('unavailable fields are not dashes');
});
check('an offline extender says so', () => {
  const off = ctx.buildExtenderRow({ ip: '192.0.2.139', online: false });
  if (!off.innerHTML.includes('Offline')) throw new Error(off.innerHTML);
});
check('text an extender reports is never markup', () => {
  const evil = ctx.buildExtenderRow({ ip: '"><img src=x onerror=1>', model: '<script>x</script>', version: "'\"<b>", mac: '<i>' });
  if (/<img|<script|<b>|<i>/.test(evil.innerHTML)) throw new Error(evil.innerHTML);
});

check('the table header places Uptime immediately left of TimeZone', () => {
  const heads = [...INDEX.matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map(m => m[1].trim());
  const at = heads.indexOf('Uptime');
  if (at < 0 || heads[at + 1] !== 'TimeZone') throw new Error(heads.join('|'));
  if (heads[at - 1] !== 'Codec') throw new Error('Uptime is not after Codec: ' + heads.join('|'));
});
check('the status cell index follows the header', () => {
  const heads = [...INDEX.matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map(m => m[1].trim());
  // heads omits the checkbox column (its header holds an input), so Status is 12 here
  // and the 14th cell of a row.
  eq(heads.indexOf('Status'), 12);
  if (/cells\??\.?\[12\]/.test(INDEX.replace(/\/\/[^\n]*/g, ''))) throw new Error('a stale status index remains');
});

console.log(failures.length ? `\n${failures.length} FAILED` : '\ndevice manager test: all checks passed');
process.exit(failures.length ? 1 : 0);

/*
 * Device Info columns: every cell holds the field its header names.
 *
 * Model showed the firmware, Timezone showed the NTP server and Uptime showed the timezone
 * because the poll patched cells by literal index and the Uptime column had since moved them.
 * Values here are deliberately all different so aliasing between any two columns fails.
 *
 * Run: node tests/device_columns_test.js
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
function eq(a, e, l) { if (a !== e) throw new Error(`${l || ''} expected ${JSON.stringify(e)}, got ${JSON.stringify(a)}`); }

function lift(name) {
  const start = INDEX.indexOf('function ' + name + '(');
  if (start < 0) throw new Error('function not found: ' + name);
  let depth = 0, i = INDEX.indexOf('{', start);
  for (; i < INDEX.length; i++) {
    if (INDEX[i] === '{') depth++;
    else if (INDEX[i] === '}' && --depth === 0) return INDEX.slice(start, i + 1);
  }
  throw new Error('unbalanced: ' + name);
}
const stripComments = (t) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const HEADERS = ['select', 'IP', 'Hostname', 'MAC', 'Link Speed', 'Codec', 'Uptime', 'TimeZone', 'NTP Server',
  'Type', 'Model', 'Version', 'Password', 'Status', 'Blink', 'Preview', 'USB', 'Log'];

const mapSrc = INDEX.match(/var DEVICE_COL = Object\.freeze\(\{[\s\S]*?\}\);/);
check('the column map exists', () => { if (!mapSrc) throw new Error('DEVICE_COL missing'); });
const ctx = vm.createContext({ console });
vm.runInContext(mapSrc[0] + '\n' + lift('updatePolledUnitDetails') + '\nfunction linkspeedMarkup(x){return String(x)}', ctx);
const COL = vm.runInContext('DEVICE_COL', ctx);

check('the map agrees with the table header, in order', () => {
  const ths = [...INDEX.matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map((m) => m[1].trim());
  const at = ths.indexOf('IP') - 1;
  const names = { ip: 'IP', hostname: 'Hostname', mac: 'MAC', linkspeed: 'Link Speed', codec: 'Codec', uptime: 'Uptime',
    timezone: 'TimeZone', ntp: 'NTP Server', type: 'Type', model: 'Model', version: 'Version', password: 'Password', status: 'Status' };
  for (const [key, header] of Object.entries(names)) eq(ths[at + COL[key]], header, key);
});

check('the row template emits each field in the cell the map says', () => {
  const body = stripComments(INDEX);
  const start = body.indexOf('tr.innerHTML = `');
  const end = body.indexOf('`;', start);
  const cells = body.slice(start, end).split(/<td[\s>]/).slice(1);
  const must = { ip: 'u.ip', hostname: 'u.hostname', mac: 'u.mac', linkspeed: 'linkspeed', codec: 'buildCodecCell',
    uptime: 'uptime_seconds', timezone: '${escapeHtml(timezone', ntp: '${escapeHtml(ntpServer', type: 'u.type',
    model: 'u.model', version: 'u.version', password: 'passwordSource' };
  for (const [key, needle] of Object.entries(must)) {
    if (!cells[COL[key]] || !cells[COL[key]].includes(needle)) throw new Error(`${key} cell ${COL[key]} lacks ${needle}`);
  }
  // timezone and ntp are computed from different fields
  if (!/const timezone = \(\s*u\.timezone/.test(body)) throw new Error('timezone not from timezone');
  if (!/const ntpServer = \(\s*u\.ntp_server/.test(body)) throw new Error('ntp not from ntp_server');
});

function row() {
  const cells = HEADERS.map(() => ({ textContent: '', innerHTML: '' }));
  cells[COL.uptime].textContent = '3d 1h 2m'; cells[COL.timezone].textContent = 'old/Zone';
  cells[COL.ntp].textContent = 'old.ntp.example'; cells[COL.model].textContent = 'HW-OMNI-E4511';
  cells[COL.type].textContent = 'Encoder'; cells[COL.version].textContent = '1.0.0';
  cells[COL.hostname].textContent = 'old-host';
  return { cells, getAttribute: () => '203.0.113.9', querySelector: () => null };
}

check('a poll with different timezone, NTP, firmware and hostname patches only its own cells', () => {
  const r = row();
  ctx.updatePolledUnitDetails(r, { ip: '203.0.113.9', hostname: 'new-host', timezone: 'Europe/Oslo',
    ntp_server: 'ntp.example.net', fw: '9.8.7', version: '9.8.7', model: 'SHOULD-NOT-APPEAR' });
  eq(r.cells[COL.hostname].textContent, 'new-host', 'hostname');
  eq(r.cells[COL.timezone].textContent, 'Europe/Oslo', 'timezone');
  eq(r.cells[COL.ntp].textContent, 'ntp.example.net', 'ntp');
  eq(r.cells[COL.version].textContent, '9.8.7', 'version');
  eq(r.cells[COL.model].textContent, 'HW-OMNI-E4511', 'model untouched');
  eq(r.cells[COL.uptime].textContent, '3d 1h 2m', 'uptime untouched');
  eq(r.cells[COL.type].textContent, 'Encoder', 'type untouched');
});

check('a poll that carries no timezone leaves the displayed one alone', () => {
  const r = row();
  ctx.updatePolledUnitDetails(r, { ip: '203.0.113.9', fw: '2.2.2' });
  eq(r.cells[COL.timezone].textContent, 'old/Zone');
  eq(r.cells[COL.ntp].textContent, 'old.ntp.example');
  eq(r.cells[COL.model].textContent, 'HW-OMNI-E4511');
});

check('no cell is read or written by a bare numeric index in the polling or action code', () => {
  const body = stripComments(INDEX);
  // type must be read through the map; 8 is the NTP column
  if (/cells\??\.?\[8\]/.test(body)) throw new Error('cells[8] (NTP) is being read as something else');
  if (/cells\??\.?\[(6|7)\]/.test(body)) throw new Error('a bare timezone/uptime index remains');
});

if (failures.length) { console.log(`\n${failures.length} failed`); process.exit(1); }
console.log('\nall device column checks passed');

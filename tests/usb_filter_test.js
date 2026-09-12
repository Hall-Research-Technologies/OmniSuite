/**
 * Configure > USB inventory filter.
 *
 * The defect this guards: the filter searched `device_type`, which for an
 * integrated endpoint holds the UDP-reported USB *role* name. An E4521's Icron
 * endpoint reports "AT-OMNI-311" and a D4511's reports "AT-OMNI-324", so
 * searching "-311" matched every E4521 row. Those values are present on the
 * fixtures below precisely so a regression would be caught.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = path.join(__dirname, '..', 'ui', 'matrix', 'usb-extenders.js');
const failures = [];
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures.push(`${name}: ${err && err.message}`); console.log(`  FAIL ${name}: ${err && err.message}`); }
}

// ---- load the module in a sandbox -----------------------------------------
const noop = () => {};
const element = () => ({
  value: '', innerHTML: '', textContent: '', onclick: null, style: {},
  addEventListener: noop, querySelectorAll: () => [], querySelector: () => null,
  classList: {add: noop, remove: noop, contains: () => false},
});
const sandbox = {
  document: {
    querySelector: () => element(), querySelectorAll: () => [],
    addEventListener: noop, createElement: element, body: element(),
  },
  window: {addEventListener: noop},
  fetch: async () => ({json: async () => ({ok: true, devices: [], ranges: []})}),
  setInterval: () => 0, clearInterval: noop, setTimeout: noop, clearTimeout: noop,
  console, JSON, Object, Array, String, Number, Boolean, Math, Date, RegExp, Error, Promise,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), sandbox, {filename: 'usb-extenders.js'});
const matches = sandbox.usbRowMatches;
if (typeof matches !== 'function') {
  console.error('usbRowMatches was not exposed by usb-extenders.js');
  process.exit(1);
}

// ---- synthetic inventory ---------------------------------------------------
// Addresses and MACs share nothing with the bench. device_type deliberately
// carries the misleading UDP role name on the integrated rows.
const INVENTORY = [
  {display_model: 'AT-OMNI-311', device_type: 'AT-OMNI-311', classification: 'STANDALONE',
   usb_role: 'USB Host / LEX', ip: '10.20.0.11', usb_ip: '10.20.0.11',
   mac: 'AA:11:00:00:00:11', parent_hostname: '', firmware: '1.9.4',
   network_mode: 'DHCP', online: true, link_state: 'LINKED', link_state_fresh: true},
  {display_model: 'AT-OMNI-324', device_type: 'AT-OMNI-324', classification: 'STANDALONE',
   usb_role: 'USB Device / REX', ip: '10.20.0.24', usb_ip: '10.20.0.24',
   mac: 'AA:11:00:00:00:24', parent_hostname: '', firmware: '1.9.4',
   network_mode: 'DHCP', online: true, link_state: 'NOT_LINKED', link_state_fresh: true},
  // The trap: an E4521 whose UDP-reported type is "AT-OMNI-311".
  {display_model: 'HW-OMNI-E4521', device_type: 'AT-OMNI-311', classification: 'INTEGRATED',
   usb_role: 'USB Host / LEX', parent_ip: '10.30.0.41', usb_ip: '10.30.0.141',
   mac: 'BB:22:00:00:00:41', parent_hostname: 'syn-encoder-0041', firmware: '2.1.2',
   network_mode: 'STATIC', online: true, link_state: 'UNKNOWN', link_state_fresh: false},
  // And a D4511 whose UDP-reported type is "AT-OMNI-324".
  {display_model: 'HW-OMNI-D4511', device_type: 'AT-OMNI-324', classification: 'INTEGRATED',
   usb_role: 'USB Device / REX', parent_ip: '10.30.0.51', usb_ip: '10.30.0.151',
   mac: 'BB:22:00:00:00:51', parent_hostname: 'syn-decoder-0051', firmware: '2.1.2',
   network_mode: 'DHCP', online: false, link_state: 'UNKNOWN', link_state_fresh: false},
];

const models = q => INVENTORY.filter(d => matches(d, q)).map(d => d.display_model);
const expect = (q, want) => {
  const got = models(q);
  const same = got.length === want.length && want.every((m, i) => got[i] === m);
  if (!same) throw new Error(`filter ${JSON.stringify(q)} -> [${got}] , expected [${want}]`);
};

// ---- the reported defect ---------------------------------------------------
for (const q of ['311', '-311', 'OMNI-311', 'AT-OMNI-311', 'at-omni-311']) {
  check(`"${q}" matches only AT-OMNI-311`, () => expect(q, ['AT-OMNI-311']));
}
for (const q of ['324', '-324', 'OMNI-324', 'AT-OMNI-324']) {
  check(`"${q}" matches only AT-OMNI-324`, () => expect(q, ['AT-OMNI-324']));
}
check('the integrated role name is not searchable at all', () => {
  // Both integrated rows carry a device_type of AT-OMNI-311/324; neither may match.
  if (models('311').includes('HW-OMNI-E4521')) throw new Error('E4521 matched "311"');
  if (models('324').includes('HW-OMNI-D4511')) throw new Error('D4511 matched "324"');
});

check('"E4521" matches the encoder rows', () => expect('E4521', ['HW-OMNI-E4521']));
check('"D4511" matches the decoder rows', () => expect('D4511', ['HW-OMNI-D4511']));
check('"OMNI" still matches every model', () => {
  if (models('OMNI').length !== 4) throw new Error(`got ${models('OMNI').length}`);
});

// ---- general search must keep working --------------------------------------
check('IP search', () => expect('10.30.0.41', ['HW-OMNI-E4521']));
check('partial IP search', () => {
  if (models('10.20.0.').length !== 2) throw new Error('expected both standalone rows');
});
check('MAC search', () => expect('BB:22:00:00:00:51', ['HW-OMNI-D4511']));
check('partial MAC search', () => {
  if (models('AA:11').length !== 2) throw new Error('expected both standalone rows');
});
check('hostname search', () => expect('syn-decoder', ['HW-OMNI-D4511']));
check('role search', () => {
  const rex = models('REX');
  if (rex.length !== 2) throw new Error(`expected both REX rows, got [${rex}]`);
});
check('firmware search', () => {
  if (models('2.1.2').length !== 2) throw new Error('expected both integrated rows');
});
check('status search', () => expect('offline', ['HW-OMNI-D4511']));

// ---- field-qualified search ------------------------------------------------
check('model:311 is model-scoped', () => expect('model:311', ['AT-OMNI-311']));
check('model:324 is model-scoped', () => expect('model:324', ['AT-OMNI-324']));
check('ip: prefix searches addresses', () => expect('ip:10.30.0.51', ['HW-OMNI-D4511']));
check('mac: prefix searches MACs', () => expect('mac:00:00:00:11', ['AT-OMNI-311']));
check('role: prefix searches the USB role', () => {
  if (models('role:lex').length !== 2) throw new Error('expected both LEX rows');
});
check('status: prefix searches liveness', () => expect('status:offline', ['HW-OMNI-D4511']));
check('an unknown prefix falls back to plain text', () => {
  if (models('nosuchfield:zzz').length !== 0) throw new Error('should match nothing');
});

// ---- multi-term and clearing -----------------------------------------------
check('multiple terms narrow the result', () => expect('omni lex 10.20', ['AT-OMNI-311']));
check('an empty filter restores every row', () => {
  for (const q of ['', '   ', null, undefined]) {
    if (INVENTORY.filter(d => matches(d, q)).length !== INVENTORY.length) {
      throw new Error(`filter ${JSON.stringify(q)} hid rows`);
    }
  }
});
check('a filter that matches nothing hides everything without error', () => {
  if (models('zzzz-no-such-device').length !== 0) throw new Error('expected no rows');
});
check('filtering never mutates the inventory', () => {
  const before = JSON.stringify(INVENTORY);
  models('311'); models(''); models('mac:AA');
  if (JSON.stringify(INVENTORY) !== before) throw new Error('the inventory was modified');
});

console.log('');
if (failures.length) {
  console.error(`FAILED: ${failures.length} check(s)`);
  failures.forEach(f => console.error('  - ' + f));
  process.exit(1);
}
console.log('usb-extenders.js filter test: all checks passed');
process.exit(0);

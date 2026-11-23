// Renders the Admin USB Matrix (ui/matrix/usb.js, unchanged) from a payload given as a JSON file and prints,
// as JSON, what each cell is: disabled, paired, active. It exists so the user matrix's eligibility can be
// compared with the Admin page's own rendering rather than with a second reading of its rules.
//
//   node tests/usb_admin_cells_probe.js <payload.json>
//
// The DOM stub is the one tests/usb_render_smoke.js already uses, lifted from its source so there is one
// stub to maintain.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const smoke = fs.readFileSync(path.join(__dirname, 'usb_render_smoke.js'), 'utf8').split('\r\n').join('\n');
const from = smoke.indexOf('function makeElement(tag)');
const to = smoke.indexOf('// ---- representative live API payload');
if (from < 0 || to < 0) throw new Error('the shared DOM stub moved; update the probe');
// eslint-disable-next-line no-eval
const helpers = new Function(smoke.slice(from, to) + '\nreturn {makeElement, buildDom};')();
const {document, registry} = helpers.buildDom();

const payload = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const sandbox = {
  document,
  window: {innerWidth: 1440, innerHeight: 900, addEventListener() {}, removeEventListener() {}},
  localStorage: {getItem: () => null, setItem() {}, removeItem() {}},
  console: {log() {}, error() {}, warn() {}, info() {}},
  setTimeout, clearTimeout, setInterval() { return 0; }, clearInterval() {},
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  fetch: async () => ({ok: true, status: 200, json: async () => payload, text: async () => JSON.stringify(payload)}),
  toast() {}, toastError() {}, toastOk() {},
  Symbol, Promise, Date, Math, JSON, Number, String, Boolean, Array, Object, Set, Map, Error,
};
sandbox.globalThis = sandbox;
sandbox.window.document = document;
const context = vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'ui', 'matrix', 'usb.js'), 'utf8'), context,
                {filename: 'usb.js'});
Object.assign(context, {__STATE__: payload});
vm.runInContext('render(__STATE__)', context, {filename: 'render'});
const html = registry.get('usbMatrix').innerHTML;
const cells = [];
for (const m of html.matchAll(/<td\s([^>]*?)>/g)) {
  const attrs = new Map();
  for (const a of m[1].matchAll(/([\w-]+)="([^"]*)"/g)) attrs.set(a[1], a[2]);
  cells.push({rex: attrs.get('data-rex'), lex: attrs.get('data-lex'),
              rexMac: attrs.get('data-rex-mac'), lexMac: attrs.get('data-lex-mac'),
              disabled: /\bdisabled\b/.test(attrs.get('class') || ''),
              paired: attrs.get('data-paired') === 'true', active: attrs.get('data-active') === 'true',
              path: attrs.get('data-path')});
}
process.stdout.write(JSON.stringify(cells));
setTimeout(() => process.exit(0), 0);

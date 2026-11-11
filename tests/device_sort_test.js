/*
 * Device Info sorting: addresses sort as addresses, numbers as numbers, versions
 * with numeric collation, and the link-speed colours come from tokens.
 *
 * The functions are lifted out of ui/index.html and run for real.
 *
 * Run: node tests/device_sort_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const PAGE = fs.readFileSync(path.join(ROOT, 'ui', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
const CSS = fs.readFileSync(path.join(ROOT, 'ui', 'device-table.css'), 'utf8');
let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures += 1; console.log(`  FAIL ${name}: ${err.message}`); }
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }

function lift(startPattern, endPattern) {
  const at = PAGE.search(startPattern);
  if (at < 0) throw new Error('not found: ' + startPattern);
  const rest = PAGE.slice(at);
  const stop = rest.search(endPattern);
  return rest.slice(0, stop < 0 ? rest.length : stop);
}
const ctx = {escapeHtml: (s) => String(s).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]))};
vm.createContext(ctx);
vm.runInContext(lift(/const IPV4_TEXT/, /\nfunction sortTable\(/), ctx);
vm.runInContext(lift(/function linkspeedMarkup\(/, /\nfunction setStatus\(/), ctx);
const sorted = (values) => [...values].sort(ctx.compareDeviceValues);

check('10.0.0.9 sorts before 10.0.0.10', () => {
  assert(ctx.compareDeviceValues('10.0.0.9', '10.0.0.10') < 0);
  assert(ctx.compareDeviceValues('10.0.0.10', '10.0.0.9') > 0);
});
check('192.168.101.9 sorts before 192.168.101.10, and before 198.18.150.2', () => {
  assert(ctx.compareDeviceValues('192.168.101.9', '192.168.101.10') < 0);
  assert(ctx.compareDeviceValues('192.168.101.200', '198.18.150.2') < 0);
});
check('a whole column of addresses is in numeric order, not character order', () => {
  const column = ['192.168.101.9', '192.168.101.100', '192.168.101.10', '192.168.101.2', '192.168.99.250'];
  assert(JSON.stringify(sorted(column)) === JSON.stringify(
    ['192.168.99.250', '192.168.101.2', '192.168.101.9', '192.168.101.10', '192.168.101.100']), sorted(column).join(' '));
});
check('equal addresses compare equal', () => assert(ctx.compareDeviceValues('10.1.1.1', '10.1.1.1') === 0));
check('numbers sort as numbers', () => assert(ctx.compareDeviceValues('900', '1000') < 0 && ctx.compareDeviceValues('-5', '3') < 0));
check('versions and hostnames use numeric collation', () => {
  assert(ctx.compareDeviceValues('2.0.9', '2.0.10') < 0, '2.0.9 < 2.0.10');
  assert(ctx.compareDeviceValues('enc-9', 'enc-10') < 0, 'enc-9 < enc-10');
  assert(ctx.compareDeviceValues('2.1.2', '2.0.3b') > 0, '2.1.2 > 2.0.3b');
});
check('an address beside text does not throw and stays ordered', () => {
  assert(typeof ctx.compareDeviceValues('10.0.0.1', '') === 'number');
  assert(typeof ctx.compareDeviceValues('', '') === 'number');
});
check('link speed is classed green at exactly 1000 and red otherwise, never a literal colour', () => {
  assert(/linkspeed-ok/.test(ctx.linkspeedMarkup(1000)) && !/linkspeed-slow/.test(ctx.linkspeedMarkup(1000)));
  for (const slow of [100, 10, 2500, '0']) assert(/linkspeed-slow/.test(ctx.linkspeedMarkup(slow)), 'slow: ' + slow);
  assert(!/#[0-9a-fA-F]{3,6}|style=/.test(ctx.linkspeedMarkup(1000)), 'no inline colour');
});
check('link speed sorts by its number, and text from a misbehaving device is escaped', () => {
  assert(/data-sort="1000"/.test(ctx.linkspeedMarkup('1000')) && /data-sort="100"/.test(ctx.linkspeedMarkup(100)));
  assert(!/data-sort/.test(ctx.linkspeedMarkup('n/a')), 'a non-number carries no sort key');
  assert(/&lt;b&gt;/.test(ctx.linkspeedMarkup('<b>')), 'device text is escaped');
});
check('the link-speed colours are state TEXT tokens in the stylesheet', () => {
  const css = CSS.replace(/\/\*[\s\S]*?\*\//g, '');
  assert(/\.linkspeed-ok\s*\{\s*color:\s*var\(--state-success-text\)/.test(css), 'ok');
  assert(/\.linkspeed-slow\s*\{\s*color:\s*var\(--state-error-text\)/.test(css), 'slow');
});
check('sorting never makes a header translucent: no opacity on header cells', () => {
  const body = PAGE.slice(PAGE.indexOf('function sortTable('), PAGE.indexOf('// Poll Units functionality'))
    .replace(/\/\/.*$/gm, '');
  assert(!/style\.opacity|opacity\s*:/.test(body), 'sortTable must not set opacity on a header');
  assert(/th\.classList\.toggle\('th-dim'/.test(body), 'dimming is a class');
  const css = CSS.replace(/\/\*[\s\S]*?\*\//g, '');
  assert(/th\.th-dim\s*\{\s*color:\s*var\(--text-muted\)/.test(css), 'dimmed by a theme colour token');
});
check('sortable headers are keyboard operable and announce their order', () => {
  const body = PAGE.slice(PAGE.indexOf('function makeSortableHeadersKeyboardOperable'), PAGE.indexOf('// Poll Units functionality'));
  assert(/th\.tabIndex = 0/.test(body), 'focusable');
  assert(/event\.key === 'Enter' \|\| event\.key === ' '/.test(body) && /th\.click\(\)/.test(body), 'Enter and Space sort');
  assert(/makeSortableHeadersKeyboardOperable\(\);/.test(PAGE), 'it is called');
  assert(/aria-sort', idx === colIndex/.test(PAGE), 'sortTable keeps aria-sort current');
  const css = CSS.replace(/\/\*[\s\S]*?\*\//g, '');
  assert(/th:focus-visible\s*\{[^}]*outline/.test(css), 'focus is visible');
  assert(!/:focus\s*\{\s*outline:\s*none/.test(css), 'focus is never hidden outright');
});
check('link-speed text is readable on light pages through the shared text tokens', () => {
  // The darkening for a light page lives in the --state-*-text tokens, restated per mode
  // and proved against every preset by state_text_contrast_test.js. A light-only rule here
  // would be a second place to keep in step.
  const css = CSS.replace(/\/\*[\s\S]*?\*\//g, '');
  assert(!/html\.light \.linkspeed|body\.light \.linkspeed/.test(css), 'no light-only link-speed rule remains');
});
check('the sort indicator text is unchanged', () => {
  assert(/sortAscending \? ' ↑' : ' ↓'/.test(PAGE), 'arrows kept');
});

if (failures) { console.log(`\nDevice sort test: ${failures} failure(s)`); process.exit(1); }
console.log('\nDevice sort test: all checks passed');

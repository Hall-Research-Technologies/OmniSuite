/*
 * The A/V Matrix multi-display selection is held by identity, not by address.
 *
 * A tick follows the unit that was ticked. When a unit moves and another takes
 * its address, the newcomer must not appear ticked, and a route to the selection
 * must resolve each ticked unit to where it is now. Functions are lifted from
 * ui/matrix/matrix.js and run; the source assertions are comment-stripped.
 *
 * Run: node tests/matrix_selection_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const MATRIX = fs.readFileSync(path.join(ROOT, 'ui', 'matrix', 'matrix.js'), 'utf8').replace(/\r\n/g, '\n');

const failures = [];
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures.push(name); console.log(`  FAIL ${name}: ${err && err.message}`); }
}
function eq(a, b, m) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error((m || 'not equal') + `: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`); }
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }

function lift(source, startPattern) {
  const at = source.search(startPattern);
  if (at < 0) throw new Error('not found in matrix.js: ' + startPattern);
  const params = source.slice(at).search(/\)\s*\{/);
  let i = source.indexOf('{', at + params), depth = 0;
  for (; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === '{') depth += 1;
    else if (ch === '}') { depth -= 1; if (depth === 0) return source.slice(at, i + 1); }
  }
  throw new Error('unbalanced: ' + startPattern);
}
const stripComments = (code) => code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

const context = {};
vm.createContext(context);
vm.runInContext('let selectedDecoders = new Set();', context);
for (const pattern of [/function decoderKey\(/, /function pruneDecoderSelection\(/, /function checkedDecoderIps\(/]) {
  vm.runInContext(lift(MATRIX, pattern), context);
}
const run = (code) => vm.runInContext(code, context);

const A = {ip: '198.51.100.1', mac: 'B8:98:B0:00:07:01'};
const B = {ip: '198.51.100.2', mac: 'b8-98-b0-00-07-02'};
const C = {ip: '198.51.100.3', mac: 'b8:98:b0:00:07:03'};
context.A = A; context.B = B; context.C = C;

check('a key is the canonical identity, however the MAC is written', () => {
  eq(run('decoderKey(B)'), 'mac:b898b0000702');
  eq(run('decoderKey(A)'), 'mac:b898b0000701');
});
check('a unit with no usable MAC falls back to its address and never to a half identity', () => {
  eq(run("decoderKey({ip: '198.51.100.9'})"), 'ip:198.51.100.9');
  eq(run("decoderKey({ip: '198.51.100.9', mac: 'b898'})"), 'ip:198.51.100.9');
});
check('after A moves and C takes its old address, the newcomer is not ticked and A still is', () => {
  run('selectedDecoders.clear(); selectedDecoders.add(decoderKey(A));');
  context.after = [{ip: '198.51.100.77', mac: A.mac}, B, {ip: A.ip, mac: C.mac}];
  assert(run('selectedDecoders.has(decoderKey(after[0]))'), 'A still ticked at its new address');
  assert(!run('selectedDecoders.has(decoderKey(after[2]))'), 'C is not ticked just because it holds A\'s old address');
  eq(run("checkedDecoderIps(['mac:b898b0000701'], after)"), ['198.51.100.77']);
});
check('a ticked key resolves to nothing once its unit is gone', () => {
  context.gone = [B, C];
  eq(run("checkedDecoderIps(['mac:b898b0000701'], gone)"), []);
});
check('pruning drops selections for absent units, so a later arrival does not inherit them', () => {
  run('selectedDecoders.clear(); selectedDecoders.add(decoderKey(A)); selectedDecoders.add(decoderKey(B));');
  context.present = [B, C];
  run('pruneDecoderSelection(present)');
  eq(Array.from(run('selectedDecoders')), ['mac:b898b0000702']);
  context.back = [A, B];
  assert(!run('selectedDecoders.has(decoderKey(back[0]))'), 'A returning is not silently re-ticked');
});
check('routes come out in the grid order and only for ticked units', () => {
  context.grid = [A, B, C];
  eq(run("checkedDecoderIps(['mac:b898b0000703', 'mac:b898b0000701'], grid)"), ['198.51.100.1', '198.51.100.3']);
});
check('the page keys its tick state and its route list by identity, not by data-dec-ip', () => {
  const code = stripComments(MATRIX);
  const render = code.slice(code.indexOf('const rows = dec.map(d=>{'), code.indexOf('t.innerHTML = head + rows;'));
  assert(render.includes('selectedDecoders.has(decoderKey(d))'), 'rows are ticked by identity');
  assert(render.includes('data-dec-key='), 'the checkbox carries its identity');
  const change = code.slice(code.indexOf("el.matches('.group-checkbox')"), code.indexOf('async function onMatrixCellClick'));
  assert(!change.includes("getAttribute('data-dec-ip')"), 'the change handler never records an address');
  const click = code.slice(code.indexOf('async function onMatrixCellClick'), code.indexOf('const requestedTargets'));
  assert(click.includes('checkedDecoderIps('), 'the click resolves ticked units through the helper');
  assert(!/map\(cb => cb\.getAttribute\('data-dec-ip'\)\)/.test(click), 'the click does not read addresses off the DOM');
});

if (failures.length) { console.log(`\nMatrix selection test: ${failures.length} failure(s)`); process.exit(1); }
console.log('\nMatrix selection test: all checks passed');

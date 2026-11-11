/*
 * The A/V Matrix lifecycle: structure versus state, delegation, render-before-poll
 * and the prepared snapshot.
 *
 * What this exists to hold:
 *  - a state-only change patches the cells that moved and rebuilds nothing;
 *  - the grid's listeners are bound once, not once per cell per render;
 *  - the grid is drawn from /api/state before any decoder is asked anything;
 *  - the prepared snapshot expires, is replaced when the inventory changes, and is
 *    fetched once however many events ask for it.
 *
 * Functions are lifted out of ui/matrix/matrix.js and run against a model of just
 * what they touch. A source-text assertion would prove the text; this drives them.
 *
 * Run: node tests/matrix_lifecycle_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const MATRIX = fs.readFileSync(path.join(ROOT, 'ui', 'matrix', 'matrix.js'), 'utf8').replace(/\r\n/g, '\n');
const PREP = fs.readFileSync(path.join(ROOT, 'ui', 'matrix-prep.js'), 'utf8');

const failures = [];
async function check(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures.push(name); console.log(`  FAIL ${name}: ${err && err.message}`); }
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
function eq(a, b, m) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error((m || 'not equal') + `: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`); }

// Lift a top-level declaration by brace matching, so the test runs the real text.
function lift(source, startPattern) {
  const at = source.search(startPattern);
  if (at < 0) throw new Error('not found in matrix.js: ' + startPattern);
  const params = source.slice(at).search(/\)\s*\{/);   // the body's brace, not one in a default parameter
  let i = source.indexOf('{', at + params), depth = 0;
  for (; i < source.length; i += 1) {
    const ch = source[i];
    if (ch === '{') depth += 1;
    else if (ch === '}') { depth -= 1; if (depth === 0) return source.slice(at, i + 1); }
  }
  throw new Error('unbalanced: ' + startPattern);
}
function stripComments(code) { return code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, ''); }

// ---- a grid the lifted functions can patch and count writes on -------------------------
function makeGrid(enc, dec, flagsOf) {
  const writes = {radio: 0, dot: 0, cls: 0};
  const cells = [];
  for (const d of dec) for (const e of enc) {
    const f = flagsOf(d, e);
    const classes = new Set(f.codecCompatible ? [] : ['codec-blocked']);
    if (f.audioMatch) classes.add('audio-on');
    const radio = {_checked: f.videoMatch, _disabled: !f.codecCompatible,
      get checked() { return this._checked; }, set checked(v) { writes.radio += 1; this._checked = v; },
      get disabled() { return this._disabled; }, set disabled(v) { writes.cls += 1; this._disabled = v; }};
    const dot = {classList: {contains: (c) => classes.has('audio-on') && c === 'audio-on',
      toggle: (c, on) => { writes.dot += 1; if (on) classes.add(c); else classes.delete(c); }}};
    const attrs = {};
    cells.push({radio, dot, classes, attrs,
      querySelector: (sel) => (sel.includes('radio') ? radio : sel === '.dot' ? dot : null),
      classList: {contains: (c) => classes.has(c), toggle: (c, on) => { writes.cls += 1; if (on) classes.add(c); else classes.delete(c); }},
      removeAttribute: (k) => { delete attrs[k]; }, setAttribute: (k, v) => { attrs[k] = v; }});
  }
  return {writes, cells, table: {querySelectorAll: () => cells}};
}

const routes = new Map();   // decoder ip -> encoder ip actually carrying its stream
const context = {
  pendingRoutes: new Map(),
  routeCodecCompatible: () => true,
  routeCodecMismatchMessage: () => 'codec mismatch',
  videoMatchesEncoder: (d, e) => routes.get(d.ip) === e.ip,
  audioMatchesEncoder: (d, e) => routes.get(d.ip) === e.ip,
  decoderMultiview: (d) => d.mv || '',
  console,
};
vm.createContext(context);
for (const pattern of [/function unitIsOffline\(/, /function routeOfflineMessage\(/, /function decoderKey\(/,
                       /function matrixStructureKey\(/, /function matrixCellFlags\(/, /function patchMatrixState\(/,
                       /function syncMasterCheckbox\(/, /function bindMatrixDelegation\(/]) {
  vm.runInContext(lift(MATRIX, pattern), context);
}

const ENC = [{ip: '192.0.2.10', host: 'e1'}, {ip: '192.0.2.11', host: 'e2'}, {ip: '192.0.2.12', host: 'e3'}];
const DEC = [{ip: '192.0.2.20', host: 'd1'}, {ip: '192.0.2.21', host: 'd2'}];

(async () => {
  await check('the structure key ignores state and sees membership, identity and Multiview', () => {
    const k = context.matrixStructureKey(ENC, DEC);
    routes.set('192.0.2.20', '192.0.2.11');
    eq(context.matrixStructureKey(ENC, DEC), k, 'a route change is state');
    assert(context.matrixStructureKey(ENC, [...DEC, {ip: '192.0.2.22', host: 'd3'}]) !== k, 'a new decoder is structure');
    assert(context.matrixStructureKey(ENC.slice(1), DEC) !== k, 'a removed encoder is structure');
    assert(context.matrixStructureKey(ENC, [{...DEC[0], host: 'renamed'}, DEC[1]]) !== k, 'a hostname is structure');
    assert(context.matrixStructureKey(ENC, [{...DEC[0], mv: 'Quad'}, DEC[1]]) !== k, 'a MULTIVIEW badge is structure');
    routes.clear();
  });

  await check('patching an unchanged state writes nothing', () => {
    routes.set('192.0.2.20', '192.0.2.11'); routes.set('192.0.2.21', '192.0.2.10');
    const grid = makeGrid(ENC, DEC, (d, e) => context.matrixCellFlags(d, e));
    context.patchMatrixState(grid.table, ENC, DEC);
    eq(grid.writes, {radio: 0, dot: 0, cls: 0}, 'DOM writes for an identical state');
    routes.clear();
  });

  await check('a moved route rewrites only the cells that changed', () => {
    routes.set('192.0.2.20', '192.0.2.11'); routes.set('192.0.2.21', '192.0.2.10');
    const grid = makeGrid(ENC, DEC, (d, e) => context.matrixCellFlags(d, e));
    routes.set('192.0.2.20', '192.0.2.12');            // d1: e2 -> e3, d2 untouched
    context.patchMatrixState(grid.table, ENC, DEC);
    eq(grid.writes.radio, 2, 'two crosspoints change (one off, one on)');
    eq(grid.writes.dot, 2, 'two audio dots change');
    const d1 = grid.cells.slice(0, 3);
    eq(d1.map((c) => c.radio.checked), [false, false, true], 'd1 now ticks e3');
    eq(grid.cells.slice(3).map((c) => c.radio.checked), [true, false, false], 'd2 is as it was');
    routes.clear();
  });

  await check('a codec block appears and clears in place, with its reason', () => {
    const grid = makeGrid(ENC, DEC, (d, e) => context.matrixCellFlags(d, e));
    context.routeCodecCompatible = (e) => e.ip !== '192.0.2.10';
    context.patchMatrixState(grid.table, ENC, DEC);
    assert(grid.cells[0].classes.has('codec-blocked') && grid.cells[0].attrs.title === 'codec mismatch', 'blocked with a title');
    assert(grid.cells[0].radio.disabled === true, 'and disabled');
    context.routeCodecCompatible = () => true;
    context.patchMatrixState(grid.table, ENC, DEC);
    assert(!grid.cells[0].classes.has('codec-blocked') && !('title' in grid.cells[0].attrs), 'cleared again');
  });

  await check('a unit the server has seen not answer is structure, and one nothing has looked at is not', () => {
    const k = context.matrixStructureKey(ENC, DEC);
    assert(context.matrixStructureKey([{...ENC[0], online: false}, ...ENC.slice(1)], DEC) !== k, 'an offline encoder redraws its column');
    assert(context.matrixStructureKey(ENC, [{...DEC[0], online: false}, DEC[1]]) !== k, 'an offline decoder redraws its row');
    eq(context.matrixStructureKey([{...ENC[0], online: true}, ...ENC.slice(1)], DEC), k, 'online is the same as not having been looked at');
  });

  await check('an offline unit blocks its crosspoints in place, says why, and clears again', () => {
    const offline = {...ENC[1], online: false};
    const live = ENC.map((e) => (e.ip === offline.ip ? offline : e));
    const grid = makeGrid(live, DEC, () => ({codecCompatible: true, videoMatch: false, audioMatch: false}));
    context.patchMatrixState(grid.table, live, DEC);
    // the offline encoder is the second column of each of the two decoder rows
    for (const i of [1, 4]) {
      assert(grid.cells[i].classes.has('offline-blocked'), `cell ${i} is blocked`);
      assert(grid.cells[i].radio.disabled === true, `cell ${i} cannot be chosen`);
      assert(/e2 \(source\) is offline/.test(grid.cells[i].attrs.title), 'and says which unit: ' + grid.cells[i].attrs.title);
    }
    for (const i of [0, 2, 3, 5]) assert(!grid.cells[i].classes.has('offline-blocked'), `cell ${i} is untouched`);
    const writes = JSON.stringify(grid.writes);
    context.patchMatrixState(grid.table, live, DEC);
    eq(JSON.stringify(grid.writes), writes, 'a second patch of the same state writes nothing');
    context.patchMatrixState(grid.table, ENC, DEC);
    for (const i of [1, 4]) {
      assert(!grid.cells[i].classes.has('offline-blocked') && !('title' in grid.cells[i].attrs), 'cleared when the unit answers');
      assert(grid.cells[i].radio.disabled === false, 'and can be chosen again');
    }
  });

  await check('an offline display names itself, and both ends are named when neither answers', () => {
    eq(context.routeOfflineMessage({ip: '192.0.2.10', host: 'e1'}, {ip: '192.0.2.20', host: 'd1', online: false}), 'd1 (display) is offline, so this route is not available.');
    eq(context.routeOfflineMessage({ip: '192.0.2.10', online: false}, {ip: '192.0.2.20', host: 'd1', online: false}),
       '192.0.2.10 (source) and d1 (display) are offline, so this route is not available.');
  });

  await check('the click handler refuses an offline end and drops offline decoders from a group selection', () => {
    const body = stripComments(lift(MATRIX, /async function onMatrixCellClick\(/));
    const refuse = body.indexOf('unitIsOffline(encoderUnit) || unitIsOffline(decoderUnit)');
    assert(refuse > 0 && refuse < body.indexOf('enqueueRouteWrite'), 'refused before any request is queued');
    assert(/!unitIsOffline\(targetUnit\)/.test(body), 'a group selection skips units that are not answering');
  });

  await check('delegation is bound once however many renders ask for it', () => {
    const listened = [];
    const table = {dataset: {}, addEventListener: (type) => listened.push(type)};
    for (let i = 0; i < 25; i += 1) context.bindMatrixDelegation(table);
    eq(listened.sort(), ['change', 'click', 'mouseout', 'mouseover'], 'one listener per event, not per render');
  });

  await check('the grid is drawn before any decoder is asked anything', () => {
    const body = stripComments(lift(MATRIX, /async function refresh\(/));
    assert(!/await\s+pollDecoderInputs/.test(body), 'refresh must not await the decoder poll');
    const painted = body.indexOf('applyMatrixState(s)');
    const polled = body.indexOf('pollDecoderInputs(');
    assert(painted > 0 && polled > painted, 'paint first, poll after');
    assert(/\.catch\(/.test(body.slice(polled)), 'the background poll reports through its own catch');
  });

  await check('a decoder sweep is single flight per decoder set', () => {
    const body = stripComments(lift(MATRIX, /async function pollDecoderInputs\(/));
    assert(/_decoderSweeps\.has\(/.test(body) && /_decoderSweeps\.delete\(/.test(body), 'guarded and released');
    assert(/finally\s*\{[\s\S]*_decoderSweeps\.delete/.test(body), 'released on every exit, including failure');
  });

  await check('the page paints a prepared snapshot first, and only a fresh one', () => {
    const tail = stripComments(MATRIX.slice(MATRIX.indexOf('function paintPrepared')));
    assert(/OmniMatrixPrep\.read\(60000\)/.test(tail), 'a snapshot older than a minute is not used');
    assert(tail.indexOf('applyMatrixState(prepared') < tail.indexOf('refresh({loadVideoWallSettings'), 'before the request');
  });

  // ---- the prepared-state module -------------------------------------------------------------
  function loadPrep(fetchImpl, now) {
    const mem = new Map();
    const win = {};
    const ctx = {window: win, sessionStorage: {getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, v), removeItem: (k) => mem.delete(k)},
      fetch: fetchImpl, Date: {now: () => now.t}, JSON, Promise, setTimeout};
    vm.createContext(ctx);
    vm.runInContext(PREP, ctx);
    return {prep: win.OmniMatrixPrep, mem};
  }
  const state = {ok: true, encoders: [], decoders: [], routes: {}};

  await check('a snapshot expires, and storage that throws means no snapshot', () => {
    const now = {t: 1000};
    const {prep} = loadPrep(async () => ({ok: true, json: async () => state}), now);
    prep.store(state);
    assert(prep.read(60000) !== null, 'fresh snapshot is used');
    now.t += 60001;
    assert(prep.read(60000) === null, 'a stale snapshot is not');
    const broken = loadPrep(async () => null, now).prep;
    broken.store(state);            // sessionStorage works here; now make it fail
    const throwing = {window: {}, sessionStorage: {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); }}, fetch: async () => null, Date, JSON, Promise};
    vm.createContext(throwing); vm.runInContext(PREP, throwing);
    assert(throwing.window.OmniMatrixPrep.read(60000) === null, 'throwing storage reads as nothing');
    throwing.window.OmniMatrixPrep.store(state); throwing.window.OmniMatrixPrep.invalidate();
  });

  await check('concurrent events cause one request, and the inventory change replaces the snapshot', async () => {
    let calls = 0;
    const now = {t: 5000};
    const {prep} = loadPrep(async () => { calls += 1; await Promise.resolve(); return {ok: true, json: async () => ({...state, n: calls})}; }, now);
    await Promise.all([prep.inventoryChanged(), prep.prepare(), prep.prepare(), prep.prepare()]);
    eq(calls, 1, 'one fetch for four callers');
    eq(prep.read(60000).n, 1, 'the answer is kept');
    now.t += 500;
    await prep.prepare();
    eq(calls, 1, 'not again inside the minimum interval');
    now.t += 5000;
    await prep.inventoryChanged();
    eq(calls, 2, 'an inventory change after the interval fetches again');
    eq(prep.read(60000).n, 2, 'and replaces what was kept');
  });

  await check('a read already in flight when the inventory changes is discarded, not stored', async () => {
    let calls = 0;
    const releases = [];
    const now = {t: 20000};
    const {prep} = loadPrep(() => new Promise((resolve) => { calls += 1; const n = calls; releases.push(() => resolve({ok: true, json: async () => ({...state, n})})); }), now);
    const first = prep.prepare();                 // starts read 1 (the old world)
    const change = prep.inventoryChanged();       // the inventory moves while it is in flight
    releases[0]();                                // read 1 answers late
    await first;
    assert(prep.read(60000) === null, 'the stale answer was not kept');
    await Promise.resolve(); await Promise.resolve();
    releases[1]();                                // the follow-up read
    await change;
    eq(calls, 2, 'one follow-up read, not a skipped one');
    eq(prep.read(60000).n, 2, 'only the post-change answer is stored');
  });

  await check('an error answer is never kept', async () => {
    const now = {t: 9000};
    const {prep} = loadPrep(async () => ({ok: false, json: async () => ({ok: false})}), now);
    await prep.inventoryChanged();
    assert(prep.read(60000) === null, 'nothing stored from a failed read');
    const bad = loadPrep(async () => { throw new Error('network'); }, now).prep;
    assert((await bad.inventoryChanged()) === false, 'a rejected fetch resolves false, not throws');
  });

  if (failures.length) { console.log(`\nA/V Matrix lifecycle test: ${failures.length} failure(s)`); process.exit(1); }
  console.log('\nA/V Matrix lifecycle test: all checks passed');
})();

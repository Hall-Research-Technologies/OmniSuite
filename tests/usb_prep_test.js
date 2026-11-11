/*
 * The USB Matrix's prepared state: ui/usb-prep.js, and how ui/matrix/usb.js uses it.
 *
 * What this exists to hold:
 *  - a snapshot is painted only while the server's inventory signature still matches it;
 *  - a mismatch, a stale snapshot, a failed signature read and storage that throws all mean
 *    "no snapshot", never a stale matrix;
 *  - an inventory change drops the snapshot and a read already in flight is discarded;
 *  - concurrent events cost one request;
 *  - opening the page never overwrites a live answer that got there first, and never reads a
 *    device itself (the only request it makes is the signature).
 *
 * The module is run for real against a model of just what it touches; the page's own block is
 * lifted from usb.js and driven. A source-text assertion would prove the text.
 *
 * Run: node tests/usb_prep_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const PREP = fs.readFileSync(path.join(ROOT, 'ui', 'usb-prep.js'), 'utf8');
const USB = fs.readFileSync(path.join(ROOT, 'ui', 'matrix', 'usb.js'), 'utf8').replace(/\r\n/g, '\n');

const failures = [];
async function check(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures.push(name); console.log(`  FAIL ${name}: ${err && err.message}`); }
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
function eq(a, b, m) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error((m || 'not equal') + `: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`); }
const tick = () => new Promise((r) => setImmediate(r));

function loadPrep(fetchImpl, now, mem) {
  mem = mem || new Map();
  const win = {};
  const ctx = {window: win, sessionStorage: {getItem: (k) => (mem.has(k) ? mem.get(k) : null), setItem: (k, v) => mem.set(k, v), removeItem: (k) => mem.delete(k)},
    fetch: fetchImpl, Date: {now: () => now.t}, JSON, Promise, setTimeout};
  vm.createContext(ctx);
  vm.runInContext(PREP, ctx);
  return {prep: win.OmniUsbPrep, mem};
}
const state = (sig, extra) => Object.assign({ok: true, inventory_signature: sig, lex: [], rex: [], matrix_lex: [], matrix_rex: []}, extra || {});
const answer = (obj) => async () => ({ok: true, json: async () => obj});

(async () => {
  await check('a snapshot is painted when fresh and the inventory still matches', async () => {
    const now = {t: 1000};
    const {prep} = loadPrep(answer({ok: true, signature: 'abc'}), now);
    prep.store(state('abc', {marker: 7}));
    const got = await prep.readIfCurrent(60000);
    assert(got && got.marker === 7, 'matching signature paints');
  });

  await check('a different signature drops the snapshot, and it is not offered again', async () => {
    const now = {t: 1000};
    const {prep, mem} = loadPrep(answer({ok: true, signature: 'changed'}), now);
    prep.store(state('abc'));
    eq(await prep.readIfCurrent(60000), null, 'mismatch is no snapshot');
    assert(!mem.has(prep.KEY), 'the stale copy is removed, not kept for the next open');
  });

  await check('a stale snapshot is not used, and asks the server nothing', async () => {
    const now = {t: 1000};
    let asked = 0;
    const {prep} = loadPrep(async () => { asked += 1; return {ok: true, json: async () => ({ok: true, signature: 'abc'})}; }, now);
    prep.store(state('abc'));
    now.t += 60001;
    eq(await prep.readIfCurrent(60000), null);
    eq(asked, 0, 'nothing to check, so nothing requested');
  });

  await check('no snapshot costs no request at all', async () => {
    let asked = 0;
    const {prep} = loadPrep(async () => { asked += 1; return null; }, {t: 1});
    eq(await prep.readIfCurrent(60000), null);
    eq(asked, 0);
  });

  await check('a failed or refused signature read means no snapshot', async () => {
    for (const impl of [async () => { throw new Error('offline'); }, async () => ({ok: false, json: async () => ({})}), async () => ({ok: true, json: async () => ({ok: false})}), async () => null]) {
      const {prep} = loadPrep(impl, {t: 1});
      prep.store(state('abc'));
      eq(await prep.readIfCurrent(60000), null);
    }
  });

  await check('only a state with a signature is kept, so an old-format answer is never painted', () => {
    const {prep, mem} = loadPrep(answer({}), {t: 1});
    prep.store({ok: true, lex: []});
    prep.store({ok: false, inventory_signature: 'x'});
    prep.store(null);
    assert(!mem.has(prep.KEY), 'nothing stored');
  });

  await check('storage that throws means no snapshot and no exception', async () => {
    const ctx = {window: {}, sessionStorage: {getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); }, removeItem() { throw new Error('blocked'); }},
      fetch: answer({ok: true, signature: 'abc'}), Date, JSON, Promise};
    vm.createContext(ctx); vm.runInContext(PREP, ctx);
    ctx.window.OmniUsbPrep.store(state('abc'));
    ctx.window.OmniUsbPrep.invalidate();
    eq(await ctx.window.OmniUsbPrep.readIfCurrent(60000), null);
  });

  await check('concurrent events cause one request, and an inventory change replaces the snapshot', async () => {
    let calls = 0;
    const now = {t: 5000};
    const {prep, mem} = loadPrep(async () => { calls += 1; await Promise.resolve(); return {ok: true, json: async () => state('s' + calls, {n: calls})}; }, now);
    await Promise.all([prep.inventoryChanged(), prep.prepare(), prep.prepare(), prep.prepare()]);
    eq(calls, 1, 'one fetch for four callers');
    eq(JSON.parse(mem.get(prep.KEY)).state.n, 1, 'the answer is kept');
    now.t += 500;
    await prep.prepare();
    eq(calls, 1, 'not again inside the minimum interval');
    now.t += 5000;
    await prep.inventoryChanged();
    eq(calls, 2, 'an inventory change after the interval fetches again');
    eq(JSON.parse(mem.get(prep.KEY)).state.n, 2, 'and replaces what was kept');
  });

  await check('a read in flight when the inventory changes is discarded, not stored', async () => {
    let calls = 0;
    const releases = [];
    const now = {t: 20000};
    const {prep, mem} = loadPrep(() => new Promise((resolve) => { calls += 1; const n = calls; releases.push(() => resolve({ok: true, json: async () => state('s' + n, {n})})); }), now);
    const first = prep.prepare();
    prep.inventoryChanged();                 // the inventory moves while read 1 is in flight
    releases[0]();                           // read 1 answers late
    await first;
    assert(!mem.has(prep.KEY), 'the stale answer was not kept');
    await tick(); await tick();
    assert(releases.length === 2, 'a second read follows rather than being skipped');
    releases[1]();
    await tick(); await tick(); await tick();
    eq(JSON.parse(mem.get(prep.KEY)).state.n, 2, 'only the answer from after the change is kept');
  });

  // ---- the page's own block, lifted from usb.js and driven --------------------------------------
  function liftPaintPrepared() {
    const at = USB.indexOf('(function paintPrepared()');
    assert(at > 0, 'usb.js has the prepared-paint block');
    const end = USB.indexOf('\n})();', at) + '\n})();'.length;
    return USB.slice(at, end);
  }
  function runPaint({prepared, lastState, applied}) {
    const log = {adopted: [], rendered: [], overlayHidden: false, signatureAsked: 0};
    const overlay = {classList: {add: (c) => { if (c === 'hidden') log.overlayHidden = true; }}};
    const ctx = {
      window: {OmniUsbPrep: {readIfCurrent: async () => { log.signatureAsked += 1; return prepared; }}},
      document: {getElementById: (id) => (id === 'usb_loading_overlay' ? overlay : null)},
      lastState, usbStateApplied: applied,
      adoptUsbState: (s) => log.adopted.push(s),
      requestUsbRender: (force) => log.rendered.push(force),
      Promise,
    };
    vm.createContext(ctx);
    vm.runInContext(liftPaintPrepared(), ctx);
    return {log, ctx};
  }

  await check('opening the page paints the prepared matrix and hides the loading overlay', async () => {
    const s = state('abc', {marker: 1});
    const {log} = runPaint({prepared: s, lastState: null, applied: 0});
    await tick(); await tick();
    eq(log.adopted.length, 1); assert(log.adopted[0] === s, 'the prepared state is adopted');
    eq(log.rendered, [true], 'rendered unconditionally');
    assert(log.overlayHidden, 'the loading overlay is hidden');
  });

  await check('a live answer that got there first is never overwritten', async () => {
    for (const [lastState, applied] of [[{lex: []}, 0], [null, 3]]) {
      const {log} = runPaint({prepared: state('abc'), lastState, applied});
      await tick(); await tick();
      eq(log.adopted.length, 0, 'not adopted'); eq(log.rendered.length, 0, 'not rendered');
    }
  });

  await check('nothing prepared means nothing painted', async () => {
    const {log} = runPaint({prepared: null, lastState: null, applied: 0});
    await tick(); await tick();
    eq(log.adopted.length, 0); eq(log.rendered.length, 0);
  });

  await check('the page reads the signature, never a device, before it confirms', () => {
    const code = USB.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    const tail = code.slice(code.indexOf('(function paintPrepared()'));
    assert(tail.indexOf('readIfCurrent(60000)') > 0, 'fresh snapshots only');
    assert(tail.indexOf('readIfCurrent') < tail.indexOf('\nrefresh();'), 'prepared paint is armed before the confirming request');
    assert(/OmniUsbPrep\.store\(s\)/.test(code.slice(code.indexOf('async function refresh'), code.indexOf('function standaloneRow'))), 'a live answer is kept for the next open');
  });

  await check('usb.html loads the module, and Device Info raises the inventory change at every site', () => {
    const html = fs.readFileSync(path.join(ROOT, 'ui', 'matrix', 'usb.html'), 'utf8');
    const first = html.indexOf('/ui/usb-prep.js'), page = html.indexOf('/ui/matrix/usb.js');
    assert(first > 0 && first < page, 'loaded before the page script');
    const index = fs.readFileSync(path.join(ROOT, 'ui', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
    assert(index.includes('/ui/usb-prep.js'), 'Device Info loads it');
    const matrixCalls = (index.match(/OmniMatrixPrep\.inventoryChanged\(\)/g) || []).length;
    const usbCalls = (index.match(/OmniUsbPrep\.inventoryChanged\(\)/g) || []).length;
    assert(usbCalls >= matrixCalls + 1, 'every site that rebuilds the A/V Matrix rebuilds the USB Matrix, plus the late standalone arrivals');
  });

  console.log(failures.length ? `\n${failures.length} FAILED` : '\nall passed');
  process.exit(failures.length ? 1 : 0);
})();

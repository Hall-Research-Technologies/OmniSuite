/*
 * Device Info asks for each encoder's thumbnail status when it draws the table. Drawn for dozens of
 * encoders that do not answer, those reads filled the browser's six connections to the server for over a
 * minute, so scripts, Settings and the next page all waited behind them (measured: static assets took 12 s,
 * Settings never opened inside 30 s). The reads are now queued: three at a time, and an encoder asked about
 * a moment ago is not asked again on the next redraw.
 *
 * The functions are lifted out of ui/index.html and run for real, with a fetch that records concurrency.
 *
 * Run: node tests/thumbnail_queue_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const PAGE = fs.readFileSync(path.join(__dirname, '..', 'ui', 'index.html'), 'utf8').replace(/\r\n/g, '\n');
let failures = 0;
const pending = [];
function check(name, fn) { pending.push([name, fn]); }
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }

function lift() {
  const at = PAGE.indexOf('const THUMB_STATUS_CONCURRENCY');
  if (at < 0) throw new Error('queue not found');
  const stop = PAGE.indexOf('// Track the currently loading preview', at);
  if (stop < 0) throw new Error('end of the status functions not found');
  return PAGE.slice(at, stop);
}

function world() {
  const state = {live: 0, max: 0, asked: [], resolvers: [], checkboxes: {}};
  const ctx = {
    console, Date, Map, Promise,
    document: {querySelector: (sel) => { const m = /data-ip="([^"]+)"/.exec(sel); return m ? (state.checkboxes[m[1]] = state.checkboxes[m[1]] || {checked: false}) : null; }},
    fetch: (url, opts) => {
      const ip = JSON.parse(opts.body).ip;
      state.asked.push(ip); state.live += 1; state.max = Math.max(state.max, state.live);
      return new Promise((resolve) => state.resolvers.push(() => {
        state.live -= 1;
        resolve({ok: true, json: async () => ({ok: true, enable: true})});
      }));
    },
  };
  vm.createContext(ctx);
  vm.runInContext(lift(), ctx);
  return {ctx, state};
}
const tick = () => new Promise((r) => setImmediate(r));
async function settleAll(state) { for (let i = 0; i < 200 && state.resolvers.length; i += 1) { state.resolvers.shift()(); await tick(); await tick(); } }

check('sixty encoders are never more than three reads at once', async () => {
  const {ctx, state} = world();
  for (let i = 0; i < 60; i += 1) ctx.fetchThumbnailStatus('203.0.113.' + (i + 1));
  await tick();
  assert(state.max <= 3, 'in flight reached ' + state.max);
  assert(state.live === 3, 'the queue did not fill to its limit: ' + state.live);
  await settleAll(state);
  assert(state.asked.length === 60, 'only ' + state.asked.length + ' of 60 encoders were eventually read');
  assert(state.max <= 3, 'in flight reached ' + state.max + ' while draining');
});

check('an encoder asked about a moment ago is not asked again on a redraw', async () => {
  const {ctx, state} = world();
  ctx.fetchThumbnailStatus('203.0.113.9');
  ctx.fetchThumbnailStatus('203.0.113.9');
  await settleAll(state);
  ctx.fetchThumbnailStatus('203.0.113.9');
  await settleAll(state);
  assert(state.asked.length === 1, 'asked ' + state.asked.length + ' times');
});

check('the answer still reaches the checkbox', async () => {
  const {ctx, state} = world();
  ctx.fetchThumbnailStatus('203.0.113.7');
  await settleAll(state);
  assert(state.checkboxes['203.0.113.7'] && state.checkboxes['203.0.113.7'].checked === true, 'checkbox not set');
});

check('a failed read does not stop the queue', async () => {
  const {ctx, state} = world();
  ctx.fetch = () => { state.asked.push('x'); return Promise.reject(new Error('down')); };
  const silence = ctx.console; ctx.console = {...silence, error: () => {}};
  for (let i = 0; i < 8; i += 1) ctx.fetchThumbnailStatus('203.0.113.' + (i + 20));
  for (let i = 0; i < 20; i += 1) await tick();
  assert(state.asked.length === 8, 'queue stalled after a failure: ' + state.asked.length);
});

(async () => {
  for (const [name, fn] of pending) {
    try { await fn(); console.log('  ok   ' + name); }
    catch (err) { failures += 1; console.log('  FAIL ' + name + ': ' + err.message); }
  }
  console.log(failures ? failures + ' failed' : pending.length + ' scenarios passed');
  process.exit(failures ? 1 : 0);
})();

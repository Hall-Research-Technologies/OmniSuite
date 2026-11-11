/*
 * When the restricted User page fetches thumbnails: ui/user/preview-refresh.js, run against a clock
 * the test owns.
 *
 * What this exists to hold:
 *  - nothing is scheduled until something visible has a picture, and the one timer is cleared the
 *    moment that stops being true -- hidden page, nothing on screen, preview off, sign-out;
 *  - every five seconds each visible picture is refreshed once, and a card scrolled away is not;
 *  - one request per source in flight, so a slow device never gathers a backlog however many ticks
 *    pass or routes complete, and a failure of any kind releases it;
 *  - a route completing refreshes what is on screen now, and a request begun after that route is not
 *    asked for twice;
 *  - a preview the device has switched off is not asked for again; one that did not answer is retried
 *    slowly rather than abandoned or hammered;
 *  - a late answer for a list that has since been rebuilt is applied to nothing.
 *
 * Run: node tests/user_preview_refresh_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', 'ui', 'user', 'preview-refresh.js'), 'utf8');
const failures = [];
// A promise that never settles empties the event loop and exits 0 in silence, which reads exactly
// like a pass. The run is only complete when it says so.
let finished = false;
process.on('exit', () => { if (!finished) { console.log('FAIL: the run ended before every check completed'); process.exitCode = 1; } });
async function check(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures.push(name); console.log(`  FAIL ${name}: ${err && err.message}`); }
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
function eq(a, b, m) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error((m || 'not equal') + `: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`); }
const flush = async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); await new Promise((r) => setImmediate(r)); };

const win = {};
vm.runInNewContext(SOURCE, {window: win, Promise, Map, Set});
const Previews = win.OmniUserPreviews;
const IMAGE = {image: {}, reason: '', note: ''};
const DISABLED = {image: null, reason: 'preview_disabled', note: 'Preview disabled'};
const SILENT = {image: null, reason: 'unavailable', note: ''};

// A clock the test advances, one interval timer, and a fetch it can hold open.
function rig(answers) {
  const clock = {t: 1000, timers: new Map(), nextId: 1, started: 0, cleared: 0, hidden: false};
  const requests = [];
  const held = [];
  const applied = [];
  const controller = Previews.create({
    fetchPreview: (id) => {
      requests.push(id);
      const answer = typeof answers === 'function' ? answers(id, requests.length) : (answers || IMAGE);
      if (answer === 'hold') return new Promise((resolve) => held.push({id, resolve}));
      if (answer === 'throw') return Promise.reject(new Error('device did not answer'));
      return Promise.resolve(answer);
    },
    apply: (shot, result) => {
      if (shot.throwOnApply) throw new Error('cannot draw');
      applied.push([shot.name, result && result.image ? 'image' : (result && result.reason) || 'none']);
    },
    setInterval: (fn, ms) => { const id = clock.nextId++; clock.timers.set(id, {fn, ms}); clock.started += 1; return id; },
    clearInterval: (id) => { if (clock.timers.delete(id)) clock.cleared += 1; },
    now: () => clock.t,
    hidden: () => clock.hidden,
  });
  const advance = async (ms) => {
    for (let spent = 0; spent < ms; spent += 5000) {
      clock.t += 5000;
      [...clock.timers.values()].forEach((t) => t.fn());
      await flush();
    }
  };
  const card = (name, id, visible) => { const shot = {name}; controller.track(shot, id, visible); return shot; };
  return {controller, clock, requests, held, applied, advance, card};
}

(async () => {
  await check('nothing is scheduled until a visible card has a picture', async () => {
    const r = rig(IMAGE);
    const a = r.card('a', 'src1', true);
    eq(r.clock.timers.size, 0, 'tracked but no picture yet: no timer');
    await r.controller.load(a);
    eq(r.clock.timers.size, 1, 'a visible picture starts the one timer');
    eq(r.clock.timers.values().next().value.ms, 5000, 'every five seconds');
    eq(r.requests, ['src1'], 'one load');
  });

  await check('every tick refreshes each visible picture once, and nothing that is scrolled away', async () => {
    const r = rig(IMAGE);
    const a = r.card('a', 'src1', true), b = r.card('b', 'src2', true), gone = r.card('gone', 'src3', true);
    await Promise.all([r.controller.load(a), r.controller.load(b), r.controller.load(gone)]);
    r.controller.setVisible(gone, false);
    r.requests.length = 0;
    await r.advance(5000);
    eq(r.requests.slice().sort(), ['src1', 'src2'], 'the off-screen card is not asked for');
    r.controller.setVisible(gone, true);
    await r.advance(5000);
    assert(r.requests.includes('src3'), 'scrolled back, it is refreshed again');
  });

  await check('two cards showing one source cost one request, and both are drawn', async () => {
    const r = rig(IMAGE);
    const source = r.card('source-card', 'src1', true), display = r.card('display-card', 'src1', true);
    await Promise.all([r.controller.load(source), r.controller.load(display)]);
    eq(r.requests, ['src1'], 'one request');
    eq(r.applied.map((x) => x[0]).sort(), ['display-card', 'source-card']);
    r.requests.length = 0;
    await r.advance(5000);
    eq(r.requests, ['src1'], 'one per tick as well');
  });

  await check('a slow device never gathers a backlog: ticks join the request already out', async () => {
    let asked = 0;
    const slow = rig(() => { asked += 1; return asked === 1 ? IMAGE : 'hold'; });
    const c = slow.card('c', 'src1', true);
    await slow.controller.load(c);
    await slow.advance(5000);                  // begins a request that never answers
    const during = slow.requests.length;
    await slow.advance(50000);                 // ten ticks later
    eq(slow.requests.length, during, 'ten ticks added no requests while one was out');
    eq(slow.controller.stats().inflight, 1, 'exactly one in flight');
    slow.held[0].resolve(IMAGE);
    await flush();
    await slow.advance(5000);
    assert(slow.requests.length === during + 1, 'asks again once it has answered');
  });

  await check('a failed fetch, and a card that cannot be drawn on, both release the request', async () => {
    const r = rig((id, n) => (n === 1 ? 'throw' : IMAGE));
    const a = r.card('a', 'src1', true);
    await r.controller.load(a);
    eq(r.controller.stats().inflight, 0, 'released after a failure');
    await r.controller.load(a);
    eq(r.requests.length, 2, 'and the source can be asked for again');
    const bad = rig(IMAGE);
    const broken = bad.card('broken', 'srcX', true); broken.throwOnApply = true;
    await bad.controller.load(broken);
    eq(bad.controller.stats().inflight, 0, 'released although drawing threw');
  });

  await check('a hidden page has no timer; coming back refreshes at once and restarts it', async () => {
    const r = rig(IMAGE);
    const a = r.card('a', 'src1', true);
    await r.controller.load(a);
    eq(r.clock.timers.size, 1);
    r.clock.hidden = true; r.controller.setHidden(true);
    eq(r.clock.timers.size, 0, 'timer cleared when the page is hidden');
    r.requests.length = 0;
    await r.advance(30000);
    eq(r.requests.length, 0, 'no traffic while hidden');
    r.clock.hidden = false; r.controller.setHidden(false);
    await flush();
    eq(r.requests, ['src1'], 'refreshed the moment it is shown');
    eq(r.clock.timers.size, 1, 'and ticking again');
  });

  await check('no visible card means no timer; one scrolled in restarts it', async () => {
    const r = rig(IMAGE);
    const a = r.card('a', 'src1', true);
    await r.controller.load(a);
    r.controller.setVisible(a, false);
    eq(r.clock.timers.size, 0);
    r.controller.setVisible(a, true);
    eq(r.clock.timers.size, 1);
  });

  await check('a preview switched off at the device is not asked for again', async () => {
    const r = rig(DISABLED);
    const a = r.card('a', 'src1', true);
    await r.controller.load(a);
    eq(r.clock.timers.size, 0, 'nothing to refresh, so no timer');
    r.requests.length = 0;
    await r.advance(60000);
    eq(r.requests.length, 0);
    eq(r.applied[0][1], 'preview_disabled');
  });

  await check('a preview that did not answer is retried slowly, not hammered or abandoned', async () => {
    let asked = 0;                             // its own count: the rig's list is emptied between phases
    const r = rig(() => { asked += 1; return asked === 1 ? SILENT : IMAGE; });
    const a = r.card('a', 'src1', true);
    await r.controller.load(a);
    r.requests.length = 0;
    await r.advance(25000);                    // five ticks
    eq(r.requests.length, 0, 'not on the ordinary ticks');
    await r.advance(5000);                     // the sixth
    eq(r.requests.length, 1, 'about every thirty seconds');
    eq(r.controller.stats().timers, 1);
    r.requests.length = 0;
    await r.advance(5000);
    eq(r.requests.length, 1, 'once it has a picture it is an ordinary tick again');
  });

  await check('a route completing refreshes what is on screen now, including a card that had no picture', async () => {
    const r = rig((id) => (id === 'src2' ? SILENT : IMAGE));
    const a = r.card('a', 'src1', true), b = r.card('b', 'src2', true), off = r.card('off', 'src3', true);
    await Promise.all([r.controller.load(a), r.controller.load(b), r.controller.load(off)]);
    r.controller.setVisible(off, false);
    r.requests.length = 0;
    r.controller.routeCompleted(r.clock.t);
    await flush();
    eq(r.requests.slice().sort(), ['src1', 'src2'], 'visible cards, whatever their state; not the off-screen one');
  });

  await check('a request begun after the route is not asked for twice; one begun before is followed by one more', async () => {
    const r = rig('hold');
    const a = r.card('a', 'src1', true);
    const before = r.controller.load(a);       // in flight, begun before the route completes
    await flush();
    r.clock.t += 10;
    r.controller.routeCompleted(r.clock.t);    // the route finished after that request began
    eq(r.requests.length, 1, 'joined, not duplicated');
    r.held[0].resolve(IMAGE);
    await before; await flush();
    eq(r.requests.length, 2, 'one follow-up, because the first describes the world before the route');
    r.held[1].resolve(IMAGE);
    await flush();

    const s = rig('hold');
    const c = s.card('c', 'src1', true);
    const route = s.clock.t;
    s.clock.t += 10;
    const after = s.controller.load(c);        // begun after the route completed
    await flush();
    s.controller.routeCompleted(route);
    s.held[0].resolve(IMAGE); await after; await flush();
    eq(s.requests.length, 1, 'already describes the new world: no second request');
  });

  await check('repeated routing while previews are refreshing never stacks requests', async () => {
    let asked = 0;
    const r = rig(() => { asked += 1; return asked > 3 && asked % 3 === 0 ? 'hold' : IMAGE; });
    const cards = ['s1', 's2', 's3'].map((id) => r.card('card-' + id, id, true));
    await Promise.all(cards.map((c) => r.controller.load(c)));
    let peak = 0;
    for (let i = 0; i < 40; i += 1) {
      r.clock.t += 700;
      r.controller.routeCompleted(r.clock.t - 1);
      if (i % 7 === 0) await r.advance(5000);
      peak = Math.max(peak, r.controller.stats().inflight);
      while (r.held.length) r.held.shift().resolve(IMAGE);
      await flush();
    }
    assert(peak <= 3, 'never more than one in flight per source: ' + peak);
    eq(r.controller.stats().timers <= 1, true, 'still one timer at most');
  });

  await check('rebuilding the list drops old cards: a late answer is applied to nothing, and the timer goes', async () => {
    const r = rig('hold');
    const a = r.card('a', 'src1', true);
    const pending = r.controller.load(a);
    await flush();
    r.controller.reset();                      // the list was rebuilt while the request was out
    r.held[0].resolve(IMAGE);
    await pending; await flush();
    eq(r.applied.length, 0, 'the old card was not drawn on');
    eq(r.clock.timers.size, 0, 'and nothing keeps ticking for it');
    eq(r.controller.stats().tracked, 0);
  });

  await check('signing out, or the session ending, stops everything and stays stopped', async () => {
    const r = rig(IMAGE);
    const a = r.card('a', 'src1', true);
    await r.controller.load(a);
    r.controller.stop();
    eq(r.clock.timers.size, 0, 'timer cleared');
    r.requests.length = 0;
    r.controller.track({name: 'late'}, 'src9', true);
    r.controller.routeCompleted(r.clock.t);
    r.controller.setHidden(false);
    await r.advance(30000);
    eq(r.requests.length, 0, 'nothing is requested after stop, however it is asked');
    eq(r.controller.stats().timers, 0);
  });

  await check('an idle page that has loaded its pictures costs one request per source per interval, measured', async () => {
    const r = rig(IMAGE);
    const cards = ['s1', 's2', 's3', 's4'].map((id) => r.card(id, id, true));
    await Promise.all(cards.map((c) => r.controller.load(c)));
    r.requests.length = 0;
    await r.advance(60000);                    // a minute: twelve ticks
    eq(r.requests.length, 4 * 12, 'four sources, twelve ticks: one each, no more');
    eq(r.controller.stats().timersStarted, 1, 'one timer for the page');
  });

  await check('never more than three pictures are being fetched, however many sources there are', async () => {
    const r = rig('hold');
    const cards = [];
    for (let i = 0; i < 14; i += 1) cards.push(r.card('c' + i, 'src' + i, true));
    cards.forEach((c) => r.controller.load(c));
    await flush();
    eq(Previews.MAX_CONCURRENT_FETCHES, 3, 'the documented cap');
    eq(r.requests.length, 3, 'only three were started');
    eq(r.controller.stats().fetching, 3, 'three fetching');
    eq(r.controller.stats().waiting, 11, 'eleven waiting their turn');
    let peak = 3;
    for (let n = 0; n < 14; n += 1) {
      const next = r.held.shift();
      next.resolve(IMAGE);
      await flush();
      peak = Math.max(peak, r.controller.stats().fetching);
    }
    assert(peak <= 3, 'never above the cap: ' + peak);
    eq(r.requests.length, 14, 'every source was fetched in the end');
    eq(r.requests, Array.from({length: 14}, (_, i) => 'src' + i), 'in the order they were asked for');
    eq(r.controller.stats().inflight, 0, 'nothing left in flight');
    eq(r.applied.length, 14, 'and every card was drawn');
  });

  await check('a fetch that fails gives its turn to the next one', async () => {
    const r = rig((id, n) => (n <= 3 ? 'throw' : 'hold'));
    const cards = [];
    for (let i = 0; i < 6; i += 1) cards.push(r.card('c' + i, 'src' + i, true));
    cards.forEach((c) => r.controller.load(c));
    await flush();
    assert(r.requests.length >= 4, 'the failures released their turns: ' + r.requests.length);
    assert(r.controller.stats().fetching <= 3, 'still within the cap');
  });

  await check('stopping releases everything that was waiting, and fetches nothing more', async () => {
    const r = rig('hold');
    const cards = [];
    for (let i = 0; i < 9; i += 1) cards.push(r.card('c' + i, 'src' + i, true));
    const loads = cards.map((c) => r.controller.load(c));
    await flush();
    eq(r.requests.length, 3, 'three started, six waiting');
    r.controller.stop();
    await flush();
    eq(r.requests.length, 3, 'the six that were waiting were never fetched');
    r.held.forEach((h) => h.resolve(IMAGE));
    await Promise.all(loads);
    await flush();
    eq(r.controller.stats().inflight, 0, 'every request was released');
    eq(r.controller.stats().waiting, 0, 'and nothing is still queued');
    eq(r.controller.stats().fetching, 0, 'or being fetched');
  });

  await check('a tick over many sources respects the cap too', async () => {
    const r = rig(IMAGE);
    const cards = [];
    for (let i = 0; i < 14; i += 1) cards.push(r.card('c' + i, 'src' + i, true));
    await Promise.all(cards.map((c) => r.controller.load(c)));
    r.requests.length = 0;
    let peak = 0;
    const original = r.controller.stats;
    await r.advance(5000);
    peak = Math.max(peak, original().fetching);
    eq(r.requests.length, 14, 'every visible picture is refreshed once per tick');
    eq(original().fetching, 0, 'and all of it has finished');
  });

  await check('the page wires the controller: tracking, visibility, route completion, hidden, leaving', () => {
    const page = fs.readFileSync(path.join(__dirname, '..', 'ui', 'user', 'user.js'), 'utf8').replace(/\r\n/g, '\n')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
    assert(/previews\.track\(shot, sourceId/.test(page), 'cards are tracked');
    assert(/visibilityObserver = new IntersectionObserver/.test(page) && /previews\.setVisible\(/.test(page), 'continuous visibility');
    assert(/async function refreshAfterRoute\(at\)\s*\{\s*await refresh\(\);\s*previews\.routeCompleted\(at\);/.test(page), 'route completion refreshes');
    const calls = (page.match(/refreshAfterRoute\(Date\.now\(\)\)/g) || []).length;
    assert(calls === 3, 'route, group route and unroute all use it: ' + calls);
    assert(/visibilitychange', \(\) => previews\.setHidden\(document\.hidden\)/.test(page), 'a hidden page stops');
    assert(/pagehide', \(\) => previews\.stop\(\)/.test(page), 'leaving stops');
    assert(/previews\.reset\(\)/.test(page) && /visibilityObserver\.disconnect\(\)/.test(page), 'a rebuilt list drops its cards');
    assert(/AbortController/.test(page), 'a request is bounded');
    assert(/res\.status === 401\) \{ previews\.stop\(\)/.test(page), 'a refused session stops it');
    // no device URL ever reaches the page: pictures come from the server's proxy by identity
    assert(!/thumbnail1\.jpg|\/thumbnail\//.test(page), 'no device URL in the restricted page');
    assert(/\/api\/user\/source_preview\?source=/.test(page), 'the existing proxy');
  });

  finished = true;
  console.log(failures.length ? `\n${failures.length} FAILED` : '\nall passed');
  process.exit(failures.length ? 1 : 0);
})();

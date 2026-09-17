/*
 * Frontend test for the A/V Matrix's awareness of an active Multiview.
 *
 * A decoder that is compositing a Multiview is not showing any single encoder,
 * but its ip_input1 still holds whatever it last watched -- so without this the
 * matrix draws a video crosspoint for a picture nobody is looking at. Routing a
 * conventional source to such a decoder is still allowed, and warns first.
 *
 * This loads ui/matrix/matrix.js into a tolerant DOM stub and drives the real
 * functions. The stub answers anything the page asks of the DOM, because the
 * page does a great deal at load time that is not what is under test here; the
 * few things that ARE under test -- the rendered row, storage, and the dialog --
 * are modelled properly.
 *
 * Run: node tests/matrix_multiview_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const SOURCE = path.join(ROOT, 'ui', 'matrix', 'matrix.js');

const failures = [];
async function check(name, fn) {
  try { await fn(); console.log(`  ok   ${name}`); }
  catch (err) {
    failures.push(`${name}: ${err && err.message}`);
    console.log(`  FAIL ${name}: ${err && err.message}`);
  }
}
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}

// ---- a DOM that answers anything -----------------------------------------
//
// Every property read returns another tolerant node, and every call returns one
// too, so matrix.js can run its start-up without this file having to model the
// whole page. The things that ARE under test are modelled properly below.
function tolerant(name) {
  const target = function () {};
  target._name = name;
  return new Proxy(target, {
    get(obj, key) {
      if (key === Symbol.toPrimitive || key === 'toString') return () => '';
      if (key === Symbol.iterator) return [][Symbol.iterator].bind([]);
      if (key === 'then') return undefined;               // not a promise
      if (key === 'length') return 0;
      if (key === 'classList') {
        return {add() {}, remove() {}, toggle() {}, contains() { return false; }};
      }
      if (key === 'style' || key === 'dataset') return {};
      if (key === 'value' || key === 'textContent' || key === 'innerHTML'
          || key === 'innerText') {
        const stored = obj['_' + String(key)];
        return stored === undefined ? '' : stored;
      }
      if (key === 'checked' || key === 'disabled' || key === 'hidden') {
        return !!obj['_' + String(key)];
      }
      if (!(key in obj)) obj[key] = tolerant(`${name}.${String(key)}`);
      return obj[key];
    },
    set(obj, key, value) {
      obj['_' + String(key)] = value;
      obj[key] = value;
      return true;
    },
    apply() { return tolerant(`${name}()`); },
    has() { return true; },
  });
}

function makeContext() {
  const registry = new Map();
  const matrixTable = {
    _html: '',
    set innerHTML(value) { this._html = String(value); },
    get innerHTML() { return this._html; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    addEventListener() {},
    classList: {add() {}, remove() {}, toggle() {}, contains() { return false; }},
    style: {},
    closest() { return null; },
  };
  registry.set('#matrix', matrixTable);

  const document = new Proxy({
    addEventListener() {}, removeEventListener() {},
    createElement(tag) { return tolerant(`<${tag}>`); },
    querySelectorAll() { return []; },
    body: tolerant('body'),
    documentElement: tolerant('html'),
    readyState: 'complete',
  }, {
    get(obj, key) {
      if (key === 'querySelector') {
        // An element this test has not modelled is simply absent, which is what
        // the page sees on a matrix page that has not finished loading.
        return (selector) => (registry.has(selector) ? registry.get(selector) : null);
      }
      if (!(key in obj)) obj[key] = tolerant(`document.${String(key)}`);
      return obj[key];
    },
  });

  function makeStorage() {
    const map = new Map();
    return {
      getItem: (k) => (map.has(k) ? map.get(k) : null),
      setItem: (k, v) => { map.set(k, String(v)); },
      removeItem: (k) => { map.delete(k); },
      clear: () => map.clear(),
      _map: map,
    };
  }

  const context = {
    document,
    localStorage: makeStorage(),
    sessionStorage: makeStorage(),
    console: {log() {}, warn() {}, error() {}, info() {}, debug() {}},
    setTimeout: () => 0, clearTimeout: () => {},
    setInterval: () => 0, clearInterval: () => {},
    requestAnimationFrame: () => 0,
    fetch: async () => ({ok: true, status: 200, json: async () => ({}),
                         text: async () => ''}),
    CSS: {escape: (s) => String(s)},
    location: {href: 'http://127.0.0.1/matrix', pathname: '/matrix',
               search: '', origin: 'http://127.0.0.1'},
    navigator: {clipboard: {writeText: async () => {}}},
    Image: function () { return {}; },
    AbortController: function () { return {signal: {}, abort() {}}; },
    IntersectionObserver: function () { return {observe() {}, disconnect() {}}; },
    ResizeObserver: function () { return {observe() {}, disconnect() {}}; },
    MutationObserver: function () { return {observe() {}, disconnect() {}}; },
    EventSource: function () { return {addEventListener() {}, close() {}}; },
    URL, URLSearchParams,
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    alert() { throw new Error('the page used a native alert'); },
    confirm() { throw new Error('the page used a native confirm'); },
    prompt() { throw new Error('the page used a native prompt'); },
  };
  context.window = context;
  context.globalThis = context;
  context.matrixTable = matrixTable;
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), context, {filename: SOURCE});
  return context;
}

// ---- fixtures -------------------------------------------------------------
const ENCODER = {ip: '192.0.2.20', host: 'enc-1', v_mcast: '233.252.0.11',
                 v_port: 1000, a_mcast: '233.252.0.12', a_port: 1000};
const PLAIN = {ip: '192.0.2.10', host: 'dec-plain',
               ip1_addr: '233.252.0.11', ip1_port: 1000,
               multiview_active: false, multiview_name: null};
// Deliberately still holding the same stale subscription as the plain decoder:
// that is exactly the state which used to draw a crosspoint that was untrue.
const COMPOSING = Object.assign({}, PLAIN, {
  ip: '192.0.2.11', host: 'dec-mv',
  multiview_active: true, multiview_name: 'multiviewLive'});

function withConfirm(context, answer) {
  const calls = [];
  context.omniConfirm = async (options) => { calls.push(options); return answer; };
  return calls;
}

async function main() {
  console.log('A/V Matrix Multiview awareness');
  const ctx = makeContext();

  // ---- the rule -----------------------------------------------------------
  await check('a decoder compositing a Multiview is named as such', () => {
    assert(ctx.decoderMultiview(COMPOSING) === 'multiviewLive',
           `got ${ctx.decoderMultiview(COMPOSING)}`);
  });

  await check('an ordinary decoder is not', () => {
    assert(ctx.decoderMultiview(PLAIN) === null);
    assert(ctx.decoderMultiview(undefined) === null);
    assert(ctx.decoderMultiview({}) === null);
  });

  await check('an active Multiview with no name still reads as active', () => {
    assert(ctx.decoderMultiview({multiview_active: true}) === 'Multiview');
  });

  await check('no video crosspoint is drawn for a composed picture', () => {
    assert(ctx.videoMatchesEncoder(PLAIN, ENCODER) === true,
           'the ordinary decoder lost its crosspoint');
    assert(ctx.videoMatchesEncoder(COMPOSING, ENCODER) === false,
           'a crosspoint was drawn for a decoder that is compositing');
  });

  // ---- what the row says ---------------------------------------------------
  await check('the row is marked, and explains itself on hover', () => {
    ctx.render({encoders: [ENCODER], decoders: [PLAIN, COMPOSING]});
    const html = ctx.matrixTable.innerHTML;
    assert(html.includes('dec-multiview'), 'the row is not marked');
    assert(html.includes('>MULTIVIEW<'), 'the row does not say MULTIVIEW');
    // The explanation has to be on the row head itself. Matching anywhere in
    // the row passes on the badge alone, which is how removing the row title
    // survived a mutation of this very check.
    const head = (html.match(/<th class="row-head"[^>]*>/g) || [])
      .find(tag => tag.includes('title='));
    assert(head, 'the row head carries no hover explanation');
    assert(head.includes('multiviewLive'), 'the hover does not name the Multiview');
    assert(/Routing a normal A\/V source/.test(head),
           'the hover does not say what a route will do');
    assert(/saved Multiview is kept/i.test(head),
           'the hover does not say the saved Multiview survives');
    assert(/<span class="mv-badge" title="[^"]*multiviewLive/.test(html),
           'the badge carries no hover explanation of its own');
    const plainRows = html.split('<tr').filter(r => r.includes('dec-plain'));
    assert(plainRows.length === 1 && !plainRows[0].includes('MULTIVIEW'),
           'the ordinary decoder was marked too');
  });

  await check('only the compositing decoder loses its checked crosspoint', () => {
    ctx.render({encoders: [ENCODER], decoders: [PLAIN, COMPOSING]});
    const rows = ctx.matrixTable.innerHTML.split('<tr').slice(1);
    const plainRow = rows.find(r => r.includes('dec-plain'));
    const mvRow = rows.find(r => r.includes('dec-mv'));
    assert(/<input type="radio"[^>]*checked/.test(plainRow),
           'the ordinary decoder lost its route indicator');
    assert(!/<input type="radio"[^>]*checked/.test(mvRow),
           'the compositing decoder was shown as routed to an encoder');
  });

  // ---- the warning ---------------------------------------------------------
  await check('the first route is warned about, and Continue proceeds', async () => {
    const c = makeContext();
    const calls = withConfirm(c, {ok: true, suppress: false});
    const proceed = await c.confirmMultiviewExit(COMPOSING, 'multiviewLive');
    assert(proceed === true, 'Continue did not allow the route');
    assert(calls.length === 1, `omniConfirm called ${calls.length} times`);
    assert(/Multiview is active/.test(calls[0].title), calls[0].title);
    assert(/exit Multiview/i.test(calls[0].message), calls[0].message);
    assert(/kept/i.test(calls[0].message), 'the warning does not say it is kept');
    assert(calls[0].suppressLabel, 'no "do not show again" was offered');
  });

  await check('Cancel stops the route', async () => {
    const c = makeContext();
    withConfirm(c, {ok: false, suppress: false});
    assert((await c.confirmMultiviewExit(COMPOSING, 'multiviewLive')) === false);
  });

  await check('Cancel remembers nothing, so the next route asks again', async () => {
    const c = makeContext();
    const calls = withConfirm(c, {ok: false, suppress: false});
    await c.confirmMultiviewExit(COMPOSING, 'multiviewLive');
    assert(c.sessionStorage.getItem('matrix_multiview_exit_acknowledged') === null,
           'cancelling was recorded as an acknowledgement');
    await c.confirmMultiviewExit(COMPOSING, 'multiviewLive');
    assert(calls.length === 2, 'the second route was not warned about');
  });

  await check('the second route in the same session is not warned about', async () => {
    const c = makeContext();
    const calls = withConfirm(c, {ok: true, suppress: false});
    assert((await c.confirmMultiviewExit(COMPOSING, 'multiviewLive')) === true);
    assert((await c.confirmMultiviewExit(COMPOSING, 'multiviewLive')) === true);
    assert(calls.length === 1, `the warning appeared ${calls.length} times`);
  });

  await check('the acknowledgement is session-scoped, not permanent', async () => {
    const c = makeContext();
    withConfirm(c, {ok: true, suppress: false});
    await c.confirmMultiviewExit(COMPOSING, 'multiviewLive');
    assert(c.sessionStorage.getItem('matrix_multiview_exit_acknowledged') === 'true',
           'nothing was remembered for the session');
    assert(c.localStorage.getItem('matrix_multiview_exit_suppressed') === null,
           'one acknowledgement suppressed the warning for ever');
  });

  await check('a page refresh does not bring the warning back', async () => {
    // A refresh re-runs the script against storage that survives it. Reloading
    // the source into that same session storage is exactly that.
    const first = makeContext();
    withConfirm(first, {ok: true, suppress: false});
    await first.confirmMultiviewExit(COMPOSING, 'multiviewLive');
    const after = makeContext();
    first.sessionStorage._map.forEach((v, k) => after.sessionStorage.setItem(k, v));
    const calls = withConfirm(after, {ok: true, suppress: false});
    assert((await after.confirmMultiviewExit(COMPOSING, 'multiviewLive')) === true);
    assert(calls.length === 0, 'the refresh reset the acknowledgement');
  });

  await check('a genuinely new session is warned again', async () => {
    const c = makeContext();          // fresh session storage, nothing carried
    const calls = withConfirm(c, {ok: true, suppress: false});
    await c.confirmMultiviewExit(COMPOSING, 'multiviewLive');
    assert(calls.length === 1, 'a new session was not warned');
  });

  await check('do-not-show-again is remembered beyond the session', async () => {
    const c = makeContext();
    withConfirm(c, {ok: true, suppress: true});
    await c.confirmMultiviewExit(COMPOSING, 'multiviewLive');
    assert(c.localStorage.getItem('matrix_multiview_exit_suppressed') === 'true',
           'the preference was not stored');
    const next = makeContext();       // a new session, with the preference kept
    next.localStorage.setItem('matrix_multiview_exit_suppressed', 'true');
    const calls = withConfirm(next, {ok: true, suppress: false});
    assert((await next.confirmMultiviewExit(COMPOSING, 'multiviewLive')) === true);
    assert(calls.length === 0, 'the suppressed warning came back');
  });

  await check('storage that refuses to answer means asking, not assuming', async () => {
    const c = makeContext();
    c.sessionStorage.getItem = () => { throw new Error('storage disabled'); };
    c.localStorage.getItem = () => { throw new Error('storage disabled'); };
    const calls = withConfirm(c, {ok: true, suppress: false});
    await c.confirmMultiviewExit(COMPOSING, 'multiviewLive');
    assert(calls.length === 1,
           'a browser with storage disabled silently skipped the warning');
  });

  // ---- the consent travels with the request --------------------------------
  await check('the route request carries the operator consent', () => {
    const source = fs.readFileSync(SOURCE, 'utf8');
    assert(/exit_multiview:\s*!!decoderMultiview\(/.test(source),
           'the route request does not carry exit_multiview');
  });

  console.log(failures.length
    ? `\nmatrix Multiview awareness: ${failures.length} failure(s)`
    : '\nA/V Matrix Multiview awareness test: all checks passed');
  process.exit(failures.length ? 1 : 0);
}

main();

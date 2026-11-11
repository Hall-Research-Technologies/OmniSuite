/*
 * Frontend test for the A/V Matrix's awareness of an active Multiview.
 *
 * A decoder that is compositing a Multiview is not showing any single encoder,
 * but its ip_input1 still holds whatever it last watched -- so without this the
 * matrix draws a video crosspoint for a picture nobody is looking at. Routing a
 * conventional source to such a decoder is still allowed, and asks nothing.
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
    dataset: {},
    rows: [],
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

  // ---- a route asks nothing -------------------------------------------------
  //
  // A normal A/V route means single view, so putting a source on a display that
  // is compositing IS the instruction. The server reads the decoder and leaves
  // the Multiview inside the same verified transaction; this page asks nothing,
  // sends no answer, and keeps no preference for a question that no longer
  // exists. These tests dispatch the real click handler rather than reading its
  // text.
  async function clickRoute(c, decoder) {
    const posts = [];
    let asked = 0;
    c.omniConfirm = async () => { asked++; return true; };
    c.fetch = async (url, init) => {
      posts.push({url, body: init && init.body ? JSON.parse(init.body) : null});
      return {ok: true, status: 200, text: async () => JSON.stringify({ok: true})};
    };
    vm.runInContext('lastState = ' + JSON.stringify({
      encoders: [ENCODER], decoders: [PLAIN, COMPOSING],
      _rawEncoders: [ENCODER], _rawDecoders: [PLAIN, COMPOSING]}), c);
    const attrs = {'data-dec': decoder.ip, 'data-enc': ENCODER.ip};
    const cell = {getAttribute: (k) => attrs[k]};
    await c.onMatrixCellClick(cell, {stopPropagation() {}}, tolerant('table'));
    return {posts: posts.filter(x => x.url === '/api/route'), asked};
  }

  await check('routing to a compositing decoder asks nothing and sends one plain route',
              async () => {
    const c = makeContext();
    const {posts, asked} = await clickRoute(c, COMPOSING);
    assert(asked === 0, 'the operator was asked about a route they had just chosen');
    assert(posts.length === 1, 'expected exactly one route request, got ' + posts.length);
    assert(posts[0].body.decoder === COMPOSING.ip && posts[0].body.encoder === ENCODER.ip
           && posts[0].body.mode === 'av', JSON.stringify(posts[0].body));
    assert(!('exit_multiview' in posts[0].body),
           'the request carries an answer to a question nobody is asked');
  });

  await check('routing to an ordinary decoder is the same request', async () => {
    const c = makeContext();
    const {posts, asked} = await clickRoute(c, PLAIN);
    assert(asked === 0 && posts.length === 1, `asked ${asked}, posts ${posts.length}`);
    assert(Object.keys(posts[0].body).sort().join() === 'decoder,encoder,mode',
           JSON.stringify(posts[0].body));
  });

  await check('a route writes no preference for the retired question', async () => {
    const c = makeContext();
    await clickRoute(c, COMPOSING);
    assert(c.sessionStorage._map.size === 0 && c.localStorage._map.size === 0,
           'the route left a preference behind');
  });

  await check('the page keeps no trace of the retired confirmation', () => {
    const source = fs.readFileSync(SOURCE, 'utf8');
    assert(!/confirmMultiviewExit|multiviewWarningSuppressed|rememberMultiviewWarning/
           .test(source), 'the retired confirmation helpers are still defined');
    assert(!/matrix_multiview_exit/.test(source),
           'the page still reads a preference for a question that no longer exists');
    assert(!/exit_multiview|omniMultiviewExit/.test(source),
           'the route request still carries an answer nobody is asked for');
  });

  console.log(failures.length
    ? `\nmatrix Multiview awareness: ${failures.length} failure(s)`
    : '\nA/V Matrix Multiview awareness test: all checks passed');
  process.exit(failures.length ? 1 : 0);
}

main();

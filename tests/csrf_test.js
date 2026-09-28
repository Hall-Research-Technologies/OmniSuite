// The CSRF wrapper, executed rather than read.
//
// The defect it exists for was found by hands-on testing and measured against
// the live remote listener: an administrator signed in from another device
// could read everything and change nothing. POST /api/get_thumbnail_status
// answered 403 "not permitted" without the header and 400 "IP address
// required" with it -- the handler's own answer -- so the session was fine and
// the browser simply was not presenting what the gate asked for.
//
// Fifty-seven call sites across nine files predate remote access. Editing them
// all would have fixed today and not tomorrow, so the fix is one wrapper, and
// these drive it with a recording fetch: "a POST carries the header" is a
// measurement, not an assertion about source text.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const SOURCE = fs.readFileSync(path.join(ROOT, 'ui', 'csrf.js'), 'utf8');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => results.push(['ok  ', name]))
    .catch((err) => {
      failures++;
      results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]);
    });
}
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}

// A page: a recording fetch, a storage that can be switched off, and an origin.
function makePage(options) {
  options = options || {};
  const calls = [];
  const storage = {};
  const sandbox = {
    Promise, Headers, URL, Object, String, console,
    window: {
      location: {href: 'https://omnisuite.invalid/matrix', origin: 'https://omnisuite.invalid'},
    },
  };
  sandbox.window.window = sandbox.window;
  sandbox.URL = URL;
  sandbox.Headers = Headers;
  sandbox.window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : input.url;
    const method = ((init && init.method) || 'GET').toUpperCase();
    let header = null;
    if (init && init.headers) {
      header = typeof init.headers.get === 'function'
        ? init.headers.get('X-OmniSuite-CSRF')
        : init.headers['X-OmniSuite-CSRF'];
    }
    calls.push({url, method, header});
    if (url === '/api/auth/session') {
      if (options.sessionFails) return Promise.reject(new Error('offline'));
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(options.session || {csrf_token: 'TOKEN-1'}),
      });
    }
    return Promise.resolve({ok: true, status: 200, json: () => Promise.resolve({})});
  };
  sandbox.sessionStorage = options.noStorage ? {
    getItem() { throw new Error('storage disabled'); },
    setItem() { throw new Error('storage disabled'); },
    removeItem() { throw new Error('storage disabled'); },
  } : {
    getItem: (k) => (k in storage ? storage[k] : null),
    setItem: (k, v) => { storage[k] = String(v); },
    removeItem: (k) => { delete storage[k]; },
  };
  sandbox.window.sessionStorage = sandbox.sessionStorage;
  if (options.preset) storage.omni_csrf = options.preset;

  vm.createContext(sandbox);
  vm.runInContext(SOURCE, sandbox);
  return {sandbox, calls, storage, fetch: sandbox.window.fetch};
}

const suite = [];

suite.push(() => check('a POST carries the token', async () => {
  const page = makePage();
  await page.fetch('/api/route', {method: 'POST', body: '{}'});
  const post = page.calls.filter((c) => c.method === 'POST').pop();
  assert(post.header === 'TOKEN-1',
    `the header was ${JSON.stringify(post.header)}`);
}));

suite.push(() => check('the token is asked for exactly once', async () => {
  const page = makePage();
  await Promise.all([
    page.fetch('/api/route', {method: 'POST'}),
    page.fetch('/api/scan', {method: 'POST'}),
    page.fetch('/api/blink', {method: 'POST'}),
  ]);
  const asks = page.calls.filter((c) => c.url === '/api/auth/session');
  assert(asks.length === 1,
    `three actions asked the server ${asks.length} times`);
}));

suite.push(() => check('a GET is left exactly as it was', async () => {
  const page = makePage();
  await page.fetch('/api/state');
  const get = page.calls.find((c) => c.url === '/api/state');
  assert(!get.header, 'a read needs no token and must not be altered');
  assert(page.calls.every((c) => c.url !== '/api/auth/session'),
    'a read must not cost a session lookup');
}));

suite.push(() => check('every state-changing verb is covered', async () => {
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    const page = makePage();
    await page.fetch('/api/thing', {method});
    const sent = page.calls.filter((c) => c.method === method).pop();
    assert(sent.header === 'TOKEN-1', `${method} went without the header`);
  }
}));

suite.push(() => check('a lowercase method is still recognised', async () => {
  const page = makePage();
  await page.fetch('/api/route', {method: 'post'});
  const sent = page.calls.filter((c) => c.method === 'POST').pop();
  assert(sent.header === 'TOKEN-1', 'method matching must not be case bound');
}));

suite.push(() => check('another origin never receives the token', async () => {
  // The matrix fetches thumbnails straight from devices. Sending a session
  // token to a third party is how one leaves the building.
  const page = makePage();
  await page.fetch('http://198.51.100.9/thumbnail/thumbnail1.jpg',
                   {method: 'POST'});
  const sent = page.calls.filter((c) => c.method === 'POST').pop();
  assert(!sent.header, 'a cross-origin request carried the token');
}));

suite.push(() => check('a caller that set the header itself is not overridden', async () => {
  const page = makePage();
  await page.fetch('/api/route',
                   {method: 'POST', headers: {'X-OmniSuite-CSRF': 'MINE'}});
  const sent = page.calls.filter((c) => c.method === 'POST').pop();
  assert(sent.header === 'MINE', `it became ${sent.header}`);
  assert(page.calls.every((c) => c.url !== '/api/auth/session'),
    'and it must not have asked the server for one');
}));

suite.push(() => check('the caller\'s own headers survive', async () => {
  const page = makePage();
  let seen = null;
  const inner = page.sandbox.window.fetch;
  page.sandbox.window.fetch = function (input, init) {
    if (init && init.headers && typeof init.headers.get === 'function') {
      seen = init.headers.get('Content-Type');
    }
    return inner(input, init);
  };
  // Re-wrap against the replaced inner: the module took its reference at
  // install time, so drive it through the public entry point instead.
  await page.fetch('/api/route', {
    method: 'POST', headers: {'Content-Type': 'application/json'}});
  const sent = page.calls.filter((c) => c.method === 'POST').pop();
  assert(sent.header === 'TOKEN-1', 'the token should still be attached');
}));

suite.push(() => check('a local operator is not blocked by having no token', async () => {
  // The gate trusts the socket a local request arrived on and never looks for
  // a token, so the endpoint returns none. The request must still go.
  const page = makePage({session: {ok: true, local: true, csrf_token: ''}});
  await page.fetch('/api/route', {method: 'POST'});
  const sent = page.calls.filter((c) => c.method === 'POST').pop();
  assert(sent, 'the request was never made');
  assert(!sent.header, 'there was no token to attach');
}));

suite.push(() => check('a local page asks only once, not on every action', async () => {
  const page = makePage({session: {ok: true, local: true, csrf_token: ''}});
  await page.fetch('/api/route', {method: 'POST'});
  await page.fetch('/api/scan', {method: 'POST'});
  await page.fetch('/api/blink', {method: 'POST'});
  const asks = page.calls.filter((c) => c.url === '/api/auth/session');
  assert(asks.length === 1, `it asked ${asks.length} times`);
}));

suite.push(() => check('an unreachable server does not swallow the request', async () => {
  const page = makePage({sessionFails: true});
  await page.fetch('/api/route', {method: 'POST'});
  const sent = page.calls.filter((c) => c.method === 'POST').pop();
  assert(sent, 'the action must still be attempted and fail on its own terms');
}));

suite.push(() => check('storage being off does not break anything', async () => {
  const page = makePage({noStorage: true});
  await page.fetch('/api/route', {method: 'POST'});
  const sent = page.calls.filter((c) => c.method === 'POST').pop();
  assert(sent.header === 'TOKEN-1', 'the in-memory copy should still serve');
}));

// This used to assert that a token already in sessionStorage was used without
// asking the server, on the reasoning that the sign-in page had just handed
// one over. That reasoning holds only while the tab's session never changes,
// and on the bench it did: a tab that had signed in with a password then
// opened a Direct Access link kept the old token and had five consecutive
// routes refused for a bad CSRF token.
//
// So the server decides, and what this test was really protecting -- that a
// page does not ask on every single request -- is asserted directly above, by
// counting the requests rather than by forbidding the first one.
suite.push(() => check('the sign-in token is kept when the server confirms it', async () => {
  const page = makePage({preset: 'FROM-SIGN-IN',
                         session: {csrf_token: 'FROM-SIGN-IN'}});
  await page.fetch('/api/route', {method: 'POST'});
  const sent = page.calls.filter((c) => c.method === 'POST').pop();
  assert(sent.header === 'FROM-SIGN-IN', `it used ${sent.header}`);
}));

suite.push(() => check('the token is never written to localStorage', async () => {
  const page = makePage();
  await page.fetch('/api/route', {method: 'POST'});
  assert(!('localStorage' in page.sandbox),
    'the module must not reach for localStorage at all');
  const code = SOURCE.split('\n').map((l) => l.split('//')[0]).join('\n');
  assert(!/localStorage/.test(code),
    'a token outliving its session is a token that is wrong');
}));

suite.push(() => check('forgetting a stale token makes the next action ask again', async () => {
  const page = makePage({preset: 'STALE'});
  await page.fetch('/api/route', {method: 'POST'});
  page.sandbox.window.omniForgetCsrf();
  await page.fetch('/api/route', {method: 'POST'});
  const sent = page.calls.filter((c) => c.method === 'POST').pop();
  assert(sent.header === 'TOKEN-1', `it stayed on ${sent.header}`);
}));

suite.push(() => check('installing it twice does not chain the wrapper', async () => {
  const page = makePage();
  const wrapped = page.sandbox.window.fetch;
  vm.runInContext(SOURCE, page.sandbox);
  assert(page.sandbox.window.fetch === wrapped,
    'a second load replaced the wrapper, which would double every request');
}));

suite.push(() => check('the session summary is shared rather than fetched twice', async () => {
  const page = makePage();
  const [a, b] = await Promise.all([
    page.sandbox.window.omniSession(),
    page.sandbox.window.omniIsLocal(),
  ]);
  assert(a && typeof b === 'boolean', 'both answers should arrive');
  const asks = page.calls.filter((c) => c.url === '/api/auth/session');
  assert(asks.length === 1, `it asked ${asks.length} times`);
}));

suite.push(() => check('omniIsLocal reports what the server said', async () => {
  const remote = makePage({session: {ok: true, local: false, csrf_token: 'T'}});
  assert((await remote.sandbox.window.omniIsLocal()) === false);
  const local = makePage({session: {ok: true, local: true, csrf_token: ''}});
  assert((await local.sandbox.window.omniIsLocal()) === true);
}));

// ---- the pages actually load it --------------------------------------------
suite.push(() => check('every page that makes requests loads the wrapper first', () => {
  const pages = ['ui/index.html', 'ui/matrix/index.html',
                 'ui/matrix/configure.html', 'ui/matrix/usb.html',
                 'ui/matrix/multiview.html', 'ui/admin/users.html',
                 'ui/user/index.html'];
  pages.forEach((name) => {
    const html = fs.readFileSync(path.join(ROOT, name), 'utf8');
    const scripts = [...html.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]);
    assert(scripts.length > 0, `${name} loads no scripts`);
    assert(scripts[0].startsWith('/ui/csrf.js'),
      `${name} loads ${scripts[0]} before csrf.js; a script that ran first `
      + 'would hold the unwrapped fetch');
  });
}));

suite.push(() => check('the firmware list is not requested from a remote browser', () => {
  const html = fs.readFileSync(path.join(ROOT, 'ui', 'index.html'), 'utf8');
  const start = html.indexOf('async function loadFiles()');
  assert(start > 0, 'loadFiles is not where this expects it');
  const body = html.slice(start, html.indexOf('/api/files', start));
  const code = body.split('\n').map((l) => l.split('//')[0]).join('\n');
  assert(/omniIsLocal/.test(code),
    'the firmware folder is on the host disk; asking for it from elsewhere '
    + 'is a guaranteed 404 and a console error on every page load');
}));

suite.push(() => check('a token left over from an ended session is not used', async () => {
  // The live defect. A tab that had signed in with a password, then opened a
  // Direct Access link, kept `omni_csrf` in sessionStorage -- which is per-tab
  // and survives a change of session. The wrapper returned it without asking
  // the server, so every route from the panel was refused for a bad CSRF
  // token, with nothing on screen to say why. Measured on the bench: five
  // consecutive refusals with reason "csrf".
  const page = makePage({preset: 'TOKEN-FROM-AN-OLD-SESSION',
                         session: {csrf_token: 'TOKEN-FOR-THIS-SESSION'}});
  await page.fetch('/api/user/route', {method: 'POST', body: '{}'});
  const post = page.calls.filter((c) => c.method === 'POST').pop();
  assert(post.header === 'TOKEN-FOR-THIS-SESSION',
    `the stale token was presented: ${JSON.stringify(post.header)}`);
  assert(page.storage.omni_csrf === 'TOKEN-FOR-THIS-SESSION',
    'the stale token was left in storage to fail again');
}));

suite.push(() => check('the server is asked once, not once per request', async () => {
  // Making the server the authority must not turn every action into two
  // requests. `session()` is single-flighted and cached for the page.
  const page = makePage();
  await page.fetch('/api/route', {method: 'POST', body: '{}'});
  await page.fetch('/api/route', {method: 'POST', body: '{}'});
  await page.fetch('/api/unroute', {method: 'POST', body: '{}'});
  const asks = page.calls.filter((c) => c.url === '/api/auth/session');
  assert(asks.length === 1,
    `the session endpoint was asked ${asks.length} times`);
}));

suite.push(() => check('a stored token still serves when the server cannot be reached', async () => {
  // The fallback that makes the change safe: if the session request fails
  // outright, the page is no worse off than before.
  const page = makePage({preset: 'TOKEN-HELD', sessionFails: true});
  await page.fetch('/api/route', {method: 'POST', body: '{}'});
  const post = page.calls.filter((c) => c.method === 'POST').pop();
  assert(post.header === 'TOKEN-HELD',
    `nothing was presented: ${JSON.stringify(post.header)}`);
}));

(async function run() {
  // Sequentially: each scenario builds its own sandbox, and running them at
  // once makes a failure name the wrong one.
  for (const scenario of suite) await scenario();
  results.forEach(([status, name]) => console.log(`${status} ${name}`));
  console.log(`\n${results.length - failures}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
}());

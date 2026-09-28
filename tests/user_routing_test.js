// The restricted shell's routing flow, source cards and sign-out — executed.
//
// The reported defect was that routing was hit-or-miss, and the shell's part
// of the fix is the half a Python test cannot see: one tap makes one request,
// the operator is asked before a display leaves a composition, their answer
// travels with the second request, and nothing is sent at all if they say no.
//
// So these drive the real handler against a recording fetch and a scripted
// dialog. "Cancel makes no request" is then a measurement rather than an
// assertion about source text.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');

function read(...parts) {
  return fs.readFileSync(path.join(ROOT, ...parts), 'utf8')
    .split('\r\n').join('\n');
}

const SHELL_JS = read('ui', 'user', 'user.js');
const SHELL_HTML = read('ui', 'user', 'index.html');
const REMOTE_CSS = read('ui', 'remote.css');
const CONFIRM_JS = read('ui', 'confirm.js');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => {
      failures++;
      results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]);
    });
}
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}
function code(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((l) => l.split('//')[0]).join('\n');
}

// Lift one function out of the module and run it against a scripted world.
function lift(name, extras) {
  const start = SHELL_JS.indexOf('  ' + name);
  assert(start >= 0, `${name} is not where this expects it`);
  const end = SHELL_JS.indexOf('\n  }\n', start);
  assert(end > start, `could not find the end of ${name}`);
  const body = SHELL_JS.slice(start, end + 4);
  const sandbox = Object.assign({Promise, JSON, Object, console}, extras);
  vm.createContext(sandbox);
  vm.runInContext(`${body}\nglobalThis.__lifted = ${name.split('(')[0].replace('async function ', '').replace('function ', '').trim()};`, sandbox);
  return sandbox.__lifted;
}

const suite = [];

// ------------------------------------------------------------- wording ---
function failureText() {
  return lift('function failureText(body, status) {', {});
}

suite.push(() => check('a busy display is not reported as a bare failure', () => {
  const text = failureText()({reason: 'resource_busy'}, 409);
  assert(/busy with another routing operation/i.test(text), text);
  assert(!/409/.test(text), 'the operator must never be shown a status code');
}));

suite.push(() => check('a composition is named as one', () => {
  const text = failureText()({reason: 'multiview_active'}, 409);
  assert(/Multiview/.test(text), text);
}));

suite.push(() => check('the two 409s say different things', () => {
  const busy = failureText()({reason: 'resource_busy'}, 409);
  const mv = failureText()({reason: 'multiview_active'}, 409);
  assert(busy !== mv, 'both 409s produced the same sentence');
}));

suite.push(() => check('a permission change reads as a change, not a telling-off', () => {
  const text = failureText()({}, 403);
  assert(/permission/i.test(text), text);
  assert(!/you do not have permission for that\.?$/i.test(text.trim()),
    'the likely cause is an administrator changing something');
}));

suite.push(() => check('an unreachable device is not "that did not work"', () => {
  assert(/did not respond|did not answer/i.test(failureText()({}, 502)));
  assert(/did not respond|did not answer/i.test(failureText()({}, 503)));
}));

// --------------------------------------------------------- the route flow ---
function makeWorld(script) {
  const requests = [];
  const asked = [];
  const world = {
    requests,
    asked,
    state: {source: 'src-a', destination: 'dst-a', sources: [], destinations: []},
    named: () => 'Front Display',
    api: async (path, options) => {
      requests.push({path, body: JSON.parse(options.body)});
      return script.shift() || {status: 200, body: {ok: true}};
    },
    window: {
      omniMultiviewExit: {
        ask: async (options) => { asked.push(options); return world.answer; },
      },
    },
    answer: true,
  };
  return world;
}

function liftRouteAv(world) {
  const start = SHELL_JS.indexOf('  async function routeAv(answered) {');
  assert(start >= 0, 'routeAv is not where this expects it');
  const end = SHELL_JS.indexOf('\n  }\n', start);
  const body = SHELL_JS.slice(start, end + 4);
  const sandbox = Object.assign({Promise, JSON, Object, console}, world);
  vm.createContext(sandbox);
  vm.runInContext(`${body}\nglobalThis.__routeAv = routeAv;`, sandbox);
  return sandbox.__routeAv;
}

suite.push(() => check('an ordinary route is one request and no question', async () => {
  const world = makeWorld([{status: 200, body: {ok: true}}]);
  const result = await liftRouteAv(world)(false);
  assert(world.requests.length === 1, `${world.requests.length} requests`);
  assert(world.asked.length === 0, 'nobody should have been asked anything');
  assert(result.status === 200);
  assert(!('exit_multiview' in world.requests[0].body),
    'an ordinary route must not carry an answer nobody gave');
}));

suite.push(() => check('a composition asks, then carries the answer', async () => {
  const world = makeWorld([
    {status: 409, body: {reason: 'multiview_active', multiview: 'quad'}},
    {status: 200, body: {ok: true}},
  ]);
  const result = await liftRouteAv(world)(false);
  assert(world.asked.length === 1, 'the operator should have been asked once');
  assert(world.asked[0].multiview === 'quad', JSON.stringify(world.asked[0]));
  assert(world.requests.length === 2, `${world.requests.length} requests`);
  assert(world.requests[1].body.exit_multiview === true,
    'the second request must carry the answer');
  assert(result.status === 200);
}));

suite.push(() => check('saying no sends nothing more', async () => {
  const world = makeWorld([
    {status: 409, body: {reason: 'multiview_active', multiview: 'quad'}},
  ]);
  world.answer = false;
  const result = await liftRouteAv(world)(false);
  assert(world.requests.length === 1,
    `Cancel produced ${world.requests.length} requests; it must produce one`);
  assert(result === null, 'a declined route must report that nothing happened');
}));

suite.push(() => check('a refusal that already carried the answer is not retried', async () => {
  // Otherwise a display that refuses for some other reason would be asked
  // about forever.
  const world = makeWorld([
    {status: 409, body: {reason: 'multiview_active', multiview: 'quad'}},
  ]);
  const result = await liftRouteAv(world)(true);
  assert(world.requests.length === 1, `${world.requests.length} requests`);
  assert(world.asked.length === 0, 'it must not ask again');
  assert(result.status === 409, 'and it must report the refusal');
}));

suite.push(() => check('a busy display is never turned into a question', async () => {
  const world = makeWorld([{status: 409, body: {reason: 'resource_busy'}}]);
  const result = await liftRouteAv(world)(false);
  assert(world.asked.length === 0, 'busy is not a question for the operator');
  assert(world.requests.length === 1, 'and it is not retried');
  assert(result.status === 409);
}));

suite.push(() => check('without the shared dialog nothing is sent silently', async () => {
  // The dialog says what will happen to a display somebody is watching. If it
  // is unavailable the action is refused, not taken.
  const world = makeWorld([
    {status: 409, body: {reason: 'multiview_active', multiview: 'quad'}},
  ]);
  world.window = {};
  const result = await liftRouteAv(world)(false);
  assert(world.requests.length === 1, `${world.requests.length} requests`);
  assert(result.status === 409, 'it must report the refusal rather than proceed');
}));

// ----------------------------------------------------------- the markup ---
suite.push(() => check('the shell loads the shared confirmation dialog', () => {
  assert(/<script src="\/ui\/confirm\.js"><\/script>/.test(SHELL_HTML),
    'the Multiview question needs it, and it must not be a second dialog');
}));

suite.push(() => check('the shared dialog carries its own styling', () => {
  // It used to be styled by ui/matrix/matrix.css, so it was a dialog only on
  // pages that happened to load that stylesheet. Measured on /admin/users the
  // backdrop was position:static and the card transparent and full width.
  const body = code(CONFIRM_JS);
  assert(/omni_confirm_style/.test(body), 'no stylesheet is injected');
  assert(/\.confirm-backdrop\{position:fixed/.test(body.replace(/\s+/g, '')),
    'the backdrop must be fixed, or the dialog is not a dialog');
  const matrixCss = code(read('ui', 'matrix', 'matrix.css'));
  assert(!/\.confirm-card\{/.test(matrixCss.replace(/\s+/g, '')),
    'two definitions of one dialog is how they drift apart');
}));

// -------------------------------------------------------- source previews ---
suite.push(() => check('a source card carries the picture, not a button', () => {
  const body = code(SHELL_JS);
  assert(/pick-shot/.test(body), 'no preview element on the card');
  assert(/has-preview/.test(body), 'the card does not become a preview card');
  // The whole card stays the selection target: the picture is inside the
  // same <button>, not a sibling with its own handler.
  assert(/button\.append\(shot, text\)/.test(body),
    'the preview must be inside the card, so the whole card is tappable');
}));

suite.push(() => check('nothing is fetched until a card is on screen', () => {
  const body = code(SHELL_JS);
  assert(/IntersectionObserver/.test(body),
    'a long source list must not fetch every picture on load');
  assert(/rootMargin/.test(body), 'it should fetch just before it is needed');
}));

suite.push(() => check('the observer is disconnected before the cards are replaced', () => {
  const body = code(SHELL_JS);
  assert(/previewObserver\.disconnect\(\)/.test(body),
    'an observer left watching detached nodes accumulates');
}));

suite.push(() => check('there is no repeating preview timer', () => {
  const body = code(SHELL_JS);
  const timers = body.match(/setInterval\s*\(/g) || [];
  assert(timers.length === 0,
    `the shell must make no periodic device request; found ${timers.length}`);
}));

suite.push(() => check('a missing picture is a card, never a broken image', () => {
  const body = code(SHELL_JS);
  assert(/function noPreview/.test(body), 'no fallback for a card with no picture');
  assert(/image\.onerror/.test(body), 'a failed image must fall back too');
  assert(/Preview disabled/.test(SHELL_JS),
    'a deliberately disabled preview should say so');
}));

suite.push(() => check('the preview is requested by identity, never an address', () => {
  const body = code(SHELL_JS);
  assert(/source_preview\?source=/.test(body), 'preview request not found');
  assert(!/thumbnail1\.jpg/.test(body),
    'the shell must not build a device URL; that would expose the address');
  assert(!/http:\/\//.test(body.replace(/https?:\/\/[^\s'"]*schema/g, '')),
    'no absolute device URL belongs in the restricted shell');
}));

suite.push(() => check('the preview keeps its aspect and is not cropped', () => {
  const css = code(REMOTE_CSS).replace(/\s+/g, '');
  assert(/aspect-ratio:16\/9/.test(css), 'the card should hold a 16:9 shape');
  assert(/object-fit:contain/.test(css),
    'cropping can hide the corner somebody uses to tell two inputs apart');
}));

suite.push(() => check('selection stays obvious over an arbitrary picture', () => {
  const css = code(REMOTE_CSS).replace(/\s+/g, '');
  assert(/\.pick\.has-preview\[aria-pressed="true"\]/.test(css),
    'a selected preview card needs its own treatment');
  // An edge, because a tint is what a busy picture swallows -- and at FULL
  // strength from the accent the presets set, because the selection token is
  // 16% alpha and 16% alpha over a white source is nothing at all. Measured:
  // the footer tint alone is deltaE 12.9, the edge is rgb(30,144,255) solid.
  assert(/box-shadow:inset000\d+px/.test(css),
    'selection should be carried by an edge');
  assert(/box-shadow:inset000\d+pxvar\(--action-primary\)/.test(css),
    'the edge must be the full-strength accent, not the selection tint');
}));

// ------------------------------------------------------------- routing UI ---
suite.push(() => check('the display says it is routing', () => {
  assert(/Routing/.test(SHELL_JS), 'no in-flight state on the display');
  assert(/renderRoutingState/.test(code(SHELL_JS)));
}));

suite.push(() => check('a display mid-route does not accept another tap', () => {
  const body = code(SHELL_JS);
  assert(/function busyWith/.test(body), 'no guard on the display being routed');
  assert(/if \(busyWith\(button\)\) return;/.test(body),
    'the guard exists but nothing consults it');
}));

suite.push(() => check('the page is usable again after a failure', () => {
  const body = code(SHELL_JS);
  const finallyBlock = body.slice(body.indexOf('} finally {'));
  assert(/state\.busy = false/.test(finallyBlock),
    'a failed route must not leave the action bar disabled');
  assert(/state\.routingTo = null/.test(finallyBlock),
    'and must not leave a display marked as routing for ever');
}));

suite.push(() => check('success says Routed rather than Done', () => {
  assert(/window\.toast\('Routed', 'ok'\)/.test(SHELL_JS),
    'the confirmation should name what happened');
}));

// ---------------------------------------------------------------- signing out ---
suite.push(() => check('signing out clears the token this tab held', () => {
  const body = code(SHELL_JS);
  const handler = body.slice(body.indexOf("el('signout')"));
  assert(/omniForgetCsrf/.test(handler),
    'a token belonging to a dead session must not be presented by the next login');
}));

suite.push(() => check('the toast helper is the one that exists', () => {
  // window.omniToast has never existed; the export is window.toast. Guarded
  // calls to the wrong name fail silently, so the copy confirmation simply
  // never appeared.
  assert(!/omniToast/.test(SHELL_JS), 'user.js calls a toast that does not exist');
  assert(!/omniToast/.test(read('ui', 'admin', 'users.js')),
    'users.js calls a toast that does not exist');
}));

suite.push(() => check('the sign-out control is one module, not markup per page', () => {
  const signout = read('ui', 'signout.js');
  assert(/omni_signout/.test(signout));
  assert(/__omniSignOutInstalled/.test(code(signout)),
    'injection must be guarded or a second load duplicates every id');
  assert(/session\.local/.test(code(signout)),
    'the local operator has no session to end and must not be offered one');
  ['ui/index.html', 'ui/matrix/index.html', 'ui/matrix/configure.html',
   'ui/matrix/usb.html', 'ui/matrix/multiview.html', 'ui/admin/users.html']
    .forEach((page) => {
      const html = read(...page.split('/'));
      assert(/<script src="\/ui\/signout\.js"><\/script>/.test(html),
        `${page} offers no way to sign out`);
    });
}));

suite.push(() => check('signing out does not withdraw a direct access link', () => {
  const signout = code(read('ui', 'signout.js'));
  assert(/\/api\/auth\/logout/.test(signout));
  assert(!/direct_access/.test(signout),
    'ending a session must not touch the link that can start another');
}));

(async function run() {
  for (const scenario of suite) await scenario();
  results.forEach(([status, name]) => console.log(`${status} ${name}`));
  console.log(`\n${results.length - failures}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
}());

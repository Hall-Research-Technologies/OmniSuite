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

suite.push(() => check('the one 409 that remains says what to do', () => {
  // There used to be two. A normal route no longer refuses over a Multiview,
  // so a busy display is the only conflict an operator can meet.
  const busy = failureText()({reason: 'resource_busy'}, 409);
  assert(/wait|moment|try again/i.test(busy), busy);
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
// A normal route means single view, so there is no question and no second
// request. These hold the absence: the flow that existed only to ask, and to
// carry the answer, is gone from every surface.

function routeHandlerSource() {
  const start = SHELL_JS.indexOf("el('route_btn').addEventListener");
  assert(start >= 0, 'the route button handler is not where this expects it');
  const end = SHELL_JS.indexOf('\n  });\n', start);
  return SHELL_JS.slice(start, end);
}

suite.push(() => check('the shell asks nothing before routing', () => {
  const body = code(routeHandlerSource());
  assert(!/omniMultiviewExit/.test(body),
    'the shell still asks about Multiview before a normal route');
  assert(!/exit_multiview/.test(body),
    'nothing has to be asked, so nothing has to be sent');
}));

suite.push(() => check('the shell makes one request per route', () => {
  const body = code(routeHandlerSource());
  const posts = body.match(/\/api\/user\/route/g) || [];
  assert(posts.length === 1,
    `the handler names the route endpoint ${posts.length} times; the second `
    + 'request existed only to carry an answer to a question that is gone');
}));

suite.push(() => check('the retired helper is gone from every surface', () => {
  ['ui/user/user.js', 'ui/matrix/matrix.js', 'ui/matrix/multiview.js']
    .forEach((page) => {
      const source = code(read(...page.split('/')));
      assert(!/omniMultiviewExit/.test(source),
        `${page} still asks the retired question`);
    });
  // And the helper itself, so nothing can start asking it again by accident.
  assert(!/omniMultiviewExit\s*=/.test(code(CONFIRM_JS)),
    'confirm.js still defines the retired helper');
}));

suite.push(() => check('the shared dialog itself is untouched', () => {
  // It is still what asks before a Direct Access link is replaced.
  const body = code(CONFIRM_JS);
  assert(/window\.omniConfirm\s*=/.test(body), 'the shared dialog was removed');
  assert(/omniSuppression/.test(body),
    'the shared version-scoped suppression store was removed');
  assert(/confirmText/.test(read('ui', 'admin', 'users.js')),
    'the Direct Access confirmation should still be asked');
}));

suite.push(() => check('the preference the question wrote is dropped', () => {
  // A key that is no longer written still reads, so a browser would carry a
  // suppression for a question that no longer exists.
  const body = code(CONFIRM_JS);
  assert(/matrix_multiview_exit_suppressed/.test(body)
    && /removeItem/.test(body),
    'the retired preference is neither written nor cleared');
}));

suite.push(() => check('no surface sends exit_multiview any more', () => {
  ['ui/user/user.js', 'ui/matrix/matrix.js', 'ui/matrix/multiview.js']
    .forEach((page) => {
      const source = code(read(...page.split('/')));
      assert(!/exit_multiview/.test(source),
        `${page} still sends an answer nobody is asked for`);
    });
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

suite.push(() => check('signing out does not withdraw a direct access link', () => {
  // Ending a session must never touch the link that can start another one:
  // withdrawing a link is an administrator's decision.
  const nav = code(read('ui', 'adminnav.js'));
  assert(/\/api\/auth\/logout/.test(nav));
  assert(!/direct_access/.test(nav),
    'signing out reaches for the Direct Access link');
}));

// ---------------------------------------------------------------- this pass

suite.push(() => check('a remembered display is honoured only if still offered', () => {
  // The rule that makes a bookmark safe: it is matched against the list the
  // server has just sent, so a display that was unassigned is dropped rather
  // than presented as a working selection -- and nothing is ever selected on
  // the operator's behalf.
  const stored = {};
  const restore = lift('function restoreSelection(', {
    // The two helpers it closes over, standing in for the storage module.
    recall: (k) => (k in stored ? stored[k] : null),
    remember: (k, v) => { if (v) stored[k] = v; else delete stored[k]; },
  });

  const list = [{id: 'b8:98:b0:00:00:11'}, {id: 'b8:98:b0:00:00:12'}];

  // Nothing remembered, nothing selected: a list of one is not consent.
  assert(restore([{id: 'b8:98:b0:00:00:11'}], null, 'destination') === null,
    'a single assigned display was selected without being chosen');

  // Remembered and still offered.
  stored.destination = 'b8:98:b0:00:00:12';
  assert(restore(list, null, 'destination') === 'b8:98:b0:00:00:12',
    'a remembered display that is still offered was not restored');

  // Remembered but no longer offered: cleared, not swapped for another.
  stored.destination = 'b8:98:b0:00:00:99';
  assert(restore(list, null, 'destination') === null,
    'a display that is no longer assigned was not cleared');
  assert(!('destination' in stored),
    'the stale record was left behind to fail again next time');

  // A current selection that is still valid wins without consulting storage.
  stored.destination = 'b8:98:b0:00:00:12';
  assert(restore(list, 'b8:98:b0:00:00:11', 'destination')
         === 'b8:98:b0:00:00:11');
}));

suite.push(() => check('the shell itself writes no browser storage', () => {
  // It can see a Direct Access link, so the fence is that it writes nothing at
  // all; the one thing worth remembering lives in a module that cannot see a
  // link. This is the browser-side half of the Python fence.
  const shell = code(SHELL_JS);
  assert(!/localStorage/.test(shell),
    'the shell touches localStorage directly');
  assert(!/sessionStorage\.setItem\(\s*['"]omni_user/.test(shell));
  assert(/omniRemember/.test(shell),
    'the shell does not go through the storage module');
  const store = code(read('ui', 'user', 'remember.js'));
  assert(/localStorage/.test(store), 'the storage module stores nothing');
  assert(!/token|link|csrf/i.test(store),
    'the storage module can reach a credential');
}));

suite.push(() => check('unroute asks first and sends nothing when refused', async () => {
  // A display left showing nothing cannot be undone by choosing something
  // else, so it is confirmed. Driven rather than asserted about: Cancel making
  // no request is the measurement.
  const requests = [];
  let answer = false;
  const sandbox = {
    fetch: (url, opts) => {
      requests.push({url, opts});
      return Promise.resolve({
        status: 200, ok: true,
        json: () => Promise.resolve({ok: true, status: 'UNROUTED'}),
      });
    },
    window: {
      omniConfirm: () => Promise.resolve(answer),
      toast: () => {},
    },
  };

  // The listener, not the line in renderAction that also names the button.
  const body = SHELL_JS.slice(
    SHELL_JS.indexOf("el('unroute_btn').addEventListener"));
  const handler = body.slice(0, body.indexOf('\n  });\n') + 6);

  // The dialog is consulted before anything is sent.
  assert(/omniConfirm/.test(handler), 'unroute does not confirm');
  assert(handler.indexOf('omniConfirm') < handler.indexOf('/api/user/unroute'),
    'the request is built before the operator is asked');
  assert(/if \(!go\) return;/.test(handler),
    'a refusal does not stop the request');

  // And it refuses rather than degrading to a browser dialog, because the
  // sentence is the point of the dialog.
  assert(/typeof ask !== 'function'/.test(handler),
    'unroute does not check that the shared dialog is there');
  assert(!/\bconfirm\(/.test(code(handler)),
    'unroute can fall back to a native dialog');
  assert(requests.length === 0);
}));

suite.push(() => check('a display card previews only a permitted source', () => {
  // `showing.source_id` is null for a Multiview, an unrouted display and a
  // source outside this account's world, so an unauthorised thumbnail cannot
  // be reached from a card by construction.
  const body = SHELL_JS.slice(SHELL_JS.indexOf('const showing = item.showing'));
  const card = body.slice(0, body.indexOf('\n    button.append(name, sub);'));
  assert(/showing\.source_id \|\| null/.test(card),
    'a display card takes its preview from something other than what the '
    + 'server said it is showing');
  assert(!/item\.id/.test(card.slice(card.indexOf('previewId'),
                                     card.indexOf('previewNote'))
                         .replace(/kind === 'source' \? item\.id/, '')),
    'a display card previews itself rather than its source');
  assert(/none: 'Unrouted'/.test(card), 'an unrouted display says nothing');
  assert(/multiview: 'Multiview'/.test(card));
  assert(/other: 'Another source'/.test(card),
    'a display showing an unpermitted source is named rather than described');
}));

suite.push(() => check('the first preview of a page asks for a fresh read', () => {
  // Enabling a thumbnail must show up on a refresh rather than whenever a
  // minutes-long cache happens to lapse -- and an idle page must still make no
  // device request, so only the first request per source sets it.
  const body = SHELL_JS.slice(SHELL_JS.indexOf('async function loadPreview'));
  const fn = body.slice(0, body.indexOf('\n  function noPreview'));
  assert(/const first = !previewSeen\.has\(sourceId\)/.test(fn));
  assert(/first \? '&fresh=1'/.test(fn), 'a page load does not re-read');
  assert(/: '&t=' \+ Date\.now\(\)/.test(fn),
    'later reads are not cache-busted');
}));

suite.push(() => check('a direct access session is a panel, not an account', () => {
  const body = SHELL_JS.slice(SHELL_JS.indexOf("const panel = state.directLink"));
  const block = body.slice(0, body.indexOf('\n    renderAv()'));
  for (const control of ['signout', 'my_link_btn', 'who']) {
    assert(new RegExp(`el\\('${control}'\\)[\\s\\S]{0,80}panel`).test(block),
      `${control} is still offered to a direct access session`);
  }
  // And the page learns which it is from the session rather than guessing.
  const adopt = SHELL_JS.slice(SHELL_JS.indexOf('async function adoptSessionToken'));
  assert(/body\.method === 'direct_link'/.test(adopt));
  assert(!/if \(csrf\(\)\) return;/.test(
    adopt.slice(0, adopt.indexOf('\n  }'))),
    'a reload short-circuits before the page learns it is a panel');
}));

suite.push(() => check('the unroute button is loaded and styled', () => {
  assert(/id="unroute_btn"/.test(SHELL_HTML), 'no unroute control on the page');
  assert(/remember\.js/.test(SHELL_HTML), 'the storage module is not loaded');
  assert(SHELL_HTML.indexOf('remember.js') < SHELL_HTML.indexOf('user/user.js'),
    'the storage module loads after the shell that needs it');
  assert(/btn-secondary-lg/.test(REMOTE_CSS),
    'the unroute button has no style of its own');
}));
// `toLogin` calls `onAccessUrl`, so both are lifted into one scope: stubbing
// the helper would test the stub rather than the rule it encodes.
function liftLostSession(pathname, location) {
  const from = SHELL_JS.indexOf('  function onAccessUrl(');
  const to = SHELL_JS.indexOf('\n  }\n', SHELL_JS.indexOf('  function toLogin('));
  assert(from >= 0 && to > from, 'the lost-session handling is not where this expects it');
  const body = SHELL_JS.slice(from, to + 4);
  const sandbox = {Object, JSON, console, encodeURIComponent,
                   location: Object.assign({pathname}, location)};
  vm.createContext(sandbox);
  vm.runInContext(`${body}\nglobalThis.__toLogin = toLogin;`, sandbox);
  return sandbox.__toLogin;
}

suite.push(() => check('a panel with no session re-opens its own link, not a sign-in page', () => {
  // Sessions live in memory on the host, so an OmniSuite restart ends every
  // one of them without anything having been revoked. A wall panel that met
  // that with /login was unusable until somebody walked over and reloaded it
  // -- and it carried the bearer token into a `next` parameter on the way.
  const calls = [];
  const toLogin = liftLostSession('/access/SOME-TOKEN', {
    replace: () => {
      throw new Error('a panel was sent to a sign-in page it cannot answer');
    },
    reload: () => { calls.push('reload'); },
  });
  toLogin();
  assert(calls.length === 1, 'the panel did not re-open its own address');
}));

suite.push(() => check('an ordinary page still goes to the sign-in page', () => {
  // The recovery is scoped to /access/. A password user whose session ended
  // must still be asked to sign in.
  let sentTo = null;
  const toLogin = liftLostSession('/user', {
    replace: (url) => { sentTo = url; },
    reload: () => { throw new Error('a password page reloaded instead'); },
  });
  toLogin();
  assert(sentTo && sentTo.indexOf('/login?next=') === 0,
    `it went to ${JSON.stringify(sentTo)}`);
}));

suite.push(() => check('the bearer token is never put into a sign-in URL', () => {
  // The old path carried it in `next=`, which is a credential moved somewhere
  // new by the very code meant to recover from losing it.
  const source = code(SHELL_JS);
  const fn = source.slice(source.indexOf('function toLogin('));
  const body = fn.slice(0, fn.indexOf('\n  }'));
  assert(/onAccessUrl\(\)/.test(body),
    'toLogin does not distinguish a panel from an ordinary page');
  assert(body.indexOf('location.reload()') < body.indexOf('/login?next='),
    'a panel reaches the sign-in branch before the reload one');
}));


(async function run() {
  for (const scenario of suite) await scenario();
  results.forEach(([status, name]) => console.log(`${status} ${name}`));
  console.log(`\n${results.length - failures}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
}());

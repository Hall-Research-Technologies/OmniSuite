// The administrator's shell: a way back, and a way out.
//
// Measured live, every administrative surface already carries a visible link
// to the application -- so the injected one is a safety net for the page
// somebody adds next rather than a fix for a page that is broken today. That
// only means something if the net is tested, which live measurement cannot do:
// there is no broken page to point it at. So these build one.
//
// The other half is what it must NOT do. The matrix pages own a tab bar whose
// first entry IS the application, and a second control beside it would be two
// things pointing at one place.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');

function read(...parts) {
  return fs.readFileSync(path.join(ROOT, ...parts), 'utf8')
    .split('\r\n').join('\n');
}

const NAV_JS = read('ui', 'adminnav.js');

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

// ------------------------------------------------------------- a tiny DOM ---
// Enough for the module: elements, one query, and geometry it can read.
function makeDom(options) {
  options = options || {};
  const byId = new Map();
  const links = options.links || [];

  function element(tag) {
    const node = {
      tagName: tag, id: '', href: '', textContent: '', style: {},
      children: [], parentNode: null, _attrs: {},
      appendChild(child) { child.parentNode = node; node.children.push(child); return child; },
      insertBefore(child, before) {
        child.parentNode = node;
        const at = node.children.indexOf(before);
        node.children.splice(at < 0 ? node.children.length : at, 0, child);
        return child;
      },
      addEventListener() {},
      getAttribute(name) { return name === 'href' ? node.href : (node._attrs[name] ?? null); },
      setAttribute(name, value) { node._attrs[name] = value; },
      getBoundingClientRect() {
        return node._rect || {width: 100, height: 20, top: 0, right: 100, left: 0};
      },
    };
    return node;
  }

  const header = element('header');
  const created = [];
  const document = {
    head: element('head'),
    documentElement: element('html'),
    body: element('body'),
    readyState: 'complete',
    createElement(tag) { const n = element(tag); created.push(n); return n; },
    getElementById(id) { return byId.get(id) || null; },
    querySelector() { return header; },
    querySelectorAll(selector) {
      return selector === 'a[href]' ? links : [];
    },
    addEventListener() {},
  };
  if (options.gear) {
    const gear = element('button');
    gear.id = 'header_gear';
    gear.parentNode = header;
    header.children.push(gear);
    byId.set('header_gear', gear);
  }
  const sandbox = {
    document, Promise, Object, String, console,
    window: {addEventListener() {}, location: {replace() {}}},
    fetch: () => Promise.resolve({ok: true}),
    sessionStorage: {removeItem() {}},
  };
  sandbox.window.document = document;
  sandbox.window.omniSession = options.session
    ? () => Promise.resolve(options.session) : undefined;
  sandbox.window.fetch = sandbox.fetch;
  return {sandbox, document, header, byId, created,
          register: (node) => { if (node.id) byId.set(node.id, node); }};
}

function run(dom) {
  vm.createContext(dom.sandbox);
  vm.runInContext(NAV_JS, dom.sandbox);
  // The module looks things up by id after creating them; mirror that.
  dom.created.forEach(dom.register);
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function link(href, text, rect) {
  return {
    getAttribute: (n) => (n === 'href' ? href : null),
    id: '', textContent: text,
    getBoundingClientRect: () => rect || {width: 90, height: 18, top: 10,
                                          right: 90, left: 0},
  };
}

const suite = [];

suite.push(() => check('a page with no way back is given one', async () => {
  const dom = makeDom({links: []});
  await run(dom);
  const back = dom.created.find((n) => n.id === 'omni_back');
  assert(back, 'no way back was injected');
  assert(back.href === '/', `it points at ${back.href}`);
  assert(/Back to OmniSuite/.test(back.textContent), back.textContent);
}));

suite.push(() => check('a page that owns one is left alone', async () => {
  // The matrix pages own a tab bar whose first entry is the application.
  const dom = makeDom({links: [link('/', 'Device Info')]});
  await run(dom);
  assert(!dom.created.some((n) => n.id === 'omni_back'),
    'a second control was added beside one the page already owns');
}));

suite.push(() => check('a logo linking home does not count as a way back', async () => {
  // An image wrapped in a link has no text, and nobody reads it as navigation.
  const dom = makeDom({links: [link('/', '   ')]});
  await run(dom);
  assert(dom.created.some((n) => n.id === 'omni_back'),
    'an unlabelled logo was mistaken for a way back');
}));

suite.push(() => check('a hidden link does not count either', async () => {
  const dom = makeDom({links: [
    link('/', 'Device Info', {width: 0, height: 0, top: 0, right: 0, left: 0})]});
  await run(dom);
  assert(dom.created.some((n) => n.id === 'omni_back'),
    'a zero-sized link was counted as a visible way back');
}));

suite.push(() => check('the way back does not depend on being signed in', async () => {
  // The local operator has no session at all and needs one just as much.
  const dom = makeDom({links: [], session: {local: true, username: null}});
  await run(dom);
  assert(dom.created.some((n) => n.id === 'omni_back'),
    'the local operator was left with no way back');
  assert(!dom.created.some((n) => n.id === 'omni_signout'),
    'the local operator was offered a sign-out with no session to end');
}));

suite.push(() => check('a remote administrator gets both', async () => {
  const dom = makeDom({links: [],
                       session: {local: false, username: 'principal'}});
  await run(dom);
  assert(dom.created.some((n) => n.id === 'omni_back'), 'no way back');
  assert(dom.created.some((n) => n.id === 'omni_signout'), 'no way out');
}));

suite.push(() => check('loading it twice changes nothing', async () => {
  const dom = makeDom({links: []});
  await run(dom);
  const first = dom.created.filter((n) => n.id === 'omni_back').length;
  vm.runInContext(NAV_JS, dom.sandbox);
  await new Promise((r) => setTimeout(r, 0));
  const after = dom.created.filter((n) => n.id === 'omni_back').length;
  assert(first === 1 && after === 1,
    `injected ${first} then ${after}; a guard is missing`);
}));

// ------------------------------------------------------- every page has one ---
suite.push(() => check('every administrative page loads the shell', () => {
  ['ui/index.html', 'ui/matrix/index.html', 'ui/matrix/configure.html',
   'ui/matrix/usb.html', 'ui/matrix/multiview.html', 'ui/admin/users.html']
    .forEach((page) => {
      const html = read(...page.split('/'));
      assert(/<script src="\/ui\/adminnav\.js"><\/script>/.test(html),
        `${page} does not load the administrator's shell`);
    });
}));

suite.push(() => check('the restricted shell does not', () => {
  // Administrative navigation must never be offered to a User or a Direct
  // Access session, and the restricted shell owns its own header.
  const html = read('ui', 'user', 'index.html');
  assert(!/adminnav\.js/.test(html),
    'the restricted shell loads administrative navigation');
  assert(/id="signout"/.test(html),
    'the restricted shell should still own its own sign out');
}));

suite.push(() => check('Manage users can reach the rest of the application', () => {
  // Measured before this: it had a way back and nothing else, so an
  // administrator wanting the USB Matrix had to go back and navigate again.
  const html = read('ui', 'admin', 'users.html');
  ['/', '/matrix', '/matrix/usb', '/matrix/multiview'].forEach((href) => {
    assert(html.includes(`href="${href}"`),
      `Manage users offers no way to ${href}`);
  });
}));

suite.push(() => check('signing out is still what it was', () => {
  const body = code(NAV_JS);
  assert(/\/api\/auth\/logout/.test(body));
  assert(/omniForgetCsrf/.test(body),
    'a token belonging to a dead session must not survive');
  assert(/session\.local/.test(body),
    'the local trust model must be untouched');
}));

(async function run2() {
  for (const scenario of suite) await scenario();
  results.forEach(([status, name]) => console.log(`${status} ${name}`));
  console.log(`\n${results.length - failures}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
}());

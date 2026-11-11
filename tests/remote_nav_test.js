// The Remote Access entry in the primary navigation: executed, not read.
//
// It exists while Remote Access is on and is absent while it is off -- navigation
// that leads to accounts nobody can sign in with has no function. The page that
// IS the entry keeps it while it is open. If the state cannot be read, the bar is
// left exactly as it is.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const NAV_JS = read('ui', 'adminnav.js');
const USERS_HTML = read('ui', 'admin', 'users.html');
const INDEX_HTML = read('ui', 'index.html');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]); });
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }

function element(tag) {
  const node = {
    tagName: tag, id: '', href: '', className: '', textContent: '', style: {},
    dataset: {}, children: [], parentNode: null, _attrs: {},
    appendChild(child) { child.parentNode = node; node.children.push(child); return child; },
    insertBefore(child, before) {
      child.parentNode = node;
      const at = node.children.indexOf(before);
      node.children.splice(at < 0 ? node.children.length : at, 0, child);
      return child;
    },
    remove() {
      if (node.parentNode) {
        node.parentNode.children = node.parentNode.children.filter((c) => c !== node);
        node.parentNode = null;
      }
    },
    addEventListener() {},
    getAttribute(name) { return name === 'href' ? node.href : (node._attrs[name] ?? null); },
    setAttribute(name, value) { node._attrs[name] = value; },
    getBoundingClientRect() { return {width: 100, height: 20}; },
    querySelector(sel) {
      const wanted = /a\[data-nav="remote"\]/.test(sel);
      return wanted ? (node.children.find((c) => c.dataset && c.dataset.nav === 'remote') || null) : null;
    },
  };
  return node;
}

function run(opts) {
  const tabs = element('div');
  const labels = ['Device Info', 'Configure', 'A/V Matrix', 'USB Matrix', 'Multiview'];
  labels.forEach((t, i) => { const a = element('a'); a.textContent = t; a.href = '/x' + i; tabs.appendChild(a); });
  if (opts.static) {
    const a = element('a');
    a.textContent = 'Remote Access'; a.href = '/admin/users'; a.dataset.nav = 'remote';
    if (opts.current) a.setAttribute('aria-current', 'page');
    tabs.appendChild(a);
  }
  const fetches = [];
  const document = {
    head: element('head'), documentElement: element('html'), body: element('body'),
    readyState: 'complete',
    createElement: element,
    getElementById: () => null,
    querySelector: (sel) => {
      if (/omni-remote-access/.test(sel)) {
        return opts.served === undefined ? null
          : {getAttribute: () => (opts.served ? '1' : '0')};
      }
      return /\.tabs/.test(sel) ? (opts.noTabs ? null : tabs) : null;
    },
    querySelectorAll: () => [],
    addEventListener() {},
  };
  const window = {addEventListener() {}};
  const sandbox = {
    window, document, location: {replace() {}}, sessionStorage: {removeItem() {}},
    fetch: async (url) => {
      fetches.push(url);
      if (opts.fail) throw new Error('offline');
      return {ok: opts.ok !== false, json: async () => ({enabled: opts.enabled})};
    },
    Promise, JSON, Object, Array, console,
  };
  vm.createContext(sandbox);
  vm.runInContext(NAV_JS, sandbox);
  // What the bar held the instant the script finished, before any request
  // could have answered: that is what the first painted frame shows.
  const first = tabs.children.map((c) => c.textContent);
  return new Promise((resolve) => setTimeout(() => resolve({tabs, fetches, first}), 20));
}
const names = (tabs) => tabs.children.map((c) => c.textContent);

const suite = [];
suite.push(() => check('Remote Access is added to the bar while it is on', async () => {
  const {tabs} = await run({enabled: true});
  assert(names(tabs).join('|') === 'Device Info|Configure|A/V Matrix|USB Matrix|Multiview|Remote Access', names(tabs));
  assert(tabs.children[5].href === '/admin/users');
}));
suite.push(() => check('it is not added while Remote Access is off', async () => {
  const {tabs} = await run({enabled: false});
  assert(!names(tabs).includes('Remote Access'));
}));
suite.push(() => check('it is added once, however often the page asks', async () => {
  const {tabs} = await run({enabled: true, static: true, current: false});
  assert(names(tabs).filter((n) => n === 'Remote Access').length === 1);
}));
suite.push(() => check('a stale entry is removed when Remote Access has been turned off', async () => {
  const {tabs} = await run({enabled: false, static: true, current: false});
  assert(!names(tabs).includes('Remote Access'));
}));
suite.push(() => check('the page that IS the entry keeps it while open, even when off', async () => {
  const {tabs} = await run({enabled: false, static: true, current: true});
  assert(names(tabs).includes('Remote Access'));
}));
suite.push(() => check('an unreadable state leaves the bar exactly as it was', async () => {
  const {tabs: a} = await run({fail: true});
  assert(!names(a).includes('Remote Access'));
  const {tabs: b} = await run({fail: true, static: true, current: false});
  assert(names(b).includes('Remote Access'), 'unknown must not remove it');
  const {tabs: c} = await run({ok: false, enabled: true});
  assert(!names(c).includes('Remote Access'));
}));
suite.push(() => check('a page with no navigation bar is left alone and makes no request', async () => {
  const {fetches} = await run({enabled: true, noTabs: true});
  assert(!fetches.includes('/api/remote_access'));
}));
suite.push(() => check('the state is one read of local state, never a device', async () => {
  const {fetches} = await run({enabled: true});
  assert(fetches.filter((u) => u === '/api/remote_access').length === 1);
}));

suite.push(() => check('served on: the tab is in the first frame and nothing is asked', async () => {
  const {tabs, fetches, first} = await run({served: true, enabled: false, fail: true});
  assert(first.includes('Remote Access'), 'it must not wait for a request: ' + first);
  assert(names(tabs).filter((n) => n === 'Remote Access').length === 1);
  assert(!fetches.includes('/api/remote_access'), 'the page already said');
}));
suite.push(() => check('served on: a failing or disagreeing read never removes it', async () => {
  for (const o of [{fail: true}, {enabled: false}, {ok: false}]) {
    const {tabs} = await run(Object.assign({served: true}, o));
    assert(names(tabs).includes('Remote Access'), JSON.stringify(o));
  }
}));
suite.push(() => check('served off: no tab and no request', async () => {
  const {tabs, fetches} = await run({served: false, enabled: true});
  assert(!names(tabs).includes('Remote Access'));
  assert(fetches.length === 0);
}));
suite.push(() => check('the tab stays last, so nothing before it can move', async () => {
  const {tabs, first} = await run({served: true});
  assert(first[first.length - 1] === 'Remote Access');
  assert(names(tabs).slice(0, 5).join('|') === 'Device Info|Configure|A/V Matrix|USB Matrix|Multiview');
}));

suite.push(() => check('the Remote Access page carries the same primary navigation and gear', () => {
  const html = USERS_HTML;
  ['Device Info', 'Configure', 'A/V Matrix', 'USB Matrix', 'Multiview', 'Remote Access'].forEach((label) => {
    assert(html.includes('>' + label + '</a>'), label + ' is missing from the page navigation');
  });
  assert(/id="header_gear"/.test(html), 'the Settings gear is missing');
  assert(/aria-current="page"[^>]*data-nav="remote"|data-nav="remote"[^>]*aria-current="page"/.test(html));
  assert(!/Back to OmniSuite/.test(html), 'the old text link should be gone');
  const order = ['/ui/csrf.js', '/ui/modal-stack.js', '/ui/adminnav.js'].map((s) => html.indexOf(s));
  assert(order[0] > 0 && order[0] < order[1] && order[1] < order[2]);
  assert(html.includes('/ui/settings.js'), 'Settings must be available on this page');
}));
suite.push(() => check('the main pages do not hard-code a Remote Access entry', () => {
  assert(!/data-nav="remote"/.test(INDEX_HTML), 'the entry belongs to the module so it follows the setting');
}));

(async () => {
  for (const run of suite) await run();
  results.forEach(([s, l]) => console.log(`${s} ${l}`));
  console.log(`${suite.length - failures}/${suite.length} checks passed`);
  process.exit(failures ? 1 : 0);
})();

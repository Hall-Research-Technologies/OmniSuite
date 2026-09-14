// Light and dark have one owner, and every page uses it.
//
// The regression this guards: appearance.js applied the layout, the palette and
// the light background, but only *followed* the mode, leaving each page to
// implement its own toggle. Three pages did that three different ways, and two
// of them bound #dark_switch / #dark_mode_toggle / #header_dark_label -- controls
// that had been removed from every page. The result was a shared Settings
// dialog whose Theme select worked on Device Info and nowhere else.
//
// ui/appearance.js is executed for real here, once per page path, against a
// scripted DOM and storage. fetch is a local recorder, so this suite cannot
// reach a device, a server or the network.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const APPEARANCE = path.join(ROOT, 'ui', 'appearance.js');
const SETTINGS = path.join(ROOT, 'ui', 'settings.js');

const PAGES = [
  ['Device Info', 'ui/index.html', '/'],
  ['Configure', 'ui/matrix/configure.html', '/matrix/configure'],
  ['A/V Matrix', 'ui/matrix/index.html', '/matrix'],
  ['USB Matrix', 'ui/matrix/usb.html', '/matrix/usb'],
];

let failures = 0;
const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push(['ok  ', name]);
  } catch (err) {
    failures++;
    results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]);
  }
}
function assert(condition, message) {
  if (!condition) throw new Error(message);
}
function equal(actual, expected, message) {
  if (actual !== expected) {
    throw new Error(`${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// ---- a DOM small enough to read, real enough to drive ----------------------
function makeClassList() {
  const set = new Set();
  return {
    _set: set,
    add: (...c) => c.forEach(x => set.add(x)),
    remove: (...c) => c.forEach(x => set.delete(x)),
    contains: c => set.has(c),
    toggle: (c, force) => {
      const want = force === undefined ? !set.has(c) : !!force;
      if (want) set.add(c); else set.delete(c);
      return want;
    },
  };
}

function makeNode() {
  const node = {
    classList: makeClassList(),
    attributes: {},
    _styles: {},
    style: {
      setProperty: (k, v) => { node._styles[k] = v; },
      removeProperty: k => { delete node._styles[k]; },
      getPropertyValue: k => node._styles[k] || '',
    },
    setAttribute(k, v) { node.attributes[k] = String(v); },
    getAttribute(k) { return k in node.attributes ? node.attributes[k] : null; },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
  };
  return node;
}

// One environment per page, so a page path cannot leak between cases.
function load(pathname, {stored = {}, withBody = true, prefs = null} = {}) {
  const root = makeNode();
  const body = withBody ? makeNode() : null;
  const storage = new Map(Object.entries(stored));
  const listeners = {};
  const observers = [];
  const fetches = [];

  const sandbox = {
    console: {error: () => {}, warn: () => {}, log: () => {}},
    location: {pathname},
    document: {
      documentElement: root,
      get body() { return body; },
      readyState: 'complete',
      addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
      querySelector: () => null,
      querySelectorAll: () => [],
    },
    localStorage: {
      getItem: k => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
      removeItem: k => storage.delete(k),
    },
    window: {
      addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    },
    // A recorder. Nothing here opens a socket.
    fetch: async (url, opts) => {
      fetches.push({url, opts});
      if (String(url).includes('/api/ui_preferences') && (!opts || !opts.method || opts.method === 'GET')) {
        return {ok: true, status: 200, json: async () => (prefs || {ok: false})};
      }
      return {ok: true, status: 200, json: async () => ({ok: true})};
    },
    // Constructing one would be a regression; record any attempt.
    MutationObserver: function MutationObserverStub() {
      observers.push(true);
      return {observe: () => {}, disconnect: () => {}};
    },
    setTimeout, clearTimeout,
  };
  sandbox.globalThis = sandbox;
  sandbox.window.document = sandbox.document;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(APPEARANCE, 'utf8'), sandbox, {filename: 'appearance.js'});
  return {sandbox, root, body, storage, listeners, observers, fetches};
}

const isLight = env => ({
  html: env.root.classList.contains('light'),
  body: env.body ? env.body.classList.contains('light') : null,
  stored: env.storage.get('dark'),
});

// ---- 1. the mode is selectable from every page -----------------------------
for (const [name, , pathname] of PAGES) {
  check(`${name}: Light applies and is stored`, async () => {
    const env = load(pathname, {stored: {dark: 'true'}});
    await env.sandbox.setTheme('light');
    const state = isLight(env);
    equal(state.html, true, 'html.light');
    equal(state.body, true, 'body.light');
    equal(state.stored, 'false', 'stored mode');
    equal(env.sandbox.appearance.theme, 'light', 'appearance.theme');
  });

  check(`${name}: Dark applies and is stored`, async () => {
    const env = load(pathname, {stored: {dark: 'false'}});
    equal(isLight(env).html, true, 'starts light from storage');
    await env.sandbox.setTheme('dark');
    const state = isLight(env);
    equal(state.html, false, 'html.light');
    equal(state.body, false, 'body.light');
    equal(state.stored, 'true', 'stored mode');
  });

  check(`${name}: a stored Light is applied on load`, async () => {
    // This is what navigation and refresh actually do: a new document reading
    // the stored preference.
    const env = load(pathname, {stored: {dark: 'false'}});
    equal(isLight(env).html, true, 'html.light after load');
    equal(isLight(env).body, true, 'body.light after load');
  });
}

// ---- 2. the mode survives the things that used to lose it ------------------
check('the server reconcile does not reset the mode', async () => {
  // /api/ui_preferences carries layout, preset and background -- not the mode.
  // Applying its reply must not drop the operator back to dark.
  const env = load('/matrix/usb', {stored: {dark: 'false'}});
  await env.sandbox.loadAppearance();
  equal(isLight(env).html, true, 'still light after the server reply');
  equal(env.sandbox.appearance.theme, 'light', 'theme kept');
});

check('a server reply carrying a layout keeps the chosen mode', async () => {
  const env = load('/matrix', {
    stored: {dark: 'false'},
    prefs: {ok: true, template: 'compact', preset: 'ocean', light_background: '#eef2f5'},
  });
  await env.sandbox.loadAppearance();
  equal(env.sandbox.appearance.template, 'compact', 'template from server');
  equal(env.sandbox.appearance.preset, 'ocean', 'preset from server');
  equal(env.sandbox.appearance.theme, 'light', 'mode is still the local choice');
  equal(isLight(env).html, true, 'html.light');
});

check('another tab changing the mode is followed', async () => {
  const env = load('/matrix/configure', {stored: {dark: 'true'}});
  equal(isLight(env).html, false, 'starts dark');
  env.storage.set('dark', 'false');
  const onStorage = (env.listeners.storage || []);
  assert(onStorage.length >= 1, 'a storage listener is registered');
  onStorage.forEach(fn => fn({key: 'dark', newValue: 'false'}));
  equal(isLight(env).html, true, 'follows the other tab');
});

check('the mode is applied before <body> exists', async () => {
  // appearance.js runs in <head> on the matrix pages. The canvas is painted
  // from <html>, so the root element must be right on the first frame.
  const env = load('/matrix/usb', {stored: {dark: 'false'}, withBody: false});
  equal(env.root.classList.contains('light'), true, 'html.light with no body yet');
});

// ---- 3. reset ---------------------------------------------------------------
check('Reset Appearance returns to dark and says so in storage', async () => {
  const env = load('/matrix/usb', {stored: {dark: 'false'}});
  await env.sandbox.resetUiPreferences();
  equal(env.root.classList.contains('light'), false, 'html back to dark');
  equal(env.body.classList.contains('light'), false, 'body back to dark');
  equal(env.storage.get('dark'), 'true', 'stored mode is dark');
  equal(env.sandbox.appearance.template, 'classic', 'layout default');
  equal(env.sandbox.appearance.preset, 'default', 'preset default');
});

check('Reset Appearance from every page behaves the same', async () => {
  for (const [name, , pathname] of PAGES) {
    const env = load(pathname, {stored: {dark: 'false'}});
    await env.sandbox.resetUiPreferences();
    equal(env.storage.get('dark'), 'true', `${name}: stored mode`);
    equal(env.root.classList.contains('light'), false, `${name}: html`);
  }
});

// ---- 4. no observer, no timer, no accumulation ------------------------------
check('no MutationObserver is created', async () => {
  // The module used to watch <body> for a class change so it could follow the
  // page's own toggle. It sets the class now, so there is nothing to observe.
  for (const [name, , pathname] of PAGES) {
    const env = load(pathname);
    equal(env.observers.length, 0, `${name}: observers constructed`);
  }
});

check('applying the mode repeatedly registers nothing further', async () => {
  const env = load('/', {stored: {dark: 'true'}});
  const before = Object.values(env.listeners).reduce((n, list) => n + list.length, 0);
  for (let i = 0; i < 10; i += 1) {
    await env.sandbox.setTheme(i % 2 ? 'dark' : 'light');
  }
  const after = Object.values(env.listeners).reduce((n, list) => n + list.length, 0);
  equal(after, before, 'listener count after ten changes');
  equal(env.observers.length, 0, 'observers');
});

// ---- 5. the pages themselves ------------------------------------------------
check('every page loads the shared appearance and settings modules', async () => {
  for (const [name, file] of PAGES) {
    const html = fs.readFileSync(path.join(ROOT, file), 'utf8');
    assert(/src="\/ui\/appearance\.js/.test(html), `${name} loads appearance.js`);
    assert(/src="\/ui\/settings\.js/.test(html), `${name} loads settings.js`);
    assert(/href="\/ui\/settings\.css/.test(html), `${name} loads settings.css`);
  }
});

check('no page carries its own theme implementation', async () => {
  // Each of these was a separate initTheme(); two bound controls that no longer
  // exist, which is why the shared Theme select did nothing outside Device Info.
  const owners = ['ui/index.html', 'ui/matrix/matrix.js', 'ui/matrix/usb.js',
                  'ui/matrix/configure.html', 'ui/matrix/index.html', 'ui/matrix/usb.html'];
  for (const file of owners) {
    const text = fs.readFileSync(path.join(ROOT, file), 'utf8');
    assert(!/function\s+initTheme/.test(text), `${file} defines initTheme`);
    assert(!/initTheme\s*\(\s*\)/.test(text), `${file} calls initTheme`);
    for (const dead of ['dark_switch', 'dark_mode_toggle', 'header_dark_label']) {
      assert(!text.includes(dead), `${file} still references the removed ${dead}`);
    }
  }
});

check('only the shared modules write the stored mode', async () => {
  const writers = [];
  const walk = dir => {
    for (const entry of fs.readdirSync(dir, {withFileTypes: true})) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) { walk(full); continue; }
      if (!/\.(js|html)$/.test(entry.name)) continue;
      const text = fs.readFileSync(full, 'utf8');
      if (/localStorage\.setItem\(\s*(['"])dark\1/.test(text) ||
          /localStorage\.setItem\(\s*THEME_KEY/.test(text)) {
        writers.push(path.relative(ROOT, full).replace(/\\/g, '/'));
      }
    }
  };
  walk(path.join(ROOT, 'ui'));
  equal(writers.join(','), 'ui/appearance.js', 'files writing the stored mode');
});

check('the Theme select is bound once, by the module that owns the dialog', async () => {
  const settings = fs.readFileSync(SETTINGS, 'utf8');
  assert(settings.includes("id=\"cfg_theme\""), 'settings.js renders the Theme select');
  const bindings = settings.match(/themeSelect\.addEventListener\(\s*'change'/g) || [];
  equal(bindings.length, 1, 'change listeners bound to the Theme select');
  assert(/setTheme\(/.test(settings), 'settings.js calls the shared setTheme');
  // initConfig runs once per page, which is what keeps that binding single.
  const initCalls = (settings.match(/^\s*initConfig\(\);/gm) || []).length;
  equal(initCalls, 1, 'initConfig call sites');
});

check('no page-specific script binds the Theme select', async () => {
  for (const file of ['ui/index.html', 'ui/matrix/matrix.js', 'ui/matrix/usb.js',
                      'ui/matrix/usb-extenders.js']) {
    const text = fs.readFileSync(path.join(ROOT, file), 'utf8');
    assert(!text.includes('cfg_theme'), `${file} reaches into the shared Theme select`);
  }
});

check('the pre-paint scripts agree with the shared module', async () => {
  // They run before appearance.js to avoid a flash, so they must read the same
  // key and mean the same thing by it.
  for (const [name, file] of PAGES) {
    const html = fs.readFileSync(path.join(ROOT, file), 'utf8');
    assert(/localStorage\.getItem\((['"])dark\1\)\s*!==\s*(['"])false\2/.test(html),
           `${name}: pre-paint script reads the shared key the same way`);
  }
});

// ---- 6. Escape closes Settings, from one shared handler ---------------------
check('Escape is handled once, by the module that owns the dialog', async () => {
  const settings = fs.readFileSync(SETTINGS, 'utf8');
  const listeners = settings.match(/document\.addEventListener\(\s*'keydown'/g) || [];
  equal(listeners.length, 1, "document-level keydown listeners in settings.js");
  assert(/event\.key !== 'Escape'/.test(settings), 'the handler filters on Escape');
  // Registered in initConfig, which runs once per page. Binding it when the
  // dialog opens would add one listener per open.
  const initConfig = settings.slice(settings.indexOf('function initConfig()'));
  assert(initConfig.indexOf("document.addEventListener('keydown'") > -1,
         'the listener is registered inside initConfig');
});

check('no page binds Escape to Settings itself', async () => {
  for (const file of ['ui/index.html', 'ui/matrix/matrix.js', 'ui/matrix/usb.js',
                      'ui/matrix/configure.html', 'ui/matrix/index.html',
                      'ui/matrix/usb.html']) {
    const text = fs.readFileSync(path.join(ROOT, file), 'utf8');
    if (!text.includes('Escape')) continue;
    assert(!/cfg_backdrop/.test(text),
           `${file} reaches into the shared Settings backdrop`);
  }
});

check('Escape defers to whatever is above or beside Settings', async () => {
  const settings = fs.readFileSync(SETTINGS, 'utf8');
  const handler = settings.slice(settings.indexOf("document.addEventListener('keydown'"));
  const body = handler.slice(0, 900);
  // The folder browser sits above Settings and takes Escape first.
  assert(body.indexOf('cfg_browser_backdrop') < body.indexOf('closeSettings()'),
         'the folder browser is checked before Settings');
  // An acknowledgement is not dismissible by a key.
  assert(/notice_backdrop/.test(body), 'the notice is checked');
  // Any other page modal keeps its own Escape.
  assert(/foreignModalShowing\(\)/.test(body), 'foreign modals are deferred to');
  assert(body.indexOf('foreignModalShowing()') < body.indexOf('closeSettings()'),
         'foreign modals are checked before Settings closes');
});

check('every close path goes through one function', async () => {
  const settings = fs.readFileSync(SETTINGS, 'utf8');
  assert(/const closeSettings = \(\) =>/.test(settings), 'closeSettings exists');
  // The close button, the backdrop click and Escape must not drift apart.
  const calls = settings.match(/closeSettings\(\)/g) || [];
  assert(calls.length >= 2, 'closeSettings is used by more than one path');
  assert(/closeBtn\.addEventListener\('click', closeSettings\)/.test(settings),
         'the close button uses it');
});

check('closing returns focus to the control that opened the dialog', async () => {
  const settings = fs.readFileSync(SETTINGS, 'utf8');
  const close = settings.slice(settings.indexOf('const closeSettings'));
  assert(/gear\.focus\(\)/.test(close.slice(0, 400)),
         'focus returns to the gear');
});

check('the shared dialog is loaded by every page, so Escape reaches all four', async () => {
  // The behaviour is one handler in one module; what makes it true on four
  // pages is that four pages load that module. Asserted here so removing the
  // script from a page fails a test rather than silently losing the key.
  for (const [name, file] of PAGES) {
    const html = fs.readFileSync(path.join(ROOT, file), 'utf8');
    assert(/src="\/ui\/settings\.js/.test(html), `${name} loads settings.js`);
  }
});

// ---- report -----------------------------------------------------------------
setTimeout(() => {
  results.forEach(([status, name]) => console.log(`${status} ${name}`));
  if (failures) {
    console.log(`appearance theme test: ${failures} failure(s)`);
    process.exit(1);
  }
  console.log(`appearance theme test: all ${results.length} checks passed`);
}, 100);

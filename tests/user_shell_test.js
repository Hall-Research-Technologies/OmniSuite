// The restricted user shell: what it renders, and what it must never hold.
//
// Two of these guard a defect that unit tests could not see and a browser
// found immediately. `.remote-tabs` and `.action-bar` are `display: flex`, and
// a class selector outranks the `hidden` attribute's UA `display: none` -- so
// `element.hidden = true` set the property, every assertion about the property
// passed, and the element stayed on screen. A single-feature account was shown
// a navigation bar with one tab in it.
//
// So these assert on the *stylesheet* as well as on the markup: the rule that
// makes `hidden` win has to exist, and the elements that are toggled have to be
// covered by it.
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const CSS = fs.readFileSync(path.join(ROOT, 'ui', 'remote.css'), 'utf8');
const SHELL_JS = fs.readFileSync(path.join(ROOT, 'ui', 'user', 'user.js'), 'utf8');
const SHELL_HTML = fs.readFileSync(path.join(ROOT, 'ui', 'user', 'index.html'), 'utf8');
const LOGIN_HTML = fs.readFileSync(path.join(ROOT, 'ui', 'login.html'), 'utf8');
const ADMIN_JS = fs.readFileSync(path.join(ROOT, 'ui', 'admin', 'users.js'), 'utf8');

let failures = 0;
const results = [];
function check(name, fn) {
  try {
    fn();
    results.push(['ok  ', name]);
  } catch (err) {
    failures++;
    results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]);
  }
}
function assert(condition, message) {
  if (!condition) throw new Error(message || 'assertion failed');
}

// Strip comments so an explanatory sentence cannot satisfy an assertion about
// the code it explains.
function code(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((line) => line.split('//')[0]).join('\n');
}

// ---------------------------------------------------------------- hidden ---
check('the stylesheet makes the hidden attribute win', () => {
  assert(/\[hidden\]\s*\{[^}]*display:\s*none\s*!important/.test(code(CSS)),
    'without this rule, any class with a display value defeats element.hidden');
});

check('every element the shell toggles is a real element in the markup', () => {
  const toggled = [...code(SHELL_JS).matchAll(/el\('([a-z_]+)'\)\.hidden/g)]
    .map((m) => m[1]);
  const templated = [...code(SHELL_JS).matchAll(/el\('panel_' \+ ([a-z]+)\)/g)];
  assert(toggled.length > 0, 'the shell should toggle something');
  toggled.forEach((id) => {
    assert(SHELL_HTML.includes(`id="${id}"`), `#${id} is toggled but not present`);
  });
  if (templated.length) {
    ['panel_av', 'panel_usb', 'panel_multiview'].forEach((id) => {
      assert(SHELL_HTML.includes(`id="${id}"`), `#${id} is missing`);
    });
  }
});

// ----------------------------------------------------------- CSS tokens ---
// A stylesheet must not ask for a colour nobody defines. `var(--x)` with no
// fallback resolves to nothing, silently: the user-management dialog rendered
// with `background: rgba(0,0,0,0)` because settings.css asks for `var(--card)`
// and that token is declared in matrix.css, which the remote pages do not load.
// Everything else about the dialog was right, which is what made it read as a
// layout problem rather than a missing colour.
check('every token the remote pages use without a fallback is defined', () => {
  const SETTINGS_CSS = fs.readFileSync(
    path.join(ROOT, 'ui', 'settings.css'), 'utf8');
  const TEMPLATES_CSS = fs.readFileSync(
    path.join(ROOT, 'ui', 'templates.css'), 'utf8');

  // What the remote pages load, and therefore what has to be self-sufficient.
  const loaded = code(TEMPLATES_CSS) + code(SETTINGS_CSS) + code(CSS);
  const defined = new Set(
    [...loaded.matchAll(/(--[a-z0-9-]+)\s*:/g)].map((m) => m[1]));

  const missing = new Set();
  // var(--x) with no comma is a use with no fallback.
  for (const m of loaded.matchAll(/var\(\s*(--[a-z0-9-]+)\s*\)/g)) {
    if (!defined.has(m[1])) missing.add(m[1]);
  }
  assert(missing.size === 0,
    `used with no fallback and never defined: ${[...missing].join(', ')}`);
});

check('the base tokens are concrete, not aliases of what derives from them', () => {
  // templates.css derives --surface-raised from --card. Defining --card from
  // --surface-raised closes the loop, and a cyclic custom property resolves to
  // nothing -- which emptied both and left the dialog transparent again.
  const body = code(CSS);
  const cycles = [
    ['--card', '--surface-raised'],
    ['--panel-2', '--surface-elevated'],
    ['--bg', '--surface'],
    ['--border', '--edge'],
  ];
  cycles.forEach(([base, semantic]) => {
    const pattern = new RegExp(`${base}\\s*:\\s*var\\(\\s*${semantic}`);
    assert(!pattern.test(body),
      `${base} must not be defined from ${semantic}; that is a cycle`);
  });
});

// ------------------------------------------------------------ credentials ---
check('the shell never reads the session cookie', () => {
  assert(!/document\.cookie/.test(code(SHELL_JS)),
    'the session cookie is HttpOnly and must stay unreadable to the page');
  assert(!/document\.cookie/.test(code(ADMIN_JS)));
});

check('nothing is kept in localStorage by the remote pages', () => {
  [['shell', SHELL_JS], ['admin', ADMIN_JS], ['login', LOGIN_HTML]]
    .forEach(([name, source]) => {
      const body = code(source);
      // The appearance pre-paint script reads the theme flag, which is a
      // per-browser convenience and carries nothing sensitive.
      const writes = [...body.matchAll(/localStorage\.setItem\(([^)]*)\)/g)];
      assert(writes.length === 0,
        `${name} writes to localStorage: ${writes.map((m) => m[1]).join(', ')}`);
    });
});

check('the CSRF token is the only thing held, and only for the tab', () => {
  const body = code(SHELL_JS) + code(ADMIN_JS) + code(LOGIN_HTML);
  const stored = [...body.matchAll(/sessionStorage\.setItem\('([^']+)'/g)]
    .map((m) => m[1]);
  stored.forEach((key) => {
    assert(key === 'omni_csrf', `unexpected stored value: ${key}`);
  });
});

check('no password is ever stored or logged by the pages', () => {
  const body = code(SHELL_JS) + code(ADMIN_JS) + code(LOGIN_HTML);
  assert(!/(?:local|session)Storage\.setItem\([^)]*password/i.test(body),
    'a password must never be stored');
  assert(!/console\.(log|info|warn|error)\([^)]*password/i.test(body),
    'a password must never be logged');
});

// ------------------------------------------------------------ projection ---
check('the shell reads the projection, never the administrative state', () => {
  const body = code(SHELL_JS);
  assert(body.includes('/api/user/state'), 'the shell should read the projection');
  assert(!/['"]\/api\/state['"]/.test(body),
    'the administrative inventory must not be downloaded and hidden client-side');
  assert(!/['"]\/api\/cache['"]/.test(body));
});

check('every state-changing call carries the CSRF header', () => {
  [['shell', SHELL_JS], ['admin', ADMIN_JS]].forEach(([name, source]) => {
    const body = code(source);
    assert(body.includes('X-OmniSuite-CSRF'),
      `${name} must send the CSRF token on writes`);
  });
});

// -------------------------------------------------------------- no polling ---
check('the shell starts no repeating timer', () => {
  const body = code(SHELL_JS);
  assert(!/setInterval\s*\(/.test(body),
    'the host is the device-I/O authority; ten browsers must not mean ten ' +
    'times the device traffic');
});

// ---------------------------------------------------------- operational ---
// Phases G, H and I made all three capabilities operational, so the "not
// available yet" panels are gone. This is the inverse of the check that stood
// here: nothing may claim a feature is unbuilt once it is built, and nothing
// may present a control that does not work.
check('every granted feature has an operating panel', () => {
  assert(!/not available yet/i.test(SHELL_HTML),
    'USB and Multiview are operational; nothing should say otherwise');
  ['panel_av', 'panel_usb', 'panel_multiview'].forEach((id) => {
    assert(SHELL_HTML.includes(`id="${id}"`), `#${id} is missing`);
  });
});

check('USB is described as USB rather than as A/V', () => {
  // A LEX sits with a computer and a REX with the peripherals. Forcing
  // source-and-destination onto that would misdescribe the hardware.
  const usb = SHELL_HTML.slice(SHELL_HTML.indexOf('id="panel_usb"'),
                               SHELL_HTML.indexOf('id="panel_multiview"'));
  assert(/computer/i.test(usb), 'the USB panel should talk about a computer');
  assert(/USB devices|peripherals/i.test(usb),
    'the USB panel should talk about the peripherals');
  assert(!/destination/i.test(usb),
    'USB is not source-to-destination and should not say it is');
});

check('the Multiview panel operates and never administers', () => {
  const body = code(SHELL_JS);
  assert(body.includes('/api/user/multiview_show'));
  assert(body.includes('/api/user/multiview_switch'));
  // Administrative Multiview vocabulary has no place in an operator shell.
  ['/api/multiview/apply', '/api/multiview/delete', '/api/multiview/copy',
   '/api/multiview/plan', '/api/multiview/layouts',
   '/api/multiview/groups'].forEach((rule) => {
    assert(!body.includes(rule), `the shell must not call ${rule}`);
  });
});

check('no Multiview engineering vocabulary reaches the operator', () => {
  const visible = SHELL_HTML + code(SHELL_JS);
  ['ip_input', 'Encoder 2', 'Session 2', 'scaler', 'bitrate', 'multicast',
   'SAP'].forEach((term) => {
    assert(!visible.includes(term),
      `${term} is OmniSuite's concern, not a teacher's`);
  });
});

// --------------------------------------------------------------- mobile ---
check('the shell is built for a phone, not for the admin minimum width', () => {
  const css = code(CSS);
  assert(/body\.remote[^}]*min-width:\s*0/.test(css),
    'the restricted shell must not inherit the 1000px administrative minimum');
  assert(/--touch-target:\s*44px/.test(css), 'touch targets should be 44px');
  assert(/@media\s*\(max-width:\s*560px\)/.test(css),
    'there should be a phone layout');
});

check('text inputs are 16px so a phone does not zoom on focus', () => {
  assert(/\.field input\s*\{[^}]*font-size:\s*16px/.test(code(CSS)),
    'iOS zooms the page when a focused input is smaller than 16px');
});


// ------------------------------------------------- the encryption claim ---
// What the sign-in page says about THIS connection. Executed rather than read:
// a headless browser will not navigate to a self-signed certificate -- which
// is the warning the User Guide describes -- so the HTTPS branch is reached by
// running the page's own script with the protocol set.
function transportNote(protocol) {
  // login.html has two inline scripts (the theme bootstrap runs first), so
  // take the one that actually carries this, not the first one found.
  const blocks = [...LOGIN_HTML.matchAll(/<script>([\s\S]*?)<\/script>/g)]
    .map((m) => m[1]).filter((s) => s.includes('function statement('));
  assert(blocks.length === 1,
    `expected exactly one script carrying the statement, found ${blocks.length}`);
  const inline = [null, blocks[0]];
  const note = {
    id: 'transport_note', textContent: '', hidden: true, className: '',
    classList: {add(name) { note.className = (note.className + ' ' + name).trim(); }},
  };
  const document_ = {
    getElementById(id) { return id === 'transport_note' ? note : null; },
    addEventListener() {},
  };
  const window_ = {location: {protocol: protocol, pathname: '/login', search: ''}};
  // Only the statement runs; the rest of the page needs elements this shim
  // deliberately does not provide, and stops at the first one it cannot find.
  const body = inline[1];
  const start = body.indexOf('(function statement()');
  assert(start >= 0, 'the statement should be its own function');
  const end = body.indexOf('}());', start) + '}());'.length;
  const fn = new Function('document', 'window', body.slice(start, end));
  fn(document_, window_);
  return note;
}

check('over HTTPS the page says the connection is encrypted', () => {
  const note = transportNote('https:');
  assert(note.hidden === false, 'the note must be shown');
  assert(/This connection is encrypted\./.test(note.textContent),
    `said: ${note.textContent}`);
  assert(!/not encrypted/.test(note.textContent),
    'it must not claim the opposite over TLS');
  assert(/form-note-ok/.test(note.className), 'it should carry the ok state');
});

check('over plain HTTP the page says so, and says what to do about it', () => {
  const note = transportNote('http:');
  assert(note.hidden === false, 'the note must be shown');
  assert(/This connection is not encrypted\./.test(note.textContent),
    `said: ${note.textContent}`);
  assert(/network you trust/.test(note.textContent),
    'a warning with no advice is half a warning');
  assert(/form-note-warn/.test(note.className), 'it should carry the warn state');
});

check('no verdict is baked into the served markup', () => {
  const tag = LOGIN_HTML.match(/<p[^>]*id="transport_note"[^>]*>([\s\S]*?)<\/p>/);
  assert(tag, 'the note element should exist');
  assert(tag[1].trim() === '', 'the element must be empty until the page decides');
  assert(/id="transport_note"[^>]*hidden/.test(LOGIN_HTML),
    'and hidden, so it cannot flicker from one claim to the other');
});

check('the two states differ by a rule, not only by a hue', () => {
  const css = code(CSS);
  assert(/\.form-note-ok\b/.test(css) && /\.form-note-warn\b/.test(css),
    'both states need styles or the distinction is invisible');
  assert(/border-left:\s*3px solid/.test(css),
    'carry the distinction with an edge as well as a colour');
  assert(/\.form-note-ok\s*\{[^}]*--state-success/.test(css)
      && /\.form-note-warn\s*\{[^}]*--state-warning/.test(css),
    'use the shared state tokens, not literal colours');
});

// --------------------------------------------------------------- report ---
results.forEach(([status, name]) => console.log(`${status} ${name}`));
if (failures) {
  console.log(`\nuser shell test: ${failures} failure(s)`);
  process.exit(1);
}
console.log(`\nuser shell test: all ${results.length} checks passed`);

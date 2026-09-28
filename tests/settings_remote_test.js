// Remote Access in Settings: what it says about the connection, and where the
// sign-in page is willing to send a browser afterwards.
//
// Two defects are fenced here, and both were invisible to assertions about
// properties:
//
//  1. `.cfg-warning { display: flex }` outranks the user agent's rule for the
//     `hidden` attribute. So `warning.hidden = true` set the property, every
//     assertion about the property passed, and the plaintext warning stayed on
//     screen -- over an encrypted listener, telling the operator their HTTPS
//     connection was not encrypted. The stylesheet has to restate the rule, and
//     that is asserted here as well as the behaviour.
//
//  2. The sign-in page sent every account to the restricted shell, so an
//     administrator signing in remotely landed on the User screen.
//
// The notice function is executed rather than read. It is lifted out of
// settings.js and run against a DOM small enough to be obviously correct, so
// "the warning is not shown under HTTPS" is a measurement.
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
// Read with line endings normalised. These files are CRLF on Windows and LF
// elsewhere, and a slice looking for a newline has to mean the same thing in
// both.
function read(...parts) {
  return fs.readFileSync(path.join(ROOT, ...parts), 'utf8')
    .split('\r\n').join('\n');
}

const SETTINGS_JS = read('ui', 'settings.js');
const SETTINGS_CSS = read('ui', 'settings.css');
const LOGIN_HTML = read('ui', 'login.html');

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

// Comments are stripped before any assertion about source, so an explanatory
// sentence naming the very selector under test cannot satisfy it.
function code(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .split('\n').map((line) => line.split('//')[0]).join('\n');
}

// ------------------------------------------------------------ a tiny DOM ---
// Only what renderTransportNotice touches. Anything it reaches for that is not
// here throws, which is the point: a silent undefined would let a broken
// function pass.
function makeDom(ids) {
  const nodes = new Map();
  function element(id) {
    return {
      id,
      hidden: false,
      children: [],
      textContent: '',
      replaceChildren() { this.children = []; },
      append(...parts) {
        parts.forEach((p) => this.children.push(
          typeof p === 'string' ? p : p.textContent));
      },
      get text() { return this.children.join(''); },
    };
  }
  ids.forEach((id) => nodes.set(id, element(id)));
  return {
    nodes,
    document: {
      getElementById: (id) => nodes.get(id) || null,
      createElement: () => element('created'),
    },
  };
}

function loadNotice() {
  const start = SETTINGS_JS.indexOf('  function renderTransportNotice(state) {');
  assert(start >= 0, 'renderTransportNotice is not where this expects it');
  const end = SETTINGS_JS.indexOf('\n  }\n', start);
  assert(end > start, 'could not find the end of renderTransportNotice');
  const body = SETTINGS_JS.slice(start, end + 4);
  // eslint-disable-next-line no-new-func
  return new Function('document', `${body}\nreturn renderTransportNotice;`);
}

const NOTICE_IDS = ['cfg_remote_warning_row', 'cfg_remote_warning',
                    'cfg_remote_warning_text', 'cfg_remote_secure_row',
                    'cfg_remote_secure', 'cfg_remote_secure_text'];

function renderWith(state) {
  const dom = makeDom(NOTICE_IDS);
  loadNotice()(dom.document)(state);
  return dom.nodes;
}

// --------------------------------------------------------- the stylesheet ---
check('the stylesheet makes the hidden attribute win for the warning', () => {
  const css = code(SETTINGS_CSS);
  assert(/\.cfg-warning\[hidden\]\s*\{[^}]*display:\s*none/.test(css),
    'without this rule, display:flex on .cfg-warning defeats element.hidden');
});

check('the encrypted notice is covered by the same rule', () => {
  const css = code(SETTINGS_CSS);
  assert(/\.cfg-note\[hidden\]\s*\{[^}]*display:\s*none/.test(css),
    '.cfg-note also sets display, so it needs the same rule');
});

check('the rule would actually be needed, which is why it is there', () => {
  const css = code(SETTINGS_CSS);
  assert(/\.cfg-warning\s*\{[^}]*display:\s*flex/.test(css),
    'if this class no longer sets display, say so rather than deleting the '
    + 'guard above');
});

// ------------------------------------------------------------- behaviour ---
check('under HTTP the plaintext warning is shown', () => {
  const nodes = renderWith({transport: 'http', certificate: {}});
  assert(nodes.get('cfg_remote_warning').hidden === false,
    'the warning must be shown when nothing encrypts the connection');
  assert(nodes.get('cfg_remote_warning_row').hidden === false,
    'and so must the row it sits in');
  assert(/not encrypted/.test(nodes.get('cfg_remote_warning_text').text),
    'it has to say what is wrong');
});

check('under HTTP the encrypted notice is not shown', () => {
  const nodes = renderWith({transport: 'http', certificate: {}});
  assert(nodes.get('cfg_remote_secure').hidden === true);
  assert(nodes.get('cfg_remote_secure_row').hidden === true);
});

check('under HTTPS the plaintext warning is not shown', () => {
  // The reported defect: Security said HTTPS and the page said connections
  // were not encrypted, at the same time.
  const nodes = renderWith({
    transport: 'https',
    certificate: {present: true, readable: true, self_signed: true,
                  matches_address: true},
  });
  assert(nodes.get('cfg_remote_warning').hidden === true,
    'an encrypted listener must not carry a notice saying it is not');
  assert(nodes.get('cfg_remote_warning_row').hidden === true,
    'the row goes too, or an empty label is left behind');
});

check('under HTTPS the page says traffic is encrypted', () => {
  const nodes = renderWith({
    transport: 'https',
    certificate: {present: true, readable: true, self_signed: true,
                  matches_address: true},
  });
  const said = nodes.get('cfg_remote_secure_text').text;
  assert(nodes.get('cfg_remote_secure').hidden === false);
  assert(/Traffic is encrypted/.test(said), said);
  assert(!/not encrypted/.test(said),
    'it must not manage to say the opposite in the same sentence');
});

check('a self-signed certificate explains the browser warning', () => {
  const nodes = renderWith({
    transport: 'https',
    certificate: {present: true, readable: true, self_signed: true,
                  matches_address: true},
  });
  const said = nodes.get('cfg_remote_secure_text').text;
  assert(/self-signed/.test(said), said);
  assert(/browsers will warn/i.test(said), said);
});

check('an issued certificate is not described as self-signed', () => {
  const nodes = renderWith({
    transport: 'https',
    certificate: {present: true, readable: true, self_signed: false,
                  matches_address: true},
  });
  const said = nodes.get('cfg_remote_secure_text').text;
  assert(!/self-signed/.test(said.replace('rather than self-signed', '')),
    said);
});

check('a certificate for another address is called out, not glossed over', () => {
  const nodes = renderWith({
    transport: 'https',
    certificate: {present: true, readable: true, self_signed: true,
                  matches_address: false, for_address: '198.51.100.80'},
  });
  const said = nodes.get('cfg_remote_secure_text').text;
  assert(/different address/.test(said), said);
  assert(nodes.get('cfg_remote_warning').hidden === true,
    'it is still encrypted, so the plaintext warning is still wrong');
});

check('with no certificate at all the notice says HTTPS cannot start', () => {
  const nodes = renderWith({transport: 'https', certificate: {}});
  assert(/cannot start over HTTPS/.test(
    nodes.get('cfg_remote_secure_text').text));
});

// ---------------------------------------------------- where sign-in leads ---
function destination() {
  const start = LOGIN_HTML.indexOf('  function destination(role) {');
  assert(start >= 0, 'destination() is not where this expects it');
  const end = LOGIN_HTML.indexOf('\n  }\n', start);
  assert(end > start, 'could not find the end of destination()');
  const body = LOGIN_HTML.slice(start, end + 4);
  // eslint-disable-next-line no-new-func
  return new Function('search', `
    const location = {search};
    ${body}
    return destination;`);
}

check('an administrator lands on the application, not the restricted shell', () => {
  assert(destination()('')('admin') === '/',
    'an administrator signing in remotely landed on the User screen');
});

check('a restricted user lands on the restricted shell', () => {
  assert(destination()('')('user') === '/user');
  assert(destination()('')(undefined) === '/user',
    'an unknown role gets the least privileged landing');
});

check('where the browser was going is honoured', () => {
  assert(destination()('?next=%2Fmatrix%2Fusb')('admin') === '/matrix/usb');
  assert(destination()('?next=%2Fuser')('user') === '/user');
  assert(destination()('?next=%2Fhelp')('user') === '/help',
    'the User Guide is reachable by anybody signed in');
});

check('a restricted account is not sent where it will be refused', () => {
  // Found live: opening the advertised address sends a User to
  // /login?next=/ and, without this, straight back to / after signing in --
  // the administrator's application, which the gate refuses. Landing on a
  // refusal having just typed a correct password reads as a wrong password.
  ['%2F', '%2Fmatrix', '%2Fmatrix%2Fusb', '%2Fadmin%2Fusers'].forEach((next) => {
    const landed = destination()(`?next=${next}`)('user');
    assert(landed === '/user', `a User was sent to ${landed}`);
  });
  // An administrator is unaffected.
  assert(destination()('?next=%2Fmatrix')('admin') === '/matrix');
});

check('an off-site destination is refused', () => {
  // An open redirect on a sign-in page is how somebody is sent to a
  // convincing copy of it. `//host` is protocol-relative and is the one that
  // gets missed.
  const hostile = ['//evil.test', 'https://evil.test', 'http://evil.test',
                   '///evil.test', '\\\\evil.test', 'javascript:alert(1)',
                   '/\\evil.test', 'evil.test'];
  hostile.forEach((wanted) => {
    const landed = destination()(`?next=${encodeURIComponent(wanted)}`)('admin');
    assert(landed === '/', `${wanted} was accepted as a destination (${landed})`);
  });
});

check('the sign-in page does not send a signed-in browser back to itself', () => {
  assert(destination()('?next=%2Flogin')('admin') === '/',
    'a loop back to the sign-in page is not a landing');
});

// ------------------------------------------------------------- the favicon ---
check('the sign-in page asks for an icon that the build serves', () => {
  assert(/rel="icon"/.test(LOGIN_HTML), 'the page should declare its icon');
  assert(fs.existsSync(path.join(ROOT, 'ui', 'favicon.ico')),
    'the asset the page points at has to exist in the build');
});

// ----------------------------------------------------------------- report ---
results.forEach(([status, name]) => console.log(`${status} ${name}`));
console.log(`\n${results.length - failures}/${results.length} checks passed`);
process.exit(failures ? 1 : 0);

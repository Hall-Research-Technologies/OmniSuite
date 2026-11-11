/*
 * The application version's status in Settings: green when current, red when an update exists, and
 * neither when the check could not say.
 *
 * What this exists to hold:
 *  - each of the four answers gets its own state, and a failed check is never green and never red;
 *  - the status line is what is coloured, never the Version field beside it;
 *  - the colours are the derived text tokens, which state_text_contrast_test.js holds to 4.5:1 in
 *    every preset in both palettes, so this cannot reach for a raw fill and read well in one preset;
 *  - red is told apart by form as well as hue (bold, in a border), because in some presets the
 *    accent and the error colour sit close together.
 *
 * The page's own function is lifted out of ui/settings.js and driven against a model of what it
 * touches. A source-text assertion would prove the text.
 *
 * Run: node tests/version_status_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const UI = path.join(__dirname, '..', 'ui');
const JS = fs.readFileSync(path.join(UI, 'settings.js'), 'utf8').replace(/\r\n/g, '\n');
const CSS = fs.readFileSync(path.join(UI, 'settings.css'), 'utf8').replace(/\r\n/g, '\n');

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures++; console.log(`  FAIL ${name}: ${err && err.message}`); }
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
function eq(a, b, m) { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error((m || 'not equal') + `: ${JSON.stringify(a)} vs ${JSON.stringify(b)}`); }

function lift(source, startPattern) {
  const at = source.search(startPattern);
  assert(at >= 0, 'not found in settings.js: ' + startPattern);
  let i = source.indexOf('{', at), depth = 0;
  for (; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    else if (source[i] === '}') { depth -= 1; if (depth === 0) return source.slice(at, i + 1); }
  }
  throw new Error('unbalanced: ' + startPattern);
}

const elements = {};
const make = (id) => (elements[id] = {id, textContent: '', className: '', hidden: false, href: '',
  setAttribute(k, v) { this[k] = v; }, removeAttribute(k) { delete this[k]; }});
const context = {document: {getElementById: (id) => elements[id] || null}, Date, Number};
vm.createContext(context);
vm.runInContext(JS.slice(JS.indexOf('const UPDATE_STATES = {'), JS.indexOf('};', JS.indexOf('const UPDATE_STATES = {')) + 2), context);
vm.runInContext(lift(JS, /function describeAge\(/), context);
vm.runInContext(lift(JS, /function renderUpdateStatus\(/), context);

function render(data) {
  ['cfg_update_status', 'cfg_release_link', 'cfg_asset_link', 'cfg_app_version'].forEach(make);
  context.renderUpdateStatus(data);
  return elements.cfg_update_status;
}

check('current is green, and says which version is current', () => {
  const line = render({status: 'CURRENT', installed_version: 'V1.2.0', latest_version: 'V1.2.0'});
  eq(line.className, 'update-status ok');
  assert(/V1\.2\.0 is current/.test(line.textContent), line.textContent);
});

check('a build ahead of the newest release is current, so green, not an error', () => {
  eq(render({status: 'CURRENT', installed_version: 'V1.3.0', latest_version: 'V1.2.0'}).className, 'update-status ok');
});

check('a last answer that has gone stale is still green and says how old it is', () => {
  const line = render({status: 'CURRENT', installed_version: 'V1.2.0', stale: true, checked_at: '2020-01-01 00:00:00'});
  eq(line.className, 'update-status ok');
  assert(/last checked/.test(line.textContent), line.textContent);
});

check('an update being available is red', () => {
  const line = render({status: 'UPDATE_AVAILABLE', installed_version: 'V1.2.0', latest_version: 'V1.3.0', release_url: 'https://example.invalid/r'});
  eq(line.className, 'update-status bad');
  assert(/UPDATE AVAILABLE/.test(line.textContent) && /V1\.3\.0/.test(line.textContent), line.textContent);
});

check('a failed check is neither green nor red', () => {
  for (const data of [{status: 'UNABLE_TO_CHECK'}, null, undefined, {}, {status: 'SOMETHING_NEW'}]) {
    const line = render(data);
    eq(line.className, 'update-status warn', 'unknown stays amber for ' + JSON.stringify(data));
    assert(!/ok|bad/.test(line.className.replace('update-status', '')), 'never a verdict');
    assert(/Unable to check/.test(line.textContent), line.textContent);
  }
});

check('an update with no version to name is not announced, so it is not red either', () => {
  assert(render({status: 'UPDATE_AVAILABLE'}).className !== 'update-status bad', 'nothing to announce');
});

check('checking is neutral', () => {
  eq(render({status: 'CHECKING'}).className, 'update-status muted');
});

check('only the status line is coloured: nothing is ever set on the Version field', () => {
  render({status: 'UPDATE_AVAILABLE', latest_version: 'V9', installed_version: 'V1'});
  eq(elements.cfg_app_version.className, '', 'the field keeps no class');
  eq(elements.cfg_app_version.style, undefined, 'and no inline colour');
  const body = lift(JS, /function renderUpdateStatus\(/).replace(/\/\/.*$/gm, '');
  assert(!/cfg_app_version/.test(body), 'the renderer does not touch the Version field at all');
  assert(!/#cfg_app_version[^{]*\{[^}]*color/.test(CSS) && !/input[^{]*\{[^}]*state-(success|error)/.test(CSS), 'and no rule colours it');
});

function rule(selector) {
  const at = CSS.indexOf(selector + ' {') >= 0 ? CSS.indexOf(selector + ' {') : CSS.indexOf(selector + ' ');
  assert(at >= 0, 'missing rule ' + selector);
  return CSS.slice(at, CSS.indexOf('}', at));
}

check('green, red and amber are the derived text tokens, never the raw fill', () => {
  assert(/color:\s*var\(--state-success-text\)/.test(rule('.update-status.ok')), 'current uses the success text token');
  assert(/color:\s*var\(--state-error-text\)/.test(rule('.update-status.bad')), 'an update uses the error text token');
  assert(/color:\s*var\(--state-warning-text\)/.test(rule('.update-status.warn')), 'unknown uses the warning text token');
  for (const sel of ['.update-status.ok', '.update-status.bad', '.update-status.warn']) {
    assert(!/color:\s*var\(--state-(success|error|warning)\)/.test(rule(sel)), sel + ' reaches for a raw fill');
    assert(!/color:\s*#/i.test(rule(sel)), sel + ' reaches for a literal colour');
  }
});

check('red is told apart by form as well as hue', () => {
  const css = rule('.update-status.bad');
  assert(/font-weight:\s*700/.test(css), 'bold');
  assert(/border:\s*1px solid var\(--state-error-text\)/.test(css), 'in a border');
});

check('every token the status uses is defined where the page defines its tokens', () => {
  const templates = fs.readFileSync(path.join(UI, 'templates.css'), 'utf8');
  for (const token of ['--state-success-text', '--state-error-text', '--state-warning-text']) {
    assert(new RegExp(token + ':').test(templates), token + ' is not defined');
  }
});

console.log(failures ? `\nversion status: ${failures} failure(s)` : '\nversion status test: all checks passed');
process.exit(failures ? 1 : 0);

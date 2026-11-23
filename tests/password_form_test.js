// Every password field the application renders sits inside a <form>.
//
// The browser reports "[DOM] Password field is not contained in a form" for each
// one that does not (cfg_boot_pw and cfg_boot_pw2 were the ones noticed). A form
// is also what lets a password manager understand a field, and what stops it
// offering to save a device credential as a site sign-in.
//
// Two things are measured, and neither is a source-text match on one id:
//   * the shared confirmation dialog is RUN, and the markup it builds is read;
//   * the markup in Settings and the pages is scanned tag by tag.
// And the forms are inert: they do not navigate, and no field moved out of the
// element the page's own code looks it up in.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
let failures = 0;
const check = (name, fn) => {
  try { fn(); console.log('ok   ' + name); }
  catch (err) { failures++; console.log('FAIL ' + name + ': ' + (err && err.message || err)); }
};
const assert = (c, m) => { if (!c) throw new Error(m || 'assertion failed'); };

// Passwords outside any form, by walking the tags in order. Comments and
// <style> bodies are skipped, so prose that mentions the markup is not mistaken
// for it.
function passwordsOutsideForms(markup) {
  const text = markup.replace(/<!--[\s\S]*?-->/g, '').replace(/<style\b[\s\S]*?<\/style>/gi, '');
  const tag = /<\/?(form|input)\b[^>]*>/gi;
  let depth = 0, m;
  const loose = [];
  while ((m = tag.exec(text))) {
    const whole = m[0], name = m[1].toLowerCase();
    if (name === 'form') { depth += whole[1] === '/' ? -1 : 1; continue; }
    if (/type\s*=\s*["']?password\b/i.test(whole) && depth <= 0) {
      loose.push((whole.match(/id\s*=\s*["']([^"']+)/i) || [])[1] || whole.slice(0, 50));
    }
  }
  return loose;
}

// ---- the scanner has to be able to fail --------------------------------
check('the scanner flags a bare password field', () => {
  assert(passwordsOutsideForms('<div><input id="a" type="password"></div>').join() === 'a');
});
check('the scanner accepts a password field inside a form', () => {
  assert(passwordsOutsideForms('<form><div><input id="a" type="password"></div></form>').length === 0);
});
check('the scanner does not read a form that has already closed', () => {
  assert(passwordsOutsideForms('<form></form><input id="b" type="password">').join() === 'b');
});
check('the scanner ignores prose and style rules that name the attribute', () => {
  assert(passwordsOutsideForms('<!-- <input type="password"> --><style>input[type=password]{}</style>').length === 0);
});

// ---- the shared confirmation dialog, run -----------------------------------
check('the shared confirmation dialog builds its passphrase field inside a form', () => {
  let built = '';
  const el = (tag) => ({tag, style: {}, dataset: {}, children: [],
    set innerHTML(v) { built += v; }, get innerHTML() { return built; },
    appendChild() {}, setAttribute() {}, addEventListener() {}, querySelector() { return null; },
    querySelectorAll() { return []; }, classList: {add() {}, remove() {}, toggle() {}}});
  const document = {getElementById: () => null, createElement: el, head: {appendChild() {}},
    documentElement: {appendChild() {}}, body: {appendChild() {}}, addEventListener() {}};
  const window = {};
  vm.runInNewContext(read('ui', 'confirm.js'), {window, document, console, setTimeout, Promise});
  assert(typeof window.omniConfirm === 'function', 'the dialog module did not load');
  // The dialog is built on first use.
  try { window.omniConfirm({title: 't', message: 'm'}); } catch (e) { /* the stub is not a browser */ }
  assert(/type="password"/.test(built), 'the passphrase field was not built, so this measured nothing');
  assert(passwordsOutsideForms(built).length === 0, 'built outside a form: ' + passwordsOutsideForms(built));
  assert(/onsubmit="return false"/.test(built), 'the form can navigate');
});

// ---- the pages and Settings ------------------------------------------------
const files = [['ui', 'index.html'], ['ui', 'login.html'], ['ui', 'admin', 'users.html'],
               ['ui', 'settings.js'], ['ui', 'confirm.js']];
for (const parts of files) {
  check(parts.join('/') + ': every password field is inside a form', () => {
    const source = read(...parts);
    assert(/type\s*=\s*["']?password/.test(source), 'no password field here: this file is not measuring anything');
    const loose = passwordsOutsideForms(source);
    assert(loose.length === 0, 'outside a form: ' + loose.join(', '));
  });
}

// ---- inert, and nothing moved -----------------------------------------
check('every form in these files cannot submit', () => {
  for (const parts of files) {
    const source = read(...parts).replace(/<!--[\s\S]*?-->/g, '');
    const forms = source.match(/<form\b[^>]*>/gi) || [];
    for (const f of forms) {
      if (/id\s*=\s*["']login_form/.test(f)) continue;       // sign-in has its own handler
      assert(/onsubmit\s*=\s*["']return false/.test(f), parts.join('/') + ': ' + f);
      assert(/novalidate/.test(f), parts.join('/') + ' lacks novalidate: ' + f);
    }
  }
});
check('no form carries an action, so no credential can travel in a URL', () => {
  for (const parts of files) {
    const forms = read(...parts).match(/<form\b[^>]*>/gi) || [];
    for (const f of forms) assert(!/\baction\s*=/.test(f) && !/method\s*=\s*["']?get/i.test(f), f);
  }
});
check('the fields the page code looks up are still there by id', () => {
  const settings = read('ui', 'settings.js');
  for (const id of ['cfg_boot_user', 'cfg_boot_pw', 'cfg_boot_pw2', 'cfg_boot_create',
                    'cfg_password', 'cfg_fallback_password', 'cfg_pw_toggle', 'cfg_fallback_pw_toggle']) {
    assert(settings.indexOf('id="' + id + '"') >= 0, id + ' is gone');
  }
  const index = read('ui', 'index.html');
  for (const id of ['sync_primary_password', 'sync_fallback_password', 'confirm_field_input']) {
    assert(index.indexOf('id="' + id + '"') >= 0, id + ' is gone');
  }
  assert(read('ui', 'admin', 'users.html').indexOf('id="f_password"') >= 0, 'f_password is gone');
});
check('the new-password fields keep autocomplete=new-password', () => {
  const s = read('ui', 'settings.js');
  assert((s.match(/autocomplete="new-password"/g) || []).length >= 2);
  assert(/id="f_password" type="password" autocomplete="new-password"/.test(read('ui', 'admin', 'users.html')));
});

console.log(failures ? failures + ' check(s) failed' : 'all checks passed');
process.exit(failures ? 1 : 0);

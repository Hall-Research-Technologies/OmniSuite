// The shared dialog's three additions, executed: a value offered in advance,
// a line of explanation under the field, and an answer the caller can refuse
// without the dialog closing. The local port question is built from them.
//
// The Device Info page's own copy of the dialog is lifted and run; ui/confirm.js
// (used by every other page) is held to the same contract structurally, because
// it builds its markup from a string and needs a real DOM to run.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {El} = require('./_fakedom.js');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const INDEX_HTML = read('ui', 'index.html');
const CONFIRM_JS = read('ui', 'confirm.js');

let failures = 0, total = 0;
const results = [];
function check(name, fn) {
  total += 1;
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures += 1; results.push(['FAIL', name + ': ' + (err && err.message || err)]); });
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map((l) => l.split('//')[0]).join('\n');

function lift(source, signature, extras) {
  const start = source.indexOf(signature);
  assert(start >= 0, signature + ' not found');
  const end = source.indexOf('\n}\n', start);
  const name = signature.replace(/^(async )?function /, '').split('(')[0];
  const sandbox = Object.assign({Promise, JSON, Object, Array, String, console}, extras);
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source.slice(start, end + 3) + '\nglobalThis.__l = ' + name + ';', sandbox);
  return sandbox.__l;
}

function world() {
  const document = {activeElement: null, _listeners: {}};
  class Node extends El {
    constructor(tag) { super(tag); this.isConnected = true; }
    focus() { document.activeElement = this; }
    removeEventListener(type, fn) { this._listeners[type] = (this._listeners[type] || []).filter((f) => f !== fn); }
  }
  document.addEventListener = (t, fn) => { (document._listeners[t] = document._listeners[t] || []).push(fn); };
  document.removeEventListener = (t, fn) => { document._listeners[t] = (document._listeners[t] || []).filter((f) => f !== fn); };
  const ids = {};
  ['confirm_backdrop', 'confirm_title', 'confirm_message', 'confirm_summary', 'confirm_list',
   'confirm_ok', 'confirm_cancel', 'confirm_checkbox', 'confirm_checkbox_input',
   'confirm_checkbox_label', 'confirm_field', 'confirm_field_input', 'confirm_field_label']
    .forEach((id) => { ids[id] = Object.assign(new Node('div'), {id}); });
  const hint = Object.assign(new Node('span'), {className: 'confirm-field-hint'});
  const error = Object.assign(new Node('span'), {className: 'confirm-field-error'});
  ids.confirm_field.append(ids.confirm_field_label, ids.confirm_field_input, hint, error);
  return {document, ids, hint, error, $: (s) => ids[s.replace(/^#/, '')] || null};
}

const settle = () => new Promise((r) => setImmediate(r));

(async function () {
  await check('the page dialog offers the value, shows the hint, and refuses in place', async () => {
    const w = world();
    const omniConfirm = lift(INDEX_HTML, 'function omniConfirm(', {
      $: w.$, document: w.document, escapeAttr: (v) => String(v), window: {}});
    let delivered = null, settled = null;
    const promise = omniConfirm({
      title: 'Use a fixed local OmniSuite port', message: 'a\nb',
      field: {label: 'TCP port:', type: 'text', value: '28999', hint: 'Keep it.\nRestart needed.',
              validate: (v) => (/^\d+$/.test(v) ? '' : 'Digits only.'), onValue: (v) => { delivered = v; }},
      confirmText: 'Save and Restart'}).then((r) => { settled = r; return r; });
    await settle();
    assert(w.ids.confirm_field_input.value === '28999', 'not pre-filled: ' + w.ids.confirm_field_input.value);
    assert(w.ids.confirm_field_input.type === 'text');
    assert(w.hint.textContent === 'Keep it.\nRestart needed.' && w.hint.style.display === '');
    assert(w.ids.confirm_ok.textContent === 'Save and Restart');
    // an invalid answer keeps the dialog open and says why
    w.ids.confirm_field_input.value = 'abc';
    await w.ids.confirm_ok.fire('click');
    await settle();
    assert(settled === null, 'the dialog closed on an answer it should have refused');
    assert(w.ids.confirm_backdrop.style.display === 'flex', 'the dialog is no longer open');
    assert(w.error.textContent === 'Digits only.' && w.error.style.display === '', 'no reason shown');
    assert(delivered === null, 'the refused value reached the caller');
    // a valid answer closes it and is delivered
    w.ids.confirm_field_input.value = '28100';
    for (const fn of (w.document._listeners.keydown || []).slice()) {
      fn({key: 'Enter', target: w.ids.confirm_field_input, preventDefault() {}});
    }
    assert(await promise === true);
    assert(delivered === '28100');
    assert(w.ids.confirm_field_input.value === '', 'the field is wiped on the way out');
  });

  await check('a dialog with no value, hint or validator behaves as before', async () => {
    const w = world();
    const omniConfirm = lift(INDEX_HTML, 'function omniConfirm(', {
      $: w.$, document: w.document, escapeAttr: (v) => String(v), window: {}});
    const promise = omniConfirm({title: 'Passphrase', field: {label: 'Passphrase', type: 'password'}});
    await settle();
    assert(w.ids.confirm_field_input.value === '', 'a field with no value must start empty');
    assert(w.hint.style.display === 'none');
    w.ids.confirm_field_input.value = 'x';
    await w.ids.confirm_ok.fire('click');
    assert(await promise === true);
  });

  await check('the shared module carries the same three additions', async () => {
    const src = strip(CONFIRM_JS);
    assert(/field\.value\s*==\s*null\s*\?\s*''\s*:\s*String\(field\.value\)/.test(src), 'no pre-fill');
    assert(/field\.hint/.test(src) && /confirm-field-hint/.test(src), 'no hint');
    assert(/typeof field\.validate === 'function'/.test(src) && /confirm-field-error/.test(src), 'no validation');
    assert(/function commit\(\)/.test(src), 'OK and Enter must share one commit path');
    assert(/function onOk\(\)\s*\{\s*commit\(\)/.test(src), 'OK bypasses validation');
    assert(/event\.key === 'Enter' && field[\s\S]{0,120}commit\(\)/.test(src), 'Enter bypasses validation');
    assert(/white-space:pre-line/.test(CONFIRM_JS), 'a newline in a message is not a line break');
  });

  results.forEach(([s, n]) => console.log(s + ' ' + n));
  console.log((total - failures) + '/' + total + ' checks passed');
  process.exit(failures ? 1 : 0);
})();

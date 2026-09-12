// The engineering notice, and what each answer to it does.
//
// This runs ui/device-log.js for real against a scripted dialog and a recording
// fetch, so every claim below is about behaviour rather than about source text.
// window.confirm and window.alert are installed as throwing stubs: if the flow
// ever reaches a native dialog the suite fails rather than quietly passing.
//
// No network of any kind is reachable from here -- fetch is a local recorder --
// so this suite cannot touch a device.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = path.join(__dirname, '..', 'ui', 'device-log.js');

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

// ---- a DOM small enough to read and real enough to drive -------------------
function makeElement(tag) {
  const el = {
    tagName: String(tag).toUpperCase(),
    dataset: {},
    attributes: {},
    children: [],
    parentNode: null,
    textContent: '',
    disabled: false,
    title: '',
    href: '',
    download: '',
    clicked: 0,
    _classes: new Set(),
    classList: {
      add: c => el._classes.add(c),
      remove: c => el._classes.delete(c),
      contains: c => el._classes.has(c),
    },
    setAttribute(name, value) { el.attributes[name] = String(value); },
    getAttribute(name) { return name in el.attributes ? el.attributes[name] : null; },
    removeAttribute(name) { delete el.attributes[name]; },
    appendChild(child) { child.parentNode = el; el.children.push(child); return child; },
    remove() {
      if (!el.parentNode) return;
      const i = el.parentNode.children.indexOf(el);
      if (i >= 0) el.parentNode.children.splice(i, 1);
      el.parentNode = null;
    },
    click() { el.clicked++; },
    closest(selector) {
      if (selector === '.log-btn' && el._classes.has('log-btn')) return el;
      return el.parentNode && el.parentNode.closest ? el.parentNode.closest(selector) : null;
    },
  };
  return el;
}

function buildSandbox() {
  const body = makeElement('body');
  const listeners = {};
  const sandbox = {
    console,
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms || 0, 1)),
    clearTimeout,
    URL: {
      created: [],
      revoked: [],
      createObjectURL(blob) { this.created.push(blob); return 'blob:test-' + this.created.length; },
      revokeObjectURL(url) { this.revoked.push(url); },
    },
    document: {
      body,
      __deviceLogBound: false,
      createElement: makeElement,
      addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
      dispatch(type, event) { (listeners[type] || []).forEach(fn => fn(event)); },
    },
    Promise, Error, Set, Map, Object, Array, String, Number, Boolean, JSON, RegExp,
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  // Every call the flow could make to a native dialog is a failure, not a
  // fallback. Requirement: no native confirm()/alert() is involved.
  sandbox.confirm = () => { throw new Error('native confirm() was called'); };
  sandbox.alert = () => { throw new Error('native alert() was called'); };
  sandbox.prompt = () => { throw new Error('native prompt() was called'); };

  sandbox.__requests = [];
  sandbox.__fetchImpl = null;
  sandbox.fetch = (url, options) => {
    sandbox.__requests.push(url);
    return sandbox.__fetchImpl
      ? sandbox.__fetchImpl(url, options)
      : Promise.resolve(okResponse());
  };

  sandbox.__toasts = [];
  sandbox.toast = (message, kind) => sandbox.__toasts.push([String(message), kind]);

  // The dialog is scripted per test; every call is recorded so a test can prove
  // one was opened, or that none was.
  sandbox.__dialogs = [];
  sandbox.__answer = false;
  sandbox.omniConfirm = (options) => {
    sandbox.__dialogs.push(options);
    const answer = typeof sandbox.__answer === 'function'
      ? sandbox.__answer(options) : sandbox.__answer;
    return Promise.resolve(answer);
  };

  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), sandbox, {filename: 'device-log.js'});
  return sandbox;
}

function okResponse(filename) {
  return {
    ok: true,
    status: 200,
    headers: {get: name => (name === 'Content-Disposition'
      ? `attachment; filename="${filename || 'host_10.0.0.1_20260911-120000.vdf'}"` : null)},
    blob: async () => ({size: 42}),
    json: async () => ({}),
  };
}

function logButton(sandbox, ip) {
  const button = makeElement('button');
  button._classes.add('log-btn');
  button.dataset.ip = ip || '192.0.2.11';
  button.textContent = 'Log';
  sandbox.document.body.appendChild(button);
  return button;
}

const settle = () => new Promise(resolve => setTimeout(resolve, 5));

(async () => {
  // ---- 1. clicking Log opens the engineering notice ----
  await check('a Log click opens the engineering notice', async () => {
    const s = buildSandbox();
    const button = logButton(s);
    s.document.dispatch('click', {target: button});
    await settle();
    if (s.__dialogs.length !== 1) throw new Error(`opened ${s.__dialogs.length} dialog(s)`);
    const opened = s.__dialogs[0];
    if (!/encrypted/i.test(opened.message)) throw new Error(`message was ${opened.message}`);
    if (!/Engineering/.test(opened.message)) throw new Error('the notice must name Engineering');
    if (opened.confirmText !== 'OK' || opened.cancelText !== 'Cancel') {
      throw new Error(`buttons were ${opened.confirmText}/${opened.cancelText}`);
    }
    if (opened.defaultAction !== 'cancel') throw new Error('Cancel must hold the focus');
    if (opened.requireDialog !== true) throw new Error('a missing dialog must not fall back');
  });

  // ---- 2. opening it makes no request ----
  await check('opening the notice makes no backend request', async () => {
    const s = buildSandbox();
    // The dialog never answers, so the flow is parked with it open.
    s.__answer = () => new Promise(() => {});
    const button = logButton(s);
    s.document.dispatch('click', {target: button});
    await settle();
    if (s.__dialogs.length !== 1) throw new Error('the notice did not open');
    if (s.__requests.length !== 0) throw new Error(`made ${s.__requests.length} request(s)`);
  });

  // ---- 3. Cancel makes no request ----
  await check('Cancel causes zero backend requests', async () => {
    const s = buildSandbox();
    s.__answer = false;
    const button = logButton(s);
    s.document.dispatch('click', {target: button});
    await settle();
    if (s.__requests.length !== 0) throw new Error(`made ${s.__requests.length} request(s)`);
    if (s.URL.created.length !== 0) throw new Error('a download was started');
    if (button.disabled) throw new Error('the button was left disabled');
    if (button.textContent !== 'Log') throw new Error(`button reads ${button.textContent}`);
  });

  // ---- 4. Escape makes no request ----
  await check('Escape causes zero backend requests', async () => {
    const s = buildSandbox();
    // omniConfirm resolves false for Escape, for the backdrop and for Cancel
    // alike; what the flow must do with that answer is identical.
    s.__answer = () => Promise.resolve(false);
    const button = logButton(s);
    s.document.dispatch('click', {target: button});
    await settle();
    if (s.__requests.length !== 0) throw new Error(`made ${s.__requests.length} request(s)`);
    if (s.URL.created.length !== 0) throw new Error('a download was started');
  });

  // ---- 5. OK makes exactly one request ----
  await check('OK causes exactly one log request', async () => {
    const s = buildSandbox();
    s.__answer = true;
    const button = logButton(s, '192.0.2.25');
    s.document.dispatch('click', {target: button});
    await settle();
    if (s.__requests.length !== 1) throw new Error(`made ${s.__requests.length} request(s)`);
    if (s.__requests[0] !== '/api/device_log?ip=192.0.2.25') {
      throw new Error(`requested ${s.__requests[0]}`);
    }
    if (s.URL.created.length !== 1) throw new Error('no download was produced');
    if (!s.__toasts.some(([, kind]) => kind === true)) throw new Error('no success toast');
  });

  // ---- 6. repeated clicking while pending cannot duplicate ----
  await check('clicking repeatedly while pending cannot duplicate the request', async () => {
    const s = buildSandbox();
    let release;
    s.__answer = true;
    s.__fetchImpl = () => new Promise(resolve => { release = () => resolve(okResponse()); });
    const button = logButton(s);
    s.document.dispatch('click', {target: button});
    await settle();
    for (let i = 0; i < 5; i++) s.document.dispatch('click', {target: button});
    await settle();
    if (s.__requests.length !== 1) throw new Error(`made ${s.__requests.length} request(s)`);
    if (s.__dialogs.length !== 1) throw new Error(`opened ${s.__dialogs.length} dialog(s)`);
    if (!button.disabled) throw new Error('the button was not disabled while pending');
    if (button.getAttribute('aria-busy') !== 'true') throw new Error('aria-busy was not set');
    if (button.textContent === 'Log') throw new Error('the button gave no pending feedback');
    release();
    await settle();
    if (button.disabled) throw new Error('the button stayed disabled after completion');
  });

  await check('clicking repeatedly while the notice is open cannot stack dialogs', async () => {
    const s = buildSandbox();
    s.__answer = () => new Promise(() => {});
    const button = logButton(s);
    for (let i = 0; i < 4; i++) s.document.dispatch('click', {target: button});
    await settle();
    if (s.__dialogs.length !== 1) throw new Error(`opened ${s.__dialogs.length} dialog(s)`);
    if (s.__requests.length !== 0) throw new Error('a request escaped');
  });

  // ---- 7. the notice returns next time ----
  await check('after completion, clicking Log again shows the notice again', async () => {
    const s = buildSandbox();
    s.__answer = true;
    const button = logButton(s);
    s.document.dispatch('click', {target: button});
    await settle();
    s.document.dispatch('click', {target: button});
    await settle();
    if (s.__dialogs.length !== 2) throw new Error(`opened ${s.__dialogs.length} dialog(s)`);
    if (s.__requests.length !== 2) throw new Error(`made ${s.__requests.length} request(s)`);
  });

  await check('a cancelled download does not suppress the next notice', async () => {
    const s = buildSandbox();
    s.__answer = false;
    const button = logButton(s);
    s.document.dispatch('click', {target: button});
    await settle();
    s.__answer = true;
    s.document.dispatch('click', {target: button});
    await settle();
    if (s.__dialogs.length !== 2) throw new Error(`opened ${s.__dialogs.length} dialog(s)`);
    if (s.__requests.length !== 1) throw new Error(`made ${s.__requests.length} request(s)`);
  });

  await check('the notice is never remembered', async () => {
    const s = buildSandbox();
    s.__answer = true;
    const button = logButton(s);
    for (let i = 0; i < 3; i++) { s.document.dispatch('click', {target: button}); await settle(); }
    if (s.__dialogs.length !== 3) throw new Error(`opened ${s.__dialogs.length} dialog(s)`);
    const source = fs.readFileSync(SOURCE, 'utf8');
    for (const banned of ['localStorage', 'sessionStorage', "Don't show", 'dontShow', 'suppress']) {
      if (source.includes(banned)) throw new Error(`the flow persists something: ${banned}`);
    }
  });

  // ---- 8. no native dialog is involved ----
  await check('no native confirm/alert is involved on any path', async () => {
    // The sandbox's confirm/alert/prompt throw, so reaching one fails the run.
    for (const answer of [true, false]) {
      const s = buildSandbox();
      s.__answer = answer;
      const button = logButton(s);
      s.document.dispatch('click', {target: button});
      await settle();
    }
    const source = fs.readFileSync(SOURCE, 'utf8');
    const code = source.replace(/\/\/.*$/gm, '');
    for (const banned of ['confirm(', 'alert(', 'prompt(']) {
      const hits = code.split(banned).length - 1;
      const allowed = banned === 'confirm(' ? code.split('omniConfirm(').length - 1 : 0;
      if (hits - allowed > 0) throw new Error(`${banned} appears in the flow`);
    }
  });

  await check('a missing dialog refuses rather than downloading', async () => {
    const s = buildSandbox();
    s.omniConfirm = undefined;
    const button = logButton(s);
    s.document.dispatch('click', {target: button});
    await settle();
    if (s.__requests.length !== 0) throw new Error('a request was made with no dialog');
    if (!s.__toasts.some(([, kind]) => kind === 'error')) throw new Error('no error was reported');
    if (button.disabled) throw new Error('the button was left disabled');
  });

  // ---- failure handling still uses the toast ----
  await check('a failed download reports through the toast and restores the button', async () => {
    const s = buildSandbox();
    s.__answer = true;
    s.__fetchImpl = async () => ({
      ok: false, status: 502,
      headers: {get: () => null},
      json: async () => ({error: '192.0.2.11 did not return a debug log.'}),
    });
    const button = logButton(s);
    s.document.dispatch('click', {target: button});
    await settle();
    const errors = s.__toasts.filter(([, kind]) => kind === 'error');
    if (errors.length !== 1) throw new Error(`reported ${errors.length} error(s)`);
    if (!errors[0][0].includes('did not return a debug log')) {
      throw new Error(`message was ${errors[0][0]}`);
    }
    if (button.disabled || button.textContent !== 'Log') throw new Error('the button was not restored');
  });

  results.forEach(([status, name]) => console.log(`  ${status} ${name}`));
  console.log(failures
    ? `device-log test: ${failures} check(s) failed`
    : 'device-log test: all checks passed');
  process.exit(failures ? 1 : 0);
})();

// The local application port control, executed rather than read.
//
// ui/local-port.js is a factory over its dependencies, so these drive the real
// flows with a scripted dialog and a recording request function. "Cancel makes
// no request" and "the dialog is pre-filled from the actual port" are
// measurements here, not readings of the source.
//
// Scenarios share one request log and run strictly in sequence.
'use strict';
const fs = require('fs');
const path = require('path');
const {El} = require('./_fakedom.js');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const LP = require(path.join(ROOT, 'ui', 'local-port.js'));

let failures = 0, total = 0;
const results = [];
async function check(name, fn) {
  total += 1;
  try { await fn(); results.push(['ok  ', name]); }
  catch (err) { failures += 1; results.push(['FAIL', name + ': ' + (err && err.message ? err.message : err)]); }
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
function eq(a, b, m) { assert(a === b, (m || 'values differ') + ': ' + JSON.stringify(a) + ' !== ' + JSON.stringify(b)); }
function code(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map((l) => l.split('//')[0]).join('\n');
}

const IDS = ['section', 'controls', 'fixed', 'port', 'change', 'url', 'help', 'mode_note',
  'message', 'temp', 'temp_title', 'temp_configured', 'temp_current', 'temp_retry',
  'temp_make', 'temp_change'];

function world(opts) {
  opts = opts || {};
  const els = {};
  IDS.forEach((id) => { els['cfg_localport_' + id] = new El(id === 'fixed' ? 'input' : 'div'); });
  els.cfg_localport_fixed.type = 'checkbox';
  const w = {els, requests: [], dialogs: [], toasts: [], navigated: [], probes: [],
             replies: opts.replies || {}, answers: opts.answers || [], local: opts.local !== false,
             probeScript: opts.probeScript || [], origin: opts.origin || 'http://127.0.0.1:28123/'};
  w.status = opts.status || {ok: true, available: true, mode: 'automatic', fixed: false,
    configured_port: null, actual_port: 28123, temporary: false, conflict: false,
    url: 'http://127.0.0.1:28123/', restarting: false, problem: ''};
  const deps = {
    byId: (id) => els[id] || null,
    request: async (p, method, body) => {
      w.requests.push({path: p, method, body});
      if (p === '/api/local_port' && method === 'GET') return {status: 200, body: w.status};
      const key = method + ' ' + p;
      return w.replies[key] || {status: 200, body: {ok: true, restarting: true}};
    },
    confirm: async (options) => {
      w.dialogs.push(options);
      const answer = w.answers.length ? w.answers.shift() : {value: null};
      if (answer.cancel) return false;
      if (options.field && options.field.onValue) {
        const value = answer.value != null ? answer.value : options.field.value;
        const problem = options.field.validate ? options.field.validate(value) : '';
        if (problem) { w.lastProblem = problem; return false; }
        options.field.onValue(value);
      }
      return true;
    },
    toast: (m, k) => w.toasts.push([k, m]),
    isLocal: async () => w.local,
    probe: async (url) => { w.probes.push(url); return w.probeScript.length ? w.probeScript.shift() : true; },
    sleep: async () => {},
    navigate: (url) => w.navigated.push(url),
    origin: () => w.origin,
    here: () => w.origin,
    attempts: opts.attempts || 6, interval: 0,
  };
  w.ctl = LP.create(deps);
  return w;
}
const posts = (w) => w.requests.filter((r) => r.method === 'POST');

(async function main() {
  await check('the helper sentence is the one the brief gives', async () => {
    eq(LP.HELPER, 'Use a fixed port to keep OmniSuite’s local browser address consistent after restart.');
  });

  await check('port parsing mirrors the server: digits only, 1024-65535', async () => {
    ['', '  ', 'abc', '80', '1023', '65536', '8080.5', '-1', '+8080', '8 080', '8e3'].forEach((bad) => {
      assert(!LP.parsePort(bad).ok, 'accepted ' + JSON.stringify(bad));
    });
    eq(LP.parsePort('1024').port, 1024); eq(LP.parsePort(' 65535 ').port, 65535);
    eq(LP.parsePort('28123').port, 28123);
  });

  await check('automatic mode: the actual port, not editable, explained', async () => {
    const w = world({status: Object.assign({}, world().status, {actual_port: 28777, url: 'http://127.0.0.1:28777/'})});
    await w.ctl.refresh();
    const e = w.els;
    assert(!e.cfg_localport_section.hidden, 'section shown for the local operator');
    eq(e.cfg_localport_fixed.checked, false);
    eq(e.cfg_localport_port.value, '28777', 'the field shows the ACTUAL port');
    eq(e.cfg_localport_port.readOnly, true, 'not editable while automatic');
    eq(e.cfg_localport_url.textContent, 'Current local address: http://127.0.0.1:28777/');
    assert(/automatically/.test(e.cfg_localport_mode_note.textContent), 'explains automatic selection');
    eq(e.cfg_localport_temp.hidden, true);
    eq(e.cfg_localport_change.hidden, true);
    eq(e.cfg_localport_help.textContent, LP.HELPER);
  });

  await check('fixed mode: the configured port, a way to change it, the restart requirement', async () => {
    const w = world({status: {ok: true, available: true, mode: 'fixed', fixed: true,
      configured_port: 28300, actual_port: 28300, temporary: false, conflict: false,
      url: 'http://127.0.0.1:28300/', restarting: false, problem: ''}});
    await w.ctl.refresh();
    eq(w.els.cfg_localport_fixed.checked, true);
    eq(w.els.cfg_localport_port.value, '28300');
    eq(w.els.cfg_localport_change.hidden, false);
    assert(/requires OmniSuite to restart/.test(w.els.cfg_localport_mode_note.textContent));
  });

  await check('temporary state: configured and current ports are distinguished, three actions', async () => {
    const w = world({status: {ok: true, available: true, mode: 'fixed', fixed: true,
      configured_port: 28300, actual_port: 28301, temporary: true, conflict: false,
      url: 'http://127.0.0.1:28301/', restarting: false, problem: ''}});
    await w.ctl.refresh();
    const e = w.els;
    eq(e.cfg_localport_temp.hidden, false);
    eq(e.cfg_localport_temp_title.textContent, '⚠ Running temporarily on TCP port 28301');
    eq(e.cfg_localport_temp_configured.textContent, 'Configured fixed port: 28300');
    eq(e.cfg_localport_temp_current.textContent, 'Current port: 28301');
    eq(e.cfg_localport_temp_retry.textContent, 'Retry Fixed Port');
    eq(e.cfg_localport_temp_make.textContent, 'Make 28301 Fixed');
    eq(e.cfg_localport_temp_change.textContent, 'Change Fixed Port');
    eq(e.cfg_localport_fixed.checked, true, 'the box stays checked');
    eq(e.cfg_localport_change.hidden, true, 'one way to change, not two');
  });

  await check('no launcher: it says so and offers nothing', async () => {
    const w = world({status: {ok: true, available: false, message: 'x'}});
    await w.ctl.refresh();
    eq(w.els.cfg_localport_controls.hidden, true);
    assert(/Not available in this run/.test(w.els.cfg_localport_mode_note.textContent));
  });

  await check('a remote browser never sees the section and asks nothing', async () => {
    const w = world({local: false});
    w.els.cfg_localport_section.hidden = false;
    await w.ctl.refresh();
    eq(w.els.cfg_localport_section.hidden, true);
    eq(w.requests.length, 0, 'no request is made for a remote browser');
  });

  await check('the enable dialog carries the exact contents and is pre-filled from the ACTUAL port', async () => {
    const w = world({status: Object.assign({}, world().status, {actual_port: 28999, url: 'http://127.0.0.1:28999/'}),
                     answers: [{cancel: true}]});
    w.ctl.bind();
    w.els.cfg_localport_fixed.checked = true;
    await w.els.cfg_localport_fixed.fire('change');
    eq(w.dialogs.length, 1);
    const d = w.dialogs[0];
    eq(d.title, 'Use a fixed local OmniSuite port');
    assert(d.message.indexOf('A fixed port keeps OmniSuite’s local browser address consistent after restart.') === 0);
    assert(d.message.indexOf('Current local address: http://127.0.0.1:28999/') > 0);
    eq(d.field.label, 'TCP port:');
    eq(d.field.value, '28999', 'prefilled with the actual bound port');
    assert(d.field.hint.indexOf('The current port has been filled in automatically. Keep it or enter another available TCP port.') === 0);
    assert(/Changing this setting requires OmniSuite to restart\./.test(d.field.hint));
    eq(d.confirmText, 'Save and Restart');
    eq(d.cancelText, 'Cancel');
    ['', 'abc', '80', '70000'].forEach((bad) => assert(d.field.validate(bad), 'accepted ' + bad));
    eq(d.field.validate('28100'), '');
  });

  await check('Cancel changes nothing: no request, the box goes back', async () => {
    const w = world({answers: [{cancel: true}]});
    w.ctl.bind();
    w.els.cfg_localport_fixed.checked = true;
    await w.els.cfg_localport_fixed.fire('change');
    eq(posts(w).length, 0);
    eq(w.navigated.length, 0);
    eq(w.els.cfg_localport_fixed.checked, false, 'reverted to the stored state');
  });

  await check('Save and Restart posts the typed port as a number, then reconnects there', async () => {
    const w = world({answers: [{value: '28400'}], probeScript: [true]});
    w.ctl.bind();
    w.els.cfg_localport_fixed.checked = true;
    await w.els.cfg_localport_fixed.fire('change');
    const sent = posts(w);
    eq(sent.length, 1);
    eq(sent[0].path, '/api/local_port/fixed');
    eq(sent[0].body.port, 28400);
    eq(typeof sent[0].body.port, 'number');
    eq(w.navigated.length, 1);
    eq(w.navigated[0], 'http://127.0.0.1:28400/');
  });

  await check('an invalid value is refused in the dialog and nothing is sent', async () => {
    const w = world({answers: [{value: 'abc'}]});
    w.ctl.bind();
    w.els.cfg_localport_fixed.checked = true;
    await w.els.cfg_localport_fixed.fire('change');
    eq(posts(w).length, 0);
    assert(w.lastProblem, 'the dialog reported why');
  });

  await check('a refusal from the server is shown and nothing navigates', async () => {
    const w = world({answers: [{value: '28401'}],
                     replies: {'POST /api/local_port/fixed': {status: 409, body: {ok: false, reason: 'port_in_use', error: 'TCP port 28401 is not available right now. Enter another port.'}}}});
    w.ctl.bind();
    w.els.cfg_localport_fixed.checked = true;
    await w.els.cfg_localport_fixed.fire('change');
    eq(w.navigated.length, 0);
    eq(w.toasts.length, 1);
    assert(/not available right now/.test(w.toasts[0][1]));
  });

  await check('same address: reconnect only after it has been seen to stop answering', async () => {
    const w = world({answers: [{value: '28123'}], probeScript: [true, true, false, false, true], attempts: 8});
    w.ctl.bind();
    w.els.cfg_localport_fixed.checked = true;
    await w.els.cfg_localport_fixed.fire('change');
    eq(w.probes.length, 5, 'kept probing past the old listener still answering');
    eq(w.navigated.length, 1);
  });

  await check('a restart that never comes back says where to look', async () => {
    const w = world({answers: [{value: '28402'}], probeScript: [false, false, false], attempts: 3});
    w.ctl.bind();
    w.els.cfg_localport_fixed.checked = true;
    await w.els.cfg_localport_fixed.fire('change');
    eq(w.navigated.length, 0);
    assert(/launcher window/.test(w.els.cfg_localport_message.textContent));
    assert(/28402/.test(w.els.cfg_localport_message.textContent));
  });

  await check('disable: a confirmation first, Cancel sends nothing, Restart posts', async () => {
    const fixed = {ok: true, available: true, mode: 'fixed', fixed: true, configured_port: 28300,
      actual_port: 28300, temporary: false, conflict: false, url: 'http://127.0.0.1:28300/',
      restarting: false, problem: ''};
    let w = world({status: fixed, answers: [{cancel: true}]});
    w.ctl.bind(); await w.ctl.refresh();
    w.els.cfg_localport_fixed.checked = false;
    await w.els.cfg_localport_fixed.fire('change');
    eq(w.dialogs[0].message, 'OmniSuite will return to automatic local port selection. The local browser address may change after restart.');
    eq(posts(w).length, 0);
    eq(w.els.cfg_localport_fixed.checked, true, 'box restored');
    w = world({status: fixed, answers: [{}], probeScript: [false, true], attempts: 4, origin: 'http://127.0.0.1:28300/'});
    w.ctl.bind(); await w.ctl.refresh();
    w.els.cfg_localport_fixed.checked = false;
    await w.els.cfg_localport_fixed.fire('change');
    eq(posts(w).length, 1);
    eq(posts(w)[0].path, '/api/local_port/disable');
  });

  const temp = {ok: true, available: true, mode: 'fixed', fixed: true, configured_port: 28300,
    actual_port: 28301, temporary: true, conflict: false, url: 'http://127.0.0.1:28301/',
    restarting: false, problem: ''};

  await check('Make Fixed confirms, then posts; Cancel posts nothing', async () => {
    let w = world({status: temp, answers: [{cancel: true}]});
    w.ctl.bind(); await w.ctl.refresh();
    await w.els.cfg_localport_temp_make.fire('click');
    eq(posts(w).length, 0);
    assert(/28301/.test(w.dialogs[0].title));
    w = world({status: temp, answers: [{}]});
    w.ctl.bind(); await w.ctl.refresh();
    await w.els.cfg_localport_temp_make.fire('click');
    eq(posts(w).length, 1);
    eq(posts(w)[0].path, '/api/local_port/make_fixed');
    eq(w.navigated.length, 0, 'no restart: the port is already serving');
  });

  await check('Retry Fixed Port posts, and a refusal says why', async () => {
    const w = world({status: temp, replies: {'POST /api/local_port/retry': {status: 409, body: {ok: false, error: 'TCP port 28300 is still in use.'}}}});
    w.ctl.bind(); await w.ctl.refresh();
    await w.els.cfg_localport_temp_retry.fire('click');
    eq(posts(w)[0].path, '/api/local_port/retry');
    assert(/still in use/.test(w.toasts[0][1]));
    eq(w.navigated.length, 0);
  });

  await check('Change Fixed Port from the temporary state offers the ACTUAL port', async () => {
    const w = world({status: temp, answers: [{cancel: true}]});
    w.ctl.bind(); await w.ctl.refresh();
    await w.els.cfg_localport_temp_change.fire('click');
    eq(w.dialogs[0].field.value, '28301');
    eq(w.dialogs[0].title, 'Change the fixed local port');
  });

  await check('single flight: a second click while a dialog is open does nothing', async () => {
    let release;
    const w = world();
    const gate = new Promise((r) => { release = r; });
    const original = w.ctl;
    // replace the dialog with one that waits
    const slow = world({answers: [{cancel: true}]});
    let opened = 0;
    const deps = {byId: (id) => slow.els[id], request: async (p, m) => ({status: 200, body: slow.status}),
      confirm: async () => { opened += 1; await gate; return false; }, toast() {}, isLocal: async () => true,
      probe: async () => true, sleep: async () => {}, navigate() {}, origin: () => '', here: () => '', attempts: 1, interval: 0};
    const ctl = LP.create(deps);
    ctl.bind();
    slow.els.cfg_localport_fixed.checked = true;
    const first = slow.els.cfg_localport_fixed.fire('change');
    await new Promise((r) => setTimeout(r, 5));
    slow.els.cfg_localport_fixed.checked = true;
    await slow.els.cfg_localport_fixed.fire('change');
    release();
    await first;
    eq(opened, 1, 'two dialogs stacked on one decision');
    void original;
  });

  await check('the markup carries every id the module drives, and the module is loaded by settings.js', async () => {
    const settings = read('ui', 'settings.js');
    IDS.forEach((id) => assert(settings.indexOf('id="cfg_localport_' + id + '"') > 0, 'missing ' + id));
    assert(settings.indexOf('/ui/local-port.js') > 0, 'settings.js does not load the module');
    const section = settings.indexOf('id="cfg_localport_section"');
    const remote = settings.indexOf('id="cfg_remote_section"');
    assert(section > 0 && section < remote, 'its own section, outside Remote Access');
    const remoteBlock = settings.slice(remote, settings.indexOf('id="cfg_backup_section"'));
    assert(remoteBlock.indexOf('cfg_localport') < 0, 'the local port control must not sit inside Remote Access');
    const moduleSource = read('ui', 'local-port.js');
    const declared = /const IDS = \[([^\]]*)\]/.exec(moduleSource)[1].match(/'[a-z_]+'/g).map((s) => s.slice(1, -1));
    eq(declared.join(','), IDS.join(','), 'the test and the module disagree about the ids');
  });

  await check('no native dialog, no innerHTML, and no suggested default port in the source', async () => {
    const source = code(read('ui', 'local-port.js'));
    ['alert(', 'confirm(', 'prompt('].forEach((call) => {
      const hits = source.split(call).filter((part, i, all) => i > 0 && !/[\w.]$/.test(all[i - 1]));
      assert(hits.length === 0, 'native ' + call + ' used');
    });
    assert(!/innerHTML/.test(source), 'markup built from strings');
    assert(!/8080/.test(source), 'a hard-coded default port');
  });

  results.forEach(([s, n]) => console.log(s + ' ' + n));
  console.log((total - failures) + '/' + total + ' checks passed');
  process.exit(failures ? 1 : 0);
})();

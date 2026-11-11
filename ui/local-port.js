// Settings: the LOCAL APPLICATION PORT.
//
// OmniSuite's browser address is http://127.0.0.1:<port>/. By default the port
// is chosen at startup, so the address can change from one run to the next. An
// operator who has bookmarked it can ask for a fixed port; this module is that
// control. It governs the local application listener only -- Remote Access has
// its own section, port, transport and certificate, and nothing here touches
// them.
//
// The browser holds no protocol logic and no rules of its own beyond checking
// what a person typed before sending it: the server validates again, probes,
// saves and restarts. What the page shows is what the server reports about the
// port that is ACTUALLY serving, never a default.
//
// One implementation for every page: settings.js injects the markup and loads
// this file. Built as a factory over its dependencies so the flows can be driven
// with a scripted dialog and a recording fetch -- "Cancel makes no request" is
// then a measurement, not a reading of the source.
(function (root) {
  'use strict';

  const MIN_PORT = 1024;
  const MAX_PORT = 65535;
  const HELPER = 'Use a fixed port to keep OmniSuite’s local browser address ' +
    'consistent after restart.';

  // The same rules the server enforces: digits only, 1024-65535.
  function parsePort(value) {
    const text = String(value == null ? '' : value).trim();
    if (!text) return {ok: false, error: 'Enter a TCP port number.'};
    if (!/^[0-9]+$/.test(text)) {
      return {ok: false, error: 'The port must be a whole number, digits only.'};
    }
    const port = parseInt(text, 10);
    if (port < MIN_PORT || port > MAX_PORT) {
      return {ok: false,
              error: 'The port must be between ' + MIN_PORT + ' and ' + MAX_PORT + '.'};
    }
    return {ok: true, port: port};
  }

  function addressFor(port) {
    return 'http://127.0.0.1:' + port + '/';
  }

  function create(deps) {
    const byId = deps.byId;
    const IDS = ['section', 'controls', 'fixed', 'port', 'change', 'url', 'help', 'mode_note',
                 'message', 'temp', 'temp_title', 'temp_configured',
                 'temp_current', 'temp_retry', 'temp_make', 'temp_change'];
    const els = {};
    IDS.forEach(function (id) { els[id] = byId('cfg_localport_' + id); });

    let state = null;
    let busy = false;
    let bound = false;

    function show(el, on) { if (el) el.hidden = !on; }
    function text(el, value) { if (el) el.textContent = value; }

    function say(message, tone) {
      text(els.message, message || '');
      if (els.message) {
        els.message.className = 'small' + (tone ? ' localport-' + tone : '');
      }
      show(els.message, !!message);
    }

    // ---- what the page says -------------------------------------------
    function render(status) {
      state = status;
      const section = els.section;
      if (!section) return;
      if (!status || status.ok === false) {
        show(section, false);
        return;
      }
      show(section, true);
      if (status.available === false) {
        // No launcher in this run: say so, and offer nothing that could not work.
        show(els.controls, false);
        show(els.temp, false);
        text(els.mode_note, 'Not available in this run. The local port can be ' +
          'changed only when OmniSuite was started from its launcher.');
        show(els.mode_note, true);
        return;
      }
      show(els.controls, true);
      const fixed = !!status.fixed;
      const actual = status.actual_port;
      if (els.fixed) {
        els.fixed.checked = fixed;
        els.fixed.disabled = !!status.restarting || !!status.conflict;
        // The state is set as well as the class: a control that only looks
        // checked announces nothing.
        els.fixed.setAttribute && els.fixed.setAttribute('aria-checked', fixed ? 'true' : 'false');
      }
      if (els.port) {
        // Never editable in place: a change is a decision with a restart
        // attached, so it goes through the dialog.
        els.port.readOnly = true;
        els.port.value = String(fixed ? (status.configured_port || actual || '') : (actual || ''));
      }
      text(els.url, 'Current local address: ' + (status.url || addressFor(actual)));
      text(els.help, HELPER);
      show(els.change, fixed && !status.temporary);
      if (els.change) els.change.disabled = !!status.restarting;
      text(els.mode_note, fixed
        ? 'OmniSuite binds this port every time it starts. Changing it requires OmniSuite to restart.'
        : 'OmniSuite chooses a free port automatically at startup, so the local address can change after a restart.');
      show(els.mode_note, true);
      if (status.problem === 'unreadable') {
        say('The saved port setting could not be read and was set aside. ' +
            'OmniSuite is using automatic port selection.', 'warn');
      }

      // The temporary state: serving, but not on the port that was chosen.
      const temporary = !!status.temporary;
      show(els.temp, temporary);
      if (temporary) {
        text(els.temp_title, '⚠ Running temporarily on TCP port ' + actual);
        text(els.temp_configured, 'Configured fixed port: ' + status.configured_port);
        text(els.temp_current, 'Current port: ' + actual);
        text(els.temp_retry, 'Retry Fixed Port');
        text(els.temp_make, 'Make ' + actual + ' Fixed');
        text(els.temp_change, 'Change Fixed Port');
        ['temp_retry', 'temp_make', 'temp_change'].forEach(function (k) {
          if (els[k]) els[k].disabled = !!status.restarting;
        });
      }
    }

    async function refresh() {
      let local = true;
      try { local = deps.isLocal ? await deps.isLocal() : true; } catch (err) { local = false; }
      if (!local) { render(null); return null; }
      let reply;
      try {
        reply = await deps.request('/api/local_port', 'GET');
      } catch (err) {
        render(null);
        return null;
      }
      render(reply && reply.body);
      return reply && reply.body;
    }

    // ---- the questions it asks ----------------------------------------
    async function askForPort(title, current) {
      const actual = current.actual_port;
      let typed = '';
      const answer = await deps.confirm({
        title: title,
        message: 'A fixed port keeps OmniSuite’s local browser address ' +
          'consistent after restart.\nCurrent local address: ' +
          (current.url || addressFor(actual)),
        // The ACTUAL port, as the server just reported it. Not a default.
        field: {
          label: 'TCP port:',
          type: 'text',
          value: String(actual),
          hint: 'The current port has been filled in automatically. Keep it or ' +
            'enter another available TCP port.\nChanging this setting requires ' +
            'OmniSuite to restart.',
          validate: function (value) {
            const parsed = parsePort(value);
            return parsed.ok ? '' : parsed.error;
          },
          onValue: function (value) { typed = value; },
        },
        confirmText: 'Save and Restart',
        cancelText: 'Cancel',
      });
      const yes = answer === true || (answer && answer.ok === true);
      if (!yes) return null;
      const parsed = parsePort(typed);
      return parsed.ok ? parsed.port : null;
    }

    async function saveFixed(port) {
      say('Saving…');
      const reply = await deps.request('/api/local_port/fixed', 'POST', {port: port});
      const body = (reply && reply.body) || {};
      if (!body.ok) {
        say('', '');
        deps.toast(body.error || 'The port could not be saved.', 'error');
        await refresh();
        return false;
      }
      await waitForRestart(addressFor(port));
      return true;
    }

    // The old process answers for a moment after the request returns, so a
    // restart is recognised only once the address has been seen to stop
    // answering (or it is a different address altogether). Bounded; and if the
    // new process lands in the launcher's recovery state the page says where
    // to look instead of waiting forever.
    async function waitForRestart(url) {
      say('OmniSuite is restarting. This page will reconnect when it is ready…');
      const here = deps.origin ? deps.origin() : '';
      let sawDown = here !== '' && url.indexOf(here) !== 0;
      for (let attempt = 0; attempt < deps.attempts; attempt += 1) {
        await deps.sleep(deps.interval);
        let up = false;
        try { up = !!(await deps.probe(url)); } catch (err) { up = false; }
        if (!up) sawDown = true;
        if (up && sawDown) {
          say('Reconnecting…');
          deps.navigate(url);
          return true;
        }
      }
      say('OmniSuite has not come back on ' + url + '. Look at the OmniSuite ' +
          'launcher window: if the port is in use, it offers a way to recover.',
          'warn');
      return false;
    }

    async function enableFlow() {
      const current = await refresh();
      if (!current || current.available === false) return;
      const port = await askForPort('Use a fixed local OmniSuite port', current);
      if (port === null) { await refresh(); return; }      // Cancel changes nothing
      await saveFixed(port);
    }

    async function changeFlow() {
      const current = await refresh();
      if (!current || current.available === false) return;
      const port = await askForPort('Change the fixed local port', current);
      if (port === null) return;
      await saveFixed(port);
    }

    async function disableFlow() {
      const answer = await deps.confirm({
        title: 'Return to automatic port selection',
        message: 'OmniSuite will return to automatic local port selection. ' +
          'The local browser address may change after restart.',
        confirmText: 'Restart',
        cancelText: 'Cancel',
      });
      if (!(answer === true || (answer && answer.ok === true))) {
        await refresh();                                   // box back to checked
        return;
      }
      say('Saving…');
      const reply = await deps.request('/api/local_port/disable', 'POST');
      const body = (reply && reply.body) || {};
      if (!body.ok) {
        say('');
        deps.toast(body.error || 'The setting could not be saved.', 'error');
        await refresh();
        return;
      }
      await waitForRestart(deps.here ? deps.here() : addressFor(state && state.actual_port));
    }

    async function makeFixedFlow() {
      const current = state;
      if (!current || !current.temporary) return;
      const answer = await deps.confirm({
        title: 'Make port ' + current.actual_port + ' fixed',
        message: 'OmniSuite will keep using TCP port ' + current.actual_port +
          ' as its fixed local port, from the next start onward. The address ' +
          'stays ' + (current.url || addressFor(current.actual_port)),
        confirmText: 'Make Fixed',
        cancelText: 'Cancel',
      });
      if (!(answer === true || (answer && answer.ok === true))) return;
      const reply = await deps.request('/api/local_port/make_fixed', 'POST');
      const body = (reply && reply.body) || {};
      if (!body.ok) deps.toast(body.error || 'The setting could not be saved.', 'error');
      else deps.toast('Port ' + current.actual_port + ' is now the fixed port.', 'ok');
      await refresh();
    }

    async function retryFlow() {
      const current = state;
      if (!current || !current.temporary) return;
      say('Checking TCP port ' + current.configured_port + '…');
      const reply = await deps.request('/api/local_port/retry', 'POST');
      const body = (reply && reply.body) || {};
      if (!body.ok) {
        say('');
        deps.toast(body.error || 'The fixed port could not be retried.', 'error');
        await refresh();
        return;
      }
      await waitForRestart(addressFor(current.configured_port));
    }

    // One at a time. A second click while a flow is open does nothing, so two
    // dialogs can never stack on one decision.
    function guarded(flow) {
      return async function () {
        if (busy) return;
        busy = true;
        try { await flow(); } catch (err) {
          deps.toast('The port setting could not be changed.', 'error');
        } finally { busy = false; }
      };
    }

    function bind() {
      if (bound) return;
      bound = true;
      // Bound as soon as the control exists, not after its saved value loads.
      if (els.fixed) {
        els.fixed.addEventListener('change', guarded(async function () {
          if (els.fixed.checked) await enableFlow(); else await disableFlow();
        }));
      }
      if (els.change) els.change.addEventListener('click', guarded(changeFlow));
      if (els.temp_change) els.temp_change.addEventListener('click', guarded(changeFlow));
      if (els.temp_make) els.temp_make.addEventListener('click', guarded(makeFixedFlow));
      if (els.temp_retry) els.temp_retry.addEventListener('click', guarded(retryFlow));
    }

    return {bind: bind, refresh: refresh, render: render, enableFlow: enableFlow,
            disableFlow: disableFlow, changeFlow: changeFlow,
            makeFixedFlow: makeFixedFlow, retryFlow: retryFlow,
            waitForRestart: waitForRestart, state: function () { return state; }};
  }

  // ---- in the page ------------------------------------------------------
  function browserDeps() {
    const request = async function (path, method, body) {
      const init = {method: method || 'GET', headers: {}};
      if (body !== undefined) {
        init.headers['Content-Type'] = 'application/json';
        init.body = JSON.stringify(body);
      }
      const response = await root.fetch(path, init);
      let parsed = null;
      try { parsed = await response.json(); } catch (err) { parsed = null; }
      return {status: response.status, body: parsed};
    };
    return {
      byId: function (id) { return root.document.getElementById(id); },
      request: request,
      confirm: function (options) {
        // The shared dialog or nothing: a native box would not carry the
        // sentences this question is made of.
        if (typeof root.omniConfirm !== 'function') return Promise.resolve(false);
        return root.omniConfirm(options);
      },
      toast: function (message, kind) {
        if (kind === 'error' && root.toastError) root.toastError(message);
        else if (kind === 'ok' && root.toastOk) root.toastOk(message);
        else if (root.toast) root.toast(message, kind);
      },
      isLocal: function () {
        return root.omniIsLocal ? root.omniIsLocal() : Promise.resolve(true);
      },
      probe: function (url) {
        // Opaque on purpose: all that matters is whether anything answers.
        const controller = typeof AbortController === 'function' ? new AbortController() : null;
        const timer = controller ? root.setTimeout(function () { controller.abort(); }, 2500) : 0;
        return root.fetch(url + '__health', {mode: 'no-cors', cache: 'no-store',
                                             signal: controller ? controller.signal : undefined})
          .then(function () { return true; }, function () { return false; })
          .then(function (value) { if (timer) root.clearTimeout(timer); return value; });
      },
      sleep: function (ms) { return new Promise(function (r) { root.setTimeout(r, ms); }); },
      navigate: function (url) {
        root.location.href = url.replace(/\/$/, '') + root.location.pathname + root.location.search;
      },
      origin: function () { return root.location.origin + '/'; },
      here: function () { return root.location.origin + '/'; },
      attempts: 45,
      interval: 1500,
    };
  }

  let instance = null;
  function init() {
    if (instance || !root.document || !root.document.getElementById('cfg_localport_section')) {
      return instance;
    }
    instance = create(browserDeps());
    instance.bind();
    instance.refresh();
    // Re-read whenever Settings is opened, so what it shows is what is true now.
    const gear = root.document.getElementById('header_gear');
    if (gear) gear.addEventListener('click', function () { instance.refresh(); });
    return instance;
  }

  const api = {create: create, parsePort: parsePort, addressFor: addressFor,
               init: init, MIN_PORT: MIN_PORT, MAX_PORT: MAX_PORT, HELPER: HELPER};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.omniLocalPort = api;
  if (root && root.document) init();
})(typeof window !== 'undefined' ? window : (typeof globalThis !== 'undefined' ? globalThis : this));

/*
 * ui/scan-notice.js: what to tell the operator when macOS blocked a scan.
 *
 * The server decides WHY a scan failed (`reason` and `likely_cause` in its 503
 * answer); this module only presents that verdict. It holds no protocol or
 * network logic, and it never second-guesses the server: a scan that finished
 * and found no devices is an ordinary result and is never shown here.
 *
 * The notice is shown only for reason "scan_blocked" with likely_cause
 * "local_network_permission". Every other failure keeps the page's normal
 * one-line status. The detailed diagnostics stay available under "Technical
 * details", because they are what support needs.
 *
 * Built with createElement and textContent, never innerHTML: the product name
 * comes from the server and is data, not markup.
 *
 * "Open System Settings" is offered only where it can work: the server is a Mac,
 * and this browser is on that same Mac (served from localhost), because a
 * settings link opens the machine the browser is running on, not the one the
 * application is running on. The manual path is always written out as well, so
 * the button is a convenience and never the only way.
 */
(function (root) {
  'use strict';

  var SETTINGS_URL = 'x-apple.systempreferences:com.apple.preference.security?Privacy_LocalNetwork';
  var LOCAL_HOSTS = ['127.0.0.1', 'localhost', '::1', '[::1]'];

  // "scan_blocked" + "local_network_permission" and nothing else.
  function classify(body) {
    if (!body || typeof body !== 'object') return null;
    if (body.ok !== false || body.reason !== 'scan_blocked') return null;
    return body.likely_cause === 'local_network_permission' ? 'local_network_permission' : null;
  }

  function serverIsMac(body) {
    var platform = body && body.diagnostics && body.diagnostics.platform;
    return !!platform && platform.system === 'Darwin';
  }

  // env: { hostname, platform } of the BROWSER.
  function canOpenSettings(body, env) {
    env = env || {};
    var hostname = String(env.hostname || '');
    var browserPlatform = String(env.platform || '');
    return serverIsMac(body) &&
      LOCAL_HOSTS.indexOf(hostname) !== -1 &&
      /mac/i.test(browserPlatform);
  }

  function make(doc, tag, text, className) {
    var el = doc.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = text;
    return el;
  }

  function button(doc, label, className, onClick) {
    var el = make(doc, 'button', label, className);
    el.type = 'button';
    el.addEventListener('click', onClick);
    return el;
  }

  var state = { container: null, onRetry: null, navigate: null, env: null, doc: null };

  // container: the element to render into. onRetry: runs a fresh scan.
  // navigate(url): opens a URL; env: { hostname, platform } of the browser.
  function attach(options) {
    options = options || {};
    state.container = options.container || null;
    state.doc = options.document || (typeof document !== 'undefined' ? document : null);
    state.onRetry = typeof options.onRetry === 'function' ? options.onRetry : null;
    state.navigate = typeof options.navigate === 'function' ? options.navigate : null;
    state.env = options.env || {};
    return !!state.container;
  }

  function clear(container) {
    while (container.firstChild) container.removeChild(container.firstChild);
  }

  function hide() {
    if (!state.container) return;
    clear(state.container);
    state.container.hidden = true;
  }

  // Returns true when the notice was shown. False means "not this kind of failure".
  function show(body) {
    if (!state.container || !state.doc || classify(body) !== 'local_network_permission') {
      return false;
    }
    var doc = state.doc;
    var name = String(body.app_name || 'OmniSuite');
    var box = state.container;
    clear(box);

    box.appendChild(make(doc, 'strong', 'macOS is blocking ' + name + ' from your local network'));
    box.appendChild(make(doc, 'p',
      'macOS asks before an app may look for devices on your network. Its question may ' +
      'still be open, or the answer may have been “Don’t Allow”. ' +
      'No devices were found because the scan was blocked, not because there are none.'));

    var steps = make(doc, 'ol');
    steps.appendChild(make(doc, 'li',
      'Open System Settings → Privacy & Security → Local Network.'));
    steps.appendChild(make(doc, 'li', 'Switch on ' + name + '.'));
    steps.appendChild(make(doc, 'li', 'Come back here and choose Retry Scan.'));
    box.appendChild(steps);

    var actions = make(doc, 'div', null, 'scan-notice-actions');
    if (state.onRetry) {
      actions.appendChild(button(doc, 'Retry Scan', 'btn', function () {
        hide();
        state.onRetry();
      }));
    }
    if (state.navigate && canOpenSettings(body, state.env)) {
      actions.appendChild(button(doc, 'Open System Settings', 'btn secondary', function () {
        state.navigate(SETTINGS_URL);
      }));
    }
    box.appendChild(actions);

    // The detail is kept, not discarded: it is what troubleshooting needs.
    var details = make(doc, 'details');
    details.appendChild(make(doc, 'summary', 'Technical details'));
    var lines = [];
    if (body.error) lines.push(String(body.error));
    lines.push(JSON.stringify({ reason: body.reason, likely_cause: body.likely_cause,
                                diagnostics: body.diagnostics || null }, null, 2));
    details.appendChild(make(doc, 'pre', lines.join('\n\n')));
    box.appendChild(details);

    box.setAttribute('role', 'status');
    box.hidden = false;
    return true;
  }

  var api = { classify: classify, canOpenSettings: canOpenSettings, attach: attach,
              show: show, hide: hide, SETTINGS_URL: SETTINGS_URL };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.OmniScanNotice = api;
})(typeof window !== 'undefined' ? window : globalThis);

// Operator-initiated device debug-log download.
//
// Lives in its own module rather than inside index.html so the flow can be
// exercised directly: the engineering notice, the cancel paths and the
// single-flight guard are behaviour, and behaviour asserted only by reading the
// page source is not asserted at all.
//
// What the server does with the request is documented beside /api/device_log.
// From here: one click asks one question, and only an affirmative answer causes
// exactly one request.
(function () {
  'use strict';

  // The notice is shown every time. It is not an acknowledgement to be
  // remembered -- it exists so nobody sends a .vdf somewhere expecting to read
  // it, and that is as true on the tenth download as on the first.
  const ENGINEERING_NOTICE =
    'Device logs are encrypted and can only be decrypted and read by Engineering.';

  // One request per device at a time, and the lock is taken before the dialog
  // opens: two notices stacked on one device would each go on to ask for its
  // own bundle, and the device builds a fresh one for every request.
  const inFlight = new Set();

  function dialog() {
    return typeof window.omniConfirm === 'function' ? window.omniConfirm : null;
  }

  function notifyError(message) {
    if (typeof window.toast === 'function') window.toast(message, 'error');
  }

  function notifyOk(message) {
    if (typeof window.toast === 'function') window.toast(message, true);
  }

  async function downloadDeviceLog(button) {
    const ip = button && button.dataset ? button.dataset.ip : '';
    if (!ip || inFlight.has(ip)) return 'busy';
    inFlight.add(ip);

    const originalText = button.textContent;
    const restore = () => {
      inFlight.delete(ip);
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.textContent = originalText;
      button.title = "Download this device's debug log";
    };

    try {
      const ask = dialog();
      if (!ask) {
        // Never a native confirm(): the point of the notice is the sentence it
        // carries, and a browser string box would not carry it.
        notifyError('The confirmation dialog is unavailable, so no log was requested.');
        restore();
        return 'no-dialog';
      }
      const proceed = await ask({
        title: 'Device Log',
        message: ENGINEERING_NOTICE,
        summary: [{label: 'Device', value: ip}],
        confirmText: 'OK',
        cancelText: 'Cancel',
        // Cancel holds the focus, so an accidental Enter does not start a
        // ~10 MB download the operator did not ask for.
        defaultAction: 'cancel',
        requireDialog: true,
      });
      // Cancel, Escape, the backdrop and a missing dialog all land here, and
      // all of them mean no request was made.
      if (!proceed) {
        restore();
        return 'cancelled';
      }
    } catch (err) {
      restore();
      return 'cancelled';
    }

    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    // Same width whatever it says, so a row cannot reflow mid-download.
    button.textContent = '...';
    button.title = 'Retrieving the debug log from ' + ip;

    let objectUrl = null;
    try {
      const res = await fetch('/api/device_log?ip=' + encodeURIComponent(ip));
      if (!res.ok) {
        let detail = 'HTTP ' + res.status;
        try {
          const body = await res.json();
          if (body && body.error) detail = body.error;
        } catch (_) { /* a non-JSON failure keeps the status code */ }
        throw new Error(detail);
      }
      // The server decides the filename; it is built from data OmniSuite
      // already holds, never from the path the device returned.
      const disposition = res.headers.get('Content-Disposition') || '';
      const match = /filename="([^"]+)"/.exec(disposition);
      const blob = await res.blob();
      objectUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl;
      link.download = match ? match[1] : ('device-log-' + ip + '.vdf');
      document.body.appendChild(link);
      link.click();
      link.remove();
      notifyOk('Debug log downloaded from ' + ip);
      return 'downloaded';
    } catch (err) {
      // An unreachable device is an ordinary outcome, not a page-breaking one.
      notifyError('Could not retrieve the log from ' + ip + ': ' +
                  (err && err.message ? err.message : 'request failed'));
      return 'failed';
    } finally {
      if (objectUrl) setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
      restore();
    }
  }

  // Delegated, so rows rebuilt by a scan or a poll keep working without
  // rebinding a handler per row -- which is how duplicate listeners accumulate.
  function bind(root) {
    const target = root || document;
    if (target.__deviceLogBound) return;
    target.__deviceLogBound = true;
    target.addEventListener('click', (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest('.log-btn') : null;
      if (button) downloadDeviceLog(button);
    });
  }

  window.downloadDeviceLog = downloadDeviceLog;
  window.deviceLogNotice = ENGINEERING_NOTICE;
  window.bindDeviceLog = bind;
  bind(document);
})();

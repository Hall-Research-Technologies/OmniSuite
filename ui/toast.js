// Transient status messages, shared by every page.
//
// matrix.js and usb.js each carried a byte-identical copy of this, and Device
// Info had none at all -- which is why the only feedback there was a native
// alert(). One implementation, loaded everywhere, so a message looks and
// behaves the same wherever it comes from.
//
// The styling lives in ui/templates.css with the rest of the semantic tokens,
// so a toast follows the active preset instead of being permanently near-black.
(function () {
  'use strict';
  if (window.toast) return;            // a page that already defined one wins

  const VISIBLE_MS = 2600;
  const REMOVE_MS = 3000;

  // One live element at a time. Two overlapping toasts stack on the same
  // coordinates and the operator reads neither.
  let current = null;
  let hideTimer = null;
  let removeTimer = null;

  function show(message, kind) {
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    if (removeTimer) { clearTimeout(removeTimer); removeTimer = null; }
    if (current && current.isConnected) current.remove();

    const el = document.createElement('div');
    el.className = 'toast' + (kind ? ' ' + kind : '');
    // textContent, never innerHTML: these messages routinely carry a hostname
    // or an error string that came from a device.
    el.textContent = String(message == null ? '' : message);
    // Announced without stealing focus. An error is assertive because it
    // reports something the operator asked for and did not get.
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');
    el.setAttribute('aria-live', kind === 'error' ? 'assertive' : 'polite');
    document.body.appendChild(el);
    current = el;

    requestAnimationFrame(() => el.classList.add('show'));
    hideTimer = setTimeout(() => el.classList.remove('show'), VISIBLE_MS);
    removeTimer = setTimeout(() => {
      if (el.isConnected) el.remove();
      if (current === el) current = null;
    }, REMOVE_MS);
    return el;
  }

  // `toast(msg, true)` is how the two existing callers signal success, so that
  // form keeps working; `toast(msg, 'error')` is the named form.
  window.toast = function (message, kind) {
    if (kind === true) return show(message, 'ok');
    if (kind === false || kind === undefined || kind === null) return show(message, '');
    return show(message, String(kind));
  };
  window.toastError = message => show(message, 'error');
  window.toastOk = message => show(message, 'ok');
})();

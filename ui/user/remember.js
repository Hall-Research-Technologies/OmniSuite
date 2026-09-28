/*
 * The operator's last selection, remembered between page loads.
 *
 * Deliberately its own module, and deliberately tiny. The restricted shell can
 * see a Direct Access link -- it has a "My link" sheet -- so nothing in that
 * file is allowed to write to browser storage at all, which is a fence worth
 * keeping rather than an inconvenience to work around. This module has no
 * access to a link, a token or a session: it is handed a key and a device
 * identity the page is already displaying, and it hands the same thing back.
 *
 * What it stores is presentation, not permission. A remembered identity is a
 * bookmark: the shell honours it only when the server has just listed that
 * device for this account, so a display that has been unassigned or taken away
 * is dropped rather than presented as a working selection. Nothing here
 * authorises anything.
 *
 * Storage can be absent or can throw -- a private window, blocked site data, a
 * headless capture -- so every access is guarded and the page is expected to
 * work perfectly well when it returns nothing.
 */
(function () {
  'use strict';

  const PREFIX = 'omni_user_';

  function guarded(fn, fallback) {
    try { return fn(); }
    catch (err) { return fallback; }
  }

  window.omniRemember = {
    get: function (key) {
      return guarded(() => localStorage.getItem(PREFIX + key), null) || null;
    },
    set: function (key, value) {
      guarded(() => {
        if (value) localStorage.setItem(PREFIX + key, String(value));
        else localStorage.removeItem(PREFIX + key);
      });
    },
  };
})();

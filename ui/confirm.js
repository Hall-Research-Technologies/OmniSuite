// The shared confirmation dialog.
//
// matrix.js has carried its own `omniConfirm` since before there was anywhere
// else to put one, and device-log.js already looks for `window.omniConfirm`
// rather than declaring a second. This module is that one implementation for
// pages that do not load matrix.js.
//
// Guarded the same way toast.js is: a page that already has one wins, so
// loading this alongside matrix.js changes nothing on the pages matrix.js owns.
//
// Production UI never uses native confirm(). A confirmation whose purpose is
// the summary it carries must not degrade to a browser string box, so if the
// markup cannot be created the caller is told no rather than shown less.
(function () {
  'use strict';
  if (typeof window.omniConfirm === 'function') return;

  function build() {
    let backdrop = document.getElementById('omni_confirm_backdrop');
    if (backdrop) return backdrop;
    backdrop = document.createElement('div');
    backdrop.id = 'omni_confirm_backdrop';
    backdrop.className = 'confirm-backdrop hidden';
    backdrop.innerHTML = '<div class="confirm-card" role="dialog" aria-modal="true">' +
      '<h3 class="confirm-title"></h3>' +
      '<div class="confirm-message"></div>' +
      '<div class="confirm-summary" style="display:none;"></div>' +
      '<label class="confirm-suppress" style="display:none;">' +
      '<input type="checkbox" class="confirm-suppress-box">' +
      '<span class="confirm-suppress-label"></span></label>' +
      '<div class="confirm-actions">' +
      '<button type="button" class="confirm-cancel">Cancel</button>' +
      '<button type="button" class="confirm-ok">Continue</button>' +
      '</div></div>';
    document.body.appendChild(backdrop);
    return backdrop;
  }

  // Version-scoped suppression, shared by every page that offers "do not show
  // again". A stored preference names the application version it was given
  // against; when the version changes the warning returns by itself, because
  // agreeing to what an operation did in one release is not agreement to what
  // it does in the next.
  //
  // Each caller owns its own key, so suppressing one warning never touches
  // another. Storage that refuses to answer means show the warning: a browser
  // with storage disabled must not silently lose its warnings.
  window.omniSuppression = window.omniSuppression || {
    suppressed: function (key, version) {
      if (!key || !version) return false;
      try {
        return localStorage.getItem(key) === String(version);
      } catch (err) {
        return false;
      }
    },
    remember: function (key, version) {
      if (!key || !version) return false;
      try {
        localStorage.setItem(key, String(version));
        return true;
      } catch (err) {
        return false;                    // not fatal: the warning simply returns
      }
    },
    forget: function (key) {
      try {
        localStorage.removeItem(key);
      } catch (err) { /* nothing to undo */ }
    },

    // The lighter level: acknowledged for this browser session. It survives a
    // refresh and a trip to another page, which is what "I have read it" means
    // to the person who read it, and a genuinely new session asks again.
    //
    // Version-scoped as well, so a release that changes what an operation does
    // asks again even inside a session that acknowledged the old one.
    suppressedForSession: function (key, version) {
      if (!key || !version) return false;
      try {
        return sessionStorage.getItem(key) === String(version);
      } catch (err) {
        return false;
      }
    },
    rememberForSession: function (key, version) {
      if (!key || !version) return false;
      try {
        sessionStorage.setItem(key, String(version));
        return true;
      } catch (err) {
        return false;                    // not fatal: the warning simply returns
      }
    },
  };

  window.omniConfirm = function omniConfirm(options) {
    options = options || {};
    const backdrop = build();
    const titleEl = backdrop.querySelector('.confirm-title');
    const messageEl = backdrop.querySelector('.confirm-message');
    const summaryEl = backdrop.querySelector('.confirm-summary');
    const okBtn = backdrop.querySelector('.confirm-ok');
    const cancelBtn = backdrop.querySelector('.confirm-cancel');

    titleEl.textContent = options.title || 'Confirm';
    messageEl.textContent = options.message || '';

    // textContent throughout: these rows carry hostnames, model strings and
    // error text that came from a device.
    const rows = options.summary || [];
    summaryEl.replaceChildren();
    rows.forEach(function (row) {
      const line = document.createElement('div');
      line.className = 'confirm-summary-row';
      const label = document.createElement('span');
      label.textContent = row.label == null ? '' : String(row.label);
      const value = document.createElement('strong');
      value.textContent = row.value == null ? '' : String(row.value);
      line.append(label, value);
      summaryEl.appendChild(line);
    });
    summaryEl.style.display = rows.length ? 'block' : 'none';

    okBtn.textContent = options.confirmText || 'Continue';
    okBtn.classList.toggle('confirm-danger', !!options.danger);

    // A caller that offers "do not show again" gets the checkbox back with the
    // answer, so the preference is the caller's to store rather than something
    // this dialog decides on its behalf.
    const suppressWrap = backdrop.querySelector('.confirm-suppress');
    const suppressBox = backdrop.querySelector('.confirm-suppress-box');
    const suppressLabel = backdrop.querySelector('.confirm-suppress-label');
    if (options.suppressLabel) {
      suppressLabel.textContent = options.suppressLabel;
      suppressBox.checked = false;
      suppressWrap.style.display = '';
    } else {
      suppressWrap.style.display = 'none';
    }
    backdrop.classList.remove('hidden');

    // Focus returns to whatever opened the dialog; without it, dismissing drops
    // the keyboard at the top of the document.
    const opener = document.activeElement;

    return new Promise(function (resolve) {
      function cleanup(result) {
        backdrop.classList.add('hidden');
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        backdrop.removeEventListener('click', onBackdrop);
        document.removeEventListener('keydown', onKeydown);
        if (opener && typeof opener.focus === 'function' && opener.isConnected) {
          opener.focus();
        }
        resolve(result);
      }
      function answer(ok) {
        return options.suppressLabel
          ? {ok: ok, suppress: ok && !!suppressBox.checked} : ok;
      }
      function onOk() { cleanup(answer(true)); }
      function onCancel() { cleanup(answer(false)); }
      function onBackdrop(event) {
        if (event.target === backdrop) cleanup(answer(false));
      }
      function onKeydown(event) {
        if (event.key === 'Escape') { event.stopPropagation(); cleanup(answer(false)); }
      }
      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      backdrop.addEventListener('click', onBackdrop);
      document.addEventListener('keydown', onKeydown);
      // The costly answer sits behind the focus: Cancel takes it, so the
      // keyboard default and the visual default agree.
      (options.danger ? cancelBtn : okBtn).focus();
    });
  };

  // ---- taking a decoder out of Multiview -------------------------------
  //
  // One warning, one preference, two pages. The A/V Matrix has always asked
  // before a route replaces a Multiview picture; the Multiview page's Display
  // Output does exactly the same thing by exactly the same route, so it asks
  // the same question and reads the same answer. An operator who turned it off
  // on one page has turned it off, and a second copy of this policy would be a
  // second thing to keep in step.
  //
  // The keys are the ones the A/V Matrix already wrote, so an existing
  // preference carries over rather than being silently reset.
  const MV_EXIT_SESSION_KEY = 'matrix_multiview_exit_acknowledged';
  const MV_EXIT_FOREVER_KEY = 'matrix_multiview_exit_suppressed';

  window.omniMultiviewExit = window.omniMultiviewExit || {
    SESSION_KEY: MV_EXIT_SESSION_KEY,
    FOREVER_KEY: MV_EXIT_FOREVER_KEY,

    // Storage that refuses to answer means ask: a browser with storage off
    // must not quietly lose a warning that precedes a display change.
    suppressed: function () {
      try {
        if (localStorage.getItem(MV_EXIT_FOREVER_KEY) === 'true') return true;
        return sessionStorage.getItem(MV_EXIT_SESSION_KEY) === 'true';
      } catch (err) {
        return false;
      }
    },

    remember: function (forever) {
      try {
        sessionStorage.setItem(MV_EXIT_SESSION_KEY, 'true');
        if (forever) localStorage.setItem(MV_EXIT_FOREVER_KEY, 'true');
      } catch (err) { /* not fatal: the warning simply returns */ }
    },

    // Resolves true when the operation may proceed. Nothing has been written
    // to any device before this resolves, on either page.
    // Named `ask`, not `confirm`: the page-wide guard that forbids the
    // native dialogs matches a bare `confirm(`, and a helper that trips it
    // would mean weakening the guard to accommodate a name.
    ask: async function (options) {
      options = options || {};
      if (window.omniMultiviewExit.suppressed()) return true;
      const who = options.decoder || 'this decoder';
      const answer = await window.omniConfirm({
        title: 'Multiview is active on this decoder',
        message: options.message
          || ('Completing this route will exit Multiview on ' + who + ' and '
              + 'replace the Multiview display with the selected A/V route. '
              + 'The saved Multiview is kept and can be shown again later.'),
        summary: [
          {label: 'Decoder', value: who},
          {label: 'Currently showing', value: options.multiview || 'a Multiview'},
          {label: 'After this route', value: options.after
            || 'the source you selected'},
          {label: 'Saved Multiview', value: 'kept, not deleted'},
        ],
        confirmText: 'Continue',
        suppressLabel: 'Do not show this again',
      });
      const ok = answer === true || (answer && answer.ok);
      if (ok) window.omniMultiviewExit.remember(!!(answer && answer.suppress));
      return !!ok;
    },
  };
})();

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
})();

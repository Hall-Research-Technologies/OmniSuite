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
      '<div class="confirm-actions">' +
      '<button type="button" class="confirm-cancel">Cancel</button>' +
      '<button type="button" class="confirm-ok">Continue</button>' +
      '</div></div>';
    document.body.appendChild(backdrop);
    return backdrop;
  }

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
      function onOk() { cleanup(true); }
      function onCancel() { cleanup(false); }
      function onBackdrop(event) { if (event.target === backdrop) cleanup(false); }
      function onKeydown(event) {
        if (event.key === 'Escape') { event.stopPropagation(); cleanup(false); }
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

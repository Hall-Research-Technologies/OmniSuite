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

  // Styling shared markup is part of sharing it.
  //
  // These rules lived in ui/matrix/matrix.css, so the dialog was a dialog only
  // on pages that happened to load that stylesheet. Measured on /admin/users
  // -- the page where a passwordless bearer link is replaced -- the backdrop
  // was `position: static`, the card transparent with no radius, no padding,
  // no shadow and the full width of the window: bare text at the bottom of the
  // page rather than a modal.
  //
  // So the module carries them. Any page that loads the script gets a working
  // dialog, and there is one definition rather than one per stylesheet.
  // Semantic tokens with literal fallbacks, so it follows every preset and
  // still renders on a page that defines none of them.
  function style() {
    if (document.getElementById('omni_confirm_style')) return;
    const sheet = document.createElement('style');
    sheet.id = 'omni_confirm_style';
    sheet.textContent = [
      '.confirm-backdrop{position:fixed;inset:0;background:rgba(0,0,0,.5);',
      'display:flex;align-items:center;justify-content:center;z-index:60000;',
      // Taller than the window must still be reachable: the backdrop scrolls
      // and the card centres with auto margins, so a short one is unaffected.
      'overflow:auto;padding:20px}',
      '.confirm-backdrop.hidden{display:none!important}',
      '.confirm-card{width:min(95vw,520px);margin:auto;',
      'background:var(--card,#222a33);border:1px solid var(--border,#39424e);',
      'border-radius:10px;padding:18px;color:var(--text,var(--fg,#e6e8ee));',
      'box-shadow:0 18px 50px rgba(0,0,0,.38);',
      'font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}',
      '.confirm-card h3{margin:0 0 8px;font-size:17px}',
      '.confirm-message{color:var(--muted,#a9b0c0);line-height:1.45;',
      'margin-bottom:12px}',
      '.confirm-summary{border:1px solid var(--border,#39424e);',
      'border-radius:8px;background:var(--panel-2,#1b222a);padding:10px;',
      'margin:10px 0}',
      '.confirm-summary-row{display:flex;justify-content:space-between;',
      'gap:16px;padding:3px 0;font-size:13px}',
      '.confirm-summary-row span:first-child{color:var(--muted,#a9b0c0)}',
      '.confirm-suppress{display:flex;align-items:center;gap:8px;',
      'margin-top:10px;font-size:12px;color:var(--muted,#a9b0c0)}',
      // The optional field. Styled here with the rest of the dialog, because
      // markup shared by every page renders unstyled everywhere except the
      // one whose stylesheet it came from.
      '.confirm-checkbox{display:flex;align-items:flex-start;gap:8px;',
      'margin-top:12px;font-size:13px;color:var(--text,#e6e9ef);',
      'cursor:pointer}',
      '.confirm-checkbox input{margin-top:2px;flex:0 0 auto}',
      '.confirm-field{display:block;margin-top:12px;font-size:13px;',
      'color:var(--muted,#a9b0c0)}',
      '.confirm-field-input{display:block;width:100%;box-sizing:border-box;',
      'margin-top:5px;min-height:38px;padding:0 10px;border-radius:7px;',
      'border:1px solid var(--edge,#3a4254);',
      'background:var(--surface,#11151f);color:var(--text,#e6e9ef);',
      'font-size:15px}',
      '.confirm-field-input:focus-visible{outline:2px solid ',
      'var(--focus-ring,#5b9dd9);outline-offset:1px}',
      '.confirm-actions{display:flex;justify-content:flex-end;gap:8px;',
      'margin-top:14px}',
      // The dialog's own buttons, so it does not depend on a page stylesheet
      // for the controls that answer it.
      '.confirm-actions button{font:inherit;padding:7px 14px;border-radius:8px;',
      'border:1px solid var(--border,#39424e);cursor:pointer;',
      'background:var(--accent,#3b82f6);color:var(--accent-ink,#fff)}',
      '.confirm-actions button:focus-visible{outline:2px solid ',
      'var(--accent,#3b82f6);outline-offset:2px}',
      // Quiet, but a real surface with a real border -- not a ghost outline.
      '.confirm-cancel{background:var(--panel-2,#1b222a)!important;',
      'color:var(--text,var(--fg,#e6e8ee))!important}',
      // A destructive action is distinguished by hue AND by form, because a
      // preset can bring accent and critical close together.
      '.confirm-danger{background:var(--state-error,#e5534b)!important;',
      'color:#fff!important;border-color:var(--state-error,#e5534b)!important;',
      'font-weight:600}',
    ].join('');
    (document.head || document.documentElement).appendChild(sheet);
  }

  function build() {
    style();
    let backdrop = document.getElementById('omni_confirm_backdrop');
    if (backdrop) return backdrop;
    backdrop = document.createElement('div');
    backdrop.id = 'omni_confirm_backdrop';
    backdrop.className = 'confirm-backdrop hidden';
    backdrop.innerHTML = '<div class="confirm-card" role="dialog" aria-modal="true">' +
      '<h3 class="confirm-title"></h3>' +
      '<div class="confirm-message"></div>' +
      '<div class="confirm-summary" style="display:none;"></div>' +
      // One optional field, for the cases where the question needs an answer
      // rather than a yes. A backup passphrase is a credential: prompt()
      // shows it in the clear, cannot carry the sentence explaining what it
      // is for, and is the native dialog this project does not use.
      // One optional tick box, for a second and more destructive choice --
      // the same contract as the dialog in index.html.
      '<label class="confirm-checkbox" style="display:none;">' +
      '<input class="confirm-checkbox-input" type="checkbox">' +
      '<span class="confirm-checkbox-label"></span></label>' +
      '<label class="confirm-field" style="display:none;">' +
      '<span class="confirm-field-label"></span>' +
      '<input class="confirm-field-input" type="password" ' +
      'autocomplete="off" spellcheck="false"></label>' +
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

    // An acknowledgement offers one button. Showing Cancel beside OK asks a
    // question that has no second answer -- and whatever a dialog hides for
    // one caller it must restore for the next.
    cancelBtn.style.display = options.acknowledge ? 'none' : '';

    // The optional field. Its value goes back through the caller's own
    // callback rather than being returned, so nothing this dialog keeps can
    // outlive the call -- and it is cleared on the way out either way.
    // Reset to unchecked every time, so the safe answer is the default
    // whenever this dialog opens rather than whatever the last caller left.
    const boxWrap = backdrop.querySelector('.confirm-checkbox');
    const boxInput = backdrop.querySelector('.confirm-checkbox-input');
    const boxLabel = backdrop.querySelector('.confirm-checkbox-label');
    const checkbox = options.checkbox;
    if (checkbox) {
      boxLabel.textContent = checkbox.label || '';
      boxInput.checked = false;
      boxWrap.style.display = '';
    } else {
      boxWrap.style.display = 'none';
    }

    const fieldWrap = backdrop.querySelector('.confirm-field');
    const fieldInput = backdrop.querySelector('.confirm-field-input');
    const fieldLabel = backdrop.querySelector('.confirm-field-label');
    const field = options.field;
    if (field) {
      fieldLabel.textContent = field.label || '';
      fieldInput.type = field.type || 'text';
      fieldInput.placeholder = field.placeholder || '';
      fieldInput.value = '';
      fieldWrap.style.display = '';
    } else {
      fieldWrap.style.display = 'none';
    }

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
    // Above whatever is already open, for the same reason as the dialog in
    // index.html: one shared z-index means document order decides, and
    // document order is not a decision anybody made.
    if (window.omniModalStack) window.omniModalStack.raise(backdrop);

    // Focus returns to whatever opened the dialog; without it, dismissing drops
    // the keyboard at the top of the document.
    const opener = document.activeElement;

    return new Promise(function (resolve) {
      function cleanup(result) {
        // The field's value goes to the caller on the way out and is wiped
        // here whatever the answer was, so a passphrase does not sit in a
        // detached DOM node for the life of the page.
        if (field) {
          if (result && typeof field.onValue === 'function') {
            field.onValue(fieldInput.value);
          }
          fieldInput.value = '';
        }
        if (checkbox) {
          if (result && typeof checkbox.onValue === 'function') {
            checkbox.onValue(!!boxInput.checked);
          }
          boxInput.checked = false;
          boxWrap.style.display = 'none';
        }
        backdrop.classList.add('hidden');
        if (window.omniModalStack) window.omniModalStack.release(backdrop);
        cancelBtn.style.display = '';
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
        // Enter in the field means "that is my answer", which is what anybody
        // who has just typed a passphrase expects.
        if (event.key === 'Enter' && field && event.target === fieldInput) {
          event.preventDefault();
          event.stopPropagation();
          cleanup(answer(true));
        }
      }
      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      backdrop.addEventListener('click', onBackdrop);
      document.addEventListener('keydown', onKeydown);
      // The costly answer sits behind the focus: Cancel takes it, so the
      // keyboard default and the visual default agree.
      // A dialog that asks for something puts the cursor in the thing it is
      // asking for. Otherwise the usual rule: where the costly answer is Yes,
      // Cancel takes the focus so the keyboard default and the visual default
      // agree.
      if (field) fieldInput.focus();
      else (options.danger ? cancelBtn : okBtn).focus();
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
  // The Multiview-routing question has been retired.
  //
  // A normal A/V route means single view, so routing a source to a display
  // that is compositing simply leaves the Multiview as part of the same
  // verified transaction. Nobody is asked, so there is nothing to remember
  // about not asking.
  //
  // The two preferences it wrote are dropped rather than merely ignored: a key
  // that is no longer written still reads, and a browser would otherwise carry
  // a suppression for a question that no longer exists.
  (function forgetRetiredPreferences() {
    const retired = ['matrix_multiview_exit_acknowledged',
                     'matrix_multiview_exit_suppressed'];
    try { retired.forEach((key) => sessionStorage.removeItem(key)); }
    catch (err) { /* storage off: there is nothing stored to drop */ }
    try { retired.forEach((key) => localStorage.removeItem(key)); }
    catch (err) { /* same */ }
  }());

})();

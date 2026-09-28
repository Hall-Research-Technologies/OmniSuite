// Sign out, on every page that can have a session to end.
//
// An administrator who signed in through Remote Access had no way out: no
// button anywhere in the application, so the only ways to stop being signed in
// were to clear cookies, close the browser, or know a URL. That is the defect
// this exists for.
//
// It is deliberately conditional. The operator sitting at the OmniSuite
// computer is trusted by the socket their request arrived on, not by a
// sign-in, so there is no session to end and a Logout button there would be a
// control that does nothing. The local trust model is not touched: this asks
// the server who it is talking to and stays out of the way when the answer is
// "the local operator".
//
// One module, injected, guarded -- rather than the same markup pasted into
// five pages with five copies to keep in step.
(function () {
  'use strict';
  if (window.__omniSignOutInstalled) return;
  window.__omniSignOutInstalled = true;

  const BUTTON_ID = 'omni_signout';

  function style() {
    if (document.getElementById('omni_signout_style')) return;
    const sheet = document.createElement('style');
    sheet.id = 'omni_signout_style';
    // Semantic tokens with literal fallbacks, so it follows every preset and
    // still renders on a page that defines none of them.
    sheet.textContent = [
      '#' + BUTTON_ID + '{font:inherit;font-size:13px;line-height:1;',
      'padding:8px 12px;margin-left:8px;border-radius:8px;cursor:pointer;',
      'border:1px solid var(--border,var(--edge,#39424e));',
      'background:var(--panel-2,var(--surface-elevated,#1b222a));',
      'color:var(--text,var(--fg,#e6e8ee));white-space:nowrap}',
      '#' + BUTTON_ID + ':hover{background-image:linear-gradient(',
      'var(--control-hover-veil,rgba(255,255,255,.06)),',
      'var(--control-hover-veil,rgba(255,255,255,.06)))}',
      '#' + BUTTON_ID + ':focus-visible{outline:2px solid ',
      'var(--focus-ring,var(--accent,#3b82f6));outline-offset:2px}',
      '#omni_signed_in{font-size:12px;color:var(--muted,var(--text-muted,#a9b0c0));',
      'margin-left:10px;white-space:nowrap}',
    ].join('');
    (document.head || document.documentElement).appendChild(sheet);
  }

  // Beside the Settings gear where there is one, so the control an operator's
  // eye has learned to find does not move between pages. Where there is no
  // gear, the end of the page's own header.
  function place(button, label) {
    const gear = document.getElementById('header_gear');
    if (gear && gear.parentNode) {
      gear.parentNode.insertBefore(label, gear.nextSibling);
      gear.parentNode.insertBefore(button, label.nextSibling);
      return true;
    }
    const header = document.querySelector('.admin-header, .remote-header, header');
    if (header) {
      header.appendChild(label);
      header.appendChild(button);
      return true;
    }
    return false;
  }

  async function signOut() {
    try {
      await fetch('/api/auth/logout', {
        method: 'POST', credentials: 'same-origin',
      });
    } catch (err) {
      // Going anyway: the cookie is the server's to clear, and a network
      // failure must not strand somebody signed in.
    }
    // The token this tab was holding belongs to a session that no longer
    // exists. Left behind, the next sign-in in this tab would present it.
    if (window.omniForgetCsrf) window.omniForgetCsrf();
    try { sessionStorage.removeItem('omni_csrf'); } catch (err) { /* nothing */ }
    location.replace('/login');
  }

  async function install() {
    if (!window.omniSession) return;
    let session;
    try {
      session = await window.omniSession();
    } catch (err) {
      return;
    }
    // No session to end: the local operator, or a page nobody is signed in on.
    if (!session || session.local || !session.username) return;
    if (document.getElementById(BUTTON_ID)) return;

    style();
    const button = document.createElement('button');
    button.id = BUTTON_ID;
    button.type = 'button';
    button.textContent = 'Sign out';
    button.addEventListener('click', signOut);

    const label = document.createElement('span');
    label.id = 'omni_signed_in';
    label.textContent = session.username;

    if (!place(button, label)) return;

    // Back, after signing out, must not restore a page full of authenticated
    // content from the browser's cache. The pages are served no-store, which
    // covers a reload; this covers the back-forward cache, which does not
    // re-request at all.
    window.addEventListener('pageshow', (event) => {
      if (event.persisted) location.reload();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { install(); });
  } else {
    install();
  }
}());

// The administrator's shell: a way back, and a way out.
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
  if (window.__omniAdminNavInstalled) return;
  window.__omniAdminNavInstalled = true;

  const BUTTON_ID = 'omni_signout';
  const BACK_ID = 'omni_back';

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
      '#' + BACK_ID + '{font:inherit;font-size:13px;line-height:1;',
      'padding:8px 12px;margin-left:8px;border-radius:8px;cursor:pointer;',
      'text-decoration:none;white-space:nowrap;',
      'border:1px solid var(--border,var(--edge,#39424e));',
      'background:var(--panel-2,var(--surface-elevated,#1b222a));',
      'color:var(--text,var(--fg,#e6e8ee))}',
      '#' + BACK_ID + ':focus-visible{outline:2px solid ',
      'var(--focus-ring,var(--accent,#3b82f6));outline-offset:2px}',
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

  // Is there already a visible way back to the application?
  //
  // The matrix pages own a tab bar whose first entry IS the application, so a
  // second control beside it would be two things pointing at one place. This
  // only fills a gap; it never duplicates what a page already owns.
  function hasAWayBack() {
    return [...document.querySelectorAll('a[href]')].some((link) => {
      if (link.getAttribute('href') !== '/') return false;
      if (link.id === BACK_ID) return true;
      if (!(link.textContent || '').trim()) return false;   // a logo, not a link
      const box = link.getBoundingClientRect();
      return box.width > 0 && box.height > 0;
    });
  }

  function addAWayBack() {
    if (hasAWayBack() || document.getElementById(BACK_ID)) return;
    const anchor = document.getElementById('header_gear')
      || document.querySelector('.admin-header, .remote-header, header');
    if (!anchor) return;
    style();
    const back = document.createElement('a');
    back.id = BACK_ID;
    back.href = '/';
    back.textContent = 'Back to OmniSuite';
    if (anchor.id === 'header_gear' && anchor.parentNode) {
      anchor.parentNode.insertBefore(back, anchor);
    } else {
      anchor.appendChild(back);
    }
  }

  // The Remote Access entry in the primary navigation.
  //
  // It points at a page whose whole job is managing remote sign-ins, so it is
  // there while Remote Access is on and absent while it is off -- a tab that
  // leads to accounts nobody can sign in with is navigation with no function.
  // The page that IS that entry keeps it while it is open, so an administrator
  // who typed the address is not left on a page the bar does not mention.
  //
  // One small read of local state; it touches no device.
  function placeRemoteTab(tabs) {
    if (tabs.querySelector('a[data-nav="remote"]')) return;
    const link = document.createElement('a');
    link.href = '/admin/users';
    link.className = 'tab';
    link.dataset.nav = 'remote';
    link.textContent = 'Remote Access';
    tabs.appendChild(link);
  }

  // The server says whether Remote Access is on in the page it serves, so the
  // tab is drawn synchronously, before the first paint, and never waits on a
  // request. Nothing here may remove it speculatively.
  function servedState() {
    const meta = document.querySelector('meta[name="omni-remote-access"]');
    return meta ? meta.getAttribute('content') === '1' : null;
  }

  async function syncRemoteAccessTab() {
    const tabs = document.querySelector('.header .tabs, .remote-header .tabs');
    if (!tabs) return;
    const served = servedState();
    if (served === true) placeRemoteTab(tabs);
    // The page was served with the answer; asking again would only open a
    // window in which the bar could change. A toggle reloads the page.
    if (served !== null) return;
    let enabled = false;
    try {
      const res = await fetch('/api/remote_access', {credentials: 'same-origin'});
      if (res.ok) {
        const body = await res.json();
        enabled = !!(body && body.enabled);
      }
    } catch (err) {
      return;                                   // unknown: leave the bar as it is
    }
    const existing = tabs.querySelector('a[data-nav="remote"]');
    const current = existing && existing.getAttribute('aria-current') === 'page';
    if (enabled && !existing) {
      placeRemoteTab(tabs);
    } else if (!enabled && existing && !current) {
      existing.remove();
    }
  }

  async function install() {
    syncRemoteAccessTab().catch(() => {});
    // A way back is not about being signed in: the local operator needs one
    // just as much, and they have no session at all.
    addAWayBack();
    // The signed-in name is served with the page, so the sign-out control is
    // drawn at once rather than after a session request -- an insertion beside
    // the Settings gear that arrives late moves the gear.
    const meta = document.querySelector('meta[name="omni-signed-in"]');
    const served = meta ? meta.getAttribute('content') : null;
    let session;
    if (served !== null) {
      session = served ? {username: served} : {local: true};
    } else {
      if (!window.omniSession) return;
      try {
        session = await window.omniSession();
      } catch (err) {
        return;
      }
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

  // The page says whether Remote Access is on, so the tab can be placed the
  // moment the bar exists in the document -- before the browser first paints
  // it -- instead of at DOMContentLoaded, which can be several frames later on
  // a heavy page. One observer, disconnected as soon as it has done its job.
  function placeWhenTheBarArrives() {
    if (servedState() !== true || document.readyState !== 'loading') return;
    if (typeof MutationObserver !== 'function') return;
    const find = () => document.querySelector('.header .tabs, .remote-header .tabs');
    const observer = new MutationObserver(() => {
      const tabs = find();
      if (!tabs) return;
      observer.disconnect();
      placeRemoteTab(tabs);
    });
    observer.observe(document.documentElement, {childList: true, subtree: true});
  }
  placeWhenTheBarArrives();

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { install(); });
  } else {
    install();
  }
}());

// Settings: one implementation, shared by every page.
//
// The Configuration dialog, the folder chooser it opens, and all of their
// wiring used to live inside ui/index.html, so Device Info was the only page
// that had them. Copying that markup into the three matrix pages would have
// produced four dialogs to keep in step; this module owns the markup, injects
// it wherever it is loaded, and reads and writes the same /api/config and
// /api/ui_preferences, so the state an operator sees is the same everywhere.
//
// The dialog is injected rather than written into each page for the same
// reason: one source of the markup, so a field added here appears on all four
// pages at once.

const SETTINGS_MODAL_HTML = `
<div id="cfg_backdrop" class="modal-backdrop">
  <div class="modal">
    <h3>Configuration</h3>
    <div class="modal-row" style="align-items:flex-start;">
      <label>Version</label>
      <div style="flex:1 1 auto;min-width:0;">
        <input id="cfg_app_version" type="text" value="" readonly style="width:100%;"/>
        <div id="cfg_update_status" class="update-status">Checking for updates...</div>
        <div class="update-actions">
          <button id="cfg_update_check" class="btn small secondary" type="button">Check Now</button>
          <a id="cfg_release_link" class="btn small secondary" target="_blank" rel="noopener noreferrer"
             href="https://github.com/Hall-Research-Technologies/omniSuite/releases">Releases</a>
          <a id="cfg_asset_link" class="btn small" target="_blank" rel="noopener noreferrer" hidden>Download</a>
        </div>
      </div>
    </div>
    <div class="modal-row">
      <label>Theme</label>
      <select id="cfg_theme" style="flex:1;">
        <option value="dark">Dark</option>
        <option value="light">Light</option>
      </select>
    </div>
    <div class="modal-row">
      <label>Username</label>
      <input id="cfg_username" type="text" value="admin"/>
    </div>
    <div class="modal-row">
      <label>Password</label>
      <div style="display:flex;gap:6px;flex:1;">
        <input id="cfg_password" type="password" value="password" style="flex:1;"/>
        <button id="cfg_pw_toggle" class="btn small secondary" type="button">👁️</button>
      </div>
    </div>
    <div class="modal-row">
      <label>Fallback Password</label>
      <div style="display:flex;gap:6px;flex:1;">
        <input id="cfg_fallback_password" type="password" value="Atlona" style="flex:1;"/>
        <button id="cfg_fallback_pw_toggle" class="btn small secondary" type="button">👁️</button>
      </div>
    </div>
    <div class="modal-row">
      <label>WS Port</label>
      <select id="cfg_ws_port" style="width:140px;">
        <option value="80" selected>80 (ws/http)</option>
        <option value="443">443 (wss/https)</option>
      </select>
    </div>
    <div class="modal-row">
      <label>Timeout (s)</label>
      <input id="cfg_timeout" type="number" value="4.5" step="0.1" style="width:100px;"/>
    </div>
    <div class="modal-row">
      <label>Concurrency</label>
      <select id="cfg_concurrency" style="width:100px;">
        <option>1</option><option>2</option><option>4</option><option selected>6</option><option>8</option><option>12</option>
      </select>
    </div>
    <div class="modal-row">
      <label>Firmware Path</label>
      <div class="file-chooser">
        <button id="cfg_firmware_browse" class="file-chooser-btn" type="button">Choose Folder</button>
        <input id="cfg_firmware_path" type="text" class="file-chooser-path"
               placeholder="No folder selected" aria-label="Firmware folder"/>
      </div>
    </div>
    <div class="settings-section">
      <h4>Appearance</h4>
      <div class="modal-row" style="align-items:flex-start;">
        <label>Layout</label>
        <div style="flex:1 1 auto;">
          <div id="cfg_template_choice" class="template-choice"></div>
          <div class="small" style="margin-top:6px;color:var(--muted);">
            Light and dark are chosen separately above and apply to every layout.
          </div>
        </div>
      </div>
      <div class="modal-row" style="align-items:flex-start;">
        <label>Colour preset</label>
        <div style="flex:1 1 auto;"><div id="cfg_preset_choice" class="preset-choice"></div></div>
      </div>
      <div class="modal-row" style="align-items:flex-start;">
        <label>Light background</label>
        <div style="flex:1 1 auto;">
          <div id="cfg_light_background"></div>
        </div>
      </div>
    </div>
    <!-- Configuration backup. Two buttons, because the day somebody needs
         the second one is not a day to be reading documentation. It sits
         above Remote Access because it is about this installation rather
         than about who may reach it. -->
    <div class="settings-section" id="cfg_backup_section">
      <h4>Configuration Backup</h4>
      <div class="modal-row" style="align-items:flex-start;">
        <label>Backup</label>
        <div style="flex:1 1 auto;min-width:0;">
          <div id="cfg_backup_blurb" class="small" hidden
               style="color:var(--muted);margin-bottom:8px;">
            Saves your accounts, device names, Multiview layouts, permissions
            and device sign-in details to one encrypted file. Keep it
            somewhere other than this computer &mdash; a backup that only
            exists on the machine that failed is not a backup.
          </div>
          <!-- Hidden until the SERVER says this browser is the local
               operator, so a remote administrator never sees the controls
               even for the moment that answer takes to arrive. Revealing on
               a positive answer is the safe direction; hiding on a negative
               one would show them first and think better of it. -->
          <div style="display:flex;gap:8px;flex-wrap:wrap;">
            <button id="cfg_backup_export" class="btn secondary" type="button"
                    hidden disabled>Export Configuration&hellip;</button>
            <button id="cfg_backup_restore" class="btn secondary" type="button"
                    hidden disabled>Restore Configuration&hellip;</button>
          </div>
          <!-- Shown instead of the buttons when Settings is open from another
               device. The server refuses these endpoints to anything but the
               OmniSuite computer, so offering them remotely produced a button
               that could only ever answer 404. -->
          <div id="cfg_backup_remote" class="small" hidden
               style="margin-top:4px;">
            Configuration backup and restore require local access to OmniSuite
            through 127.0.0.1. Open OmniSuite on the computer it is running on
            to export or restore a configuration.
          </div>
          <div id="cfg_backup_status" class="small"
               style="margin-top:8px;color:var(--muted);"></div>
        </div>
      </div>
    </div>
    <!-- Remote access. Off unless an administrator turns it on, and the
         section says in plain words what turning it on means: other devices
         on the chosen network can reach OmniSuite. It is deliberately the
         last section before About, because it is the one an operator visits
         rarely and should never reach by accident. -->
    <div class="settings-section" id="cfg_remote_section">
      <h4>Remote Access</h4>
      <div class="modal-row" style="align-items:flex-start;">
        <label>Status</label>
        <div style="flex:1 1 auto;min-width:0;">
          <div id="cfg_remote_status" class="update-status">Checking&hellip;</div>
          <div id="cfg_remote_url" class="small"
               style="margin-top:4px;color:var(--muted);"></div>
        </div>
      </div>

      <!-- Creating the first administrator. Only offered on the computer
           running OmniSuite, because that is the only place it is reachable. -->
      <div id="cfg_remote_bootstrap" hidden>
        <div class="modal-row" style="align-items:flex-start;">
          <label>First admin</label>
          <div style="flex:1 1 auto;min-width:0;">
            <div class="small" style="color:var(--muted);margin-bottom:6px;">
              Remote access needs an administrator account. Create one here, on
              this computer.
            </div>
            <input id="cfg_boot_user" type="text" placeholder="Username"
                   autocapitalize="none" spellcheck="false"
                   style="width:100%;margin-bottom:6px;">
            <input id="cfg_boot_pw" type="password" placeholder="Password"
                   autocomplete="new-password" style="width:100%;margin-bottom:6px;">
            <input id="cfg_boot_pw2" type="password" placeholder="Confirm password"
                   autocomplete="new-password" style="width:100%;margin-bottom:6px;">
            <button id="cfg_boot_create" class="btn small secondary" type="button">
              Create administrator</button>
          </div>
        </div>
      </div>

      <div id="cfg_remote_controls" hidden>
        <div class="modal-row">
          <label for="cfg_remote_adapter">Network</label>
          <select id="cfg_remote_adapter" style="flex:1;"></select>
        </div>

        <!-- Transport. HTTPS is the default and the only thing offered without
             a deliberate acknowledgement; the certificate row below says
             whether it can actually start. -->
        <div class="modal-row">
          <label for="cfg_remote_transport">Security</label>
          <select id="cfg_remote_transport" style="flex:1;">
            <option value="https">HTTPS &mdash; encrypted (recommended)</option>
            <option value="http">HTTP &mdash; not encrypted</option>
          </select>
        </div>

        <div class="modal-row" style="align-items:flex-start;" id="cfg_cert_row">
          <label>Certificate</label>
          <div style="flex:1 1 auto;min-width:0;">
            <div id="cfg_cert_status" class="update-status muted">&nbsp;</div>
            <div id="cfg_cert_detail" class="small"
                 style="margin-top:4px;color:var(--muted);"></div>
            <div class="update-actions" style="margin-top:6px;">
              <button id="cfg_cert_generate" class="btn small secondary" type="button">
                Create one for this network</button>
              <button id="cfg_cert_use" class="btn small secondary" type="button">
                Use my own files</button>
              <button id="cfg_cert_remove" class="btn small secondary" type="button" hidden>
                Remove certificate</button>
            </div>
            <div id="cfg_cert_files" hidden style="margin-top:8px;">
              <input id="cfg_cert_path" type="text" style="width:100%;margin-bottom:6px;"
                     placeholder="Path to certificate (.pem or .crt)">
              <input id="cfg_key_path" type="text" style="width:100%;margin-bottom:6px;"
                     placeholder="Path to private key (.pem or .key)">
              <button id="cfg_cert_save" class="btn small" type="button">
                Use these</button>
            </div>
          </div>
        </div>
        <div class="modal-row" style="align-items:flex-start;">
          <label>&nbsp;</label>
          <div style="flex:1 1 auto;">
            <button id="cfg_remote_toggle" class="btn small" type="button">
              Turn on remote access</button>
            <a id="cfg_remote_users" class="btn small secondary" href="/admin/users"
               style="text-decoration:none;margin-left:6px;">Manage users</a>
          </div>
        </div>
        <!-- Not buried, and not so loud the page is unusable. This one belongs
             to HTTP only: it used to be shown under HTTPS as well, because the
             cfg-warning class sets display:flex, which defeats the hidden
             attribute. The row is hidden as well as the callout, so no empty
             label is left behind. -->
        <div class="modal-row" style="align-items:flex-start;"
             id="cfg_remote_warning_row">
          <label>&nbsp;</label>
          <div class="cfg-warning" style="flex:1 1 auto;margin:0;"
               id="cfg_remote_warning">
            <div id="cfg_remote_warning_text">
              <strong>Connections are not encrypted.</strong>
              Anyone who can watch this network can read what is sent, including
              sign-in details. Use remote access only on a network you trust.
            </div>
          </div>
        </div>
        <!-- And what is true under HTTPS. Stated rather than left silent: the
             operator is entitled to know what protects the connection, and
             "browsers will warn" is the question they will actually have. -->
        <div class="modal-row" style="align-items:flex-start;"
             id="cfg_remote_secure_row" hidden>
          <label>&nbsp;</label>
          <div class="cfg-note" style="flex:1 1 auto;margin:0;"
               id="cfg_remote_secure">
            <div id="cfg_remote_secure_text"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- Who made it and on what terms. Deliberately small and deliberately in
         one place: this belongs in Settings, not on every page. The version
         above is the authoritative one, so it is not repeated here. -->
    <div class="modal-row cfg-about">
      <label>About</label>
      <div class="cfg-about-text">
        <div>Copyright &copy; 2026 Hall Research Technologies LLC</div>
        <!-- The Hall licence permits business use outright, so this says so
             plainly. The link below serves the licence the running build was
             actually made from, so the two can never disagree. -->
        <div class="cfg-about-terms">Source-available software.
          Free for personal and business use.</div>
        <div class="cfg-about-links">
          <a href="/license" target="_blank" rel="noopener noreferrer">License</a>
          <a href="/help" target="_blank" rel="noopener noreferrer">User Guide</a>
        </div>
      </div>
    </div>
    <div id="cfg_error" class="modal-error"></div>
    <div class="modal-buttons">
      <button id="cfg_user_guide" class="btn secondary" type="button" style="margin-right:auto;">User Guide</button>
      <button id="cfg_reset_ui" class="btn secondary" type="button"
              title="Restore the default layout, palette and colours. Devices, routes and settings are untouched.">Reset Appearance</button>
      <button id="cfg_ts_export" class="btn secondary" type="button">TS Dump</button>
      <button id="cfg_close" class="btn secondary" type="button">Close</button>
      <button id="cfg_save" class="btn" type="button">Save</button>
    </div>
  </div>
</div>
`;

const SETTINGS_BROWSER_HTML = `
<div id="cfg_browser_backdrop" class="modal-backdrop">
  <div class="modal" style="width:750px;">
    <h3>Select Folder</h3>
    <div class="browser-toolbar">
      <select id="cfg_browser_drive" class="browser-drive-select" title="Drive"></select>
      <button id="cfg_browser_home" class="btn small secondary" type="button">Home</button>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:10px;">
      <button id="cfg_browser_up" class="btn small secondary">📁 Up</button>
      <input id="cfg_browser_path" type="text" style="flex:1;" spellcheck="false"/>
      <button id="cfg_browser_go" class="btn small secondary" type="button">Go</button>
      <button id="cfg_browser_select" class="btn small">Select</button>
      <button id="cfg_browser_cancel" class="btn small secondary">Cancel</button>
    </div>
    <div class="browser-help">Type or paste a full folder path, choose a drive, or double-click a folder below.</div>
    <div id="cfg_browser_list" class="browser-list"></div>
  </div>
</div>
`;

// The first-run network notice, shown once per installation.
//
// It lives here for the same reason the Configuration dialog does: all four
// pages load this module, so one implementation means one dialog to keep in
// step. Whether it has already been seen is a server-side acknowledgement
// (/api/notices), not a localStorage flag -- localStorage is per browser and
// per origin, so the notice would have re-appeared on another machine, in
// another browser profile, and after site data was cleared.
//
// Advisory, not an error: no danger styling, and nothing about it says the
// firewall is blocking anything. Pressing Continue *is* the acknowledgement, so
// there is no separate "don't show again" checkbox -- one decision, one control.
const NETWORK_NOTICE_HTML = `
<div id="notice_backdrop" class="modal-backdrop notice-backdrop">
  <div class="modal notice-modal" role="dialog" aria-modal="true" aria-labelledby="notice_title">
    <h3 id="notice_title">Network Access Required</h3>
    <p class="notice-text">OmniSuite communicates directly with devices on the network.
       Your operating system or security software may block discovery and device
       communication.</p>
    <p class="notice-text">If prompted by your firewall, allow network access for OmniSuite
       on the networks where you manage AV devices.</p>
    <p class="notice-text">Depending on your environment, an application firewall exception
       or a temporary/system policy exception may be required.</p>
    <div class="modal-buttons">
      <button id="notice_guide" class="btn secondary" type="button" style="margin-right:auto;">User Guide</button>
      <button id="notice_continue" class="btn" type="button">Continue</button>
    </div>
  </div>
</div>
`;

function ensureSettingsMarkup() {
  // Injected once. A page that already contains the dialog (or a second call)
  // must not end up with two copies of every #cfg_ id.
  if (!document.getElementById('cfg_backdrop')) {
    document.body.insertAdjacentHTML('beforeend', SETTINGS_MODAL_HTML);
  }
  if (!document.getElementById('cfg_browser_backdrop')) {
    document.body.insertAdjacentHTML('beforeend', SETTINGS_BROWSER_HTML);
  }
  // Guarded like the others: a second call must not leave two #notice_continue
  // buttons, only one of which the wiring below would ever find.
  if (!document.getElementById('notice_backdrop')) {
    document.body.insertAdjacentHTML('beforeend', NETWORK_NOTICE_HTML);
  }
}

// `$` is defined by index.html but not by the matrix pages, so the shared code
// below cannot depend on the host page providing it.
if (typeof window.$ !== 'function') {
  window.$ = (selector) => document.querySelector(selector);
}

function initConfig() {
  const gear = $('#header_gear');
  const backdrop = $('#cfg_backdrop');
  const closeBtn = $('#cfg_close');
  const saveBtn = $('#cfg_save');
  const pwToggle = $('#cfg_pw_toggle');
  const pwInput = $('#cfg_password');
  const fallbackPwToggle = $('#cfg_fallback_pw_toggle');
  const fallbackPwInput = $('#cfg_fallback_password');
  const fbtn = $('#cfg_firmware_browse');
  const guideBtn = $('#cfg_user_guide');
  const backupExportBtn = $('#cfg_backup_export');
  const backupRestoreBtn = $('#cfg_backup_restore');
  if (backupExportBtn) {
    backupExportBtn.addEventListener('click', () => exportConfiguration());
  }
  if (backupRestoreBtn) {
    backupRestoreBtn.addEventListener('click', () => restoreConfiguration());
  }
  applyBackupLocality();
  // The layout selector lives with the rest of the application's settings.
  // Behaviour is untouched by it: this chooses spacing and grouping, nothing else.
  const templateHost = $('#cfg_template_choice');
  const presetHost = $('#cfg_preset_choice');
  const lightBgHost = $('#cfg_light_background');
  const renderAppearanceControls = () => {
    if (templateHost) renderTemplateChoice(templateHost);
    if (presetHost) renderPresetChoice(presetHost);
    if (lightBgHost) renderLightBackground(lightBgHost);
  };
  if (typeof loadAppearance === 'function') {
    loadAppearance().then(renderAppearanceControls);
  }

  // Light or dark. The dialog owns this control on every page, so its handler
  // belongs with the dialog -- leaving it to each page is how it came to be
  // wired on Device Info alone, while Configure, the A/V Matrix and the USB
  // Matrix rendered a Theme select that changed nothing.
  //
  // initConfig runs once per page, so this listener is registered once. The
  // select is re-read from the shared state whenever the dialog opens, which
  // also picks up a change made in another tab.
  const themeSelect = $('#cfg_theme');
  const syncThemeSelect = () => {
    if (themeSelect && typeof appearance === 'object') {
      themeSelect.value = appearance.theme === 'light' ? 'light' : 'dark';
    }
  };
  if (themeSelect) {
    syncThemeSelect();
    themeSelect.addEventListener('change', () => {
      if (typeof setTheme === 'function') {
        setTheme(themeSelect.value === 'light' ? 'light' : 'dark');
      }
    });
  }
  const cfgResetUiBtn = $('#cfg_reset_ui');
  if (cfgResetUiBtn) {
    cfgResetUiBtn.addEventListener('click', async () => {
      // Appearance only, and immediately visible, so no confirmation step:
      // discovered units, routes, scan settings, USB state, topology
      // acknowledgements and every credential are untouched by it.
      await resetUiPreferences();
      renderAppearanceControls();
      // resetUiPreferences returns the mode to dark; show that. The two header
      // controls this used to poke were removed from every page long ago.
      syncThemeSelect();
    });
  }

  const cfgTsExportBtn = $('#cfg_ts_export');
  
  // Store current password values so they persist even if user doesn't view them
  let currentPassword = 'password';
  let currentFallbackPassword = 'Atlona';

  // One way out, whichever way it is asked for, so the close button, the
  // backdrop and Escape cannot drift apart.
  const closeSettings = () => {
    backdrop.style.display = 'none';
    // Out of the modal stack, so it stops claiming a level and whatever is
    // still open comes back to the top.
    if (window.omniModalStack) window.omniModalStack.release(backdrop);
    // A modal returns focus to the control that opened it; without this,
    // dismissing the dialog drops the keyboard at the top of the document.
    if (gear && typeof gear.focus === 'function') gear.focus();
  };

  if (gear) gear.addEventListener('click', () => {
    syncThemeSelect();
    // Read when the dialog opens rather than on a timer: this state changes
    // only through this dialog, so polling it would be traffic for nothing.
    loadRemoteAccess();
    backdrop.style.display = 'flex';
    // Settings is injected at the end of <body> and every dialog shares one
    // z-index, so without a stack it would paint over anything opened from
    // it -- which is what hid the Export Configuration dialog underneath it.
    if (window.omniModalStack) window.omniModalStack.raise(backdrop);
  });
  if (closeBtn) closeBtn.addEventListener('click', closeSettings);

  backdrop.addEventListener('click', (e) => {
    if (e.target === backdrop) closeSettings();
  });

  // ---- Escape ----------------------------------------------------------
  //
  // Settings was the one dialog in OmniSuite that Escape did not close, on
  // every page, because the shared dialog had no binding for it.
  //
  // Registered here, at document level, exactly once: initConfig runs once per
  // page, so opening and closing the dialog any number of times adds no further
  // listeners. Binding on open would accumulate one per open.
  //
  // This handler defers rather than competes. It acts only when Settings is the
  // thing Escape should reach:
  //
  //   * the folder browser opens *above* Settings, so it takes Escape first;
  //   * the first-run network notice is an acknowledgement with one button, so
  //     it is deliberately not dismissible by a key;
  //   * any other modal on the page owns its own Escape, and this does nothing
  //     while one of them is showing.
  const isShowing = (element) =>
    !!element && getComputedStyle(element).display !== 'none';

  // The backdrops this module is responsible for. Anything else wearing
  // .modal-backdrop belongs to a page and handles its own keys.
  const OWN_BACKDROPS = ['cfg_backdrop', 'cfg_browser_backdrop', 'notice_backdrop'];
  const foreignModalShowing = () =>
    Array.from(document.querySelectorAll('.modal-backdrop'))
      .some((element) => !OWN_BACKDROPS.includes(element.id) && isShowing(element));

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;

    const browser = document.getElementById('cfg_browser_backdrop');
    if (isShowing(browser)) {
      browser.style.display = 'none';
      return;
    }
    if (isShowing(document.getElementById('notice_backdrop'))) return;
    if (foreignModalShowing()) return;
    if (!isShowing(backdrop)) return;

    closeSettings();
  });

  if (pwToggle && pwInput) {
    pwToggle.addEventListener('click', async (e) => {
      e.preventDefault();
      if (pwInput.type === 'password') {
        // Show password - fetch actual value from server
        try {
          const res = await fetch('/api/config?include_password=1');
          const cfg = await res.json();
          if (cfg.ok && cfg.password) {
            pwInput.value = cfg.password;
            currentPassword = cfg.password;
          }
        } catch (err) {}
        pwInput.type = 'text';
      } else {
        // Hide password
        pwInput.type = 'password';
      }
    });
  }

  if (fallbackPwToggle && fallbackPwInput) {
    fallbackPwToggle.addEventListener('click', async (e) => {
      e.preventDefault();
      if (fallbackPwInput.type === 'password') {
        // Show password - fetch actual value from server
        try {
          const res = await fetch('/api/config?include_password=1');
          const cfg = await res.json();
          if (cfg.ok && cfg.fallback_password) {
            fallbackPwInput.value = cfg.fallback_password;
            currentFallbackPassword = cfg.fallback_password;
          }
        } catch (err) {}
        fallbackPwInput.type = 'text';
      } else {
        // Hide password
        fallbackPwInput.type = 'password';
      }
    });
  }

  if (fbtn) {
    fbtn.addEventListener('click', (e) => {
      e.preventDefault();
      openFolderBrowser($('#cfg_firmware_path').value, '#cfg_firmware_path');
    });
  }

  if (guideBtn) {
    guideBtn.addEventListener('click', (e) => {
      e.preventDefault();
      window.open('/help', '_blank', 'noopener');
    });
  }

  // ---------------------------------------------------------- remote access
  //
  // Three states, and the section shows exactly one of them:
  //
  //   no administrator yet  -> create one, here, on this computer
  //   off                   -> choose a network and turn it on
  //   on                    -> the address other devices use, and how to stop
  //
  // Read once when Settings opens. No timer: this state changes only through
  // this dialog.
  let remoteState = null;

  function renderRemoteAccess() {
    const line = document.getElementById('cfg_remote_status');
    const url = document.getElementById('cfg_remote_url');
    const bootstrap = document.getElementById('cfg_remote_bootstrap');
    const controls = document.getElementById('cfg_remote_controls');
    const toggle = document.getElementById('cfg_remote_toggle');
    const adapter = document.getElementById('cfg_remote_adapter');
    if (!line) return;

    if (!remoteState) {
      line.textContent = 'Remote access is unavailable in this build.';
      line.className = 'update-status muted';
      if (bootstrap) bootstrap.hidden = true;
      if (controls) controls.hidden = true;
      return;
    }

    // Fill the network list FIRST, before any of the branches below can
    // return. The "configured but unavailable" branch used to return early,
    // after telling the operator to choose another network -- leaving the
    // dropdown it had just enabled completely empty.
    fillAdapterList(adapter, remoteState);

    // Creating the first administrator is only reachable from this computer,
    // so the form is only offered when the server says one is needed.
    const needsAdmin = !remoteState.has_admin;
    if (bootstrap) bootstrap.hidden = !needsAdmin;
    if (controls) controls.hidden = needsAdmin;

    if (needsAdmin) {
      line.textContent = 'Off — an administrator account is needed first.';
      line.className = 'update-status muted';
      if (url) url.textContent = '';
      return;
    }

    // What the transport notice says, decided before any branch can return.
    // The plaintext warning belongs to HTTP; an encrypted listener must never
    // carry a notice saying it is not.
    renderTransportNotice(remoteState);

    // Configured but unavailable is its own state. An installed system whose
    // network moved must not read as simply "off", or somebody re-enables it
    // on whatever adapter happens to be first in the list.
    if (!remoteState.enabled && remoteState.configured) {
      line.textContent = remoteState.restore_error
        || ('Configured for ' + (remoteState.configured_address || 'a network') +
            ', which is not available on this computer right now.');
      line.className = 'update-status remote-unavailable';
      if (url) {
        url.textContent = 'OmniSuite is running normally on this computer. ' +
          'Choose another network below to turn remote access back on.';
      }
      if (toggle) toggle.textContent = 'Turn on remote access';
      if (adapter) adapter.disabled = false;
      return;
    }

    const cert = remoteState.certificate || {};
    const mismatch = !!(remoteState.transport !== 'http' && cert.readable
                        && cert.matches_address === false);

    if (remoteState.enabled) {
      // Status has to describe what is actually happening. "ON — securely"
      // while the certificate names a different address is a claim the
      // operator's browser will contradict the moment they try it.
      if (remoteState.secure && mismatch) {
        line.textContent = 'ON — encrypted, but the certificate does not match '
          + 'this address.';
        line.className = 'update-status remote-unavailable';
      } else if (remoteState.secure) {
        line.textContent =
          'ON — other devices on this network can reach OmniSuite securely.';
        line.className = 'update-status update-available';
      } else {
        line.textContent =
          'ON — other devices on this network can reach OmniSuite.';
        line.className = 'update-status update-available';
      }
      if (url) {
        url.textContent = 'They use: ' + (remoteState.url || '') +
          (remoteState.sessions ? '  ·  ' + remoteState.sessions +
            ' signed in' : '');
      }
      if (toggle) toggle.textContent = 'Turn off remote access';
    } else {
      line.textContent = 'Off — OmniSuite is only reachable on this computer.';
      line.className = 'update-status';
      if (url) url.textContent = '';
      if (toggle) toggle.textContent = 'Turn on remote access';
    }

    // The certificate row, and what it can honestly claim.
    const certStatus = document.getElementById('cfg_cert_status');
    const certDetail = document.getElementById('cfg_cert_detail');
    const certRow = document.getElementById('cfg_cert_row');
    const transport = document.getElementById('cfg_remote_transport');
    if (transport && !transport.dataset.set) {
      transport.value = remoteState.transport === 'http' ? 'http' : 'https';
      transport.dataset.set = '1';
    }
    if (certRow) certRow.hidden = (transport && transport.value === 'http');

    if (certStatus) {
      // Offered only when a certificate exists, so the control never invites
      // an action that would do nothing.
      const remove = document.getElementById('cfg_cert_remove');
      if (remove) {
        remove.hidden = !(cert.present || cert.path_configured);
        remove.disabled = !!(remoteState.enabled && remoteState.secure);
        remove.title = remove.disabled
          ? 'Turn remote access off first — it is using this certificate.'
          : '';
      }
      const generate = document.getElementById('cfg_cert_generate');
      if (generate) {
        generate.disabled = !cert.can_generate;
        generate.title = cert.can_generate ? ''
          : 'This build cannot create a certificate. Supply one instead.';
      }
      if (remoteState.tls_error) {
        certStatus.textContent = remoteState.tls_error;
        certStatus.className = 'update-status remote-unavailable';
        if (certDetail) certDetail.textContent = '';
      } else if (!cert.present) {
        certStatus.textContent = 'No certificate yet.';
        certStatus.className = 'update-status muted';
        if (certDetail) {
          certDetail.textContent = 'Remote access cannot start over HTTPS '
            + 'until one is configured.';
        }
      } else if (!cert.readable) {
        certStatus.textContent = cert.error || 'The certificate cannot be read.';
        certStatus.className = 'update-status remote-unavailable';
      } else if (cert.expired) {
        certStatus.textContent = 'The certificate has expired.';
        certStatus.className = 'update-status remote-unavailable';
        if (certDetail) certDetail.textContent = 'Expired ' + cert.not_after;
      } else if (mismatch) {
        // The certificate is fine; it is for somewhere else. Which of the two
        // remedies applies depends on whose certificate it is, and OmniSuite
        // never replaces one the site supplied.
        certStatus.textContent = cert.generated
          ? 'This certificate is for a different address.'
          : 'The certificate you supplied does not name this address.';
        certStatus.className = 'update-status remote-unavailable';
        if (certDetail) {
          const names = (cert.san || []).join(', ') || cert.subject || 'another address';
          certDetail.textContent = cert.generated
            ? 'It names ' + names + ', and this network is '
              + (cert.for_address || 'a different address')
              + '. Choose "Create one for this network" to make a certificate '
              + 'that matches. Remote access will not start over HTTPS until '
              + 'you do.'
            : 'It names ' + names + ', and this network is '
              + (cert.for_address || 'a different address')
              + '. OmniSuite will not alter a certificate you supplied. '
              + 'Browsers will refuse the address above unless they reach '
              + 'OmniSuite by a name this certificate covers.';
        }
      } else {
        certStatus.textContent = cert.self_signed
          ? 'Self-signed certificate in place.'
          : 'Certificate in place.';
        certStatus.className = 'update-status';
        if (certDetail) {
          const parts = [];
          if (cert.subject) parts.push('For ' + cert.subject);
          if (typeof cert.days_remaining === 'number') {
            parts.push(cert.days_remaining + ' days left');
          }
          if (cert.fingerprint) {
            parts.push('Fingerprint ' + cert.fingerprint.slice(0, 16));
          }
          if (cert.self_signed) {
            parts.push('Browsers will warn the first time; check the '
                       + 'fingerprint matches.');
          }
          certDetail.textContent = parts.join(' \u00b7 ');
        }
      }
    }

    if (adapter) adapter.disabled = !!remoteState.enabled;
  }

  // What protects the connection, said once and said accurately.
  //
  // Two notices, exactly one of them on screen: the plaintext warning under
  // HTTP, and the encrypted statement under HTTPS. Both the callout and the
  // row it sits in are hidden, so nothing leaves an empty label behind. The
  // previous version toggled only the callout's `hidden` attribute, which
  // `.cfg-warning { display: flex }` overrode -- so the warning was shown
  // permanently, including over an encrypted listener.
  function renderTransportNotice(state) {
    const warnRow = document.getElementById('cfg_remote_warning_row');
    const warning = document.getElementById('cfg_remote_warning');
    const warningText = document.getElementById('cfg_remote_warning_text');
    const okRow = document.getElementById('cfg_remote_secure_row');
    const ok = document.getElementById('cfg_remote_secure');
    const okText = document.getElementById('cfg_remote_secure_text');
    const insecure = (state.transport === 'http');

    if (warnRow) warnRow.hidden = !insecure;
    if (warning) warning.hidden = !insecure;
    if (okRow) okRow.hidden = insecure;
    if (ok) ok.hidden = insecure;

    if (insecure && warningText) {
      warningText.replaceChildren();
      const strong = document.createElement('strong');
      strong.textContent = 'Connections are not encrypted. ';
      warningText.append(strong,
        'Anyone who can watch this network can read what is sent, including '
        + 'sign-in details. Use remote access only on a network you trust.');
      return;
    }
    if (insecure || !okText) return;

    const cert = state.certificate || {};
    okText.replaceChildren();
    const strong = document.createElement('strong');
    strong.textContent = 'Traffic is encrypted. ';
    let rest;
    if (cert.readable && cert.matches_address === false) {
      rest = 'The certificate in place is for a different address, so browsers '
           + 'will refuse this one until it is put right. See Certificate '
           + 'below.';
    } else if (cert.self_signed) {
      rest = 'The certificate is self-signed, so browsers will warn the first '
           + 'time somebody connects. That warning is about who is answering, '
           + 'not about whether the connection is protected.';
    } else if (cert.present) {
      rest = 'The certificate in place is issued rather than self-signed, so '
           + 'browsers should connect without warning.';
    } else {
      rest = 'Remote access cannot start over HTTPS until a certificate is '
           + 'configured.';
    }
    okText.append(strong, rest);
  }

  // The network list, rebuilt every time state is read.
  //
  // It used to be built once and cached behind a dataset flag, so an adapter
  // appearing -- or an address moving -- was never reflected. There is no
  // reason to cache six options.
  function fillAdapterList(adapter, state) {
    if (!adapter) return;
    const list = (state && state.adapters) || [];
    const previous = adapter.value;
    adapter.replaceChildren();

    if (!list.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = 'No suitable network found';
      adapter.appendChild(option);
      return;
    }

    list.forEach((entry) => {
      const option = document.createElement('option');
      // The value is the ADAPTER. Selecting a network says which adapter, not
      // which address -- the address is what changes.
      option.value = entry.id || entry.address;
      option.dataset.address = entry.address;
      option.textContent = entry.label || (entry.name + ' — ' + entry.address);
      adapter.appendChild(option);
    });

    // Keep what the operator had chosen if it is still offered; otherwise
    // prefer what is configured, and only then fall back to the first entry.
    const wanted = [previous, state.configured_adapter,
                    state.host, state.configured_address]
      .filter(Boolean);
    for (const candidate of wanted) {
      const match = [...adapter.options].find(
        (o) => o.value === candidate || o.dataset.address === candidate);
      if (match) { adapter.value = match.value; return; }
    }
    adapter.selectedIndex = 0;
  }

  async function loadRemoteAccess() {
    try {
      const res = await fetch('/api/remote_access', {credentials: 'same-origin'});
      remoteState = res.ok ? await res.json() : null;
    } catch (err) {
      remoteState = null;
    }
    renderRemoteAccess();
  }

  const bootCreate = document.getElementById('cfg_boot_create');
  if (bootCreate) {
    bootCreate.addEventListener('click', async () => {
      const username = (document.getElementById('cfg_boot_user') || {}).value || '';
      const password = (document.getElementById('cfg_boot_pw') || {}).value || '';
      const confirm = (document.getElementById('cfg_boot_pw2') || {}).value || '';
      if (password !== confirm) {
        window.toast && window.toast('The passwords do not match', 'error');
        return;
      }
      if (password.length < 8) {
        window.toast && window.toast('Use at least 8 characters', 'error');
        return;
      }
      bootCreate.disabled = true;
      try {
        const res = await fetch('/api/bootstrap/admin', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify({username: username.trim(), password, confirm}),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
          window.toast && window.toast('Administrator created', 'ok');
          ['cfg_boot_user', 'cfg_boot_pw', 'cfg_boot_pw2'].forEach((id) => {
            const field = document.getElementById(id);
            if (field) field.value = '';
          });
          await loadRemoteAccess();
        } else {
          window.toast && window.toast(data.error || 'Could not create it', 'error');
        }
      } catch (err) {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
      bootCreate.disabled = false;
    });
  }

  // ---- certificate -----------------------------------------------------
  const certGenerate = document.getElementById('cfg_cert_generate');
  const certUse = document.getElementById('cfg_cert_use');
  const certSave = document.getElementById('cfg_cert_save');
  const certFiles = document.getElementById('cfg_cert_files');
  const transportSelect = document.getElementById('cfg_remote_transport');

  if (transportSelect) {
    transportSelect.addEventListener('change', () => {
      const row = document.getElementById('cfg_cert_row');
      if (row) row.hidden = transportSelect.value === 'http';
      // One place decides which notice is shown, so choosing a transport here
      // and reading state from the server cannot disagree about it.
      renderTransportNotice({
        ...(remoteState || {}),
        transport: transportSelect.value === 'http' ? 'http' : 'https',
      });
    });
  }

  if (certUse && certFiles) {
    certUse.addEventListener('click', () => { certFiles.hidden = !certFiles.hidden; });
  }

  const certRemove = document.getElementById('cfg_cert_remove');
  if (certRemove) {
    certRemove.addEventListener('click', async () => {
      const cert = (remoteState && remoteState.certificate) || {};
      const supplied = !!cert.path_configured;
      // Removing one OmniSuite made deletes files; removing one the site
      // supplied only stops using it. The confirmation says which.
      const ok = !window.omniConfirm ? false : await window.omniConfirm({
        title: 'Remove this certificate?',
        message: supplied
          ? 'OmniSuite will stop using the certificate you supplied. The '
            + 'files themselves are left where they are. Remote access will '
            + 'not start over HTTPS until another certificate is configured.'
          : 'The certificate and its private key will be deleted from this '
            + 'computer. Remote access will not start over HTTPS until you '
            + 'create or supply another one.',
        summary: [{label: 'Certificate',
                   value: cert.subject || 'the configured certificate'}],
        confirmText: 'Remove',
        danger: true,
      });
      if (!ok) return;
      certRemove.disabled = true;
      try {
        const res = await fetch('/api/admin/certificate', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify({action: 'remove'}),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
          window.toast && window.toast('Certificate removed', 'ok');
        } else {
          window.toast && window.toast(data.error || 'Could not remove it', 'error');
        }
      } catch (err) {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
      certRemove.disabled = false;
      await loadRemoteAccess();
    });
  }

  if (certGenerate) {
    certGenerate.addEventListener('click', async () => {
      const adapter = document.getElementById('cfg_remote_adapter');
      const chosen = adapter ? adapter.selectedOptions[0] : null;
      // A certificate is made for an address, so this one wants the address
      // the chosen adapter currently has.
      const address = chosen ? (chosen.dataset.address || '') : '';
      if (!address) {
        window.toast && window.toast('Choose a network first', 'error');
        return;
      }
      certGenerate.disabled = true;
      try {
        const res = await fetch('/api/admin/certificate', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify({action: 'generate', address}),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
          window.toast && window.toast('Certificate created', 'ok');
        } else {
          window.toast && window.toast(data.error || 'Could not create it', 'error');
        }
      } catch (err) {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
      certGenerate.disabled = false;
      await loadRemoteAccess();
    });
  }

  if (certSave) {
    certSave.addEventListener('click', async () => {
      const certPath = (document.getElementById('cfg_cert_path') || {}).value || '';
      const keyPath = (document.getElementById('cfg_key_path') || {}).value || '';
      if (!certPath.trim() || !keyPath.trim()) {
        window.toast && window.toast('Both files are needed', 'error');
        return;
      }
      certSave.disabled = true;
      try {
        const res = await fetch('/api/admin/certificate', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify({action: 'use', cert_path: certPath.trim(),
                                key_path: keyPath.trim()}),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
          window.toast && window.toast('Certificate configured', 'ok');
          if (certFiles) certFiles.hidden = true;
        } else {
          window.toast && window.toast(data.error || 'Could not use those', 'error');
        }
      } catch (err) {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
      certSave.disabled = false;
      await loadRemoteAccess();
    });
  }

  const remoteToggle = document.getElementById('cfg_remote_toggle');
  if (remoteToggle) {
    remoteToggle.addEventListener('click', async () => {
      const turningOn = !(remoteState && remoteState.enabled);
      const adapter = document.getElementById('cfg_remote_adapter');
      // The option's value is the adapter; its address rides along for
      // display. The server resolves the adapter's current address itself.
      const chosen = adapter ? adapter.selectedOptions[0] : null;
      const adapterId = chosen ? chosen.value : '';
      const address = chosen ? (chosen.dataset.address || '') : '';
      if (turningOn && !adapterId) {
        window.toast && window.toast('Choose a network first', 'error');
        return;
      }
      const transport = transportSelect ? transportSelect.value : 'https';
      if (turningOn && window.omniConfirm) {
        const ok = await window.omniConfirm({
          title: 'Turn on remote access?',
          message: transport === 'http'
            ? 'Other devices on this network will be able to reach OmniSuite '
              + 'and sign in. Connections will NOT be encrypted, so sign-in '
              + 'details can be read by anyone watching this network.'
            : 'Other devices on this network will be able to reach OmniSuite '
              + 'and sign in over an encrypted connection.',
          summary: [{label: 'Network',
                     value: (chosen ? chosen.textContent : address)},
                    {label: 'Security',
                     value: transport === 'http' ? 'Not encrypted' : 'HTTPS'}],
          confirmText: 'Turn on',
          // Plaintext is the costly answer, so Cancel takes the focus.
          danger: transport === 'http',
        });
        if (!ok) return;
      }
      remoteToggle.disabled = true;
      try {
        const res = await fetch('/api/remote_access', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          credentials: 'same-origin',
          body: JSON.stringify({
            enabled: turningOn, address, transport,
            // Which adapter was chosen. The server resolves its current
            // address from this, so a stale address here cannot misdirect it.
            adapter_id: adapterId,
            // The server refuses plaintext without this, so it cannot be
            // reached by a request that simply omits a field.
            accept_insecure: transport === 'http',
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
          window.toast && window.toast(
            turningOn ? 'Remote access is on' : 'Remote access is off', 'ok');
        } else {
          window.toast && window.toast(data.error || 'That did not work', 'error');
        }
      } catch (err) {
        window.toast && window.toast('Could not reach OmniSuite', 'error');
      }
      remoteToggle.disabled = false;
      await loadRemoteAccess();
    });
  }

  if (cfgTsExportBtn) {
    cfgTsExportBtn.addEventListener('click', (e) => {
      e.preventDefault();
      $('#cfg_error').textContent = 'Preparing TS dump...';
      $('#cfg_error').style.color = 'var(--accent)';
      $('#cfg_error').style.display = 'block';
      window.location = '/api/ts_export';
    });
  }

  if (saveBtn) {
    saveBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      // Use stored values if input is empty
      const pwValue = pwInput.value || currentPassword || 'password';
      const fallbackPwValue = fallbackPwInput.value || currentFallbackPassword || 'Atlona';
      
      const payload = {
        username: $('#cfg_username').value || 'admin',
        password: pwValue,
        fallback_password: fallbackPwValue,
        ws_port: parseInt($('#cfg_ws_port').value || '80'),
        timeout: parseFloat($('#cfg_timeout').value || '4.5'),
        concurrency: parseInt($('#cfg_concurrency').value || '6'),
        firmware_path: $('#cfg_firmware_path').value
      };
      try {
        const res = await fetch('/api/config', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          $('#cfg_error').textContent = 'Configuration saved';
          $('#cfg_error').style.color = 'var(--accent)';
          $('#cfg_error').style.display = 'block';
        } else {
          $('#cfg_error').textContent = 'Save failed';
          $('#cfg_error').style.color = 'var(--danger)';
          $('#cfg_error').style.display = 'block';
        }
      } catch (e) {
        $('#cfg_error').textContent = 'Error: ' + e.message;
        $('#cfg_error').style.color = 'var(--danger)';
        $('#cfg_error').style.display = 'block';
      }
    });
  }

  // Load current config on modal open
  gear.addEventListener('click', async () => {
    try {
      const res = await fetch('/api/config?include_password=1');
      const cfg = await res.json();
      if (cfg.ok) {
        $('#cfg_error').style.display = 'none';
        $('#cfg_error').style.color = 'var(--danger)';
        $('#cfg_error').textContent = '';
        $('#cfg_username').value = cfg.username || 'admin';
        $('#cfg_ws_port').value = cfg.ws_port || 80;
        $('#cfg_timeout').value = cfg.timeout || 4.5;
        $('#cfg_concurrency').value = cfg.concurrency || 6;
        $('#cfg_firmware_path').value = cfg.firmware_path || '';
        const versionInput = $('#cfg_app_version');
        // Blank rather than an invented version: showing a placeholder is a claim.
        if (versionInput) versionInput.value = cfg.app_version || '';
        // Store current passwords for later use
        currentPassword = cfg.password || 'password';
        currentFallbackPassword = cfg.fallback_password || 'Atlona';
        // Keep the passwords prefilled, but hidden until the user clicks the eye.
        pwInput.value = currentPassword;
        fallbackPwInput.value = currentFallbackPassword;
        pwInput.type = 'password';
        fallbackPwInput.type = 'password';
      }
    } catch (e) {}
  });
}

function openFolderBrowser(startPath, targetSelector) {
  const backdrop = $('#cfg_browser_backdrop');
  const list = $('#cfg_browser_list');
  const pathInput = $('#cfg_browser_path');
  const selectBtn = $('#cfg_browser_select');
  const upBtn = $('#cfg_browser_up');
  const cancelBtn = $('#cfg_browser_cancel');
  const goBtn = $('#cfg_browser_go');
  const driveSelect = $('#cfg_browser_drive');
  const homeBtn = $('#cfg_browser_home');
  const homePath = startPath || '';

  let current = startPath || '';
  let parent = '';

  const showError = (message) => {
    list.innerHTML = '<div style="color:var(--danger);padding:12px;">Error: ' + (message || 'unknown') + '</div>';
  };

  const setBusy = () => {
    list.innerHTML = '<div style="padding:20px;text-align:center;"><span class="spinner"></span></div>';
  };

  const updateDrives = (drives) => {
    if (!driveSelect) return;
    const prior = driveSelect.value;
    driveSelect.innerHTML = '';
    if (!Array.isArray(drives) || !drives.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'Drive';
      driveSelect.appendChild(opt);
      driveSelect.disabled = true;
      return;
    }
    driveSelect.disabled = false;
    drives.forEach(drive => {
      const opt = document.createElement('option');
      opt.value = drive;
      opt.textContent = drive.replace(/\\$/, '');
      driveSelect.appendChild(opt);
    });
    const active = drives.find(d => (current || '').toLowerCase().startsWith(d.toLowerCase()));
    driveSelect.value = active || prior || drives[0];
  };

  async function load(path) {
    setBusy();
    try {
      const requested = (path || '').trim();
      const url = '/api/list_dir' + (requested ? '?path=' + encodeURIComponent(requested) : '');
      const res = await fetch(url);
      const j = await res.json();
      if (!j.ok) {
        showError(j.error || 'unknown');
        pathInput.value = requested;
        return;
      }

      current = j.path || requested || '';
      parent = j.parent || '';
      pathInput.value = current;
      updateDrives(j.drives || []);

      const folders = (j.entries || []).filter(ent => ent.is_dir);
      list.innerHTML = '';
      if (!folders.length) {
        list.innerHTML = '<div style="padding:12px;opacity:.6;">No folders</div>';
        return;
      }

      folders.forEach(ent => {
        const row = document.createElement('div');
        row.className = 'browser-item';
        row.textContent = ent.name;
        row.title = ent.path || ent.name;
        row.addEventListener('click', () => {
          list.querySelectorAll('.browser-item.selected').forEach(el => el.classList.remove('selected'));
          row.classList.add('selected');
          pathInput.value = ent.path || joinFolderPath(current, ent.name);
        });
        row.addEventListener('dblclick', () => load(ent.path || joinFolderPath(current, ent.name)));
        list.appendChild(row);
      });
    } catch (e) {
      showError(e.message || String(e));
    }
  }

  function joinFolderPath(base, child) {
    const normalized = (base || '').replace(/\//g, '\\');
    if (/^[A-Za-z]:\\?$/.test(normalized)) return normalized.replace(/\\?$/, '\\') + child;
    if (normalized.endsWith('\\')) return normalized + child;
    if (!normalized) return child;
    return normalized + '\\' + child;
  }

  if (upBtn) {
    upBtn.onclick = (e) => {
      e.preventDefault();
      if (parent) load(parent);
    };
  }
  if (goBtn) {
    goBtn.onclick = (e) => {
      e.preventDefault();
      load(pathInput.value);
    };
  }
  if (pathInput) {
    pathInput.onkeydown = (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        load(pathInput.value);
      }
    };
  }
  if (driveSelect) {
    driveSelect.onchange = () => {
      if (driveSelect.value) load(driveSelect.value);
    };
  }
  if (homeBtn) {
    homeBtn.onclick = (e) => {
      e.preventDefault();
      load(homePath);
    };
  }
  if (selectBtn) {
    selectBtn.onclick = (e) => {
      e.preventDefault();
      const target = document.querySelector(targetSelector);
      if (target) target.value = pathInput.value;
      backdrop.style.display = 'none';
    };
  }
  if (cancelBtn) {
    cancelBtn.onclick = (e) => {
      e.preventDefault();
      backdrop.style.display = 'none';
    };
  }
  if (backdrop) {
    backdrop.onclick = (e) => {
      if (e.target === backdrop) backdrop.style.display = 'none';
    };
  }

  backdrop.style.display = 'flex';
  load(current);
}

// The application version, from the one place that knows it.
//
// VERSION in the repository root is the authoritative source: the server reads
// it in _app_version(), the launcher reads it in resolve_version(), and the
// build workflow reads it for the release name. Everything the operator sees
// asks the server for that value rather than carrying its own copy -- which is
// how the footer and the Settings dialog came to show two different versions,
// neither of which matched the build.
async function applyAppVersion() {
  let version = '';
  try {
    const res = await fetch('/api/config');
    if (res.ok) {
      const cfg = await res.json();
      version = (cfg && cfg.app_version) || '';
    }
  } catch (err) {
    // Leave the fields blank rather than showing a version we cannot vouch for.
    return;
  }
  if (!version) return;
  const field = document.getElementById('cfg_app_version');
  if (field) field.value = version;
  const footer = document.getElementById('app_version_footer');
  if (footer) footer.textContent = 'OmniSuite Version ' + version;
}

// Whether a newer official release exists.
//
// The check is made by the server, cached there for hours, and is never on any
// polling path: this runs once when Settings is first wired, and again only when
// the operator presses Check Now. GitHub being unreachable is an ordinary state
// -- plenty of installations have no Internet -- so it is reported as "unable to
// check" and nothing else in the application is affected.
//
// Nothing is ever downloaded or installed from here. The buttons open the
// official releases page, and the server only ever hands back URLs it has
// validated as belonging to this project's repository.
const RELEASES_URL = 'https://github.com/Hall-Research-Technologies/omniSuite/releases';

// The four states the dialog can show. CHECKING is ours alone -- the server
// never returns it, because a server that is answering has finished checking.
const UPDATE_STATES = {
  CHECKING: 'CHECKING',
  UPDATE_AVAILABLE: 'UPDATE_AVAILABLE',
  CURRENT: 'CURRENT',
  UNABLE_TO_CHECK: 'UNABLE_TO_CHECK',
};

// The shared result, fetched once per page. Every surface that wants to know
// reads this rather than asking again, so opening four pages does not become
// four questions to GitHub.
let updateState = null;

function describeAge(checkedAt) {
  if (!checkedAt) return '';
  const then = Date.parse(checkedAt.replace(' ', 'T'));
  if (Number.isNaN(then)) return '';
  const hours = Math.floor((Date.now() - then) / 3600000);
  if (hours < 1) return 'last checked just now';
  if (hours === 1) return 'last checked 1h ago';
  if (hours < 48) return `last checked ${hours}h ago`;
  return `last checked ${Math.floor(hours / 24)}d ago`;
}

function renderUpdateStatus(data) {
  const line = document.getElementById('cfg_update_status');
  const release = document.getElementById('cfg_release_link');
  const asset = document.getElementById('cfg_asset_link');
  if (!line) return;

  // The releases link is always usable, including when the check failed -- that
  // is the whole point of it being a link and not just a status message.
  if (release) release.href = RELEASES_URL;
  if (asset) { asset.hidden = true; asset.removeAttribute('href'); }

  const status = (data && data.status) || UPDATE_STATES.UNABLE_TO_CHECK;
  const installed = (data && (data.installed_version || data.current_version)) || '';
  const latest = (data && data.latest_version) || '';

  if (status === UPDATE_STATES.CHECKING) {
    line.textContent = 'Checking for updates...';
    line.className = 'update-status muted';
    return;
  }

  if (status === UPDATE_STATES.UPDATE_AVAILABLE && latest) {
    // Deliberately loud: this is the one state an operator must not scroll past.
    line.textContent = `UPDATE AVAILABLE \u2014 ${latest}`;
    line.className = 'update-status update-available';
    if (release && data.release_url) release.href = data.release_url;
    if (asset && data.asset && data.asset.url) {
      asset.hidden = false;
      asset.href = data.asset.url;
      asset.textContent = `Download ${data.asset.platform} release`;
    }
    return;
  }

  if (status === UPDATE_STATES.CURRENT) {
    // A build ahead of the newest published release is current, not an error:
    // latest < installed is a successful check whose answer is "no update".
    const base = installed ? `No update available \u2014 ${installed} is current.`
                           : 'No update available.';
    const age = data && data.stale ? describeAge(data.checked_at) : '';
    line.textContent = age ? `${base} (${age})` : base;
    line.className = data && data.stale ? 'update-status muted' : 'update-status';
    if (release && data.release_url) release.href = data.release_url;
    return;
  }

  // Never "up to date" on the strength of a failed request.
  line.textContent = 'Unable to check for updates.';
  line.className = 'update-status muted';
}

// The Settings icon draws attention only while an available update has not been
// acknowledged. The status in the dialog is a standing fact and keeps saying an
// update exists; this is the one-time visual that stops once it has been seen.
function applyUpdateAlert(data) {
  const gear = document.getElementById('header_gear');
  if (!gear) return;
  const flashing = !!(data && data.status === UPDATE_STATES.UPDATE_AVAILABLE && data.alert);
  gear.classList.toggle('update-alert', flashing);
  if (flashing) {
    gear.setAttribute('aria-label', `Settings \u2014 update available ${data.latest_version || ''}`.trim());
    gear.title = `Update available: ${data.latest_version || ''}`.trim();
  } else {
    gear.setAttribute('aria-label', 'Settings');
    gear.title = 'Settings';
  }
}

// Opening Settings is the acknowledgement. Recorded against the version, so a
// later release alerts again and re-checking the same one does not.
async function acknowledgeUpdateAlert() {
  const gear = document.getElementById('header_gear');
  if (gear) gear.classList.remove('update-alert');       // stop immediately
  const version = updateState && updateState.latest_version;
  if (!version || !(updateState && updateState.alert)) return;
  try {
    await fetch('/api/update_ack', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({version}),
    });
    updateState = Object.assign({}, updateState, {alert: false, acknowledged_version: version});
  } catch (err) {
    // The icon has already stopped; failing to record it only means the alert
    // may return next time, which is better than pretending it was stored.
  }
  applyUpdateAlert(updateState);
}

async function checkForUpdates(force) {
  if (force) renderUpdateStatus({status: UPDATE_STATES.CHECKING});
  try {
    const res = await fetch('/api/update_check' + (force ? '?force=1' : ''));
    if (!res.ok) throw new Error('HTTP ' + res.status);
    updateState = await res.json();
  } catch (err) {
    // Not reaching GitHub is ordinary, and an older server with no such route
    // is a 404 -- neither is an application error, and neither may be shown as
    // "up to date".
    updateState = {status: UPDATE_STATES.UNABLE_TO_CHECK,
                   error_summary: (err && err.message) || 'request failed'};
  }
  renderUpdateStatus(updateState);
  applyUpdateAlert(updateState);
  return updateState;
}

// Shown once per installation, before the operator's first scan.
//
// Asked once per page load and never on a timer: the answer is held by the
// server and cannot change while the page is open except through this dialog.
// The markup is injected hidden (.modal-backdrop defaults to display:none), so
// nothing flashes on a page whose notice has already been acknowledged.
async function showFirstRunNotice() {
  let notice = null;
  try {
    const res = await fetch('/api/notices');
    if (!res.ok) return;
    const data = await res.json();
    notice = (data && data.notices && data.notices.network_access) || null;
  } catch (err) {
    // Unreadable is not "unacknowledged". Guessing the other way would show the
    // notice on every load, which is the one behaviour it must never have.
    return;
  }
  if (!notice || notice.acknowledged) return;

  const backdrop = document.getElementById('notice_backdrop');
  const guideBtn = document.getElementById('notice_guide');
  const continueBtn = document.getElementById('notice_continue');
  if (!backdrop || !continueBtn) return;

  if (guideBtn) {
    guideBtn.addEventListener('click', (e) => {
      e.preventDefault();
      // Opens beside the notice rather than replacing it: the operator still has
      // to press Continue, so reading the guide is not itself an acknowledgement.
      window.open('/help', '_blank', 'noopener');
    });
  }
  continueBtn.addEventListener('click', async (e) => {
    e.preventDefault();
    continueBtn.disabled = true;
    try {
      await fetch('/api/notices/ack', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({notice: 'network_access'})
      });
    } catch (err) {
      // The dialog closes regardless. A failed write means the notice is shown
      // once more, which is the harmless direction; trapping the operator behind
      // an un-dismissable dialog is not.
    }
    backdrop.style.display = 'none';
  });
  // Clicking the backdrop deliberately does nothing: a stray click outside must
  // not record an acknowledgement the operator never gave.
  backdrop.style.display = 'flex';
  continueBtn.focus();
}

// Injects the markup, then runs the same wiring on every page.
function initSettings() {
  ensureSettingsMarkup();
  initConfig();
  applyAppVersion();
  // Not awaited: the page is usable while the acknowledgement is being read.
  showFirstRunNotice();
  const checkNow = document.getElementById('cfg_update_check');
  if (checkNow) checkNow.addEventListener('click', () => checkForUpdates(true));
  const gear = document.getElementById('header_gear');
  if (gear) gear.addEventListener('click', acknowledgeUpdateAlert);
  // Not awaited, so the dialog is usable immediately whatever GitHub does. The
  // server has already checked once at startup and cached it, so this is a
  // local read rather than a request to GitHub.
  checkForUpdates(false);
}

// Self-starting, so a page only has to include the script and provide a
// #header_gear button.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initSettings, {once: true});
} else {
  initSettings();
}


// ------------------------------------------------------- configuration backup
//
// Two buttons and as few decisions as the job allows. The day somebody needs
// Restore is not a day to be reading documentation, so the flow states what is
// about to happen at each step and refuses clearly when it cannot continue.
//
// Nothing here ever logs, stores or echoes the passphrase. It exists for the
// length of one request and is not kept.

// Whether this browser is the operator sitting at the OmniSuite computer.
//
// Asked of the SERVER, not worked out from the address bar. A hostname is not
// an authority -- 127.0.0.1 can be forwarded, a machine can be reached by its
// own LAN address, and either way what decides is the socket the request
// arrived on, which only the server can see. `omniIsLocal` reads the same
// session answer the CSRF wrapper already fetched, so this costs no request.
//
// This is presentation, never permission. The endpoints are LOCAL_ONLY and
// answer 404 to anything else whatever this function returns; hiding the
// buttons only stops offering a control that could not work, and stops the
// remote page describing what a backup would contain.
async function applyBackupLocality() {
  const exportBtn = document.getElementById('cfg_backup_export');
  const restoreBtn = document.getElementById('cfg_backup_restore');
  const notice = document.getElementById('cfg_backup_remote');
  const blurb = document.getElementById('cfg_backup_blurb');
  if (!exportBtn && !restoreBtn) return;

  let local = true;
  try {
    if (window.omniIsLocal) local = await window.omniIsLocal();
  } catch (err) {
    // If the server cannot be asked, assume remote and offer nothing. The
    // safe direction: a hidden control on the OmniSuite computer is a
    // nuisance, an offered one elsewhere is a button that answers 404 and a
    // description of what the backup holds.
    local = false;
  }

  [exportBtn, restoreBtn].forEach((button) => {
    if (!button) return;
    button.hidden = !local;
    button.disabled = !local;
  });
  if (notice) notice.hidden = !!local;
  // The explanation of what a backup contains is for somebody who can make
  // one. Remotely it is a description of where the credentials live.
  if (blurb) blurb.hidden = !local;
  if (!local) backupStatus('');
}

// The same number the server enforces. Length is the only rule:
// letters, digits, punctuation and spaces are all accepted, and no
// shape is demanded, because a passphrase nobody can remember ends up
// written on the box the backup is kept in.
const BACKUP_MIN_PASSPHRASE = 8;

function backupStatus(text, tone) {
  const node = document.getElementById('cfg_backup_status');
  if (!node) return;
  node.textContent = text || '';
  node.style.color = tone === 'error' ? 'var(--state-error, #e5534b)'
    : (tone === 'ok' ? 'var(--state-success, #3fb950)' : 'var(--muted)');
}

async function askForPassphrase(opts) {
  // The shared dialog, with a password field injected into it. Never a browser
  // prompt(): this is a credential, and prompt() shows it in the clear and
  // cannot carry the sentence explaining what it is for.
  if (typeof window.omniConfirm !== 'function') {
    backupStatus('Cannot ask for a passphrase on this page.', 'error');
    return null;
  }
  let value = null;
  const ok = await window.omniConfirm({
    title: opts.title,
    message: opts.message,
    confirmText: opts.confirmText,
    danger: !!opts.danger,
    field: {
      type: 'password',
      label: opts.label || 'Backup passphrase',
      placeholder: opts.placeholder || '',
      onValue: (v) => { value = v; },
    },
  });
  return ok ? (value || '') : null;
}

async function exportConfiguration() {
  const passphrase = await askForPassphrase({
    title: 'Export configuration',
    message: 'Choose a passphrase for this backup. You will need it to '
           + 'restore, and nobody can recover it for you. At least 8 '
           + 'characters \u2014 letters, numbers, symbols and spaces are all '
           + 'fine.',
    confirmText: 'Export',
    placeholder: 'At least 8 characters',
  });
  if (passphrase === null) return;
  // Said here rather than after a round trip, because the operator has the
  // box open and can simply type more. The server checks it too and is what
  // actually decides -- this is courtesy, not a control.
  if (passphrase.length < BACKUP_MIN_PASSPHRASE) {
    backupStatus('That passphrase is too short. Use at least '
                 + BACKUP_MIN_PASSPHRASE + ' characters.', 'error');
    return;
  }

  backupStatus('Preparing the backup…');
  try {
    const res = await fetch('/api/backup/export', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      credentials: 'same-origin',
      body: JSON.stringify({passphrase: passphrase}),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      backupStatus(body.error || 'The backup could not be created.', 'error');
      return;
    }
    const blob = await res.blob();
    const name = filenameFrom(res.headers.get('Content-Disposition'))
                 || 'OmniSuite-backup.omnibak';
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    // Revoked later, never on this tick.
    //
    // The browser has not necessarily started reading the blob by the time
    // click() returns, and revoking the URL underneath it cancels the
    // download -- which looks exactly like a button that did nothing. A
    // minute is far longer than any save needs and the blob is released
    // either way when the page goes.
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    backupStatus('Saved ' + name + '. If your browser asked where to put it, '
                 + 'the save window may be behind this one. Keep the file '
                 + 'somewhere other than this computer.', 'ok');
  } catch (err) {
    backupStatus('The backup could not be created.', 'error');
  }
}

function filenameFrom(disposition) {
  const match = /filename="([^"]+)"/.exec(disposition || '');
  return match ? match[1] : '';
}

function chooseBackupFile() {
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.omnibak';
    input.addEventListener('change', () => {
      resolve(input.files && input.files[0] ? input.files[0] : null);
    });
    input.click();
  });
}

async function restoreConfiguration() {
  const file = await chooseBackupFile();
  if (!file) return;

  const passphrase = await askForPassphrase({
    title: 'Restore from ' + file.name,
    message: 'Enter the passphrase this backup was created with. Nothing is '
           + 'changed until you have seen what it contains and confirmed.',
    confirmText: 'Open backup',
    placeholder: 'Backup passphrase',
  });
  if (passphrase === null) return;

  // Step one: decrypt and validate, changing nothing.
  backupStatus('Checking the backup…');
  const summary = await postBackup('/api/backup/inspect', file, passphrase);
  if (!summary.ok) {
    backupStatus(summary.error || 'That backup could not be opened.', 'error');
    return;
  }

  // Step two: show what is in it, and what will not survive the move.
  const s = summary.summary || {};
  const lines = [];
  if (s.created_at) lines.push('Created ' + s.created_at);
  if (s.omnisuite_version) lines.push('From OmniSuite ' + s.omnisuite_version);
  (s.sections || []).forEach((row) => {
    lines.push('• ' + backupSectionLabel(row.name)
               + (row.detail ? ' — ' + row.detail : ''));
  });
  if (s.devices) lines.push('• ' + s.devices + ' known device(s)');
  (s.notes || []).forEach((note) => lines.push('⚠ ' + note));
  if ((s.machine_specific || []).length) {
    lines.push('⚠ Settings that belong to the other computer (network '
               + 'adapter, folders, certificate) are not copied. This '
               + 'computer keeps its own.');
  }

  const go = await window.omniConfirm({
    title: 'Replace this configuration?',
    message: 'This replaces the accounts, permissions, device names, '
           + 'Multiview layouts and device sign-in details on this computer '
           + 'with the ones in the backup. A copy of the current '
           + 'configuration is saved first and put back automatically if '
           + 'anything goes wrong.\n\n' + lines.join('\n'),
    confirmText: 'Restore',
    danger: true,
  });
  if (!go) { backupStatus('Nothing was changed.'); return; }

  // Step three: apply.
  backupStatus('Restoring…');
  const result = await postBackup('/api/backup/restore', file, passphrase);
  if (!result.ok) {
    backupStatus(result.error || 'The restore did not complete.', 'error');
    return;
  }
  renderRestoreResult(result);
}

function backupSectionLabel(name) {
  return {
    accounts: 'User accounts and permissions',
    device_names: 'Device names',
    multiview: 'Multiview layouts',
    multiview_groups: 'Multiview groups',
    usb_associations: 'USB associations',
    usb_extenders: 'USB scan ranges',
    scan_preferences: 'Scan settings',
    device_credentials: 'Device sign-in',
    remote_access: 'Remote access',
    notices: 'Acknowledged notices',
    appearance: 'Appearance',
    topology_notices: 'Network notices',
  }[name] || name;
}

async function postBackup(path, file, passphrase) {
  const form = new FormData();
  form.append('passphrase', passphrase);
  form.append('backup', file, file.name);
  try {
    const res = await fetch(path, {method: 'POST', body: form,
                                   credentials: 'same-origin'});
    return await res.json();
  } catch (err) {
    return {ok: false, error: 'OmniSuite could not be reached.'};
  }
}

function renderRestoreResult(result) {
  const parts = [];
  const applied = result.applied || {};
  parts.push('Restored ' + (applied.written || []).length + ' item(s).');
  const devices = result.devices || {};
  if (devices.matched) {
    parts.push(devices.matched + ' device(s) recognised.');
  }
  if (devices.awaiting_scan) {
    parts.push(devices.awaiting_scan + ' device(s) from the backup have not '
               + 'been seen yet — run a Scan.');
  }
  backupStatus(parts.join(' '), 'ok');

  const notes = [];
  (applied.attention || []).forEach((row) => {
    notes.push('• ' + backupSectionLabel(row.section) + ' — '
               + row.setting + ': this computer keeps ' + row.now
               + ' (the backup had ' + row.was + ')');
  });
  const recovery = result.admin_recovery || {};
  if ((recovery.administrators || []).length) {
    notes.push('• Administrator account(s) from the backup: '
               + recovery.administrators.join(', ') + '. ' + recovery.note);
  }
  if (result.status === 'RESTORED — RESTART REQUIRED' || result.error) {
    notes.push('• ' + (result.error || ''));
  }
  notes.push('• Run a Scan so OmniSuite finds the equipment again.');

  if (typeof window.omniConfirm === 'function') {
    window.omniConfirm({
      title: 'Restore complete',
      message: parts.join(' ') + '\n\n' + notes.join('\n'),
      confirmText: 'Close',
      acknowledge: true,
    });
  }
}

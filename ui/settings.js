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
    <div class="settings-section" id="cfg_comm_section">
      <h4>Device Communication</h4>
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
    </div>
    <div class="settings-section" id="cfg_fw_section">
      <h4>Firmware</h4>
    <div class="modal-row">
      <label>Firmware Path</label>
      <div class="file-chooser">
        <button id="cfg_firmware_browse" class="file-chooser-btn" type="button">Choose Folder</button>
        <input id="cfg_firmware_path" type="text" class="file-chooser-path"
               placeholder="No folder selected" aria-label="Firmware folder"/>
      </div>
    </div>
      <!-- What is available from Hall Research, checked automatically: once when
           the application starts and then every seven days. It only reports. Nothing is
           downloaded and no device is changed by it. -->
      <div class="modal-row" style="align-items:flex-start;" id="cfg_fw_row">
        <label>Availability</label>
        <div style="flex:1 1 auto;min-width:0;">
          <div id="cfg_fw_status" class="update-status muted">Checking&hellip;</div>
          <div id="cfg_fw_detail" class="small" style="margin-top:4px;color:var(--muted);"></div>
          <div class="update-actions" style="margin-top:6px;">
            <button id="cfg_fw_check" class="btn small secondary" type="button">Check Now</button>
          </div>
        </div>
      </div>
    </div>
    <div class="settings-section">
      <h4>Appearance</h4>
      <div class="modal-row">
        <label>Theme</label>
        <select id="cfg_theme" style="flex:1;">
          <option value="dark">Dark</option>
          <option value="light">Light</option>
        </select>
      </div>
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
    <!-- The local application port. Its own section, and deliberately not part
         of Remote Access: it concerns the address OmniSuite answers on at THIS
         computer, which Remote Access neither uses nor changes. Hidden until the
         server says this browser is the local operator and a launcher is
         present; the logic lives in ui/local-port.js. -->
    <div class="settings-section" id="cfg_localport_section" hidden>
      <h4>Local Application Port</h4>
      <div class="modal-row" style="align-items:flex-start;">
        <label for="cfg_localport_fixed">Local port</label>
        <div style="flex:1 1 auto;min-width:0;">
          <div id="cfg_localport_controls">
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
              <input id="cfg_localport_fixed" type="checkbox">
              <span>Use a fixed TCP port</span>
            </label>
            <div style="display:flex;align-items:center;gap:8px;margin-top:8px;">
              <label for="cfg_localport_port" style="min-width:auto;">Port:</label>
              <input id="cfg_localport_port" type="text" readonly size="6"
                     inputmode="numeric" autocomplete="off" spellcheck="false"
                     style="width:90px;">
              <button id="cfg_localport_change" class="btn small secondary" type="button" hidden>
                Change Port</button>
            </div>
            <div id="cfg_localport_url" class="small" style="margin-top:6px;"></div>
            <div id="cfg_localport_help" class="small" style="margin-top:4px;color:var(--muted);"></div>
          </div>
          <div id="cfg_localport_mode_note" class="small" style="margin-top:4px;color:var(--muted);" hidden></div>
          <div id="cfg_localport_message" class="small" style="margin-top:6px;" role="status" hidden></div>
          <div id="cfg_localport_temp" class="cfg-warning" style="margin-top:8px;display:block;" hidden>
            <div><strong id="cfg_localport_temp_title"></strong></div>
            <div id="cfg_localport_temp_configured" class="small"></div>
            <div id="cfg_localport_temp_current" class="small"></div>
            <div class="update-actions" style="margin-top:6px;">
              <button id="cfg_localport_temp_retry" class="btn small secondary" type="button">Retry Fixed Port</button>
              <button id="cfg_localport_temp_make" class="btn small secondary" type="button">Make Fixed</button>
              <button id="cfg_localport_temp_change" class="btn small secondary" type="button">Change Fixed Port</button>
            </div>
          </div>
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
          <div id="cfg_remote_protect" class="small"
               style="margin-top:4px;color:var(--muted);" hidden>
            Assigned devices are protected from Clear Units while Remote Access
            is enabled.
          </div>
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

        <!-- Transport. A first-time setup starts on HTTP, labelled as not
             encrypted, and turning it on needs a deliberate acknowledgement;
             HTTPS is the recommended choice once a certificate exists, and the
             certificate row below says whether it can actually start. -->
        <div class="modal-row">
          <label for="cfg_remote_transport">Security</label>
          <select id="cfg_remote_transport" style="flex:1;">
            <option value="http">HTTP &mdash; not encrypted (default)</option>
            <option value="https">HTTPS &mdash; checking certificate&hellip;</option>
          </select>
        </div>
        <div class="modal-row" style="align-items:flex-start;">
          <label></label>
          <div class="small" style="flex:1;color:var(--muted);" id="cfg_remote_transport_hint">
            HTTP is not encrypted: anyone on this network can read what is sent,
            including sign-ins. HTTPS is encrypted, but its certificate names this
            computer&rsquo;s address, so it stops matching when DHCP gives the
            computer a different one.
          </div>
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

    <!-- Configuration backup. Two buttons, because the day somebody needs
         the second one is not a day to be reading documentation. It follows
         Remote Access because it covers everything above it, including who
         may reach this installation. -->
    <div class="settings-section" id="cfg_backup_section">
      <h4>Backup and Restore</h4>
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
    <div class="settings-section" id="cfg_info_section">
      <h4>Application Information</h4>
    <div class="modal-row" style="align-items:flex-start;">
      <label>Version</label>
      <div style="flex:1 1 auto;min-width:0;">
        <input id="cfg_app_version" type="text" value="" readonly style="width:100%;"/>
        <div id="cfg_update_status" class="update-status">Checking for updates...</div>
        <div class="update-actions">
          <button id="cfg_update_check" class="btn small secondary" type="button">Check Now</button>
          <a id="cfg_release_link" class="btn small secondary" target="_blank" rel="noopener noreferrer"
             href="#" hidden>Releases</a>
          <a id="cfg_asset_link" class="btn small" target="_blank" rel="noopener noreferrer" hidden>Download</a>
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
    <p class="notice-text"><span data-app-name>OmniSuite</span> communicates directly with devices on the network.
       Your operating system or security software may block discovery and device
       communication.</p>
    <p class="notice-text">If prompted by your firewall, allow network access for <span data-app-name>OmniSuite</span>
       on the networks where you manage AV devices.</p>
    <p class="notice-text">Depending on your environment, an application firewall exception
       or a temporary/system policy exception may be required.</p>
    <p class="notice-text" id="notice_macos"><strong>On macOS,</strong> the first scan also asks for
       Local Network access. Choose Allow. If you chose Don’t Allow, open System Settings →
       Privacy &amp; Security → Local Network and switch on <span data-app-name>OmniSuite</span>.</p>
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
  loadLocalPortModule();
}

// The local port control is its own module (ui/local-port.js), loaded here so
// every page that has Settings has it without a script tag per page. Guarded:
// a second call must not load it twice. It initialises itself once the markup
// above exists, and a page where it cannot load simply has no such section.
function loadLocalPortModule() {
  if (window.omniLocalPort || document.getElementById('omni_local_port_script')) return;
  const script = document.createElement('script');
  script.id = 'omni_local_port_script';
  script.src = '/ui/local-port.js?v=20261105-localport';
  script.async = true;
  (document.head || document.documentElement).appendChild(script);
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

  // One quiet line, said only while it is true: with Remote Access on, Clear
  // Units keeps the units that Users and groups are assigned. An advisory is
  // not a warning banner, so it is a line in this section and nowhere else.
  function renderProtectNote(state) {
    const note = document.getElementById('cfg_remote_protect');
    if (note) note.hidden = !(state && state.enabled);
  }

  function renderRemoteAccess() {
    const line = document.getElementById('cfg_remote_status');
    const url = document.getElementById('cfg_remote_url');
    const bootstrap = document.getElementById('cfg_remote_bootstrap');
    const controls = document.getElementById('cfg_remote_controls');
    const toggle = document.getElementById('cfg_remote_toggle');
    const adapter = document.getElementById('cfg_remote_adapter');
    if (!line) return;

    // Decided before any branch can return, so no path leaves it stale.
    renderProtectNote(remoteState);

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
    describeHttpsOption(remoteState);

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
      // What the certificate IS decides what is said here, and what the Security
      // option below says -- never which option happens to be selected, and
      // never merely that HTTPS was chosen. See certificateKind.
      const kind = certificateKind(cert, remoteState);
      certStatus.textContent = certificateWords(cert, remoteState);
      certStatus.className = 'update-status'
        + (kind === 'required' ? ' remote-unavailable' : '');
      if (certDetail) certDetail.textContent = certificateDetail(cert, kind);
    }

    if (adapter) adapter.disabled = !!remoteState.enabled;
  }

  // BEGIN certificate wording
  //
  // What the certificate actually is, decided once from the server's
  // description of it and never from which Security option is selected.
  // "Active" means OmniSuite has a usable certificate to serve HTTPS with. It
  // says nothing about whether a browser trusts whoever issued it, and nothing
  // here may be read as saying so.
  //
  //   self_signed  the certificate OmniSuite created, usable for this address
  //   custom       a certificate the administrator supplied, usable
  //   required     missing, unreadable, expired, unusable (for instance a key
  //                that does not belong to it), or one OmniSuite created for
  //                another network
  //
  // A supplied certificate is never judged against the adapter: it may carry a
  // name OmniSuite has no way of knowing about, and refusing it would break a
  // correct deployment. Its address mismatch is reported as a caution only.
  // A certificate OmniSuite creates carries the computer's name as its common
  // name, so "who is it for" is answered by the addresses it names. Older and
  // supplied certificates carry none and fall back to their subject.
  function certificateFor(cert) {
    const addresses = (cert && cert.addresses) || [];
    return addresses.join(', ') || (cert && cert.subject) || '';
  }

  // Creating a certificate when one already exists REPLACES it. The
  // fingerprint changes, so every browser that accepted the old one warns
  // again, and a certificate the site supplied stops being the one in use (its
  // files are not touched). Cancel takes the focus (danger), so Enter does not
  // replace anything. With nothing to replace nothing is asked, and if the
  // shared dialog is missing the replacement is refused rather than made
  // without the sentence that explains it.
  async function confirmCertificateReplacement(existing, address) {
    if (!existing || !(existing.present || existing.path_configured)) return true;
    if (typeof window.omniConfirm !== 'function') return false;
    return !!(await window.omniConfirm({
      title: 'Replace this certificate?',
      message: (existing.path_configured
          ? 'OmniSuite will stop using the certificate you supplied (its '
            + 'files are left where they are) and use a new one it creates. '
          : 'The current certificate and its private key will be replaced. ')
        + 'The new certificate names this computer and ' + address
        + ' and is valid for three years. Its fingerprint will be '
        + 'different, so browsers that accepted the old one will warn '
        + 'again. Remote Access keeps serving the old certificate until '
        + 'it is turned off and on again.',
      summary: [{label: 'New certificate for', value: address}],
      confirmText: 'Replace',
      danger: true,
    }));
  }

  function certificateKind(cert, state) {
    if (!cert || !cert.present || !cert.readable || cert.expired) return 'required';
    // Asked of the same loader the listener uses, so "active" cannot be claimed
    // for a certificate whose key is missing or does not match.
    if (cert.usable === false) return 'required';
    const supplied = cert.generated === false;
    if (!supplied && cert.for_address && cert.matches_address === false) {
      return 'required';
    }
    return supplied ? 'custom' : 'self_signed';
  }

  function httpsLabel(kind) {
    if (kind === 'self_signed') return 'HTTPS \u2014 encrypted \u00b7 certificate active';
    if (kind === 'custom') return 'HTTPS \u2014 encrypted \u00b7 custom certificate active';
    return 'HTTPS \u2014 certificate required';
  }

  const REQUIRED_GUIDANCE = 'HTTPS certificate is missing or invalid. Turn off '
    + 'Remote Access, then create or install a certificate for this network '
    + 'before enabling HTTPS.';

  function certificateWords(cert, state) {
    if (!cert) return '';
    const kind = certificateKind(cert, state);
    if (kind === 'required') {
      let why = '';
      if (cert.present && !cert.readable) {
        why = (cert.error || 'The certificate file could not be read.') + ' ';
      } else if (cert.present && cert.expired) {
        why = 'The certificate has expired. ';
      } else if (cert.present && cert.usable === false) {
        why = (cert.unusable_reason || 'The certificate cannot be used.') + ' ';
      } else if (cert.present && cert.for_address && cert.matches_address === false) {
        why = 'The certificate OmniSuite created is for '
          + (certificateFor(cert) || 'another address') + ', not for '
          + cert.for_address + '. ';
      }
      return why + REQUIRED_GUIDANCE;
    }
    const where = cert.for_address || certificateFor(cert);
    let words = (kind === 'custom' ? 'Custom certificate active'
                                   : 'Self-signed certificate active')
      + (where ? ' for ' + where : '')
      + (typeof cert.days_remaining === 'number'
          ? ' (' + cert.days_remaining + ' days left)' : '') + '.';
    if (kind === 'self_signed') {
      words += ' Your browser may warn because this certificate is self-signed.';
    }
    if (cert.fingerprint) words += ' Fingerprint: ' + cert.fingerprint;
    return words;
  }

  // The second line: only where there is something further to do or know.
  function certificateDetail(cert, kind) {
    if (!cert || !cert.present) return '';
    const names = (cert.san || []).join(', ') || certificateFor(cert) || 'another address';
    if (kind === 'required') {
      if (cert.expired) return 'Expired ' + (cert.not_after || '');
      if (cert.readable && cert.usable !== false && cert.for_address
          && cert.matches_address === false) {
        return 'It names ' + names + ', and this network is ' + cert.for_address
          + '. Choose "Create one for this network" to make a certificate that '
          + 'matches. Remote access will not start over HTTPS until you do.';
      }
      return '';
    }
    if (kind === 'custom' && cert.for_address && cert.matches_address === false) {
      return 'It names ' + names + ', and this network is ' + cert.for_address
        + '. OmniSuite will not alter a certificate you supplied. Browsers will '
        + 'refuse the address above unless they reach OmniSuite by a name this '
        + 'certificate covers.';
    }
    return '';
  }

  // The Security selector's HTTPS entry describes the certificate that exists.
  function describeHttpsOption(state) {
    const select = document.getElementById('cfg_remote_transport');
    if (!select) return;
    const option = Array.from(select.options || []).find((o) => o.value === 'https');
    if (option) {
      option.textContent = httpsLabel(certificateKind((state || {}).certificate, state));
    }
  }
  // END certificate wording

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
      rest = 'A certificate you supplied is in use. Whether browsers accept '
           + 'it depends on who issued it and on the name it carries.';
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
      if (entry.preferred) option.dataset.preferred = '1';
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
    // Nothing chosen and nothing configured: start on the adapter the server
    // ranked best (a physical Ethernet port before Wi-Fi, before anything
    // virtual), and only then on the first entry. A saved or configured
    // choice was honoured above and never reaches this line.
    const best = [...adapter.options].find((o) => o.dataset.preferred === '1');
    adapter.selectedIndex = best ? best.index : 0;
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
      // Creating a certificate when one already exists REPLACES it, which is a
      // decision and is asked. The single-flight guard is taken before the
      // dialog opens, so two clicks cannot stack two dialogs.
      if (certGenerate.dataset.busy === '1') return;
      certGenerate.dataset.busy = '1';
      let go = false;
      try {
        go = await confirmCertificateReplacement(
          (remoteState && remoteState.certificate) || {}, address);
      } finally {
        certGenerate.dataset.busy = '';
      }
      if (!go) return;
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
      const transport = transportSelect ? transportSelect.value : 'http';
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
      
      const payload = {
        username: $('#cfg_username').value || 'admin',
        ws_port: parseInt($('#cfg_ws_port').value || '80'),
        timeout: parseFloat($('#cfg_timeout').value || '4.5'),
        concurrency: parseInt($('#cfg_concurrency').value || '6'),
        firmware_path: $('#cfg_firmware_path').value
      };
      // Only a password that is actually in the field is sent. A blank field is not "the
      // default password": the server keeps what it has, so a remote administrator, who is
      // never shown the passwords, can save any other setting without replacing them.
      if (pwInput.value) payload.password = pwInput.value;
      if (fallbackPwInput.value) payload.fallback_password = fallbackPwInput.value;
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

  // ---- firmware availability ---------------------------------------------
  //
  // What Hall Research publishes against what is installed, per model. Read from
  // the last good catalogue, so opening Settings never waits on the Internet.
  // Reports only: nothing here downloads firmware or touches a device.
  function loadFirmwareModule() {
    if (window.omniFirmware) return Promise.resolve();
    return new Promise((resolve) => {
      const script = document.createElement('script');
      script.src = '/ui/firmware-status.js?v=20261030-firmware';
      script.onload = () => resolve();
      script.onerror = () => resolve();
      document.head.appendChild(script);
    });
  }

  function renderFirmware(data) {
    const status = $('#cfg_fw_status');
    const detail = $('#cfg_fw_detail');
    if (!status || !detail || !window.omniFirmware) return;
    const summary = window.omniFirmware.summarize(data);
    status.textContent = summary.text;
    status.className = 'update-status' + (summary.tone === 'ok' ? ' ok'
      : (summary.tone === 'warn' ? ' warn' : ' muted'));
    detail.replaceChildren();
    if (summary.detail) {
      const line = document.createElement('div');
      line.textContent = summary.detail;
      detail.appendChild(line);
    }
    const rows = summary.rows || [];
    if (!rows.length) return;
    const table = document.createElement('table');
    table.className = 'fw-table';
    const head = document.createElement('tr');
    ['Model', 'Installed', 'Latest', 'Status'].forEach((label) => {
      const th = document.createElement('th');
      th.textContent = label;
      head.appendChild(th);
    });
    table.appendChild(head);
    rows.forEach((row) => {
      const tr = document.createElement('tr');
      const cells = [row.model + (row.units > 1 ? ' × ' + row.units : ''),
                     row.installed || '—', row.latest || '—', row.label];
      cells.forEach((text, index) => {
        const td = document.createElement('td');
        td.textContent = text;
        if (index === 3) {
          td.className = 'fw-state fw-' + row.state;
          // The reason belongs next to an Unknown, in words.
          if (row.state === 'unknown' && row.reason) {
            const why = document.createElement('div');
            why.className = 'fw-reason';
            why.textContent = row.reason;
            td.appendChild(why);
          }
        }
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
    detail.appendChild(table);
  }

  async function refreshFirmware() {
    await loadFirmwareModule();
    if (!window.omniFirmware) return;
    renderFirmware(await window.omniFirmware.fetchStatus());
  }

  const fwCheck = $('#cfg_fw_check');
  if (fwCheck) {
    fwCheck.addEventListener('click', async () => {
      await loadFirmwareModule();
      if (!window.omniFirmware) return;
      fwCheck.disabled = true;
      const status = $('#cfg_fw_status');
      if (status) { status.textContent = 'Checking Hall Research…'; status.className = 'update-status muted'; }
      try {
        const data = await window.omniFirmware.check();
        if (data) {
          renderFirmware(data);
        } else {
          renderFirmware(await window.omniFirmware.fetchStatus());
          window.toast && window.toast('The firmware check could not start', 'error');
        }
      } finally {
        fwCheck.disabled = false;
      }
    });
  }

  // Load current config on modal open
  gear.addEventListener('click', async () => {
    refreshFirmware().catch(() => {});
    try {
      // The device passwords are shown only on the computer OmniSuite runs on; asking for
      // them from anywhere else is refused, which used to leave this whole dialog empty.
      // Everywhere else the ordinary settings load and the password fields stay blank.
      const local = window.omniIsLocal ? await window.omniIsLocal() : true;
      const res = await fetch(local ? '/api/config?include_password=1' : '/api/config');
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
        currentPassword = cfg.password || (local ? 'password' : '');
        currentFallbackPassword = cfg.fallback_password || (local ? 'Atlona' : '');
        // Keep the passwords prefilled, but hidden until the user clicks the eye. A page
        // that was not shown them leaves the fields blank, and blank means "unchanged".
        pwInput.value = currentPassword;
        fallbackPwInput.value = currentFallbackPassword;
        pwInput.placeholder = local ? '' : 'Unchanged';
        fallbackPwInput.placeholder = local ? '' : 'Unchanged';
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
    const box = document.createElement('div');
    box.style.cssText = 'color:var(--danger);padding:12px;';
    box.textContent = 'Error: ' + (message || 'unknown');
    list.replaceChildren(box);
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
// The releases page is this product's own and comes from the server (release_url), which
// always returns one, including when the check failed. There is no address written here:
// The address belongs to the product, not to this file.

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
  if (release && data && data.release_url) { release.href = data.release_url; release.hidden = false; }
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
    // Deliberately loud: this is the one state an operator must not scroll past. Red text, and bold in a
    // border, so it is told apart by form as well as hue in a preset where red sits close to the accent.
    line.textContent = `UPDATE AVAILABLE \u2014 ${latest}`;
    line.className = 'update-status bad';
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
    // Green: the check succeeded and the answer is that nothing newer exists. A last answer that is
    // now stale is still that answer, with its age said beside it.
    line.className = 'update-status ok';
    if (release && data.release_url) release.href = data.release_url;
    return;
  }

  // Never "up to date" on the strength of a failed request, and never red either: nothing says an
  // update exists. Amber, the state for "cannot say".
  line.textContent = 'Unable to check for updates.';
  line.className = 'update-status warn';
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
  let appName = '';
  try {
    const res = await fetch('/api/notices');
    if (!res.ok) return;
    const data = await res.json();
    notice = (data && data.notices && data.notices.network_access) || null;
    appName = (data && data.app_name) || '';
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
  // The product's own name, as data from the server rather than text written into this file.
  if (appName) {
    backdrop.querySelectorAll('[data-app-name]').forEach((el) => { el.textContent = appName; });
  }

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
  // What this does to the accounts that exist here, first and in words, because
  // "replaces the accounts" is true and tells nobody who is about to be signed out.
  accountImpactLines(summary.account_impact).reverse()
    .forEach((line) => lines.unshift(line));
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
    // Replacing the accounts and permissions is the costly answer, so Cancel
    // holds the focus. `danger` alone only colours the button on the page's
    // own copy of the dialog (ui/index.html): without this, Enter restored.
    defaultAction: 'cancel',
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

// The sentences a restore owes the people it affects. Pure: it reads what the
// server worked out and builds text, so the same words are shown whatever the
// dialog is made of.
function accountImpactLines(impact) {
  if (!impact || !impact.applies) return [];
  const named = (names) => {
    const shown = names.slice(0, 6).join(', ');
    return names.length > 6 ? shown + ' and ' + (names.length - 6) + ' more' : shown;
  };
  if (impact.known === false) {
    return ['⚠ Accounts on this computer will be replaced by the backup\'s. '
            + 'Anyone signed in may be signed out and Direct Access links may '
            + 'stop working.'];
  }
  const removed = impact.removed || [];
  const replaced = impact.replaced || [];
  const lines = [];
  if (impact.in_backup === 0 && impact.current > 0) {
    lines.push('⚠ This backup has no accounts. All ' + impact.current
               + ' account(s) on this computer, including every administrator, '
               + 'will be removed. You can create a new administrator from this '
               + 'computer afterwards.');
  } else if (removed.length) {
    lines.push('⚠ ' + removed.length + ' account(s) on this computer are not in '
               + 'the backup and will be removed: ' + named(removed) + '.');
  }
  if (removed.length) {
    lines.push('⚠ Anyone signed in as a removed account is signed out at once, '
               + 'and its Direct Access link stops working.');
  }
  if (replaced.length) {
    lines.push('⚠ ' + replaced.length + ' account(s) come back with a different '
               + 'password, link, role or permissions (' + named(replaced) + '). Their open '
               + 'sign-ins end. Accounts that do not change stay signed in.');
  }
  return lines;
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
  if (result.sessions_ended) {
    parts.push(result.sessions_ended + ' sign-in(s) ended.');
  }
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
  } else if (recovery.note) {
    // No administrator came with the backup: say what to do about it rather
    // than leaving the operator at a screen that offers no way in.
    notes.push('• ' + recovery.note);
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

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
    // A modal returns focus to the control that opened it; without this,
    // dismissing the dialog drops the keyboard at the top of the document.
    if (gear && typeof gear.focus === 'function') gear.focus();
  };

  if (gear) gear.addEventListener('click', () => {
    syncThemeSelect();
    backdrop.style.display = 'flex';
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

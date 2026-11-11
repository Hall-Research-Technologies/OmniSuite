/*
 * Administrator account management.
 *
 * Permissions are assigned by picking devices from a list, never by editing
 * JSON, and what is stored is the device's stable identity rather than its
 * name or address -- the same identity the authorization gate checks, so the
 * assignment screen and the enforcement agree by construction.
 */
(function () {
  'use strict';

  const state = {
    users: [],
    devices: [],
    editing: null,        // the account being edited, or null when creating
    selected: {sources: new Set(), destinations: new Set(), groups: new Set()},
    // Display groups, as the server describes them (members named, never
    // addressed), and the one being edited in the group dialog.
    groups: [],
    editingGroup: null,
    groupMembers: new Set(),
    // identity -> the alias currently typed, for names not yet saved.
    pendingAliases: new Map(),
    // identity -> what refers to the device (Users, Display Groups), as the
    // server derived it. The page renders this; it never works it out itself.
    assignments: new Map(),
    assignmentsExpanded: new Set(),
    tab: 'accounts',
  };

  const el = (id) => document.getElementById(id);

  function csrf() {
    try { return sessionStorage.getItem('omni_csrf') || ''; }
    catch (err) { return ''; }
  }

  async function api(path, options) {
    const opts = Object.assign({credentials: 'same-origin'}, options || {});
    opts.headers = Object.assign({}, opts.headers || {});
    if (opts.method && opts.method !== 'GET') {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-OmniSuite-CSRF'] = csrf();
    }
    const res = await fetch(path, opts);
    if (res.status === 401) {
      location.replace('/login?next=' + encodeURIComponent(location.pathname));
      throw new Error('signed out');
    }
    return {status: res.status, body: await res.json().catch(() => ({}))};
  }

  // ------------------------------------------------------------- table
  function featureSummary(user) {
    if (user.role === 'admin') return 'Everything';
    const names = {av: 'A/V', usb: 'USB'};
    const features = (user.permissions && user.permissions.features) || [];
    if (!features.length) return 'Nothing assigned yet';
    const counts = user.permissions || {};
    const parts = features.map((f) => names[f] || f);
    const sources = (counts.sources || []).length;
    const destinations = (counts.destinations || []).length;
    const groups = (counts.groups || []).length;
    return parts.join(', ') + ' · ' + sources + ' source' +
      (sources === 1 ? '' : 's') + ', ' + destinations + ' display' +
      (destinations === 1 ? '' : 's') +
      (groups ? ', ' + groups + ' group' + (groups === 1 ? '' : 's') : '');
  }

  function cell(row, label, node) {
    const td = document.createElement('td');
    td.dataset.label = label;
    if (typeof node === 'string') td.textContent = node;
    else td.appendChild(node);
    row.appendChild(td);
    return td;
  }

  function enabledAdminCount() {
    return state.users.filter((u) => u.role === 'admin' && u.enabled).length;
  }

  function renderUsers() {
    const body = el('users_body');
    body.replaceChildren();

    state.users.forEach((user) => {
      const row = document.createElement('tr');
      cell(row, 'Name', user.username);

      const role = document.createElement('span');
      role.className = 'role-pill' + (user.role === 'admin' ? ' admin' : '');
      role.textContent = user.role === 'admin' ? 'Administrator' : 'User';
      cell(row, 'Role', role);

      const status = document.createElement('span');
      status.className = 'state-pill' + (user.enabled ? '' : ' disabled');
      status.textContent = user.enabled ? 'Active' : 'Disabled';
      cell(row, 'Status', status);

      cell(row, 'Can use', featureSummary(user));

      const actions = document.createElement('div');
      actions.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;';

      actions.appendChild(button('Edit', () => openEditor(user)));

      // The backend refuses to disable, demote or delete the last enabled
      // administrator. The UI says so up front instead of letting somebody
      // press a button and read an error.
      const last = user.role === 'admin' && user.enabled &&
                   enabledAdminCount() === 1;
      const toggle = button(user.enabled ? 'Disable' : 'Enable', () =>
        act(user, {action: 'set_enabled', enabled: !user.enabled},
            user.enabled ? 'Disable ' + user.username + '?' : null));
      const remove = button('Delete', () =>
        act(user, {action: 'delete'},
            'Delete ' + user.username + '? This cannot be undone.', true));
      if (last) {
        [toggle, remove].forEach((b) => {
          b.disabled = true;
          b.title = 'This is the only active administrator.';
        });
      }
      actions.append(toggle, remove);
      cell(row, '', actions);
      body.appendChild(row);
    });
  }

  function button(label, onClick) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn-quiet';
    b.textContent = label;
    b.addEventListener('click', onClick);
    return b;
  }

  async function act(user, payload, question, danger) {
    if (question && window.omniConfirm) {
      const ok = await window.omniConfirm({
        title: 'Please confirm',
        message: question,
        confirmText: danger ? 'Delete' : 'Continue',
        danger: !!danger,
      });
      if (!ok) return;
    }
    const {status, body} = await api('/api/admin/users/' + user.id,
                                     {method: 'POST', body: JSON.stringify(payload)});
    if (status === 200 && body.ok) {
      window.toast && window.toast('Saved', 'ok');
      await load();
    } else {
      window.toast && window.toast(body.error || 'That did not work', 'error');
    }
  }

  // ------------------------------------------------------------ editor
  function renderAssign(kind, hostId, countId, filterId) {
    const host = el(hostId);
    host.replaceChildren();
    const wantRole = kind === 'sources' ? 'encoder' : 'decoder';
    const devices = state.devices.filter((d) => d.role === wantRole);

    if (!devices.length) {
      const empty = document.createElement('div');
      empty.className = 'empty';
      empty.style.padding = '14px';
      empty.textContent = 'No ' + (kind === 'sources' ? 'sources' : 'displays') +
        ' have been discovered yet. Run a Scan first.';
      host.appendChild(empty);
      return;
    }

    devices.forEach((device) => {
      const row = document.createElement('label');
      row.className = 'assign-row';
      row.dataset.search = [device.name, device.model, device.ip,
                            device.location].filter(Boolean)
        .join(' ').toLowerCase();

      const box = document.createElement('input');
      box.type = 'checkbox';
      box.value = device.id;
      box.checked = state.selected[kind].has(device.id);
      box.addEventListener('change', () => {
        if (box.checked) state.selected[kind].add(device.id);
        else state.selected[kind].delete(device.id);
        updateCount(kind, countId);
      });

      const text = document.createElement('span');
      const name = document.createElement('span');
      name.className = 'a-name';
      name.textContent = device.name;
      const meta = document.createElement('span');
      meta.className = 'a-meta';
      // What the device says about itself -- location first, because that
      // is what tells two identically named units apart in a building. The
      // hostname is shown only when an alias is hiding it. None of this is
      // ever sent to a restricted user; their own view is built separately.
      const detail = [device.location,
                      device.alias ? device.device_name : '',
                      device.model, device.ip].filter(Boolean);
      meta.textContent = detail.length ? ' · ' + detail.join(' · ') : '';
      text.append(name, meta);

      row.append(box, text);
      host.appendChild(row);
    });
    updateCount(kind, countId);

    el(filterId).addEventListener('input', function () {
      const needle = this.value.trim().toLowerCase();
      host.querySelectorAll('.assign-row').forEach((row) => {
        row.classList.toggle('hidden',
          needle !== '' && row.dataset.search.indexOf(needle) === -1);
      });
    });
  }

  function updateCount(kind, countId) {
    el(countId).textContent = state.selected[kind].size + ' selected';
  }

  function openEditor(user) {
    state.editing = user || null;
    state.selected.sources = new Set(
      (user && user.permissions && user.permissions.sources) || []);
    state.selected.destinations = new Set(
      (user && user.permissions && user.permissions.destinations) || []);
    state.selected.groups = new Set(
      (user && user.permissions && user.permissions.groups) || []);

    el('editor_title').textContent = user ? 'Edit ' + user.username : 'Add account';
    el('f_username').value = user ? user.username : '';
    el('f_username').disabled = !!user;      // renaming is not an operation
    el('f_role').value = user ? user.role : 'user';
    el('f_password').value = '';
    el('f_password').placeholder = user
      ? 'Leave blank to keep the current password'
      : 'At least 8 characters';

    const features = new Set(
      (user && user.permissions && user.permissions.features) || []);
    document.querySelectorAll('.f-feature').forEach((box) => {
      box.checked = features.has(box.value);
    });
    // A new account starts with the interface every account had before there
    // was a choice; an existing one shows what it holds.
    const interfaces = new Set(
      (user && user.permissions && user.permissions.interfaces) || ['standard']);
    document.querySelectorAll('.f-interface').forEach((box) => {
      box.checked = interfaces.has(box.value);
    });
    applyInterfaceVisibility();

    renderAssign('sources', 'src_list', 'src_count', 'src_filter');
    renderAssign('destinations', 'dst_list', 'dst_count', 'dst_filter');
    renderGroupAssign();
    applyRoleVisibility();
    // Read on its own, because it is the one thing in this dialog that is not
    // part of the account record the table already holds.
    renderDirect(null);
    loadDirect().catch(() => {});
    el('editor_error').style.display = 'none';
    el('editor_backdrop').style.display = 'flex';
    el('f_username').focus();
  }

  function emptyNote(text) {
    const note = document.createElement('div');
    note.className = 'empty';
    note.style.padding = '14px';
    note.textContent = text;
    return note;
  }

  // ---------------------------------------------------- direct access
  //
  // This section does not take part in Save. Enabling it mints a credential on
  // the server, and a checkbox that quietly creates a bearer token when
  // somebody presses Save on an unrelated change would be the wrong shape
  // entirely. Each control is one deliberate action with one server answer.
  //
  // The link itself is only ever held in the input it is shown in. Nothing
  // writes it to storage -- it is a password, and a password does not belong
  // in localStorage.
  function renderDirect(payload) {
    const section = el('direct_section');
    const box = el('f_direct');
    const detail = el('direct_detail');
    const status = el('direct_status');
    const user = state.editing;

    // Nothing to attach a link to while an account is being created.
    section.hidden = !user;
    if (!user) return;

    const info = (payload && payload.direct_access) || {};
    box.checked = !!info.enabled;
    detail.hidden = !info.enabled;
    el('direct_link').value = (payload && payload.link) || '';

    if (!info.enabled) {
      status.textContent = 'No active direct link.';
      return;
    }
    if (payload && payload.note) {
      status.textContent = payload.note;
    } else if (payload && !payload.recoverable) {
      status.textContent = 'This link cannot be shown on this computer. '
        + 'Regenerate it to get one you can copy.';
    } else {
      status.textContent = 'Direct access is on. Regenerating stops the old '
        + 'link working immediately.';
    }
  }

  async function loadDirect() {
    const user = state.editing;
    if (!user) { renderDirect(null); return; }
    const res = await api('/api/admin/users/' + encodeURIComponent(user.id)
                          + '/direct_access');
    renderDirect(res.status === 200 ? res.body : null);
  }

  async function directAction(action) {
    const user = state.editing;
    if (!user) return;
    const res = await api('/api/admin/users/' + encodeURIComponent(user.id)
                          + '/direct_access',
                          {method: 'POST', body: JSON.stringify({action})});
    if (res.status !== 200 || !res.body.ok) {
      showEditorError(res.body.error || 'That could not be changed.');
      await loadDirect();
      return;
    }
    renderDirect(res.body);
    await load();
  }

  function applyRoleVisibility() {
    // An administrator has no per-device grants -- the role is the grant -- so
    // showing empty assignment lists for one would be misleading.
    el('user_only').hidden = el('f_role').value === 'admin';
  }

  // Which routing interfaces an account may use only matters to an account
  // that may route, so the row is shown with A/V and put away without it. The
  // choice is kept either way: unticking A/V and ticking it again must not
  // forget which interface was chosen.
  function applyInterfaceVisibility() {
    const av = Array.from(document.querySelectorAll('.f-feature'))
      .some((b) => b.value === 'av' && b.checked);
    el('interface_row').hidden = !av || el('f_role').value === 'admin';
  }

  function closeEditor() {
    el('editor_backdrop').style.display = 'none';
    state.editing = null;
  }

  function showEditorError(message) {
    const line = el('editor_error');
    line.textContent = message;
    line.style.display = 'block';
  }

  async function saveEditor() {
    const role = el('f_role').value;
    const password = el('f_password').value;
    const permissions = {
      features: Array.from(document.querySelectorAll('.f-feature'))
        .filter((b) => b.checked).map((b) => b.value),
      sources: Array.from(state.selected.sources),
      destinations: Array.from(state.selected.destinations),
      groups: Array.from(state.selected.groups),
      interfaces: Array.from(document.querySelectorAll('.f-interface'))
        .filter((b) => b.checked).map((b) => b.value),
    };
    if (role !== 'admin' && permissions.features.indexOf('av') !== -1
        && !permissions.interfaces.length) {
      return showEditorError('Choose at least one routing interface.');
    }

    if (!state.editing) {
      const username = el('f_username').value.trim();
      if (!username) return showEditorError('A name is required.');
      if (password.length < 8) {
        return showEditorError('The password must be at least 8 characters.');
      }
      const {status, body} = await api('/api/admin/users', {
        method: 'POST',
        body: JSON.stringify({username, password, role, permissions}),
      });
      if (status !== 200 || !body.ok) {
        return showEditorError(body.error || 'Could not create the account.');
      }
      closeEditor();
      window.toast && window.toast('Account created', 'ok');
      return load();
    }

    // Editing: each change is its own explicit action, so the audit trail
    // records what was actually done rather than "something was updated".
    const id = state.editing.id;
    const steps = [];
    if (role !== state.editing.role) steps.push({action: 'set_role', role});
    if (role !== 'admin') steps.push({action: 'set_permissions', permissions});
    if (password) {
      if (password.length < 8) {
        return showEditorError('The password must be at least 8 characters.');
      }
      steps.push({action: 'set_password', password});
    }
    for (const step of steps) {
      const {status, body} = await api('/api/admin/users/' + id,
                                       {method: 'POST', body: JSON.stringify(step)});
      if (status !== 200 || !body.ok) {
        return showEditorError(body.error || 'Could not save that change.');
      }
    }
    closeEditor();
    window.toast && window.toast(steps.length ? 'Saved' : 'No changes', 'ok');
    return load();
  }

  // ------------------------------------------------------------- wiring
  el('new_user').addEventListener('click', () => openEditor(null));
  el('editor_cancel').addEventListener('click', closeEditor);
  el('editor_save').addEventListener('click', () => {
    saveEditor().catch((err) => {
      if (String(err.message) !== 'signed out') {
        showEditorError('Could not reach OmniSuite.');
      }
    });
  });
  el('f_role').addEventListener('change', applyRoleVisibility);
  el('f_role').addEventListener('change', applyInterfaceVisibility);
  document.querySelectorAll('.f-feature').forEach((box) => {
    box.addEventListener('change', applyInterfaceVisibility);
  });

  // Each of these is one deliberate action against the server, not a field in
  // a form that gets saved later.
  el('f_direct').addEventListener('change', () => {
    directAction(el('f_direct').checked ? 'enable' : 'disable')
      .catch(() => showEditorError('Could not reach OmniSuite.'));
  });
  el('direct_regen').addEventListener('click', async () => {
    const ok = !window.omniConfirm ? false : await window.omniConfirm({
      title: 'Replace this direct link?',
      message: 'The link that has already been handed out will stop working '
             + 'immediately, and anyone using it now will be signed out. '
             + 'A new link is created in its place.',
      confirmText: 'Replace link',
      danger: true,
    });
    if (!ok) return;
    directAction('regenerate')
      .catch(() => showEditorError('Could not reach OmniSuite.'));
  });
  el('direct_copy').addEventListener('click', async () => {
    const link = el('direct_link').value;
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      if (window.toast) window.toast('Link copied.');
    } catch (err) {
      // No clipboard permission, or an insecure context. Selecting the text
      // is something the operator can act on; a silent failure is not.
      el('direct_link').select();
      if (window.toast) {
        window.toast('Press Ctrl+C or long-press to copy.', 'warn');
      }
    }
  });
  el('editor_backdrop').addEventListener('click', (event) => {
    if (event.target === el('editor_backdrop')) closeEditor();
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && el('editor_backdrop').style.display === 'flex') {
      closeEditor();
    }
  });

  document.querySelectorAll('[data-all]').forEach((b) => {
    b.addEventListener('click', () => {
      const kind = b.dataset.all;
      if (kind === 'groups') {
        state.groups.forEach((g) => state.selected.groups.add(g.id));
      } else {
        const wantRole = kind === 'sources' ? 'encoder' : 'decoder';
        state.devices.filter((d) => d.role === wantRole)
          .forEach((d) => state.selected[kind].add(d.id));
      }
      refreshEditorLists();
    });
  });
  document.querySelectorAll('[data-none]').forEach((b) => {
    b.addEventListener('click', () => {
      state.selected[b.dataset.none].clear();
      refreshEditorLists();
    });
  });

  function refreshEditorLists() {
    [['sources', 'src_list', 'src_count'], ['destinations', 'dst_list', 'dst_count'],
     ['groups', 'grp_list', 'grp_count']].forEach(([kind, listId, countId]) => {
      el(listId).querySelectorAll('input[type=checkbox]').forEach((box) => {
        box.checked = state.selected[kind].has(box.value);
      });
      updateCount(kind, countId);
    });
  }

  // ------------------------------------------------------ device names
  //
  // An alias is a label OmniSuite keeps against a device's stable identity. It
  // never reaches the device and never takes part in a permission decision --
  // the checkbox lists below still store identities, and this screen only
  // changes what those checkboxes are called.
  function renderAliases() {
    const body = el('alias_body');
    body.replaceChildren();

    state.devices.forEach((device) => {
      const row = document.createElement('tr');
      // Searchable on everything this row displays, and on nothing it
      // does not: a result the operator cannot see the reason for looks
      // arbitrary.
      row.dataset.search = [device.device_name, device.alias, device.model,
                            device.ip, device.location, device.description]
        .join(' ').toLowerCase();

      const nameCell = document.createElement('td');
      nameCell.dataset.label = 'Friendly name';
      const input = document.createElement('input');
      input.type = 'text';
      input.maxLength = 48;
      input.placeholder = device.device_name;
      input.value = state.pendingAliases.has(device.id)
        ? state.pendingAliases.get(device.id) : (device.alias || '');
      input.style.cssText = 'width:100%;min-height:32px;padding:4px 8px;' +
        'border-radius:7px;border:1px solid var(--edge);' +
        'background:var(--surface-elevated);color:var(--text);font:inherit;';
      input.addEventListener('input', () => {
        const original = device.alias || '';
        if (input.value === original) state.pendingAliases.delete(device.id);
        else state.pendingAliases.set(device.id, input.value);
        updateDirty();
      });
      nameCell.appendChild(input);
      row.appendChild(nameCell);

      // The device's own identity, always visible to an administrator: two
      // displays called "Front Display" are only distinguishable by this.
      //
      // Device and Location come from the equipment; the friendly name on the
      // left belongs to OmniSuite. They are deliberately in separate columns
      // so nobody reads one as the other.
      const deviceCell = document.createElement('td');
      deviceCell.dataset.label = 'Device';
      deviceCell.style.color = 'var(--text-muted)';
      deviceCell.appendChild(document.createTextNode(device.device_name));
      if (device.description) {
        // Free text of unpredictable length, so it goes under the name and is
        // clamped rather than becoming a column or a very wide tooltip.
        const note = document.createElement('span');
        note.className = 'device-description';
        note.textContent = device.description;
        deviceCell.append(document.createElement('br'), note);
      }
      row.appendChild(deviceCell);

      [['Location', device.location || '—'], ['Model', device.model || '—'],
       ['Address', device.ip || '—'],
       ['Role', device.role === 'encoder' ? 'Source' :
                (device.role === 'decoder' ? 'Display' : 'Other')]]
        .forEach(([label, value]) => {
          const td = document.createElement('td');
          td.dataset.label = label;
          td.textContent = value;
          td.style.color = 'var(--text-muted)';
          if (label === 'Location') td.className = 'device-location';
          row.appendChild(td);
        });

      const assignCell = document.createElement('td');
      assignCell.dataset.label = 'Assignments';
      assignCell.dataset.assignId = device.id;
      row.appendChild(assignCell);
      paintAssignment(assignCell, device.id);

      body.appendChild(row);
    });

    el('alias_count').textContent = state.devices.length + ' device' +
      (state.devices.length === 1 ? '' : 's');
    updateDirty();
    renderDuplicateWarning();
  }

  // One device's assignment summary, drawn into its cell. Used for the first
  // render and again, in place, whenever the assignments are re-read: only the
  // cell changes, so a name being typed in the same row is never disturbed.
  function paintAssignment(cell, id) {
    const info = state.assignments.get(id);
    cell.replaceChildren();
    if (!info) {
      cell.style.color = 'var(--text-muted)';
      cell.textContent = '—';
      return;
    }
    if (!info.assigned) {
      cell.style.color = 'var(--text-muted)';
      cell.textContent = info.summary || 'Unassigned';
      return;
    }
    cell.style.color = '';
    const open = state.assignmentsExpanded.has(id);
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'assign-summary';
    toggle.textContent = info.summary;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.addEventListener('click', () => {
      if (state.assignmentsExpanded.has(id)) state.assignmentsExpanded.delete(id);
      else state.assignmentsExpanded.add(id);
      paintAssignment(cell, id);
    });
    cell.appendChild(toggle);
    if (!open) return;
    const detail = document.createElement('div');
    detail.className = 'assign-detail';
    const direct = new Set(info.direct_access || []);
    const lines = [
      ['Users', (info.users || []).map((n) => direct.has(n) ? n + ' (Direct Access)' : n)],
      ['Groups', info.groups || []],
    ];
    lines.forEach(([label, names]) => {
      if (!names.length) return;
      const line = document.createElement('div');
      line.textContent = label + ': ' + names.join(', ');
      detail.appendChild(line);
    });
    cell.appendChild(detail);
  }

  function indexAssignments(body) {
    state.assignments = new Map(((body && body.units) || []).map((u) => [u.id, u]));
  }

  // Re-read what is assigned and repaint the cells in place. Cheap, no device
  // traffic, and run when the tab is opened so a change made elsewhere (Clear
  // Units, another administrator) is never shown stale.
  async function refreshAssignments() {
    const {status, body} = await api('/api/admin/inventory/assignments');
    if (status !== 200 || !body || body.ok === false) return;
    indexAssignments(body);
    document.querySelectorAll('[data-assign-id]').forEach((cell) => {
      paintAssignment(cell, cell.dataset.assignId);
    });
  }

  function updateDirty() {
    const n = state.pendingAliases.size;
    el('alias_dirty').textContent = n ? (n + ' unsaved change' +
      (n === 1 ? '' : 's')) : '';
    el('alias_save').disabled = n === 0;
  }

  // Duplicates are allowed -- two rooms may each have a "Front Display" -- but
  // an administrator picking from a list of identical names is choosing blind,
  // so the ambiguity is stated rather than prevented.
  function renderDuplicateWarning() {
    const counts = new Map();
    state.devices.forEach((device) => {
      const name = (state.pendingAliases.has(device.id)
        ? state.pendingAliases.get(device.id) : (device.alias || '')).trim();
      if (!name) return;
      const key = name.toLowerCase();
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    const repeated = [...counts.entries()].filter(([, n]) => n > 1)
      .map(([name]) => name);
    const box = el('dupe_warning');
    box.hidden = repeated.length === 0;
    if (repeated.length) {
      box.replaceChildren();
      const strong = document.createElement('strong');
      strong.textContent = 'The same name is used more than once. ';
      box.append(strong,
        'These devices will look identical when you assign permissions: ' +
        repeated.join(', ') + '. That is allowed, but give them distinct ' +
        'names if you need to tell them apart.');
    }
  }

  el('alias_filter').addEventListener('input', function () {
    const needle = this.value.trim().toLowerCase();
    el('alias_body').querySelectorAll('tr').forEach((row) => {
      row.hidden = needle !== '' && row.dataset.search.indexOf(needle) === -1;
    });
  });

  el('alias_save').addEventListener('click', async () => {
    if (!state.pendingAliases.size) return;
    const payload = {};
    state.pendingAliases.forEach((value, id) => { payload[id] = value; });
    const {status, body} = await api('/api/admin/aliases', {
      method: 'POST', body: JSON.stringify({aliases: payload}),
    });
    if (status === 200 && body.ok) {
      state.pendingAliases.clear();
      window.toast && window.toast('Names saved', 'ok');
      await load();
      selectTab('names');
    } else {
      window.toast && window.toast(body.error || 'Could not save', 'error');
    }
  });

  // ------------------------------------------------------ display groups
  //
  // A group is a name and a set of displays, held by identity. Which accounts
  // may route to it is decided in the account editor, not here.
  function renderGroupAssign() {
    const host = el('grp_list');
    host.replaceChildren();
    if (!state.groups.length) {
      host.appendChild(emptyNote('No groups have been created yet.'));
      updateCount('groups', 'grp_count');
      return;
    }
    state.groups.forEach((group) => {
      const row = document.createElement('label');
      row.className = 'assign-row';
      row.dataset.search = group.name.toLowerCase();
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.value = group.id;
      box.checked = state.selected.groups.has(group.id);
      box.addEventListener('change', () => {
        if (box.checked) state.selected.groups.add(group.id);
        else state.selected.groups.delete(group.id);
        updateCount('groups', 'grp_count');
      });
      const text = document.createElement('span');
      const name = document.createElement('span');
      name.className = 'a-name';
      name.textContent = group.name;
      const meta = document.createElement('span');
      meta.className = 'a-meta';
      const n = group.members.length;
      meta.textContent = ' \u00b7 ' + n + ' display' + (n === 1 ? '' : 's');
      text.append(name, meta);
      row.append(box, text);
      host.appendChild(row);
    });
    updateCount('groups', 'grp_count');
  }

  el('grp_filter').addEventListener('input', function () {
    const needle = this.value.trim().toLowerCase();
    el('grp_list').querySelectorAll('.assign-row').forEach((row) => {
      row.classList.toggle('hidden',
        needle !== '' && row.dataset.search.indexOf(needle) === -1);
    });
  });

  function renderGroups() {
    const body = el('groups_body');
    body.replaceChildren();
    state.groups.forEach((group) => {
      const row = document.createElement('tr');
      cell(row, 'Group', group.name);
      const n = group.members.length;
      cell(row, 'Displays', n + ' display' + (n === 1 ? '' : 's'));
      cell(row, 'Given to', (group.granted_to || []).join(', ') || 'Nobody yet');
      const actions = document.createElement('div');
      actions.style.cssText = 'display:flex;gap:6px;flex-wrap:wrap;';
      actions.appendChild(button('Edit', () => openGroupEditor(group)));
      actions.appendChild(button('Delete', () => deleteGroup(group)));
      cell(row, '', actions);
      body.appendChild(row);
    });
    if (!state.groups.length) {
      const row = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 4;
      td.className = 'empty';
      td.textContent = 'No groups yet. Add one to send a source to several displays at once.';
      row.appendChild(td);
      body.appendChild(row);
    }
  }

  function renderGroupMembers() {
    const host = el('g_list');
    host.replaceChildren();
    const displays = state.devices.filter((d) => d.role === 'decoder');
    if (!displays.length) {
      host.appendChild(emptyNote('No displays have been discovered yet. Run a Scan first.'));
    }
    displays.forEach((device) => {
      const row = document.createElement('label');
      row.className = 'assign-row';
      row.dataset.search = [device.name, device.model, device.location]
        .filter(Boolean).join(' ').toLowerCase();
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.value = device.id;
      box.checked = state.groupMembers.has(device.id);
      box.addEventListener('change', () => {
        if (box.checked) state.groupMembers.add(device.id);
        else state.groupMembers.delete(device.id);
        el('g_count').textContent = state.groupMembers.size + ' selected';
      });
      const text = document.createElement('span');
      const name = document.createElement('span');
      name.className = 'a-name';
      name.textContent = device.name;
      const meta = document.createElement('span');
      meta.className = 'a-meta';
      const detail = [device.location, device.model].filter(Boolean);
      meta.textContent = detail.length ? ' \u00b7 ' + detail.join(' \u00b7 ') : '';
      text.append(name, meta);
      row.append(box, text);
      host.appendChild(row);
    });
    el('g_count').textContent = state.groupMembers.size + ' selected';
  }

  function openGroupEditor(group) {
    state.editingGroup = group || null;
    state.groupMembers = new Set((group ? group.members : [])
      .map((m) => m.id));
    el('group_title').textContent = group ? 'Edit ' + group.name : 'Add group';
    el('g_name').value = group ? group.name : '';
    el('g_filter').value = '';
    renderGroupMembers();
    el('group_error').style.display = 'none';
    el('group_backdrop').style.display = 'flex';
    el('g_name').focus();
  }

  function closeGroupEditor() {
    el('group_backdrop').style.display = 'none';
    state.editingGroup = null;
  }

  function showGroupError(message) {
    const line = el('group_error');
    line.textContent = message;
    line.style.display = 'block';
  }

  async function saveGroup() {
    const name = el('g_name').value.trim();
    if (!name) return showGroupError('A group needs a name.');
    const members = Array.from(state.groupMembers);
    if (!members.length) return showGroupError('Choose at least one display.');
    if (!state.editingGroup) {
      const {status, body} = await api('/api/admin/groups', {
        method: 'POST', body: JSON.stringify({name, members})});
      if (status !== 200 || !body.ok) {
        return showGroupError(body.error || 'Could not create the group.');
      }
    } else {
      const id = encodeURIComponent(state.editingGroup.id);
      const steps = [];
      if (name !== state.editingGroup.name) steps.push({action: 'rename', name});
      steps.push({action: 'set_members', members});
      for (const step of steps) {
        const {status, body} = await api('/api/admin/groups/' + id, {
          method: 'POST', body: JSON.stringify(step)});
        if (status !== 200 || !body.ok) {
          return showGroupError(body.error || 'Could not save that change.');
        }
      }
    }
    closeGroupEditor();
    window.toast && window.toast('Group saved', 'ok');
    return load();
  }

  async function deleteGroup(group) {
    const holders = group.granted_to || [];
    const ok = !window.omniConfirm ? false : await window.omniConfirm({
      title: 'Delete ' + group.name + '?',
      message: holders.length
        ? 'It will be taken away from ' + holders.join(', ') + '. The '
          + 'displays in it are not changed.'
        : 'The displays in it are not changed.',
      confirmText: 'Delete',
      danger: true,
    });
    if (!ok) return;
    const {status, body} = await api(
      '/api/admin/groups/' + encodeURIComponent(group.id),
      {method: 'POST', body: JSON.stringify({action: 'delete'})});
    if (status === 200 && body.ok) {
      window.toast && window.toast('Group deleted', 'ok');
      await load();
    } else {
      window.toast && window.toast(body.error || 'That did not work', 'error');
    }
  }

  el('new_group').addEventListener('click', () => openGroupEditor(null));
  el('group_cancel').addEventListener('click', closeGroupEditor);
  el('group_save').addEventListener('click', () => {
    saveGroup().catch((err) => {
      if (String(err.message) !== 'signed out') {
        showGroupError('Could not reach OmniSuite.');
      }
    });
  });
  el('g_filter').addEventListener('input', function () {
    const needle = this.value.trim().toLowerCase();
    el('g_list').querySelectorAll('.assign-row').forEach((row) => {
      row.classList.toggle('hidden',
        needle !== '' && row.dataset.search.indexOf(needle) === -1);
    });
  });
  el('group_backdrop').addEventListener('click', (event) => {
    if (event.target === el('group_backdrop')) closeGroupEditor();
  });

  // ------------------------------------------------------------- tabs
  function selectTab(name) {
    state.tab = name;
    el('panel_accounts').hidden = name !== 'accounts';
    el('panel_names').hidden = name !== 'names';
    el('panel_groups').hidden = name !== 'groups';
    el('panel_audit').hidden = name !== 'audit';
    if (name === 'audit') loadAudit().catch(() => {});
    if (name === 'names') refreshAssignments().catch(() => {});
    el('admin_tabs').querySelectorAll('.tab').forEach((tab) => {
      tab.setAttribute('aria-selected', String(tab.dataset.panel === name));
    });
  }

  document.getElementById('admin_tabs').addEventListener('click', (event) => {
    const tab = event.target.closest('.tab');
    if (tab) selectTab(tab.dataset.panel);
  });

  // ---------------------------------------------------------- activity
  //
  // Read on demand and filtered server-side. The log is bounded, so a page
  // never holds all of it and filtering never means shipping the whole thing
  // to the browser to sift through.
  const EVENT_LABELS = {
    'login.success': 'Signed in',
    'login.failure': 'Sign-in refused',
    'login.throttled': 'Sign-in throttled',
    'logout': 'Signed out',
    'user.create': 'Account created',
    'user.deleted': 'Account deleted',
    'user.enabled': 'Account enabled',
    'user.disabled': 'Account disabled',

    'user.role_changed': 'Role changed',
    'user.permissions_changed': 'Permissions changed',
    'user.password_changed': 'Password changed',
    'inventory.cleared': 'Discovered units cleared',
    'inventory.cleared_unassigned': 'Unassigned units cleared',
    'backup.restored': 'Configuration restored',
    'backup.restore_failed': 'Restore failed and was rolled back',
    'backup.exported': 'Configuration backup made',
    'route': 'Routed',
    'unroute': 'Display cleared',
    'usb.attach': 'USB connected',
    'usb.detach': 'USB disconnected',
    'multiview.show': 'Multiview shown',
    'multiview.switch': 'Multiview window changed',
    'activity.retention_changed': 'Activity retention changed',
    'route_group': 'Routed to a group',
    'matrix.route': 'Routed from the Matrix',
    'matrix.route_group': 'Routed to a group from the Matrix',
    'user.routing_interfaces_changed': 'Routing interface changed',
    'group.created': 'Group created',
    'group.updated': 'Group changed',
    'group.deleted': 'Group deleted',
    'access.denied': 'Refused',
    'remote_access.enabled': 'Remote access on',
    'remote_access.disabled': 'Remote access off',
    'remote_access.restored': 'Remote access restored',
    'direct_access.enabled': 'Direct link created',
    'direct_access.disabled': 'Direct link removed',
    'direct_access.regenerated': 'Direct link replaced',
    'direct_access.used': 'Direct link used',
    'certificate.generated': 'Certificate created',
    'certificate.configured': 'Certificate configured',
    'certificate.removed': 'Certificate removed',
    'device.aliases_changed': 'Device names changed',
    'bootstrap.admin_created': 'First administrator created',
  };

  // An id nobody mapped must still read as English. An internal name in
  // front of an operator is a defect whichever way it got there, and the next
  // event added on the server would arrive here exactly as these two did.
  function eventLabel(id) {
    if (EVENT_LABELS[id]) return EVENT_LABELS[id];
    const words = String(id || '').replace(/[._]+/g, ' ').trim();
    if (!words) return 'Activity';
    return words.charAt(0).toUpperCase() + words.slice(1);
  }

  function when(seconds) {
    if (!seconds) return '';
    const date = new Date(seconds * 1000);
    const sameDay = date.toDateString() === new Date().toDateString();
    return sameDay ? date.toLocaleTimeString()
                   : date.toLocaleDateString() + ' ' + date.toLocaleTimeString();
  }

  // Built from the named fields an entry carries, never a dump of the record.
  function describeEntry(entry) {
    const d = entry.detail || {};
    const bits = [];
    if (d.source_label || d.source) bits.push('from ' + (d.source_label || d.source));
    if (d.destination_label || d.destination) {
      bits.push('to ' + (d.destination_label || d.destination));
    }
    if (d.display_label || d.display) bits.push('on ' + (d.display_label || d.display));
    if (d.days !== undefined && d.max_megabytes !== undefined) {
      bits.push(d.days + ' days, ' + d.max_megabytes + ' MB');
    }
    if (d.group_name) bits.push('group ' + d.group_name);
    if (d.total !== undefined && d.routed !== undefined) {
      bits.push(d.routed + ' of ' + d.total + ' displays routed');
    }
    if (d.name && !d.username) bits.push(d.name);
    if (d.preset) bits.push('layout ' + d.preset);
    if (d.window) bits.push('window ' + d.window);
    if (d.computer_label) bits.push('computer ' + d.computer_label);
    if (d.peripherals_label) bits.push('peripherals ' + d.peripherals_label);
    if (d.mode && d.mode !== 'av') bits.push(d.mode + ' only');
    if (Array.isArray(d.interfaces)) {
      bits.push('routing interface: ' + (d.interfaces.map((i) => (i === 'matrix' ? 'Matrix' : 'Standard'))
        .join(' and ') || 'none'));
    }
    if (d.username) bits.push(d.username);
    if (d.role) bits.push('role ' + d.role);
    if (d.address) bits.push(d.address);
    if (d.transport) bits.push(String(d.transport).toUpperCase());
    if (d.reason) bits.push(d.reason);
    if (d.rule) bits.push(d.rule);
    return bits.join(' \u00b7 ');
  }

  async function loadAudit() {
    const params = new URLSearchParams();
    const search = el('audit_search').value.trim();
    if (search) params.set('q', search);
    if (el('audit_event').value) params.set('event', el('audit_event').value);
    if (el('audit_actor').value) params.set('actor', el('audit_actor').value);
    params.set('limit', '200');

    const {status, body} = await api('/api/admin/audit?' + params.toString());
    const tbody = el('audit_body');
    tbody.replaceChildren();
    if (status !== 200 || !body.ok) {
      el('audit_count').textContent = 'could not load';
      return;
    }
    const entries = body.entries || [];
    entries.forEach((entry) => {
      const row = document.createElement('tr');
      cell(row, 'When', when(entry.at));
      cell(row, 'Account', entry.actor || '\u2014');
      const action = document.createElement('span');
      action.textContent = eventLabel(entry.event);
      if (['access.denied', 'login.failure', 'login.throttled']
          .indexOf(entry.event) !== -1) {
        action.className = 'state-pill disabled';
      }
      cell(row, 'Action', action);
      cell(row, 'Details', describeEntry(entry));
      tbody.appendChild(row);
    });

    const summary = body.summary || {};
    el('audit_count').textContent = entries.length + ' of ' +
      (summary.total || 0) + ' kept';
    showRetention(summary.retention);
    fillFilter('audit_event', summary.events || [],
               (v) => eventLabel(v));
    fillFilter('audit_actor', summary.accounts || [], (v) => v);
  }

  // How long Activity is kept and how much disk it may use. The inputs are
  // filled from the server's answer and left alone while somebody is typing in
  // them, so a refresh never overwrites an unsaved edit.
  function showRetention(retention) {
    if (!retention) return;
    const days = el('ret_days');
    const mb = el('ret_mb');
    if (document.activeElement !== days) days.value = retention.days;
    if (document.activeElement !== mb) mb.value = retention.max_megabytes;
    const used = (retention.used_bytes / 1048576).toFixed(1);
    el('ret_note').textContent = used + ' MB used, ' + retention.entries
      + ' entries. Default is ' + retention.defaults.days + ' days and '
      + retention.defaults.max_megabytes + ' MB.';
  }

  el('ret_save').addEventListener('click', async () => {
    const days = Number(el('ret_days').value);
    const megabytes = Number(el('ret_mb').value);
    const {status, body} = await api('/api/admin/audit/retention', {
      method: 'POST',
      body: JSON.stringify({days: days, max_megabytes: megabytes}),
    });
    if (status === 200 && body.ok) {
      window.toast && window.toast('Saved', 'ok');
      showRetention(body.retention);
      loadAudit().catch(() => {});
    } else {
      window.toast && window.toast(body.error || 'That did not work', 'error');
    }
  });

  function fillFilter(id, values, labelOf) {
    const select = el(id);
    if (select.dataset.filled === String(values.length)) return;
    const current = select.value;
    const first = select.options[0];
    select.replaceChildren(first);
    values.forEach((value) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = labelOf(value);
      select.appendChild(option);
    });
    select.value = current;
    select.dataset.filled = String(values.length);
  }

  el('audit_refresh').addEventListener('click', () => loadAudit().catch(() => {}));
  ['audit_event', 'audit_actor'].forEach((id) => {
    el(id).addEventListener('change', () => loadAudit().catch(() => {}));
  });
  el('audit_search').addEventListener('input', () => {
    clearTimeout(state.auditTimer);
    state.auditTimer = setTimeout(() => loadAudit().catch(() => {}), 300);
  });

  async function load() {
    const [users, devices, groups, assignments] = await Promise.all([
      api('/api/admin/users'),
      api('/api/admin/devices'),
      api('/api/admin/groups'),
      api('/api/admin/inventory/assignments'),
    ]);
    state.users = (users.body && users.body.users) || [];
    state.devices = (devices.body && devices.body.devices) || [];
    state.groups = (groups.body && groups.body.groups) || [];
    indexAssignments(assignments.status === 200 ? assignments.body : null);
    renderUsers();
    renderGroups();
    renderAliases();
    el('loading').hidden = true;
    el('admin_tabs').hidden = false;
    selectTab(state.tab);
  }

  load().catch((err) => {
    if (String(err.message) !== 'signed out') {
      el('loading').textContent = 'Could not load accounts.';
    }
  });
})();

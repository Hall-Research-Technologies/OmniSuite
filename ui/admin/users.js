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
    selected: {sources: new Set(), destinations: new Set()},
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
    const names = {av: 'A/V', usb: 'USB', multiview: 'Multiview'};
    const features = (user.permissions && user.permissions.features) || [];
    if (!features.length) return 'Nothing assigned yet';
    const counts = user.permissions || {};
    const parts = features.map((f) => names[f] || f);
    const sources = (counts.sources || []).length;
    const destinations = (counts.destinations || []).length;
    return parts.join(', ') + ' · ' + sources + ' source' +
      (sources === 1 ? '' : 's') + ', ' + destinations + ' display' +
      (destinations === 1 ? '' : 's');
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
      row.dataset.search = (device.name + ' ' + device.model + ' ' + device.ip)
        .toLowerCase();

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
      // The model and address help an administrator tell two identically
      // named devices apart. They are never sent to a restricted user.
      meta.textContent = ' · ' + [device.model, device.ip]
        .filter(Boolean).join(' · ');
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

    renderAssign('sources', 'src_list', 'src_count', 'src_filter');
    renderAssign('destinations', 'dst_list', 'dst_count', 'dst_filter');
    applyRoleVisibility();
    el('editor_error').style.display = 'none';
    el('editor_backdrop').style.display = 'flex';
    el('f_username').focus();
  }

  function applyRoleVisibility() {
    // An administrator has no per-device grants -- the role is the grant -- so
    // showing empty assignment lists for one would be misleading.
    el('user_only').hidden = el('f_role').value === 'admin';
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
    };

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
      const wantRole = kind === 'sources' ? 'encoder' : 'decoder';
      state.devices.filter((d) => d.role === wantRole)
        .forEach((d) => state.selected[kind].add(d.id));
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
    ['sources', 'destinations'].forEach((kind) => {
      const host = el(kind === 'sources' ? 'src_list' : 'dst_list');
      host.querySelectorAll('input[type=checkbox]').forEach((box) => {
        box.checked = state.selected[kind].has(box.value);
      });
      updateCount(kind, kind === 'sources' ? 'src_count' : 'dst_count');
    });
  }

  async function load() {
    const [users, devices] = await Promise.all([
      api('/api/admin/users'),
      api('/api/admin/devices'),
    ]);
    state.users = (users.body && users.body.users) || [];
    state.devices = (devices.body && devices.body.devices) || [];
    renderUsers();
    el('loading').hidden = true;
    el('content').hidden = false;
  }

  load().catch((err) => {
    if (String(err.message) !== 'signed out') {
      el('loading').textContent = 'Could not load accounts.';
    }
  });
})();

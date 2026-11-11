/*
 * ui/clear-units.js: the Clear Units confirmation, and View Assignments.
 *
 * With Remote Access off, Clear Units is a plain destructive confirmation and
 * that wording stays where it was (the page passes it in as `legacyConfirm`).
 *
 * With Remote Access on it is assignment aware: units that Users or Display
 * Groups refer to are kept, the rest are removed. The dialog says so, offers
 * View Assignments so nobody has to remember which units are protected, and
 * offers Clear Unassigned Units. It deliberately has no "disable Remote Access
 * and clear everything" shortcut: removing assigned units is two intentional
 * steps, and the first is made in Settings.
 *
 * This module only presents. Which units are protected is decided by the server
 * -- here from `/api/admin/inventory/assignments`, and again, independently, by
 * `/api/clear_units` when the request arrives -- so nothing in a browser can
 * make an assigned unit removable.
 *
 * If the assignments cannot be read the answer is NO. Not knowing whether
 * anything is assigned is not the same as nothing being assigned.
 *
 * Markup is built with createElement and textContent only: names come from
 * devices and from administrators, and are data, not markup.
 */
(function (root) {
  'use strict';

  var inFlight = false;

  function make(doc, tag, className, text) {
    var node = doc.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function backdrop(doc, id, label, wide) {
    var shell = make(doc, 'div', 'modal-backdrop');
    shell.id = id;
    var card = make(doc, 'div', 'modal confirm-modal' + (wide ? ' assign-modal' : ''));
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.setAttribute('aria-label', label);
    shell.appendChild(card);
    return { shell: shell, card: card };
  }

  function styleOnce(doc) {
    if (!doc.head || !doc.getElementById || doc.getElementById('omni_clear_units_style')) return;
    var sheet = doc.createElement('style');
    sheet.id = 'omni_clear_units_style';
    sheet.textContent = [
      // A table of seven columns does not fit the 520px confirmation card.
      '.confirm-modal.assign-modal{width:min(96vw,900px);max-width:96vw}',
      '.assign-table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}',
      '.assign-table td:nth-child(1),.assign-table td:nth-child(2){white-space:nowrap}',
      '.assign-table td{overflow-wrap:anywhere}',
      '.assign-table th,.assign-table td{text-align:left;padding:5px 8px;',
      'border-bottom:1px solid var(--border);vertical-align:top}',
      '.assign-table th{color:var(--muted);font-weight:600;white-space:nowrap}',
      '.assign-scroll{max-height:55vh;overflow:auto;border:1px solid var(--border);',
      'border-radius:8px;background:var(--panel-2)}',
      '.assign-note{color:var(--muted);font-size:12px;margin-top:8px}',
      '.assign-tag{color:var(--muted);font-size:11px;margin-left:4px}',
    ].join('');
    doc.head.appendChild(sheet);
  }

  function show(deps, shell) {
    shell.style.display = 'flex';
    if (deps.modalStack) deps.modalStack.raise(shell);
    deps.doc.body.appendChild(shell);
  }

  function hide(deps, shell) {
    shell.style.display = 'none';
    if (deps.modalStack) deps.modalStack.release(shell);
    if (shell.parentNode && shell.parentNode.removeChild) shell.parentNode.removeChild(shell);
  }

  function isTop(deps, shell) {
    return !deps.modalStack || deps.modalStack.top() === shell;
  }

  function usersText(row) {
    var direct = {};
    (row.direct_access || []).forEach(function (name) { direct[name] = true; });
    return (row.users || []).map(function (name) {
      return direct[name] ? name + ' (Direct Access)' : name;
    }).join(', ');
  }

  // The assignments, as a table. Names only: the point is "what is stopping
  // this unit being cleared", not an inventory of identifiers.
  function showAssignments(deps, report) {
    var doc = deps.doc;
    styleOnce(doc);
    var parts = backdrop(doc, 'assignments_backdrop', 'Unit assignments', true);
    var invoker = doc.activeElement;
    parts.card.appendChild(make(doc, 'h3', 'confirm-title', 'Unit assignments'));
    parts.card.appendChild(make(doc, 'div', 'confirm-message',
      'These units are used by Remote Access, so Clear Units keeps them while ' +
      'Remote Access is on.'));

    var assigned = (report.units || []).filter(function (row) { return row.assigned; });
    if (!assigned.length) {
      parts.card.appendChild(make(doc, 'div', 'confirm-message',
        'No discovered unit is assigned to a User or a Display Group.'));
    } else {
      var table = make(doc, 'table', 'assign-table');
      var head = make(doc, 'tr');
      ['Device', 'Type', 'Friendly name', 'Device name', 'Assignments', 'Users',
       'Display Groups'].forEach(function (title) {
        head.appendChild(make(doc, 'th', '', title));
      });
      table.appendChild(head);
      assigned.forEach(function (row) {
        var line = make(doc, 'tr');
        [row.model || '', row.type || '', row.friendly_name || '',
         row.device_name || '', String(row.count || 0), usersText(row),
         (row.groups || []).join(', ')].forEach(function (value) {
          line.appendChild(make(doc, 'td', '', value));
        });
        table.appendChild(line);
      });
      var scroll = make(doc, 'div', 'assign-scroll');
      scroll.appendChild(table);
      parts.card.appendChild(scroll);
    }
    var free = (report.units || []).length - assigned.length;
    if (free > 0) {
      parts.card.appendChild(make(doc, 'div', 'assign-note',
        free + (free === 1 ? ' unassigned unit is' : ' unassigned units are') +
        ' not listed.'));
    }

    var buttons = make(doc, 'div', 'modal-buttons');
    var close = make(doc, 'button', 'btn secondary', 'Close');
    close.type = 'button';
    buttons.appendChild(close);
    parts.card.appendChild(buttons);

    return new Promise(function (resolve) {
      function done() {
        doc.removeEventListener('keydown', onKey);
        hide(deps, parts.shell);
        if (invoker && invoker.focus) invoker.focus();
        resolve();
      }
      function onKey(event) {
        if (event.key === 'Escape' && isTop(deps, parts.shell)) {
          if (event.stopPropagation) event.stopPropagation();
          done();
        }
      }
      close.addEventListener('click', done);
      parts.shell.addEventListener('click', function (event) {
        if (event.target === parts.shell) done();
      });
      doc.addEventListener('keydown', onKey);
      show(deps, parts.shell);
      close.focus();
    });
  }

  function plural(count, one, many) { return count + ' ' + (count === 1 ? one : many); }

  // Cancel, View Assignments, Clear Unassigned Units. Cancel holds the focus
  // and the primary treatment: the costly answer is Yes, so the keyboard
  // default and the visual default agree.
  function askAssignmentAware(deps, report) {
    var doc = deps.doc;
    styleOnce(doc);
    var parts = backdrop(doc, 'clear_units_backdrop', 'Clear discovered units');
    var invoker = doc.activeElement;
    var kept = report.protected || 0;
    var clearable = report.clearable || 0;

    parts.card.appendChild(make(doc, 'h3', 'confirm-title', 'Clear discovered units?'));
    parts.card.appendChild(make(doc, 'div', 'confirm-message', 'Remote Access is enabled.'));
    parts.card.appendChild(make(doc, 'div', 'confirm-message',
      'Unassigned discovered units will be removed. Units assigned to users, ' +
      'groups, or other Remote Access resources will be kept.'));
    parts.card.appendChild(make(doc, 'div', 'confirm-message',
      'To remove assigned units, disable Remote Access in Settings first.'));

    var summary = make(doc, 'div', 'confirm-summary');
    [['Units to remove', clearable], ['Assigned units kept', kept]].forEach(function (pair) {
      var line = make(doc, 'div', 'confirm-summary-row');
      line.appendChild(make(doc, 'span', '', pair[0]));
      line.appendChild(make(doc, 'strong', '', pair[1]));
      summary.appendChild(line);
    });
    parts.card.appendChild(summary);
    if (clearable === 0) {
      parts.card.appendChild(make(doc, 'div', 'assign-note',
        kept ? 'Every discovered unit is assigned, so there is nothing to clear.'
             : 'There are no discovered units to clear.'));
    }

    var buttons = make(doc, 'div', 'modal-buttons');
    var cancel = make(doc, 'button', 'btn', 'Cancel');
    var view = make(doc, 'button', 'btn secondary', 'View Assignments');
    var go = make(doc, 'button', 'btn danger', 'Clear Unassigned Units');
    [cancel, view, go].forEach(function (b) { b.type = 'button'; buttons.appendChild(b); });
    go.disabled = clearable === 0;
    parts.card.appendChild(buttons);

    return new Promise(function (resolve) {
      function finish(answer) {
        doc.removeEventListener('keydown', onKey);
        hide(deps, parts.shell);
        if (invoker && invoker.focus) invoker.focus();
        resolve(answer);
      }
      function onKey(event) {
        if (event.key === 'Escape' && isTop(deps, parts.shell)) finish(false);
      }
      cancel.addEventListener('click', function () { finish(false); });
      go.addEventListener('click', function () { if (!go.disabled) finish(true); });
      view.addEventListener('click', function () {
        return showAssignments(deps, report).then(function () { view.focus(); });
      });
      parts.shell.addEventListener('click', function (event) {
        if (event.target === parts.shell) finish(false);
      });
      doc.addEventListener('keydown', onKey);
      show(deps, parts.shell);
      cancel.focus();
    });
  }

  function readReport(deps) {
    return Promise.resolve()
      .then(function () { return deps.fetch('/api/admin/inventory/assignments'); })
      .then(function (res) {
        return res.json().then(function (body) {
          return res.ok && body && body.ok ? body : null;
        });
      })
      .catch(function () { return null; });
  }

  /*
   * Ask. Resolves true only when the operator chose to clear.
   *
   * deps: doc, fetch, legacyConfirm() -> Promise<boolean>, modalStack (optional),
   * onError(message) (optional).
   *
   * The single-flight guard is taken before the first request, not after the
   * dialog closes: two dialogs stacked on one inventory would each go on to
   * make their own request.
   */
  function confirmClear(deps) {
    if (inFlight) return Promise.resolve(false);
    inFlight = true;
    function release(answer) { inFlight = false; return answer; }
    return readReport(deps).then(function (report) {
      if (!report) {
        if (deps.onError) {
          deps.onError('OmniSuite could not read what is assigned, so nothing was cleared.');
        }
        return false;
      }
      if (!report.remote_access) return deps.legacyConfirm();
      return askAssignmentAware(deps, report);
    }).then(release, function (err) { inFlight = false; throw err; });
  }

  // What to tell the operator afterwards, from the server's own answer.
  function outcomeMessage(outcome) {
    if (!outcome || outcome.ok === false) return '';
    if (outcome.mode === 'unassigned') {
      var cleared = outcome.cleared || 0;
      var kept = outcome.kept || 0;
      return 'Cleared ' + plural(cleared, 'unassigned unit', 'unassigned units') +
        '. ' + plural(kept, 'assigned unit was', 'assigned units were') + ' kept.';
    }
    return '';
  }

  var api = {
    confirmClear: confirmClear,
    showAssignments: showAssignments,
    outcomeMessage: outcomeMessage,
    _reset: function () { inFlight = false; },
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.OmniClearUnits = api;
})(typeof window !== 'undefined' ? window : globalThis);

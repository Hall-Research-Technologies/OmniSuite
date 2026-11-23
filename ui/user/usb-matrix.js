/*
 * ui/user/usb-matrix.js: the restricted User's USB Matrix.
 *
 * The Admin USB Matrix with this account's subset projected into it: one column per computer
 * (the Admin page's LEX), one row per USB device (its REX), one dot per pair. It is drawn from
 * what the server sent for THIS account -- /api/user/usb_state -- and from nothing else, so
 * there is no fuller inventory in the browser to hide cells out of and no device address
 * anywhere in it.
 *
 * What it holds to:
 *  - it never decides what a cell may do. The server derived every cell once, from the same
 *    payload the Admin page renders, and states whether it is available, routed or blocked and
 *    why. A click sends the two identities and the direction and nothing else; the server
 *    authorises both, re-derives the cell from state read for that request, and refuses what
 *    the Admin matrix would not offer.
 *  - the same markup and class names as the Admin grid (td.cell > .radio-wrap > input + .dot),
 *    so ui/matrix/usb-matrix.css and ui/matrix/usb-grid.css style both alike.
 *  - a cell is pending while its request is in flight and is never drawn as routed or failed
 *    until a fresh read says so. The state is read again after every request, success or not;
 *    nothing is painted from the answer alone.
 *  - a state-only refresh rewrites the existing cells in place; the grid is rebuilt only when a
 *    computer or a device was added or taken away.
 *  - the explanation is a bounded, viewport-clamped tooltip (hover and keyboard focus). A native
 *    title would render a long sentence as one very wide strip.
 *  - no timer and no polling: it draws when it is told to.
 */
(function (root) {
  'use strict';

  function create(deps) {
    var doc = deps.document;
    var wrap = deps.wrap;          // the scroll container (.matrix-wrap)
    var table = deps.table;        // table#usbMatrix
    var empty = deps.empty;
    var statusLine = deps.status;
    var legend = deps.legend || null;
    var data = {computers: [], peripherals: [], cells: {}};
    var signature = '';
    // Rows with a request in flight, by device identity: a device is on one computer at a time,
    // so a second request for the same row waits for the first rather than racing it.
    var pending = {};
    var tip = null;
    var tipTimer = null;

    function el(tag, className, text) {
      var node = doc.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    }

    function cellOf(peripheralId, computerId) {
      var row = (data.cells || {})[peripheralId] || {};
      return row[computerId] || {state: 'blocked', routed: false, active: false, enabled: false,
                                 label: 'Not routable', detail: 'This combination is not available.',
                                 stale: false};
    }

    function describe(peripheral) {
      var a = peripheral.attached_to || {};
      if (a.mode === 'computer') return 'Connected to ' + a.computer_name;
      if (a.mode === 'other') return 'Connected to another computer';
      return 'Not connected';
    }

    function say(text, kind) {
      if (!statusLine) return;
      statusLine.textContent = text || '';
      statusLine.setAttribute('data-kind', kind || '');
    }

    // ---------------------------------------------------------------- cells
    function ariaFor(peripheral, computer, cell) {
      if (cell.routed) return 'Disconnect ' + peripheral.name + ' from ' + computer.name;
      return 'Connect ' + peripheral.name + ' to ' + computer.name;
    }

    function paint(td, peripheral, computer, cell) {
      var isBusy = !!pending[peripheral.id];
      var cls = 'cell' + (cell.enabled ? '' : ' disabled') + (isBusy ? ' pending' : '');
      td.className = cls;
      td.setAttribute('data-rex', peripheral.id);
      td.setAttribute('data-lex', computer.id);
      td.setAttribute('data-active', String(!!cell.active));
      td.setAttribute('data-paired', String(!!cell.routed));
      td.setAttribute('data-state', cell.state || '');
      td.setAttribute('data-tip', cell.label || '');
      if (cell.detail) td.setAttribute('data-tip-detail', cell.detail);
      else td.removeAttribute && td.removeAttribute('data-tip-detail');
      if (cell.stale) td.setAttribute('data-stale', '1');
      else td.removeAttribute && td.removeAttribute('data-stale');
      if (isBusy) td.setAttribute('aria-busy', 'true');
      else td.removeAttribute && td.removeAttribute('aria-busy');
      var input = td.querySelector('input');
      var dot = td.querySelector('.dot');
      if (input) {
        input.checked = !!cell.routed;
        input.disabled = !cell.enabled;
        input.setAttribute('aria-label', ariaFor(peripheral, computer, cell));
      }
      if (dot) dot.className = 'dot' + (cell.active ? ' audio-on' : '');
    }

    function buildCell(peripheral, computer) {
      var td = el('td');
      var holder = el('span', 'radio-wrap');
      var input = el('input');
      input.type = 'radio';
      input.name = 'usb-' + peripheral.id;
      var dot = el('span', 'dot');
      dot.setAttribute('aria-hidden', 'true');
      holder.append(input, dot);
      td.appendChild(holder);
      paint(td, peripheral, computer, cellOf(peripheral.id, computer.id));
      return td;
    }

    function headerRow() {
      var tr = el('tr');
      var corner = el('th', 'row-head', 'USB devices \\ Computers');
      tr.appendChild(corner);
      data.computers.forEach(function (computer) {
        var th = el('th', 'enc-head');
        var box = el('div', 'col-header');
        box.appendChild(el('span', 'lex-label', computer.name));
        th.appendChild(box);
        tr.appendChild(th);
      });
      return tr;
    }

    function rowFor(peripheral) {
      var tr = el('tr');
      var head = el('th', 'row-head rex-label');
      head.appendChild(el('span', null, peripheral.name));
      head.appendChild(doc.createElement('br'));
      var small = el('small', null, describe(peripheral));
      small.setAttribute('data-attached', '1');
      head.appendChild(small);
      tr.appendChild(head);
      data.computers.forEach(function (computer) {
        tr.appendChild(buildCell(peripheral, computer));
      });
      return tr;
    }

    function signatureOf() {
      return data.computers.map(function (c) { return c.id + '=' + c.name; }).join('|') + '#' +
        data.peripherals.map(function (p) { return p.id + '=' + p.name; }).join('|');
    }

    function publishFrozenWidth() {
      // The frozen identity column's rendered width, which density, font and zoom each change;
      // usb-matrix.css reserves it as scroll padding so focus never parks under the column.
      if (!wrap || !wrap.style || typeof wrap.style.setProperty !== 'function') return;
      var head = table.querySelector && table.querySelector('th.row-head');
      if (!head || typeof head.getBoundingClientRect !== 'function') return;
      var width = Math.round(head.getBoundingClientRect().width);
      if (width) wrap.style.setProperty('--usb-frozen-col-w', width + 'px');
    }

    function rebuild() {
      var rows = [headerRow()].concat(data.peripherals.map(rowFor));
      table.replaceChildren.apply(table, rows);
      publishFrozenWidth();
    }

    function refreshInPlace() {
      var byKey = {};
      data.peripherals.forEach(function (p) { byKey[p.id] = p; });
      var computers = {};
      data.computers.forEach(function (c) { computers[c.id] = c; });
      table.querySelectorAll('td').forEach(function (td) {
        var p = byKey[td.getAttribute('data-rex')];
        var c = computers[td.getAttribute('data-lex')];
        if (p && c) paint(td, p, c, cellOf(p.id, c.id));
      });
      table.querySelectorAll('small').forEach(function (small) {
        if (small.getAttribute('data-attached') !== '1') return;
        // The note under a device name follows the device it sits beside.
        var row = small.parentNode && small.parentNode.parentNode;
        var cell = row && row.querySelector && row.querySelector('td');
        var p = cell && byKey[cell.getAttribute('data-rex')];
        if (p) small.textContent = describe(p);
      });
    }

    function render(next) {
      data = {computers: (next && next.computers) || [],
              peripherals: (next && next.peripherals) || [],
              cells: (next && next.cells) || {}};
      var none = !data.computers.length || !data.peripherals.length;
      if (wrap) wrap.hidden = none;
      if (legend) legend.hidden = none;
      if (empty) {
        empty.hidden = !none;
        if (none) {
          empty.textContent = !data.computers.length && !data.peripherals.length
            ? 'No computers or USB devices have been assigned to your account.'
            : (!data.computers.length ? 'No computers have been assigned to your account.'
                                       : 'No USB devices have been assigned to your account.');
        }
      }
      if (none) { table.replaceChildren(); signature = ''; return; }
      var now = signatureOf();
      // Structure follows membership; state follows the read. A refresh that changed only state
      // leaves the nodes where they are, so nothing under the pointer is replaced.
      if (now !== signature || !table.querySelector('td')) {
        signature = now;
        rebuild();
      } else {
        refreshInPlace();
      }
    }

    // -------------------------------------------------------------- tooltip
    function tipNode() {
      if (tip) return tip;
      tip = el('div', 'usb-cell-tip');
      tip.setAttribute('role', 'tooltip');
      tip.style.cssText = 'position:fixed;z-index:65000;max-width:320px;width:max-content;'
        + 'background:var(--card,#222a33);color:var(--text,#e9eef5);'
        + 'border:1px solid var(--border,rgba(255,255,255,.14));border-radius:6px;padding:7px 9px;'
        + 'font-size:12px;line-height:1.35;box-shadow:0 6px 20px rgba(0,0,0,.35);'
        + 'pointer-events:none;display:none;white-space:normal;overflow-wrap:anywhere;';
      (doc.body || doc.documentElement).appendChild(tip);
      return tip;
    }

    function showTip(td) {
      var label = td.getAttribute('data-tip');
      if (!label) return;
      var node = tipNode();
      if (tipTimer) { clearTimeout(tipTimer); tipTimer = null; }
      node.textContent = '';
      var head = el('div', null, label);
      head.style.fontWeight = '600';
      node.appendChild(head);
      var detail = td.getAttribute('data-tip-detail');
      if (detail) {
        var sub = el('div', null, detail);
        sub.style.cssText = 'margin-top:3px;color:var(--muted,#9aa7b4);';
        node.appendChild(sub);
      }
      node.style.display = 'block';
      if (typeof td.getBoundingClientRect === 'function' && typeof node.getBoundingClientRect === 'function') {
        var rect = td.getBoundingClientRect();
        var box = node.getBoundingClientRect();
        var margin = 8;
        var vw = (root.innerWidth || 1024), vh = (root.innerHeight || 768);
        var left = rect.left + rect.width / 2 - box.width / 2;
        left = Math.max(margin, Math.min(left, vw - box.width - margin));
        var top = rect.bottom + 6;
        if (top + box.height > vh - margin) top = rect.top - box.height - 6;
        if (top < margin) top = margin;
        node.style.left = Math.round(left) + 'px';
        node.style.top = Math.round(top) + 'px';
      }
    }

    function hideTip() {
      if (!tip) return;
      tipTimer = setTimeout(function () { if (tip) tip.style.display = 'none'; }, 60);
    }

    function cellFrom(target) {
      for (var node = target; node; node = node.parentNode) {
        if (node.tagName === 'TD' && String(node.className || '').split(/\s+/).indexOf('cell') !== -1) {
          return node;
        }
      }
      return null;
    }

    // ---------------------------------------------------------------- action
    async function activate(td) {
      if (!td) return;
      var classes = String(td.className || '').split(/\s+/);
      if (classes.indexOf('disabled') !== -1) return;
      var peripheralId = td.getAttribute('data-rex');
      var computerId = td.getAttribute('data-lex');
      // One request per device at a time.
      if (pending[peripheralId]) return;
      var routed = td.getAttribute('data-paired') === 'true';
      var peripheral = data.peripherals.filter(function (p) { return p.id === peripheralId; })[0];
      var computer = data.computers.filter(function (c) { return c.id === computerId; })[0];
      if (!peripheral || !computer) return;

      pending[peripheralId] = true;
      refreshInPlace();
      say((routed ? 'Disconnecting ' : 'Connecting ') + peripheral.name
          + (routed ? ' from ' : ' to ') + computer.name + '…', '');
      try {
        var result = await deps.request(computerId, peripheralId, routed);
        var body = result.body || {};
        if (result.status === 200 && body.ok) {
          var words = body.changed === false
            ? (routed ? 'That device was already disconnected.' : 'That device was already connected.')
            : (routed ? 'Route removed and verified' : 'Route created and verified');
          say(words, 'ok');
          if (deps.toast) deps.toast(words, 'ok');
        } else {
          var why = deps.failureText ? deps.failureText(body, result.status)
                                     : (body.error || 'That did not work.');
          say(why, 'error');
          if (deps.toast) deps.toast(why, 'error');
        }
      } catch (err) {
        if (String(err && err.message) !== 'signed out') {
          say('Could not reach OmniSuite.', 'error');
          if (deps.toast) deps.toast('Could not reach OmniSuite', 'error');
        }
      } finally {
        delete pending[peripheralId];
        // Always read again: the answer is a request's outcome, the state is what a read says.
        try { await deps.reload(); } catch (err) { /* the next read repairs it */ }
        refreshInPlace();
      }
    }

    // One delegated listener for the grid, bound once.
    table.addEventListener('click', function (event) {
      var td = cellFrom(event.target);
      if (!td) return;
      // A radio button checks itself when it is clicked. The cell is a request, not an outcome:
      // it stays as the last read left it until a fresh read says otherwise.
      if (event && typeof event.preventDefault === 'function') event.preventDefault();
      return activate(td);
    });
    table.addEventListener('mouseover', function (event) {
      var td = cellFrom(event.target);
      if (td) showTip(td);
    });
    table.addEventListener('mouseout', function (event) {
      if (cellFrom(event.target)) hideTip();
    });
    table.addEventListener('focusin', function (event) {
      var td = cellFrom(event.target);
      if (td) showTip(td);
    });
    table.addEventListener('focusout', function (event) {
      if (cellFrom(event.target)) hideTip();
    });
    if (root.addEventListener) root.addEventListener('scroll', hideTip, {passive: true});

    return {
      render: render,
      activate: activate,
      pendingCount: function () { return Object.keys(pending).length; },
    };
  }

  root.OmniUserUsbMatrix = {create: create};
})(typeof window !== 'undefined' ? window : this);

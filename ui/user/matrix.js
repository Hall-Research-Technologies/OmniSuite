/*
 * ui/user/matrix.js: the restricted User's Matrix.
 *
 * A grid of the sources this account may use (columns) by the displays and
 * display groups it may control (rows). It is drawn from what the server sent
 * for THIS account -- /api/user/matrix_state -- and from nothing else: there is
 * no fuller inventory in the browser to hide rows out of, and no device address
 * anywhere in it.
 *
 * What it holds to:
 *  - it never decides anything. A click sends one request through the page's own
 *    route function, which is the same operation the Standard page's Route button
 *    and drag and drop end in; the server revalidates everything. This file does
 *    not know what a group is made of and never routes a member itself.
 *  - the ring is the video route and the dot inside it is the audio route, as in
 *    the main Matrix. A group row says what is true of ALL its members (a filled
 *    ring), SOME of them (a hollow, dashed ring) or none, and names a mixed or
 *    unavailable group in words -- never one display's state standing in for the
 *    room.
 *  - an offline source or display is drawn disabled and says "Offline" as a word.
 *    That is presentation; the server refuses the route on its own account.
 *  - a source's picture is fetched only while the pointer or the keyboard rests
 *    on its heading: a debounce before anything is asked for, cancelled when the
 *    pointer leaves, and the picture dropped rather than hidden on close. Nothing
 *    is fetched when the grid is drawn, and there is no timer.
 */
(function (root) {
  'use strict';

  var MODES = [['av', 'AV'], ['video', 'Video'], ['audio', 'Audio']];
  var MODE_WORDS = {av: 'audio and video', video: 'video only', audio: 'audio only'};
  var PLANE_WORDS = {all: 'All', partial: 'Partial', mixed: 'Mixed', unavailable: 'Unavailable'};
  var HOVER_DELAY_MS = 350;

  // How much of a row carries a source in one plane: 'all', 'partial' or ''.
  // A display is all or nothing; a group is counted against EVERY member,
  // online or not, so a member that is not answering stops it being "all".
  function planeMatch(row, plane, sourceId) {
    if (!plane) return '';
    if (row.kind === 'group') {
      var n = plane.counts && plane.counts.sources ? plane.counts.sources[sourceId] || 0 : 0;
      if (!n) return '';
      return n === plane.total ? 'all' : 'partial';
    }
    return plane.mode === 'source' && plane.source_id === sourceId ? 'all' : '';
  }

  function stateWords(video, audio) {
    function one(label, match) {
      if (match === 'all') return label + ' routed';
      if (match === 'partial') return label + ' routed to some displays';
      return '';
    }
    var parts = [one('video', video), one('audio', audio)].filter(Boolean);
    return parts.length ? parts.join(', ') : 'not routed';
  }

  function create(deps) {
    var doc = deps.document;
    var mode = 'av';
    var data = null;
    var busy = false;
    var preview = {timer: null, token: 0, card: null, owner: null};

    function el(tag, className, text) {
      var node = doc.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    }

    // ---------------------------------------------------------- mode bar
    function renderModes() {
      var bar = deps.modeBar;
      bar.replaceChildren();
      MODES.forEach(function (entry) {
        var button = el('button', 'mx-modebtn' + (entry[0] === mode ? ' active' : ''), entry[1]);
        button.type = 'button';
        button.dataset.mode = entry[0];
        button.setAttribute('role', 'radio');
        // The class is what is painted; aria-checked is what is announced.
        button.setAttribute('aria-checked', String(entry[0] === mode));
        bar.appendChild(button);
      });
    }

    function setMode(next) {
      if (!MODE_WORDS[next] || next === mode) return;
      mode = next;
      renderModes();
      if (data) paint();
    }

    // -------------------------------------------------------------- cells
    function chip(text, className) {
      return el('span', 'mx-chip' + (className ? ' ' + className : ''), text);
    }

    function rowChips(row) {
      var chips = [];
      if (row.online === false) chips.push(chip('Offline', 'offline'));
      if (row.kind === 'display') {
        if (row.video && row.video.mode === 'multiview' && mode !== 'audio') {
          chips.push(chip('Multiview', 'info'));
        }
        return chips;
      }
      [['video', 'Video'], ['audio', 'Audio']].forEach(function (plane) {
        if (mode !== 'av' && mode !== plane[0]) return;
        var summary = row[plane[0]];
        if (!summary || summary.state === 'all') return;
        chips.push(chip(plane[1] + ': ' + PLANE_WORDS[summary.state], summary.state));
      });
      return chips;
    }

    // ------------------------------------------------- structure and state
    //
    // The grid's STRUCTURE is which sources and which rows exist; its STATE is
    // what each is carrying and whether it answers. A state-only refresh --
    // after a route, on returning to the tab -- repaints the cells that are
    // already there rather than rebuilding the table, so a hover, a focus ring
    // or a scroll position is not thrown away every time something is read.
    var structureKey = null;
    var parts = {cells: [], rowHeads: {}, sourceHeads: {}};

    function keyOf(sources, rows) {
      return JSON.stringify([
        sources.map(function (s) { return [s.id, s.name]; }),
        rows.map(function (r) { return [r.kind, r.id, r.name, r.displays || 0]; }),
      ]);
    }

    // A display and a group are different kinds of thing; the identity of a row is both.
    function rowKey(row) { return row.kind + ':' + row.id; }

    function buildCell(row, source) {
      var td = el('td', 'mx-td');
      var button = el('button', 'mx-cell');
      button.type = 'button';
      button.dataset.source = source.id;
      button.dataset.row = row.id;
      button.dataset.kind = row.kind;
      var ring = el('span', 'mx-ring');
      var dot = el('span', 'mx-dot');
      ring.appendChild(dot);
      button.appendChild(ring);
      td.appendChild(button);
      parts.cells.push({button: button, ring: ring, dot: dot, row: rowKey(row), source: source.id});
      return td;
    }

    function paint() {
      var sources = (data && data.sources) || [];
      var rows = (data && data.rows) || [];
      var sourceById = {};
      var rowById = {};
      sources.forEach(function (s) { sourceById[s.id] = s; });
      rows.forEach(function (r) { rowById[rowKey(r)] = r; });

      sources.forEach(function (source) {
        var th = parts.sourceHeads[source.id];
        if (!th) return;
        var gone = source.online === false;
        th.classList.toggle('offline', gone);
        th.chips.replaceChildren();
        if (gone) th.chips.appendChild(chip('Offline', 'offline'));
      });
      rows.forEach(function (row) {
        var th = parts.rowHeads[rowKey(row)];
        if (!th) return;
        th.chips.replaceChildren();
        rowChips(row).forEach(function (c) { th.chips.appendChild(c); });
      });
      parts.cells.forEach(function (cell) {
        var row = rowById[cell.row];
        var source = sourceById[cell.source];
        if (!row || !source) return;
        var video = planeMatch(row, row.video, source.id);
        var audio = planeMatch(row, row.audio, source.id);
        cell.ring.className = 'mx-ring' + (video ? ' video-' + video : '');
        cell.dot.className = 'mx-dot' + (audio ? ' audio-' + audio : '');
        var gone = source.online === false || row.online === false;
        cell.button.disabled = gone || busy;
        cell.button.classList.toggle('offline-blocked', gone);
        cell.button.setAttribute('aria-label',
          'Route ' + source.name + ' to ' + row.name + ' (' + MODE_WORDS[mode] + '): '
          + stateWords(video, audio) + (gone ? ', offline' : ''));
      });
    }

    function render(next) {
      var sources = (next && next.sources) || [];
      var rows = (next && next.rows) || [];
      data = next;
      var any = sources.length > 0 && rows.length > 0;
      deps.empty.hidden = any;
      deps.scroller.hidden = !any;
      if (!any) {
        closePreview();
        structureKey = null;
        deps.grid.replaceChildren();
        deps.empty.textContent = !sources.length && !rows.length
          ? 'No sources or displays have been assigned to your account.'
          : (!sources.length ? 'No sources have been assigned to your account.'
                             : 'No displays or groups have been assigned to your account.');
        return;
      }

      var key = keyOf(sources, rows);
      if (key === structureKey) { paint(); return; }

      // Membership or identity changed: rebuild.
      closePreview();
      structureKey = key;
      parts = {cells: [], rowHeads: {}, sourceHeads: {}};
      var grid = deps.grid;
      grid.replaceChildren();
      var table = el('table', 'mx');
      var head = el('thead');
      var headRow = el('tr');
      headRow.appendChild(el('th', 'mx-corner'));
      sources.forEach(function (source) {
        var th = el('th', 'mx-source-head');
        th.dataset.source = source.id;
        th.setAttribute('tabindex', '0');
        th.appendChild(el('span', 'mx-source-name', source.name));
        th.chips = el('span', 'mx-chips');
        th.appendChild(th.chips);
        parts.sourceHeads[source.id] = th;
        headRow.appendChild(th);
      });
      head.appendChild(headRow);
      table.appendChild(head);

      var body = el('tbody');
      rows.forEach(function (row) {
        var tr = el('tr', 'mx-row' + (row.kind === 'group' ? ' mx-group' : ''));
        var th = el('th', 'mx-row-head');
        th.dataset.kind = row.kind;
        th.appendChild(el('span', 'mx-row-name', row.name));
        if (row.kind === 'group') {
          th.appendChild(el('span', 'mx-row-kind',
            'Group · ' + row.displays + ' display' + (row.displays === 1 ? '' : 's')));
        }
        th.chips = el('span', 'mx-chips');
        th.appendChild(th.chips);
        parts.rowHeads[rowKey(row)] = th;
        tr.appendChild(th);
        sources.forEach(function (source) { tr.appendChild(buildCell(row, source)); });
        body.appendChild(tr);
      });
      table.appendChild(body);
      grid.appendChild(table);
      paint();
    }

    // ------------------------------------------------------------ preview
    function closePreview() {
      if (preview.timer !== null) { deps.clearTimeout(preview.timer); preview.timer = null; }
      preview.token += 1;           // a picture still on its way is no longer wanted
      preview.owner = null;
      if (preview.card) {
        // Dropped, not merely hidden.
        preview.card.replaceChildren();
        preview.card.hidden = true;
      }
    }

    function placeCard(card, head) {
      if (!head || typeof head.getBoundingClientRect !== 'function') return;
      var box = head.getBoundingClientRect();
      var width = 240;
      var viewport = deps.viewportWidth ? deps.viewportWidth() : 1024;
      var left = Math.max(8, Math.min(box.left, viewport - width - 8));
      card.style.left = left + 'px';
      card.style.top = (box.bottom + 6) + 'px';
    }

    function openPreview(head) {
      var id = head.dataset.source;
      var source = ((data && data.sources) || []).filter(function (s) { return s.id === id; })[0];
      // An offline source has no picture to ask for.
      if (!source || source.online === false || !deps.fetchPreview) return;
      if (!preview.card) {
        preview.card = el('div', 'mx-preview empty');
        preview.card.hidden = true;
        preview.card.setAttribute('aria-hidden', 'true');
        deps.previewHost.appendChild(preview.card);
      }
      var token = ++preview.token;
      preview.owner = id;
      placeCard(preview.card, head);
      preview.card.hidden = false;
      preview.card.classList.add('empty');
      preview.card.dataset.note = 'Loading…';
      Promise.resolve(deps.fetchPreview(id)).then(function (result) {
        if (token !== preview.token || !preview.card) return;   // the pointer moved on
        deps.drawPreview(preview.card, result);
      });
    }

    function scheduleHover(head) {
      closePreview();
      preview.timer = deps.setTimeout(function () {
        preview.timer = null;
        openPreview(head);
      }, HOVER_DELAY_MS);
    }

    // ------------------------------------------------------------- events
    function onClick(event) {
      var modeButton = event.target.closest('.mx-modebtn');
      if (modeButton) { setMode(modeButton.dataset.mode); return; }
      var cell = event.target.closest('.mx-cell');
      if (!cell || cell.disabled || busy) return;
      closePreview();
      // One request, through the page's own route function. The server decides.
      deps.route({source: cell.dataset.source, kind: cell.dataset.kind,
                  id: cell.dataset.row, mode: mode});
    }

    function onEnter(event) {
      var head = event.target.closest('.mx-source-head');
      if (head) scheduleHover(head);
    }

    function onLeave(event) {
      if (event.target.closest('.mx-source-head')) closePreview();
    }

    deps.scroller.addEventListener('click', onClick);
    deps.modeBar.addEventListener('click', onClick);
    deps.grid.addEventListener('mouseover', onEnter);
    deps.grid.addEventListener('mouseout', onLeave);
    deps.grid.addEventListener('focusin', onEnter);
    deps.grid.addEventListener('focusout', onLeave);
    // A drag, a scroll or the page going away is not a hover.
    deps.scroller.addEventListener('scroll', closePreview);

    renderModes();

    return {
      render: render,
      setMode: setMode,
      mode: function () { return mode; },
      setBusy: function (value) {
        busy = !!value;
        if (data) paint();
      },
      close: closePreview,
      hasPreviewTimer: function () { return preview.timer !== null; },
    };
  }

  root.OmniUserMatrix = {create: create, planeMatch: planeMatch, HOVER_DELAY_MS: HOVER_DELAY_MS};
})(typeof window !== 'undefined' ? window : this);

/*
 * ui/user/multiview.js: the restricted User's Multiview page.
 *
 * A User may be given saved Multiview layouts by an administrator. The page has two
 * levels:
 *
 *   the list    one card per assigned layout, with Show (as the administrator saved it) and
 *               "Choose sources" (open it)
 *   the editor  one layout opened: its windows, what is in each now, the sources this
 *               account may use, the choices made so far, and Show
 *
 * What it never does: create, rename, resize, reshape or delete a layout, or offer a
 * source the server did not list for this account. The design of a layout is the
 * administrator's and this page has no control for it.
 *
 * What it holds to:
 *  - it draws what /api/user/multiview_state and /api/user/multiview_layout sent for THIS
 *    account. Neither carries a device address, so there is none here to hide.
 *  - choices are PENDING until Show. They are held here as {window: source id, or '' for
 *    empty} laid over what the server says the display holds, and they are sent as exactly
 *    that. The page says so in words: Show applies them to the display; they are not saved
 *    into the administrator's layout.
 *  - every edit is a synchronous change to one small map, so any number of them in any
 *    order (clear, clear, clear; clear and at once assign another; assign, assign, unassign,
 *    assign) leaves the page showing exactly the map. Only the one question that needs a
 *    person -- a source asked to fill windows of different sizes -- is asynchronous, and the
 *    world is read again when it is answered.
 *  - a source cannot fill two windows of different sizes (one source has one scaler). The
 *    same sentence and choice the administrator gets: Cancel changes nothing; Replace empties
 *    the other window and fills this one in one step. The server refuses the combination
 *    anyway; this only asks first.
 *  - Show sends the revision the page was looking at. If the layout changed meanwhile the
 *    server says so and writes nothing; the page re-reads and keeps the person's choices
 *    over the new state. A failed Show keeps the choices, because it changed nothing the
 *    person asked for. A successful one clears them and re-reads what the display holds.
 *  - a response that was already in flight when the world changed is discarded.
 *  - a Show takes seconds on real hardware, so it is single-flight; the lock is taken before
 *    anything else happens.
 *  - no timer and no polling: state is read when the tab opens, when a layout is opened,
 *    when asked, and once after a Show.
 */
(function (root) {
  'use strict';

  var EMPTY_TEXT = 'No Multiview layouts have been given to your account.';
  var NOTE_TEXT = 'Your choices are applied to the display when you press Show. They do not change '
    + 'the layout your administrator saved; showing that layout again from the list uses the '
    + 'administrator’s sources.';
  var RESTRICTED = '\u0000restricted';

  function create(deps) {
    var doc = deps.document;
    var host = deps.host;                 // where the displays are drawn
    var statusLine = deps.status || null; // a polite live region, optional
    var load = deps.load;                 // () -> Promise<{status, body}>
    var show = deps.show;                 // (layoutId) -> Promise<{status, body}>
    var detail = deps.detail || null;     // (layoutId) -> Promise<{status, body}>
    var apply = deps.apply || null;       // ({layout, base, changes, op_id}) -> Promise<{status, body}>
    var toast = deps.toast || function () {};
    var sentenceFor = deps.failureText || function (body) {
      return (body && body.error) || 'That did not work.';
    };
    var confirmDialog = deps.confirm || function (options) {
      return typeof root.omniConfirm === 'function' ? root.omniConfirm(options) : null;
    };

    var phase = 'idle';           // idle | loading | error | ready
    var signature = null;
    var cards = {};               // layout id -> {card, badge, button, reason, result}
    var inflight = {};            // display id -> layout id being shown
    var results = {};             // layout id -> {text, kind} from the last attempt
    var data = {displays: []};
    var reading = null;
    var editor = null;            // the open layout, or null while the list is showing
    var opSeq = 0;
    var readSeq = 0;

    function el(tag, className, text) {
      var node = doc.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    }

    function say(text) {
      if (statusLine) statusLine.textContent = text || '';
    }

    function message(className, text) {
      var node = el('div', className, text);
      node.setAttribute('role', 'status');
      return node;
    }

    function sentence(body, status) {
      var text = sentenceFor(body, status) || 'That did not work.';
      return text.charAt(0).toUpperCase() + text.slice(1);
    }

    // -------------------------------------------------------- schematic
    // Where a window's top-left corner is, in canvas units. A PiP inset is anchored to its own
    // corner, so the x and y the server sends are that corner and not the top-left.
    function corner(w) {
      var anchor = String(w.anchor || '');
      var x = Number(w.x) || 0;
      var y = Number(w.y) || 0;
      if (anchor.indexOf('right') >= 0) x -= Number(w.width) || 0;
      if (anchor.indexOf('bottom') >= 0) y -= Number(w.height) || 0;
      return {x: x, y: y};
    }

    function schematic(layout) {
      var canvas = layout.canvas || {};
      var width = Number(canvas.width) || 1920;
      var height = Number(canvas.height) || 1080;
      var windows = Array.isArray(layout.windows) ? layout.windows : [];
      var box = el('div', 'mv-schematic');
      box.setAttribute('role', 'img');
      box.setAttribute('aria-label', (layout.layout_label || 'Layout') + ', '
        + windows.length + (windows.length === 1 ? ' window' : ' windows'));
      windows.forEach(function (w, i) {
        var part = el('span', 'mv-window');
        // Proportions only. The numbers are percentages of the canvas the server
        // reported, so the picture cannot disagree with the geometry.
        var at = corner(w);
        part.style.left = (100 * at.x / width) + '%';
        part.style.top = (100 * at.y / height) + '%';
        part.style.width = (100 * (Number(w.width) || 0) / width) + '%';
        part.style.height = (100 * (Number(w.height) || 0) / height) + '%';
        part.setAttribute('data-window', String(i + 1));
        box.appendChild(part);
      });
      return box;
    }

    // ------------------------------------------------------------ cards
    function buildCard(display, layout) {
      var card = el('li', 'mv-card');
      card.setAttribute('data-layout', layout.id);
      card.appendChild(schematic(layout));

      var body = el('div', 'mv-body');
      body.appendChild(el('div', 'mv-name', layout.name));
      // The kind of layout, unless the name already says it.
      var kind = layout.layout_label || 'Custom';
      if (kind !== layout.name) body.appendChild(el('div', 'mv-meta', kind));
      // A word with a check mark, not a colour alone.
      var badge = el('span', 'mv-showing', '✓ Showing');
      badge.hidden = true;
      body.appendChild(badge);
      var reason = el('div', 'mv-reason');
      reason.hidden = true;
      body.appendChild(reason);
      var result = el('div', 'mv-result');
      result.setAttribute('role', 'status');
      result.hidden = true;
      body.appendChild(result);
      card.appendChild(body);

      var button = el('button', 'mv-show btn-secondary-lg', 'Show');
      button.type = 'button';
      button.setAttribute('aria-label', 'Show ' + layout.name + ' on ' + display.name);
      button.addEventListener('click', function () { return activate(layout.id); });
      card.appendChild(button);
      var open = null;
      if (detail && apply) {
        open = el('button', 'mv-open btn-secondary-lg', 'Choose sources');
        open.type = 'button';
        open.setAttribute('aria-label', 'Choose sources for ' + layout.name + ' on ' + display.name);
        open.addEventListener('click', function () { return openLayout(layout.id); });
        card.appendChild(open);
      }

      cards[layout.id] = {card: card, badge: badge, button: button, open: open, reason: reason,
                          result: result, display: display.id, name: layout.name};
      return card;
    }

    function paint(layout) {
      var ref = cards[layout.id];
      if (!ref) return;
      var working = inflight[ref.display] === layout.id;
      var displayBusy = !!inflight[ref.display];
      ref.card.classList.toggle('showing', !!layout.showing);
      ref.card.classList.toggle('pending', working);
      ref.card.setAttribute('aria-busy', working ? 'true' : 'false');
      ref.badge.hidden = !layout.showing;
      ref.button.disabled = !layout.available || displayBusy;
      ref.button.textContent = working ? 'Working…' : 'Show';
      if (ref.open) ref.open.disabled = displayBusy;
      var why = layout.available ? '' : (layout.reason || 'This layout cannot be shown right now.');
      ref.reason.textContent = why;
      ref.reason.hidden = !why;
      var last = results[layout.id];
      ref.result.textContent = last ? last.text : '';
      ref.result.className = 'mv-result' + (last && last.kind === 'error' ? ' error' : '');
      ref.result.hidden = !last;
    }

    function shapeOf(displays) {
      return JSON.stringify(displays.map(function (d) {
        return [d.id, d.name, d.unavailable || '', (d.layouts || []).map(function (l) {
          return [l.id, l.name, l.layout_label, l.canvas, l.windows];
        })];
      }));
    }

    function build(displays) {
      cards = {};
      var frag = [];
      displays.forEach(function (display) {
        var section = el('section', 'mv-display');
        section.setAttribute('data-display', display.id);
        section.appendChild(el('h3', 'mv-display-name', display.name));
        if (display.unavailable) {
          section.appendChild(message('mv-unavailable', display.unavailable));
        }
        var list = el('ul', 'mv-cards');
        (display.layouts || []).forEach(function (layout) {
          list.appendChild(buildCard(display, layout));
        });
        if (list.children.length) section.appendChild(list);
        frag.push(section);
      });
      host.replaceChildren.apply(host, frag);
    }

    // ----------------------------------------------------------- render
    function render(body) {
      var displays = Array.isArray(body && body.displays) ? body.displays : [];
      data = {displays: displays};
      phase = 'ready';
      if (editor) return;     // the open layout is not redrawn under somebody's hand
      var shown = displays.filter(function (d) {
        return (d.layouts && d.layouts.length) || d.unavailable;
      });
      if (!shown.length) {
        signature = null;
        cards = {};
        host.replaceChildren(message('empty', EMPTY_TEXT));
        return;
      }
      var next = shapeOf(shown);
      if (next !== signature) {
        build(shown);
        signature = next;
      }
      shown.forEach(function (d) { (d.layouts || []).forEach(paint); });
    }

    function renderLoading() {
      signature = null;
      cards = {};
      host.replaceChildren(message('empty', 'Loading your Multiview layouts…'));
    }

    function renderFailure(text) {
      signature = null;
      cards = {};
      var box = el('div', 'empty mv-error');
      box.setAttribute('role', 'alert');
      box.appendChild(el('p', '', text));
      var retry = el('button', 'btn-quiet mv-retry', 'Try again');
      retry.type = 'button';
      retry.addEventListener('click', function () { return refresh(); });
      box.appendChild(retry);
      host.replaceChildren(box);
    }

    // ------------------------------------------------------------- read
    // Opening the tab reads the state; an untouched page does not read it again.
    // `quiet` is the read after a show, which must not flash the page back to a
    // loading message under somebody's finger.
    function refresh(quiet) {
      if (reading) return reading;
      if (!quiet && phase !== 'ready' && !editor) {
        phase = 'loading';
        renderLoading();
      }
      reading = Promise.resolve().then(load).then(function (reply) {
        if (!reply || reply.status !== 200 || !reply.body || reply.body.ok === false) {
          throw new Error((reply && reply.body && reply.body.error) || 'failed');
        }
        render(reply.body);
        return true;
      }).catch(function (err) {
        if (String(err && err.message) === 'signed out') return false;
        if ((quiet || editor) && phase === 'ready') return false;   // keep what is on screen
        phase = 'error';
        renderFailure('Your Multiview layouts could not be loaded. '
                      + 'This is not the same as having none.');
        return false;
      }).then(function (ok) { reading = null; return ok; });
      return reading;
    }

    // ------------------------------------------------------------- show
    function find(layoutId) {
      var found = null;
      data.displays.forEach(function (d) {
        (d.layouts || []).forEach(function (l) {
          if (l.id === layoutId) found = {display: d, layout: l};
        });
      });
      return found;
    }

    function activate(layoutId) {
      var hit = find(layoutId);
      if (!hit || !hit.layout.available) return Promise.resolve(false);
      var displayId = hit.display.id;
      // The lock comes first: nothing between the click and the request may let a
      // second click through.
      if (inflight[displayId]) return Promise.resolve(false);
      inflight[displayId] = layoutId;
      delete results[layoutId];
      data.displays.forEach(function (d) { (d.layouts || []).forEach(paint); });
      say('Showing ' + hit.layout.name + ' on ' + hit.display.name + '…');

      return Promise.resolve().then(function () { return show(layoutId); })
        .then(function (reply) {
          var body = (reply && reply.body) || {};
          if (reply && reply.status === 200 && body.ok) {
            var note = body.message || '';
            results[layoutId] = {text: note || 'Shown on ' + hit.display.name + '.',
                                 kind: note ? 'warn' : 'ok'};
            toast(note || (hit.layout.name + ' is now showing on ' + hit.display.name + '.'),
                  note ? 'warn' : 'ok');
            return true;
          }
          // The server's own sentence, started the way a sentence starts.
          var text = sentence(body, reply ? reply.status : 0);
          results[layoutId] = {text: text, kind: 'error'};
          toast(text, 'error');
          return false;
        }, function (err) {
          if (String(err && err.message) === 'signed out') return false;
          var text = 'OmniSuite could not be reached.';
          results[layoutId] = {text: text, kind: 'error'};
          toast(text, 'error');
          return false;
        })
        .then(function (ok) {
          delete inflight[displayId];
          say('');        // the card and the toast carry the outcome
          // One read to update the Showing marker, success or not: a failed show
          // may have been rolled back, and what is on the display is what the
          // device now says.
          return refresh(true).then(function () {
            data.displays.forEach(function (d) { (d.layouts || []).forEach(paint); });
            return ok;
          });
        });
    }

    // ================================================================ the editor
    //
    // `editor.base` is what the server said the display holds (windows, sources,
    // revision). `editor.pending` is the person's choices over it. Neither is ever
    // derived from the other's DOM.

    function baselineValue(win) {
      if (!win || !win.source) return '';
      return win.source.id ? win.source.id : RESTRICTED;
    }

    function windowByCell(cell) {
      var found = null;
      editor.windows.forEach(function (w) { if (w.cell === cell) found = w; });
      return found;
    }

    function hasPending(cell) {
      return Object.prototype.hasOwnProperty.call(editor.pending, cell);
    }

    // What window `cell` will hold if Show is pressed now.
    function effective(cell) {
      return hasPending(cell) ? editor.pending[cell] : baselineValue(windowByCell(cell));
    }

    function pendingCount() {
      return editor ? Object.keys(editor.pending).length : 0;
    }

    function sizeOf(cell) {
      var w = windowByCell(cell);
      return w ? (w.width + 'x' + w.height) : '';
    }

    function sourceName(id) {
      var name = '';
      editor.sources.forEach(function (s) { if (s.id === id) name = s.name; });
      if (!name) {
        editor.windows.forEach(function (w) {
          if (w.source && w.source.id === id) name = w.source.name;
        });
      }
      return name || 'This source';
    }

    function windowLabel(cell) {
      var w = windowByCell(cell);
      return w ? w.label : cell;
    }

    // Records a choice for one window. Choosing what the display already holds is
    // not a change, so it is removed from the map rather than recorded.
    function setChoice(cell, value) {
      if (value === baselineValue(windowByCell(cell))) delete editor.pending[cell];
      else editor.pending[cell] = value;
    }

    // Windows that will hold `id` and are a different size from `cell`.
    function sizeConflicts(cell, id) {
      var wanted = sizeOf(cell);
      return editor.windows.map(function (w) { return w.cell; }).filter(function (other) {
        return other !== cell && effective(other) === id && sizeOf(other) !== wanted;
      }).sort();
    }

    function busy() { return !!editor && (editor.applying || editor.phase !== 'ready'); }

    function clearWindow(cell) {
      if (!editor || busy() || editor.confirming) return false;
      if (!windowByCell(cell)) return false;
      if (effective(cell) === '') return false;             // nothing to remove
      setChoice(cell, '');
      editor.result = null;
      paintEditor();
      return true;
    }

    function assignWindow(cell, id) {
      if (!editor || busy()) return Promise.resolve(false);
      if (!id) return Promise.resolve(clearWindow(cell));
      if (editor.confirming) return Promise.resolve(false);   // one question at a time
      if (!windowByCell(cell) || !selectable(id)) return Promise.resolve(false);
      if (effective(cell) === id) return Promise.resolve(false);
      var conflicts = sizeConflicts(cell, id);
      if (!conflicts.length) {
        setChoice(cell, id);
        editor.result = null;
        paintEditor();
        return Promise.resolve(true);
      }
      editor.confirming = true;
      paintEditor();
      return Promise.resolve().then(function () { return askReplace(cell, id, conflicts); })
        .then(function (agreed) { return agreed; }, function () { return false; })
        .then(function (agreed) {
          if (!editor) return false;
          editor.confirming = false;
          var applied = false;
          // Cancel: both stay as they were. Otherwise the world is read again,
          // because it may have moved while the question was open.
          if (agreed && !editor.applying) {
            sizeConflicts(cell, id).forEach(function (other) { setChoice(other, ''); });
            setChoice(cell, id);
            editor.result = null;
            applied = true;
          }
          paintEditor();
          return applied;
        });
    }

    function selectable(id) {
      var ok = false;
      editor.sources.forEach(function (s) { if (s.id === id && s.selectable) ok = true; });
      return ok;
    }

    function askReplace(cell, id, conflicts) {
      var summary = [{label: 'Source', value: sourceName(id)}];
      conflicts.forEach(function (other) {
        summary.push({label: 'Currently in',
                      value: windowLabel(other) + ' (' + sizeOf(other) + ')'});
      });
      summary.push({label: 'Chosen for', value: windowLabel(cell) + ' (' + sizeOf(cell) + ')'});
      var answer = confirmDialog({
        title: 'Replace assignment?',
        message: 'This source is currently assigned to another window with a different size. '
          + 'Replace that assignment?',
        summary: summary,
        confirmText: 'Replace',
        cancelDefault: true,
      });
      if (!answer) {
        // The sentence is the point of this question; a browser string box would
        // not carry it, so without the shared dialog the change is refused.
        toastIfAny('This source is in a window of a different size, and the confirmation '
          + 'dialog is unavailable, so nothing was changed.', 'error');
        return Promise.resolve(false);
      }
      return Promise.resolve(answer).then(function (value) {
        return value === true || !!(value && value.ok);
      });
    }

    function toastIfAny(text, kind) { toast(text, kind); }

    function discardChoices() {
      if (!editor || busy() || editor.confirming) return false;
      editor.pending = {};
      editor.result = null;
      paintEditor();
      return true;
    }

    // The base is replaced by what the server now says; the person's choices stay,
    // minus any that the new state already satisfies.
    function rebase(body) {
      editor.windows = body.windows || [];
      editor.sources = body.sources || [];
      editor.hidden = body.hidden_sources || 0;
      editor.layout = body.layout || editor.layout;
      editor.display = body.display || editor.display;
      editor.revision = body.revision || '';
      var cells = {};
      editor.windows.forEach(function (w) { cells[w.cell] = true; });
      Object.keys(editor.pending).forEach(function (cell) {
        if (!cells[cell]) { delete editor.pending[cell]; return; }
        if (editor.pending[cell] === baselineValue(windowByCell(cell))) delete editor.pending[cell];
        else if (editor.pending[cell] && !selectable(editor.pending[cell])) {
          delete editor.pending[cell];                       // no longer a source on offer
        }
      });
    }

    function readLayout(layoutId, keepChoices) {
      readSeq += 1;
      var mine = readSeq;
      return Promise.resolve().then(function () { return detail(layoutId); }).then(function (reply) {
        if (mine !== readSeq || !editor || editor.layoutId !== layoutId) return null;   // stale
        var body = (reply && reply.body) || {};
        if (!reply || reply.status !== 200 || !body.ok) {
          return {failed: true, body: body, status: reply ? reply.status : 0};
        }
        if (keepChoices) rebase(body);
        else {
          editor.pending = {};
          rebase(body);
        }
        return {failed: false, body: body};
      }, function (err) {
        if (mine !== readSeq || !editor) return null;
        if (String(err && err.message) === 'signed out') return null;
        return {failed: true, body: {}, status: 0};
      });
    }

    function openLayout(layoutId) {
      var hit = find(layoutId);
      if (!hit || !detail || !apply) return Promise.resolve(false);
      if (inflight[hit.display.id]) return Promise.resolve(false);
      editor = {layoutId: layoutId, phase: 'loading', applying: false, confirming: false,
                pending: {}, windows: [], sources: [], hidden: 0, revision: '',
                layout: hit.layout, display: {id: hit.display.id, name: hit.display.name},
                result: null, dom: null, op: ''};
      buildEditorShell();
      paintEditor();
      return readLayout(layoutId, false).then(function (outcome) {
        if (!outcome || !editor) return false;
        if (outcome.failed) {
          editor.phase = 'error';
          editor.result = {text: sentence(outcome.body, outcome.status)
            || 'This layout could not be opened.', kind: 'error'};
        } else {
          editor.phase = 'ready';
          buildEditorWindows();
        }
        paintEditor();
        return !outcome.failed;
      });
    }

    function closeEditor() {
      if (!editor || editor.applying) return false;
      editor = null;
      readSeq += 1;                 // whatever was in flight describes a closed page
      signature = null;
      render(data);
      refresh(true);
      return true;
    }

    function refreshEditor() {
      if (!editor || busy() || editor.confirming) return Promise.resolve(false);
      var id = editor.layoutId;
      editor.phase = 'loading';
      paintEditor();
      return readLayout(id, true).then(function (outcome) {
        if (!outcome || !editor) return false;
        editor.phase = outcome.failed ? 'error' : 'ready';
        if (outcome.failed) {
          editor.result = {text: sentence(outcome.body, outcome.status), kind: 'error'};
        } else {
          buildEditorWindows();
        }
        paintEditor();
        return !outcome.failed;
      });
    }

    function applyChoices() {
      if (!editor || busy() || editor.confirming) return Promise.resolve(false);
      if (!editor.layout.available || !editor.layout.editable) return Promise.resolve(false);
      // The lock comes first.
      editor.applying = true;
      opSeq += 1;
      var op = 'mv' + Date.now().toString(36) + '-' + opSeq;
      editor.op = op;
      readSeq += 1;                 // a read begun before this describes the world before it
      var layoutId = editor.layoutId;
      var sent = Object.assign({}, editor.pending);
      editor.result = null;
      say('Showing ' + editor.layout.name + ' on ' + editor.display.name + '…');
      paintEditor();
      return Promise.resolve().then(function () {
        return apply({layout: layoutId, base: editor.revision, changes: sent, op_id: op});
      }).then(function (reply) {
        return {reply: reply, thrown: null};
      }, function (err) {
        return {reply: null, thrown: err};
      }).then(function (got) {
        if (!editor || editor.layoutId !== layoutId) return false;
        var reply = got.reply;
        var body = (reply && reply.body) || {};
        if (got.thrown && String(got.thrown && got.thrown.message) === 'signed out') {
          editor.applying = false;
          return false;
        }
        // An answer that is not to this request describes some other one.
        if (body.op_id && body.op_id !== op) { editor.applying = false; paintEditor(); return false; }
        var ok = !!(reply && reply.status === 200 && body.ok);
        if (ok) {
          var note = body.message || '';
          editor.result = {text: note || ('Shown on ' + editor.display.name + '.'),
                           kind: note ? 'warn' : 'ok'};
          toast(note || (editor.layout.name + ' is now showing on ' + editor.display.name + '.'),
                note ? 'warn' : 'ok');
        } else {
          var text = got.thrown ? 'OmniSuite could not be reached.'
            : sentence(body, reply ? reply.status : 0);
          editor.result = {text: text, kind: 'error'};
          toast(text, 'error');
        }
        editor.phase = 'loading';
        say('');
        paintEditor();
        // The server is the authority: read it again. A success starts from what the
        // display now holds; a failure keeps the choices over whatever it holds now.
        return readLayout(layoutId, !ok).then(function (outcome) {
          if (!editor || editor.layoutId !== layoutId) return ok;
          editor.applying = false;
          if (!outcome) { editor.phase = 'ready'; paintEditor(); return ok; }
          if (outcome.failed) {
            editor.phase = 'error';
            if (ok) editor.result = {text: editor.result.text, kind: editor.result.kind};
            else editor.result = {text: editor.result.text, kind: 'error'};
          } else {
            editor.phase = 'ready';
            buildEditorWindows();
          }
          paintEditor();
          refresh(true);               // the list's Showing marker follows the device
          return ok;
        });
      });
    }

    // ---- the editor's DOM: structure is built when membership changes, state is
    // painted in place.
    function buildEditorShell() {
      var root_ = el('section', 'mv-editor');
      root_.setAttribute('aria-labelledby', 'mv_editor_title');
      var head = el('div', 'mv-editor-head');
      var back = el('button', 'mv-back btn-quiet', '← All layouts');
      back.type = 'button';
      back.addEventListener('click', function () { closeEditor(); });
      head.appendChild(back);
      var heading = el('h3', 'mv-editor-title');
      heading.id = 'mv_editor_title';
      head.appendChild(heading);
      var meta = el('div', 'mv-editor-meta');
      head.appendChild(meta);
      var live = el('span', 'mv-showing', '✓ Showing now');
      live.hidden = true;
      head.appendChild(live);
      root_.appendChild(head);

      var note = el('p', 'mv-note', NOTE_TEXT);
      root_.appendChild(note);

      var body = el('div', 'mv-editor-body');
      var stageWrap = el('div', 'mv-stage-wrap');
      var stage = el('div', 'mv-stage');
      stageWrap.appendChild(stage);
      body.appendChild(stageWrap);

      var side = el('div', 'mv-side');
      var sourcesHead = el('h4', 'mv-side-title', 'Your sources');
      side.appendChild(sourcesHead);
      var palette = el('div', 'mv-palette');
      palette.setAttribute('role', 'group');
      palette.setAttribute('aria-label', 'Your sources');
      side.appendChild(palette);
      var hidden = el('p', 'mv-hidden-sources');
      hidden.hidden = true;
      side.appendChild(hidden);
      body.appendChild(side);
      root_.appendChild(body);

      var list = el('ul', 'mv-windows');
      root_.appendChild(list);

      var pending = el('div', 'mv-pending');
      pending.setAttribute('role', 'status');
      pending.setAttribute('aria-live', 'polite');
      root_.appendChild(pending);

      var result = el('div', 'mv-result mv-editor-result');
      result.setAttribute('role', 'status');
      result.hidden = true;
      root_.appendChild(result);

      var bar = el('div', 'mv-actionbar');
      var discard = el('button', 'mv-discard btn-secondary-lg', 'Discard changes');
      discard.type = 'button';
      discard.addEventListener('click', function () { discardChoices(); });
      var refreshButton = el('button', 'mv-reread btn-secondary-lg', 'Refresh');
      refreshButton.type = 'button';
      refreshButton.addEventListener('click', function () { refreshEditor(); });
      var showButton = el('button', 'mv-apply btn-primary-lg', 'Show');
      showButton.type = 'button';
      showButton.addEventListener('click', function () { applyChoices(); });
      bar.appendChild(discard);
      bar.appendChild(refreshButton);
      bar.appendChild(showButton);
      root_.appendChild(bar);

      editor.dom = {root: root_, title: heading, meta: meta, live: live, stage: stage, palette: palette,
                    hidden: hidden, list: list, pending: pending, result: result,
                    discard: discard, reread: refreshButton, show: showButton,
                    boxes: {}, rows: {}, chips: {}, picked: ''};
      host.replaceChildren(root_);
    }

    var dragging = '';

    function buildEditorWindows() {
      var dom = editor.dom;
      dom.stage.replaceChildren();
      dom.list.replaceChildren();
      dom.palette.replaceChildren();
      dom.boxes = {};
      dom.rows = {};
      dom.chips = {};
      var canvas = (editor.layout && editor.layout.canvas) || {};
      var width = Number(canvas.width) || 1920;
      var height = Number(canvas.height) || 1080;

      editor.windows.forEach(function (w) {
        var box = el('div', 'mv-box');
        box.setAttribute('data-cell', w.cell);
        box.setAttribute('role', 'group');
        var at = corner(w);
        box.style.left = (100 * at.x / width) + '%';
        box.style.top = (100 * at.y / height) + '%';
        box.style.width = (100 * (Number(w.width) || 0) / width) + '%';
        box.style.height = (100 * (Number(w.height) || 0) / height) + '%';
        var number = el('span', 'mv-box-number', w.label);
        var name = el('span', 'mv-box-source');
        var clear = el('button', 'mv-box-clear', '×');
        clear.type = 'button';
        clear.setAttribute('aria-label', 'Clear ' + w.label);
        clear.addEventListener('click', function (event) {
          if (event && event.stopPropagation) event.stopPropagation();
          clearWindow(w.cell);
        });
        box.appendChild(number);
        box.appendChild(name);
        box.appendChild(clear);
        box.addEventListener('click', function () {
          if (dom.picked) { assignWindow(w.cell, dom.picked); }
        });
        box.addEventListener('dragover', function (event) {
          if (dragging && !busy() && event.preventDefault) event.preventDefault();
        });
        box.addEventListener('drop', function (event) {
          if (event.preventDefault) event.preventDefault();
          var id = dragging;
          dragging = '';
          if (id) assignWindow(w.cell, id);
        });
        dom.stage.appendChild(box);
        dom.boxes[w.cell] = {box: box, name: name, clear: clear};

        var row = el('li', 'mv-row');
        row.setAttribute('data-cell', w.cell);
        var label = el('span', 'mv-row-label', w.label + ' (' + w.width + '×' + w.height + ')');
        var now = el('span', 'mv-row-now');
        var select = el('select', 'mv-row-select');
        select.setAttribute('aria-label', 'Source for ' + w.label);
        select.addEventListener('change', function () { assignWindow(w.cell, select.value); });
        var rowClear = el('button', 'mv-row-clear btn-quiet', 'Clear');
        rowClear.type = 'button';
        rowClear.setAttribute('aria-label', 'Clear ' + w.label);
        rowClear.addEventListener('click', function () { clearWindow(w.cell); });
        row.appendChild(label);
        row.appendChild(now);
        row.appendChild(select);
        row.appendChild(rowClear);
        dom.list.appendChild(row);
        dom.rows[w.cell] = {row: row, now: now, select: select, clear: rowClear};
      });

      editor.sources.forEach(function (s) {
        var chip = el('button', 'mv-source', s.name);
        chip.type = 'button';
        chip.setAttribute('data-source', s.id);
        chip.disabled = !s.selectable;
        chip.setAttribute('aria-pressed', 'false');
        if (s.selectable) {
          chip.draggable = true;
          chip.addEventListener('dragstart', function (event) {
            if (busy()) { if (event.preventDefault) event.preventDefault(); return; }
            dragging = s.id;
            // Nothing identifying travels in the payload; the identity stays here.
            if (event.dataTransfer) {
              event.dataTransfer.effectAllowed = 'copy';
              event.dataTransfer.setData('text/plain', 'source');
            }
          });
          chip.addEventListener('dragend', function () { dragging = ''; });
          chip.addEventListener('click', function () {
            dom.picked = dom.picked === s.id ? '' : s.id;
            paintEditor();
          });
        } else {
          chip.setAttribute('aria-describedby', 'mv_src_why_' + s.id.replace(/[^0-9a-f]/g, ''));
        }
        var cell = el('div', 'mv-source-item');
        cell.appendChild(chip);
        if (!s.selectable) {
          var why = el('div', 'mv-source-why', s.reason || 'Not ready for Multiview.');
          why.id = 'mv_src_why_' + s.id.replace(/[^0-9a-f]/g, '');
          cell.appendChild(why);
        }
        dom.palette.appendChild(cell);
        dom.chips[s.id] = chip;
      });
      if (!editor.sources.length) {
        dom.palette.appendChild(el('p', 'mv-none',
          'No sources have been given to your account for Multiview.'));
      }
      if (dom.picked && !dom.chips[dom.picked]) dom.picked = '';
    }

    function describe(cell) {
      // The window as it will be, in words.
      var value = effective(cell);
      if (value === '') return 'Empty';
      if (value === RESTRICTED) return 'Another source';
      return sourceName(value);
    }

    function nowText(w) {
      if (!w.source) return w.origin === 'unknown' ? 'Unknown source' : 'Empty';
      return w.source.name;
    }

    function paintEditor() {
      if (!editor || !editor.dom) return;
      var dom = editor.dom;
      var layout = editor.layout || {};
      dom.title.textContent = layout.name || 'Layout';
      dom.meta.textContent = (editor.display && editor.display.name ? editor.display.name : '')
        + (layout.layout_label ? ' · ' + layout.layout_label : '');
      dom.live.hidden = !layout.showing;
      dom.root.setAttribute('aria-busy', editor.applying || editor.phase === 'loading' ? 'true' : 'false');
      var locked = busy() || editor.confirming;

      editor.windows.forEach(function (w) {
        var changed = hasPending(w.cell);
        var parts = dom.boxes[w.cell];
        var row = dom.rows[w.cell];
        if (!parts || !row) return;
        var value = effective(w.cell);
        parts.name.textContent = describe(w.cell);
        parts.box.classList.toggle('changed', changed);
        parts.box.classList.toggle('empty', value === '');
        parts.box.setAttribute('aria-label', w.label + ', ' + w.width + ' by ' + w.height
          + ', ' + describe(w.cell) + (changed ? ', changed, not shown yet' : ''));
        parts.clear.disabled = locked || value === '';
        row.now.textContent = (layout.showing ? 'Now: ' : 'Saved: ') + nowText(w)
          + (layout.showing && w.source && w.health && w.health !== 'live' ? ' (' + w.health + ')' : '');
        row.row.classList.toggle('changed', changed);
        // Options: empty, every selectable source, and the current choice even if it
        // is not selectable (so the control never shows something it is not).
        var select = row.select;
        var options = [{value: '', text: 'Empty'}];
        // A window holding a source this account was not given must not read as empty in its own
        // control. The entry is there to say what is true; it is not a choice (assigning it is
        // refused because it is not one of the sources on offer).
        if (value === RESTRICTED) options.push({value: RESTRICTED, text: 'Another source'});
        editor.sources.forEach(function (s) {
          if (s.selectable) options.push({value: s.id, text: s.name});
        });
        var key = JSON.stringify(options);
        if (select.getAttribute('data-options') !== key) {
          select.replaceChildren();
          options.forEach(function (o) {
            var node = el('option', '', o.text);
            node.value = o.value;
            select.appendChild(node);
          });
          select.setAttribute('data-options', key);
        }
        select.value = value;
        select.disabled = locked;
        row.clear.disabled = locked || value === '';
      });

      Object.keys(dom.chips).forEach(function (id) {
        var chip = dom.chips[id];
        var picked = dom.picked === id;
        chip.setAttribute('aria-pressed', picked ? 'true' : 'false');
        chip.classList.toggle('picked', picked);
        var source = null;
        editor.sources.forEach(function (s) { if (s.id === id) source = s; });
        chip.disabled = locked || !source || !source.selectable;
      });

      if (editor.hidden) {
        dom.hidden.hidden = false;
        dom.hidden.textContent = editor.hidden + (editor.hidden === 1 ? ' source' : ' sources')
          + ' you were given cannot be used in a Multiview and '
          + (editor.hidden === 1 ? 'is' : 'are') + ' not listed.';
      } else {
        dom.hidden.hidden = true;
      }

      var count = pendingCount();
      var lines = [];
      editor.windows.forEach(function (w) {
        if (!hasPending(w.cell)) return;
        lines.push(w.label + ': ' + nowText(w) + ' → ' + describe(w.cell));
      });
      dom.pending.textContent = count
        ? count + (count === 1 ? ' change' : ' changes') + ' not shown yet. ' + lines.join('; ') + '.'
        : (editor.phase === 'loading' ? 'Reading the display…' : 'No changes.');

      var result = editor.result;
      dom.result.textContent = result ? result.text : '';
      dom.result.className = 'mv-result mv-editor-result' + (result && result.kind === 'error' ? ' error' : '');
      dom.result.hidden = !result;

      dom.discard.disabled = locked || !count;
      dom.reread.disabled = editor.applying || editor.phase === 'loading' || editor.confirming;
      var canShow = !!layout.available && layout.editable !== false && editor.phase === 'ready';
      dom.show.disabled = locked || !canShow;
      dom.show.textContent = editor.applying ? 'Working…'
        : (count ? 'Show with my changes' : 'Show');
      if (!layout.available && layout.reason && !result) {
        dom.result.textContent = layout.reason;
        dom.result.hidden = false;
      }
    }

    return {
      refresh: refresh,
      render: render,
      activate: activate,
      pendingCount: function () {
        return Object.keys(inflight).length + (editor && editor.applying ? 1 : 0);
      },
      // The editor, driven by the same functions the controls call.
      openLayout: openLayout,
      closeEditor: closeEditor,
      assign: function (cell, id) { return assignWindow(cell, id); },
      clear: function (cell) { return clearWindow(cell); },
      discard: discardChoices,
      applyChoices: applyChoices,
      refreshEditor: refreshEditor,
      editorState: function () {
        if (!editor) return null;
        return {layout: editor.layoutId, phase: editor.phase, applying: editor.applying,
                pending: Object.assign({}, editor.pending), revision: editor.revision,
                windows: editor.windows.map(function (w) { return w.cell; })};
      },
    };
  }

  root.OmniUserMultiview = {create: create, EMPTY_TEXT: EMPTY_TEXT, NOTE_TEXT: NOTE_TEXT};
})(typeof window !== 'undefined' ? window : this);

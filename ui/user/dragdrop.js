/*
 * ui/user/dragdrop.js: dragging a source onto a display or a group.
 *
 * Drag and drop is not a second way to route; it is a second gesture for the
 * same operation. A drop chooses the source and the destination exactly as two
 * taps would and then hands over to the page's own Route function, so it ends
 * in the very request the Route button sends -- and the server revalidates
 * session, permission revision, source, destination, online state and
 * authorisation on that request, as it does for the button.
 *
 * So this module holds no authority. It decides only whether a gesture is
 * worth attempting, from what the page has just been told it may show, so that
 * a card that cannot be used does not look as though it can:
 *  - a source is draggable only while it is online;
 *  - a display or group accepts a drop only while it is on offer, online and
 *    not already being routed to, and only while the Standard page is the one
 *    on screen and nothing else is in flight;
 *  - the dragged identity lives here in memory. It is never put in the drag
 *    payload, so dropping onto some other page or program carries nothing
 *    anybody could read.
 * Touch has no drag, and nothing here is needed for it: choose, choose, Route.
 */
(function (root) {
  'use strict';

  function create(deps) {
    var dragging = null;
    var marked = null;
    var lifted = null;

    function cardOf(event, kinds) {
      var node = event && event.target && event.target.closest
        ? event.target.closest('.pick') : null;
      return node && kinds.indexOf(node.dataset.kind) !== -1 ? node : null;
    }

    function unmark() {
      if (marked) { marked.classList.remove('drop-ok'); marked = null; }
    }

    function clear() {
      unmark();
      if (lifted) { lifted.classList.remove('dragging'); lifted = null; }
      dragging = null;
    }

    function allowedTarget(node) {
      return !!node && !node.disabled && deps.enabled()
        && deps.canDrop(node.dataset.kind, node.dataset.id);
    }

    return {
      dragstart: function (event) {
        var card = cardOf(event, ['source']);
        if (!card || card.disabled || !deps.enabled() || !deps.canDrag(card.dataset.id)) {
          if (card && event.preventDefault) event.preventDefault();
          return;
        }
        dragging = card.dataset.id;
        lifted = card;
        card.classList.add('dragging');
        if (event.dataTransfer) {
          // Some browsers will not start a drag without a payload. It says
          // nothing about what is being dragged.
          event.dataTransfer.setData('text/plain', 'omni-source');
          event.dataTransfer.effectAllowed = 'copy';
        }
      },

      dragover: function (event) {
        if (dragging === null) return;
        var card = cardOf(event, ['destination', 'group']);
        if (!allowedTarget(card)) { unmark(); return; }
        // Cancelling the default is what tells the browser this is a target.
        event.preventDefault();
        if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
        if (marked !== card) { unmark(); marked = card; card.classList.add('drop-ok'); }
      },

      dragleave: function (event) {
        if (marked && cardOf(event, ['destination', 'group']) === marked) unmark();
      },

      drop: function (event) {
        if (dragging === null) return Promise.resolve(false);
        var source = dragging;
        var card = cardOf(event, ['destination', 'group']);
        var ok = allowedTarget(card) && deps.canDrag(source);
        var kind = ok ? card.dataset.kind : null;
        var id = ok ? card.dataset.id : null;
        if (event.preventDefault) event.preventDefault();
        clear();
        if (!ok) return Promise.resolve(false);
        return Promise.resolve(deps.perform(source, kind, id)).then(function () { return true; });
      },

      dragend: function () { clear(); },

      // For the page and for tests: what is being dragged right now.
      current: function () { return dragging; },
    };
  }

  root.OmniUserDrag = {create: create};
})(typeof window !== 'undefined' ? window : this);

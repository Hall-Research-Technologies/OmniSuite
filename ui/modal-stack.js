/*
 * Which modal is on top.
 *
 * Every dialog in this application is a `.modal-backdrop`, and every one of
 * them carried the same z-index. When two are open at once the winner is
 * therefore decided by document order -- which is not a decision anybody
 * made. Settings is injected into the end of <body> at runtime; the shared
 * confirmation dialog is static markup further up. So Settings always painted
 * over it, and pressing Export Configuration appeared to do nothing until
 * Settings was closed and the dialog was found waiting underneath.
 *
 * Raising one dialog above another with a bigger constant fixes that pair and
 * leaves the next pair to be discovered the same way. What was missing is the
 * idea of a stack at all, so that is what this is: a dialog asks to be raised
 * when it opens and released when it closes, and the most recently opened one
 * is on top because it opened most recently.
 *
 * Deliberately small. It assigns an inline z-index and takes it away again;
 * it does not show, hide, focus or own any dialog, because those belong to
 * whichever dialog it is.
 */
(function () {
  'use strict';
  if (window.omniModalStack) return;

  // Above `.modal-backdrop` (20000) and the notice layer (20010), below the
  // loading overlay (30000), which is deliberately in front of everything
  // because it means the page is not ready to be used.
  var BASE = 20100;
  var STEP = 10;

  // Open dialogs, oldest first. An array rather than a counter: a dialog can
  // close out of order -- Escape on the one underneath, a caller that tidies
  // up after itself -- and a counter would then hand the next dialog a level
  // that is already taken.
  var open = [];

  function apply() {
    open.forEach(function (entry, index) {
      try {
        entry.element.style.zIndex = String(BASE + index * STEP);
      } catch (err) { /* detached: released on its next close */ }
    });
  }

  window.omniModalStack = {
    /** Put this element above everything already open. */
    raise: function (element) {
      if (!element) return 0;
      this.release(element);          // re-opening is not a second level
      open.push({element: element});
      apply();
      return BASE + (open.length - 1) * STEP;
    },

    /** Take it out of the stack and stop claiming a level for it. */
    release: function (element) {
      if (!element) return;
      var kept = [];
      open.forEach(function (entry) {
        if (entry.element === element) {
          try {
            // Back to whatever the stylesheet says, so a dialog that is
            // never stacked again is not left carrying a level.
            element.style.zIndex = '';
          } catch (err) { /* detached */ }
        } else {
          kept.push(entry);
        }
      });
      open = kept;
      apply();
    },

    /** The element currently on top, or null. For tests and for focus. */
    top: function () {
      return open.length ? open[open.length - 1].element : null;
    },

    /** How many are open. */
    depth: function () { return open.length; },
  };
})();

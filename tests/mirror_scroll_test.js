/*
 * ui/mirror-scroll.js: the shared mirrored horizontal scrollbar.
 *
 * Two classes of defect are pinned here.
 *
 * 1. Behaviour. The mirror must appear exactly when its container overflows,
 *    carry the container's real scrollWidth, drive and be driven by the
 *    container without the two ringing against each other, and clamp with the
 *    container when the content narrows. Two sections on one page must be
 *    completely independent: on Configure, scrolling Encoders must not move
 *    Decoders or USB.
 *
 * 2. Observer lifetime. This is the one that took a page down. sticky-columns.js
 *    once re-armed its observers from inside its own MutationObserver callback
 *    without disconnecting, so the observer count doubled per render -- measured
 *    at 2, 4, 8, 16, 32, 64, 128, 256 callbacks per batch of ten -- and since
 *    every callback forces a synchronous layout, one render's cost grew
 *    exponentially with how long the page had been open. Configure rebuilds its
 *    tables every five seconds, so Chrome eventually showed "Page Unresponsive".
 *    mirror-scroll.js observes only elements that outlive every render, so its
 *    counts must be flat. This test asserts that they are.
 *
 * It runs the real module against a DOM stub that implements just enough of the
 * platform -- scroll events, clamping, layout geometry, MutationObserver and
 * ResizeObserver -- to make those questions answerable without a browser.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

let failures = 0;
function check(name, fn) {
  try {
    fn();
    console.log(`  ok   ${name}`);
  } catch (err) {
    failures += 1;
    console.log(`  FAIL ${name}: ${err.message}`);
  }
}
function assert(cond, message) {
  if (!cond) throw new Error(message);
}
function equal(actual, expected, what) {
  if (actual !== expected) throw new Error(`${what}: expected ${expected}, got ${actual}`);
}

// --- the smallest DOM ui/mirror-scroll.js can run against ------------------

// Where the containers' shared parent's content box starts, in "viewport"
// coordinates. A strip with margin-left 0 begins here, which is what lets the
// module recover its natural position from one measurement.
const PARENT_LEFT = 40;

function makeDom() {
  const live = {mutation: 0, resize: 0, mutationDeliveries: 0, resizeDeliveries: 0};
  const frames = [];
  const counters = Object.create(null);

  function style() {
    const props = Object.create(null);
    return {
      width: '', marginLeft: '',
      _props: props,
      setProperty(name, value) { props[name] = value; },
      getPropertyValue(name) { return props[name] || ''; },
    };
  }

  function element(tag) {
    const el = {
      tagName: String(tag).toUpperCase(),
      className: '',
      id: '',
      dataset: {},
      hidden: false,
      children: [],
      parentNode: null,
      style: style(),
      _observers: [],
      _listeners: Object.create(null),
      // layout, set by the test
      left: PARENT_LEFT,
      clientLeft: 0,
      clientWidth: 0,
      contentWidth: 0,          // what the content wants; scrollWidth derives from it
      stripHeight: 12,
      _scrollLeft: 0,
      addEventListener(type, fn) {
        (el._listeners[type] = el._listeners[type] || []).push(fn);
      },
      dispatch(type) {
        (el._listeners[type] || []).slice().forEach(fn => fn({type}));
      },
      appendChild(child) { child.parentNode = el; el.children.push(child); return child; },
      insertBefore(child, ref) {
        const at = el.children.indexOf(ref);
        const was = el.children.indexOf(child);
        if (was >= 0) el.children.splice(was, 1);
        child.parentNode = el;
        el.children.splice(at < 0 ? el.children.length : el.children.indexOf(ref), 0, child);
        return child;
      },
      querySelectorAll() { return []; },
      querySelector() { return null; },
    };
    Object.defineProperty(el, 'firstElementChild', {
      get() { return el.children[0] || null; },
    });
    Object.defineProperty(el, 'nextElementSibling', {
      get() {
        if (!el.parentNode) return null;
        const at = el.parentNode.children.indexOf(el);
        return at < 0 ? null : (el.parentNode.children[at + 1] || null);
      },
    });
    Object.defineProperty(el, 'scrollWidth', {
      get() { return Math.max(el.clientWidth, el.contentWidth); },
    });
    Object.defineProperty(el, 'offsetHeight', {
      get() { return el.hidden ? 0 : el.stripHeight; },
    });
    // The platform clamps scrollLeft to the scroll range and fires one scroll
    // event when the value actually changes. Both of those are the point -- and
    // the clamp applies on read as well, because a scroller whose content
    // narrows under it is re-clamped by the browser with no assignment at all.
    Object.defineProperty(el, 'scrollLeft', {
      get() {
        const max = Math.max(0, el.scrollWidth - el.clientWidth);
        if (el._scrollLeft > max) el._scrollLeft = max;
        return el._scrollLeft;
      },
      set(value) {
        const max = Math.max(0, el.scrollWidth - el.clientWidth);
        const next = Math.min(Math.max(0, value), max);
        if (next === el._scrollLeft) return;
        el._scrollLeft = next;
        el.dispatch('scroll');
      },
    });
    el.getBoundingClientRect = () => ({
      left: el.left, top: 0, width: el.clientWidth, height: el.offsetHeight,
    });
    return el;
  }

  // A strip the module creates is a plain block in the container's parent, so
  // its left edge is the parent's content edge plus whatever margin is applied.
  function asStrip(el) {
    el.getBoundingClientRect = () => ({
      left: PARENT_LEFT + (parseFloat(el.style.marginLeft) || 0),
      top: 0,
      width: parseFloat(el.style.width) || 0,
      height: el.offsetHeight,
    });
    Object.defineProperty(el, 'clientWidth', {
      get() { return parseFloat(el.style.width) || 0; },
      configurable: true,
    });
    Object.defineProperty(el, 'contentWidth', {
      get() { return el.children[0] ? (parseFloat(el.children[0].style.width) || 0) : 0; },
      configurable: true,
    });
    return el;
  }

  const documentElement = element('html');
  const body = element('body');
  const containers = [];
  const byId = Object.create(null);

  const doc = {
    readyState: 'complete',
    documentElement,
    body,
    createElement: (tag) => asStrip(element(tag)),
    getElementById: (id) => byId[id] || null,
    querySelectorAll: (sel) => (sel === '[data-mirror-scroll]' ? containers.slice() : []),
    addEventListener() {},
  };

  class MutationObserverStub {
    constructor(cb) { this._cb = cb; live.mutation += 1; this._targets = []; }
    observe(target) { this._targets.push(target); target._observers.push(this); }
    disconnect() {
      live.mutation -= 1;
      for (const t of this._targets) {
        const i = t._observers.indexOf(this);
        if (i >= 0) t._observers.splice(i, 1);
      }
      this._targets = [];
    }
  }
  class ResizeObserverStub {
    constructor(cb) { this._cb = cb; live.resize += 1; this._targets = []; }
    observe(target) { this._targets.push(target); (target._resize = target._resize || []).push(this); }
    disconnect() { live.resize -= 1; this._targets = []; }
  }

  const win = {
    addEventListener() {},
    omniDiag: {
      enabled: true,
      count(name) { counters[name] = (counters[name] || 0) + 1; },
    },
  };

  // A "re-render": the childList mutation an innerHTML rewrite produces,
  // delivered to whatever is observing the container.
  function render(container) {
    container._observers.slice().forEach((obs) => {
      live.mutationDeliveries += 1;
      obs._cb([{type: 'childList'}], obs);
    });
  }
  function resized(container) {
    (container._resize || []).slice().forEach((obs) => {
      live.resizeDeliveries += 1;
      obs._cb([{target: container}], obs);
    });
  }

  function newContainer(id, opts) {
    const el = element('div');
    el.id = id;
    el.dataset.mirrorScroll = '';
    if (opts && opts.heightVar) el.dataset.mirrorHeightVar = opts.heightVar;
    if (opts && opts.stripId) el.dataset.mirrorStrip = opts.stripId;
    el.clientWidth = (opts && opts.clientWidth !== undefined) ? opts.clientWidth : 800;
    el.contentWidth = (opts && opts.contentWidth !== undefined) ? opts.contentWidth : 800;
    el.clientLeft = (opts && opts.clientLeft !== undefined) ? opts.clientLeft : 1;
    el.left = (opts && opts.left !== undefined) ? opts.left : PARENT_LEFT;
    body.appendChild(el);
    containers.push(el);
    byId[id] = el;
    return el;
  }

  function adoptStrip(id) {
    const el = asStrip(element('div'));
    el.id = id;
    el.hidden = true;
    byId[id] = el;
    return el;
  }

  function flush() {
    // Frames do not nest: a callback that asks for another frame is served by
    // the next flush, exactly as the browser would.
    const batch = frames.splice(0, frames.length);
    batch.forEach(fn => fn());
  }

  return {doc, win, body, live, counters, frames, flush, render, resized,
          newContainer, adoptStrip, element,
          MutationObserver: MutationObserverStub, ResizeObserver: ResizeObserverStub};
}

function loadModule(dom) {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'ui', 'mirror-scroll.js'), 'utf8');
  const sandbox = {
    document: dom.doc,
    window: dom.win,
    MutationObserver: dom.MutationObserver,
    ResizeObserver: dom.ResizeObserver,
    requestAnimationFrame: (fn) => dom.frames.push(fn) && dom.frames.length,
    console: {log() {}},
  };
  sandbox.globalThis = sandbox;
  dom.win.MutationObserver = dom.MutationObserver;
  dom.win.ResizeObserver = dom.ResizeObserver;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, {filename: 'mirror-scroll.js'});
  return sandbox;
}

function stripOf(dom, container) {
  const at = dom.body.children.indexOf(container);
  return at > 0 ? dom.body.children[at - 1] : null;
}

console.log('\nmirror-scroll.js');

// ---- placement -----------------------------------------------------------

check('the strip is inserted immediately before the container it drives', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  const strip = stripOf(dom, c);
  assert(strip, 'no strip was inserted');
  equal(strip.nextElementSibling, c, 'the strip is not the container\'s previous sibling');
});

check('an existing strip is adopted rather than duplicated', () => {
  const dom = makeDom();
  const existing = dom.adoptStrip('table_scroll_mirror');
  const c = dom.newContainer('units_table_wrapper',
                             {stripId: 'table_scroll_mirror', clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  equal(stripOf(dom, c), existing, 'Device Info\'s own strip must be reused, not replaced');
  equal(dom.body.children.filter(el => el.id === 'table_scroll_mirror').length, 1,
        'strip count');
});

// ---- overflow decides whether it exists at all ---------------------------

check('no overflow means no top scrollbar', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 800});
  loadModule(dom);
  dom.flush();
  equal(stripOf(dom, c).hidden, true, 'an inert scrollbar is worse than none');
});

check('overflow shows the top scrollbar, sized from the real scrollWidth', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  const strip = stripOf(dom, c);
  equal(strip.hidden, false, 'hidden while the container overflows');
  equal(strip.children[0].style.width, '1600px', 'track width');
  equal(strip.style.width, '800px', 'strip width is the container\'s client width');
  equal(strip.scrollWidth, c.scrollWidth, 'mirror scrollWidth');
  equal(strip.scrollWidth - strip.clientWidth, c.scrollWidth - c.clientWidth,
        'maximum scrollLeft');
});

check('the strip takes the container\'s left content edge, not the page\'s', () => {
  const dom = makeDom();
  // A container indented from its parent (`.matrix-wrap` is centred) and
  // carrying a border, which is what clientLeft measures.
  const c = dom.newContainer('a', {clientWidth: 600, contentWidth: 1200,
                                   left: PARENT_LEFT + 90, clientLeft: 1});
  loadModule(dom);
  dom.flush();
  const strip = stripOf(dom, c);
  equal(strip.style.marginLeft, '91px', 'left offset');
  equal(strip.getBoundingClientRect().left, c.getBoundingClientRect().left + c.clientLeft,
        'the two left edges must coincide');
});

// ---- synchronisation ------------------------------------------------------

check('top to bottom: dragging the mirror scrolls the container', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  const strip = stripOf(dom, c);
  strip.scrollLeft = 240;
  equal(c.scrollLeft, 240, 'container scrollLeft');
});

check('bottom to top: scrolling the container moves the mirror', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  const strip = stripOf(dom, c);
  c.scrollLeft = 555;
  equal(strip.scrollLeft, 555, 'mirror scrollLeft');
});

check('the reentrancy guard stops the two driving each other', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  const strip = stripOf(dom, c);
  let events = 0;
  c.addEventListener('scroll', () => { events += 1; });
  strip.addEventListener('scroll', () => { events += 1; });

  // The case that actually rings: the two ends momentarily disagree about their
  // maximum, so the follower clamps to a DIFFERENT value and its own scroll
  // event would drag the leader back. Here the track is a frame behind the
  // container -- exactly what a column that has just been shown produces.
  strip.children[0].style.width = '1400px';   // mirror max 600, container max 800
  c.scrollLeft = 800;                         // the operator drags the table right

  equal(c.scrollLeft, 800,
        'the mirror must never drag the container back to its own stale maximum');
  equal(strip.scrollLeft, 600, 'the mirror clamps to what it can reach');
  equal(events, 2, 'one event each; the exchange does not ring');

  dom.flush();                               // ownership released
  strip.children[0].style.width = '1600px';  // the next measure catches the track up
  strip.scrollLeft = 120;                    // and the other direction still works
  equal(c.scrollLeft, 120, 'container follows the mirror after ownership is released');
});

// ---- independence ---------------------------------------------------------

check('two sections on one page do not affect each other', () => {
  const dom = makeDom();
  const enc = dom.newContainer('encWrap', {clientWidth: 800, contentWidth: 2000});
  const dec = dom.newContainer('decWrap', {clientWidth: 800, contentWidth: 2400});
  const usb = dom.newContainer('usbWrap', {clientWidth: 800, contentWidth: 1800});
  loadModule(dom);
  dom.flush();
  const encStrip = stripOf(dom, enc);
  encStrip.scrollLeft = 400;
  equal(enc.scrollLeft, 400, 'the section that was scrolled');
  equal(dec.scrollLeft, 0, 'Decoders must not move');
  equal(usb.scrollLeft, 0, 'USB must not move');
  equal(stripOf(dom, dec).scrollLeft, 0, 'the Decoders mirror must not move');
  equal(stripOf(dom, usb).scrollLeft, 0, 'the USB mirror must not move');
  // ... and each keeps its own track width.
  equal(stripOf(dom, dec).children[0].style.width, '2400px', 'Decoders track');
  equal(stripOf(dom, usb).children[0].style.width, '1800px', 'USB track');
});

check('one section\'s in-flight sync does not block another\'s', () => {
  const dom = makeDom();
  const a = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  const b = dom.newContainer('b', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  // A drags, and before the frame that releases ownership, B is scrolled. A
  // guard shared between sections would swallow B's move entirely.
  stripOf(dom, a).scrollLeft = 200;
  b.scrollLeft = 350;
  equal(stripOf(dom, b).scrollLeft, 350, 'B\'s mirror followed while A still owned its own sync');
});

// ---- resize and re-measure ------------------------------------------------

check('resizing into and out of overflow shows and hides the strip', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 1600, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  const strip = stripOf(dom, c);
  equal(strip.hidden, true, 'wide viewport: nothing to scroll');

  c.clientWidth = 1000;                       // the window narrows
  dom.resized(c);
  dom.flush();
  equal(strip.hidden, false, 'narrow viewport: the strip appears');
  equal(strip.style.width, '1000px', 'strip width follows the container');

  c.clientWidth = 1600;                       // and widens again
  dom.resized(c);
  dom.flush();
  equal(strip.hidden, true, 'the strip goes away again');
});

check('a content width change updates the track width', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  const strip = stripOf(dom, c);
  equal(strip.children[0].style.width, '1600px', 'before');
  c.contentWidth = 2600;                      // a column group was shown
  dom.render(c);
  dom.flush();
  equal(strip.children[0].style.width, '2600px', 'after');
  equal(strip.scrollWidth, c.scrollWidth, 'the two ranges still agree');
});

check('scrollLeft clamps when the content narrows', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 2400});
  loadModule(dom);
  dom.flush();
  const strip = stripOf(dom, c);
  strip.scrollLeft = 1600;                    // scrolled to the far right
  equal(c.scrollLeft, 1600, 'scrolled');
  dom.flush();

  c.contentWidth = 1000;                      // columns hidden
  dom.render(c);
  dom.flush();
  equal(c.scrollLeft, 200, 'the container clamps to its new maximum');
  equal(strip.scrollLeft, 200, 'and the mirror is re-asserted to match it');
  equal(strip.scrollWidth - strip.clientWidth, c.scrollWidth - c.clientWidth,
        'the two maxima still agree');
});

check('hiding every extra column clamps to 0 and hides the strip', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 2400});
  loadModule(dom);
  dom.flush();
  const strip = stripOf(dom, c);
  strip.scrollLeft = 1600;
  dom.flush();
  c.contentWidth = 800;
  dom.render(c);
  dom.flush();
  equal(c.scrollLeft, 0, 'container scrollLeft');
  equal(strip.hidden, true, 'nothing left to scroll');
});

// ---- the height a stylesheet has to reserve ------------------------------

check('the strip height is published only for a container that asks', () => {
  const dom = makeDom();
  const c = dom.newContainer('units_table_wrapper',
                             {heightVar: '--table-mirror-h', clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  equal(dom.doc.documentElement.style.getPropertyValue('--table-mirror-h'), '12px',
        'published while the strip is shown');
  c.contentWidth = 800;
  dom.render(c);
  dom.flush();
  equal(dom.doc.documentElement.style.getPropertyValue('--table-mirror-h'), '0px',
        'hidden, it measures zero, so the viewport reclaims the strip');
});

// ---- observer lifetime: the invariant this file exists for ---------------

check('a container is observed by exactly one observer of each kind', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  equal(c._observers.length, 1, 'MutationObservers on the container');
  equal((c._resize || []).length, 1, 'ResizeObservers on the container');
});

check('the observer count does NOT grow across repeated re-renders', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  const mutation = dom.live.mutation;
  const resize = dom.live.resize;
  for (let i = 0; i < 50; i += 1) {
    dom.render(c);
    dom.resized(c);
    dom.flush();
  }
  equal(dom.live.mutation, mutation, 'MutationObserver count after 50 renders');
  equal(dom.live.resize, resize, 'ResizeObserver count after 50 renders');
  equal(c._observers.length, 1, 'observers attached to the container');
});

check('one render costs one observer delivery, however many have happened', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  const perRender = [];
  for (let batch = 0; batch < 8; batch += 1) {
    const before = dom.live.mutationDeliveries;
    dom.render(c);
    dom.flush();
    perRender.push(dom.live.mutationDeliveries - before);
  }
  equal(Math.max(...perRender), 1,
        `a single render delivered up to ${Math.max(...perRender)} callbacks (${perRender.join(',')})`);
});

check('the measure rate is flat, not accelerating', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  const perBatch = [];
  for (let batch = 0; batch < 8; batch += 1) {
    const before = dom.counters['mirror.measure'] || 0;
    for (let i = 0; i < 10; i += 1) { dom.render(c); dom.flush(); }
    perBatch.push((dom.counters['mirror.measure'] || 0) - before);
  }
  // Ten renders, ten measurements, every batch. The defect this pins made the
  // same sequence read 20, 40, 80, 160 ...
  assert(perBatch.every(n => n === perBatch[0]),
         `measures per batch of ten renders: ${perBatch.join(',')}`);
  equal(perBatch[0], 10, 'measures per batch of ten renders');
});

check('a batch of renders in one frame measures once', () => {
  const dom = makeDom();
  const c = dom.newContainer('a', {clientWidth: 800, contentWidth: 1600});
  loadModule(dom);
  dom.flush();
  const before = dom.counters['mirror.measure'] || 0;
  for (let i = 0; i < 20; i += 1) dom.render(c);   // no frame in between
  dom.flush();
  equal((dom.counters['mirror.measure'] || 0) - before, 1,
        'twenty mutations inside one frame must cost one measurement');
});

check('no observer is created from inside an observer callback', () => {
  const source = fs.readFileSync(
    path.join(__dirname, '..', 'ui', 'mirror-scroll.js'), 'utf8');
  // Every observer in the module is constructed at module scope or inside
  // observeOnce(), which is called once per container from register().
  const constructions = source.match(/new (Mutation|Resize)Observer\(/g) || [];
  assert(constructions.length === 3,
         `expected 3 observer constructions, found ${constructions.length}`);
  for (const match of source.matchAll(/new (Mutation|Resize)Observer\(([\s\S]{0,160})/g)) {
    assert(!/new (Mutation|Resize)Observer\(/.test(match[2]),
           'an observer callback constructs another observer');
  }
  assert(!/setInterval|setTimeout/.test(source),
         'the module must be driven by observers, never by a timer');
});

if (failures) {
  console.log(`\nmirror-scroll test: ${failures} check(s) failed\n`);
  process.exit(1);
}
console.log('\nmirror-scroll test: all checks passed\n');

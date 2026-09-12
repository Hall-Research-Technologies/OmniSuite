/*
 * sticky-columns.js must not accumulate observers across re-renders.
 *
 * The defect this exists for: watch() re-armed itself from inside its own
 * MutationObserver callback, clearing the guard flag and calling watch() again
 * without disconnecting anything. Every table re-render therefore left the
 * previous observers attached, and because each surviving callback re-armed
 * once more the count doubled per render. Measured in a browser before the fix,
 * per batch of ten re-renders: 2, 4, 8, 16, 32, 64, 128, 256 callbacks. Each
 * callback calls apply(), which reads getBoundingClientRect() and forces a
 * synchronous layout, so the cost of a single render grew exponentially with
 * how long the page had been open. Configure rebuilds its tables every five
 * seconds.
 *
 * This runs sticky-columns.js against a DOM stub that implements just enough
 * MutationObserver and ResizeObserver semantics to count instances and
 * deliveries, and asserts the counts stay flat as renders accumulate.
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

// --- the smallest DOM that sticky-columns.js can run against ---------------
function makeDom() {
  const live = {mutation: 0, resize: 0, mutationDeliveries: 0, resizeDisconnects: 0};

  function element(tag, className) {
    const el = {
      tagName: tag.toUpperCase(),
      className: className || '',
      dataset: {},
      style: {
        _props: {},
        setProperty(name, value) { this._props[name] = value; },
        getPropertyValue(name) { return this._props[name] || ''; },
      },
      children: [],
      _observers: [],
      getBoundingClientRect() { return {width: 100, height: 20, left: 0, top: 0}; },
      querySelector() { return null; },
      querySelectorAll() { return []; },
      appendChild(child) { this.children.push(child); return child; },
    };
    return el;
  }

  // A table with a header row of three cells, as Device Info has.
  const table = element('table', 'device-table');
  table.dataset.stickyCols = '3';
  const headRow = element('tr');
  headRow.children = [element('th'), element('th'), element('th')];
  const thead = element('thead');
  thead.children = [headRow];
  table.children = [thead];
  table.querySelector = (sel) => {
    if (sel.includes('thead tr')) return headRow;
    if (sel.includes('tr')) return headRow;
    if (sel.includes('first-child')) return headRow.children[0];
    return null;
  };

  const doc = {
    readyState: 'complete',
    documentElement: element('html'),
    querySelectorAll: (sel) => (sel.includes('table') ? [table] : []),
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
    constructor(cb) { this._cb = cb; live.resize += 1; }
    observe() {}
    disconnect() { live.resize -= 1; live.resizeDisconnects += 1; }
  }

  // A "re-render": deliver a childList mutation to every observer attached to
  // the table, exactly as replacing its innerHTML would.
  function render() {
    const observers = table._observers.slice();
    for (const obs of observers) {
      live.mutationDeliveries += 1;
      obs._cb([{type: 'childList'}], obs);
    }
  }

  return {doc, table, live, render,
          MutationObserver: MutationObserverStub, ResizeObserver: ResizeObserverStub};
}

function loadModule(dom) {
  const source = fs.readFileSync(path.join(__dirname, '..', 'ui', 'sticky-columns.js'), 'utf8');
  const sandbox = {
    document: dom.doc,
    window: {addEventListener() {}},
    MutationObserver: dom.MutationObserver,
    ResizeObserver: dom.ResizeObserver,
    WeakMap,
    parseInt,
    Number,
    Math,
    Array,
    console: {log() {}},
  };
  sandbox.window.MutationObserver = dom.MutationObserver;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, {filename: 'sticky-columns.js'});
  return sandbox;
}

console.log('\nsticky-columns.js observer lifetime');

check('a table is watched by exactly one MutationObserver', () => {
  const dom = makeDom();
  loadModule(dom);
  // Counted on the table itself. The module also keeps one document-level
  // observer for layout/theme changes, which is created once and is not part
  // of this question.
  if (dom.table._observers.length !== 1) {
    throw new Error(`expected 1 observer on the table, got ${dom.table._observers.length}`);
  }
});

check('re-rendering does not add another MutationObserver', () => {
  const dom = makeDom();
  loadModule(dom);
  const before = dom.live.mutation;
  for (let i = 0; i < 25; i += 1) dom.render();
  if (dom.live.mutation !== before) {
    throw new Error(`observer count grew from ${before} to ${dom.live.mutation} over 25 renders`);
  }
});

check('one render costs one observer delivery, however many have happened', () => {
  const dom = makeDom();
  loadModule(dom);
  const perRender = [];
  for (let batch = 0; batch < 6; batch += 1) {
    const before = dom.live.mutationDeliveries;
    dom.render();
    perRender.push(dom.live.mutationDeliveries - before);
  }
  const worst = Math.max(...perRender);
  if (worst !== 1) {
    throw new Error(`a single render delivered up to ${worst} callbacks (${perRender.join(',')})`);
  }
});

check('the cell observer is replaced, not accumulated', () => {
  const dom = makeDom();
  loadModule(dom);
  const before = dom.live.resize;
  for (let i = 0; i < 25; i += 1) dom.render();
  if (dom.live.resize > before) {
    throw new Error(`resize observers grew from ${before} to ${dom.live.resize}`);
  }
  if (dom.live.resizeDisconnects === 0) {
    throw new Error('the previous cell observer was never disconnected, so detached cells leak');
  }
});

check('watch() is not re-armed from inside its own callback', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'ui', 'sticky-columns.js'), 'utf8');
  const start = source.indexOf('new MutationObserver(');
  const body = source.slice(start, source.indexOf('.observe(', start));
  if (/\bwatch\s*\(/.test(body)) {
    throw new Error('the MutationObserver callback calls watch(), which re-arms without disconnecting');
  }
});

if (failures) {
  console.log(`\nsticky-columns observer test: ${failures} check(s) failed\n`);
  process.exit(1);
}
console.log('\nsticky-columns observer test: all checks passed\n');

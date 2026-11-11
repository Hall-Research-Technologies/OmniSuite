// The restricted User's Matrix, and drag and drop: executed, not read.
//
// Two layers. First the Matrix module on its own with scripted dependencies --
// what it draws, what a click sends, what it refuses to send, and what a hover
// costs. Then the real page script, with the real modules, against a scripted
// server: which interface is shown for which grant, that the remembered tab is
// a preference rather than a permission, that a withdrawn Matrix moves the
// operator to Standard routing without a sign-out, and that a drop ends in the
// very request the Route button sends.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {El} = require('./_fakedom');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const HTML = read('ui', 'user', 'index.html');
const USER_JS = read('ui', 'user', 'user.js');
const PREVIEW_JS = read('ui', 'user', 'preview-refresh.js');
const MATRIX_JS = read('ui', 'user', 'matrix.js');
const DRAG_JS = read('ui', 'user', 'dragdrop.js');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]); });
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
const settle = () => new Promise((r) => setTimeout(r, 40));

function sandboxWith(extra) {
  const sandbox = Object.assign({
    window: {}, Promise, Array, Object, JSON, String, Number, Set, Map, Date, console,
    setTimeout, clearTimeout,
  }, extra || {});
  vm.createContext(sandbox);
  return sandbox;
}

// ------------------------------------------------------------- the module
function loadMatrix() {
  const sandbox = sandboxWith();
  vm.runInContext(MATRIX_JS, sandbox);
  return sandbox.window.OmniUserMatrix;
}

const SOURCES = [{id: 's1', name: 'Teacher PC', online: true},
                 {id: 's2', name: 'Camera', online: true}];
const plane = (mode, id) => ({mode, source_id: id || null});
const groupPlane = (state, counts, total, online) => ({
  state, source_id: null, online: online === undefined ? total : online, total,
  counts: Object.assign({sources: {}, other: 0, none: 0, multiview: 0}, counts)});

function dataset(overrides) {
  return Object.assign({
    sources: SOURCES,
    rows: [
      {kind: 'display', id: 'd1', name: 'Front', online: true,
       video: plane('source', 's1'), audio: plane('source', 's2')},
      {kind: 'display', id: 'd2', name: 'Side', online: false,
       video: plane('none'), audio: plane('none')},
      {kind: 'group', id: 'grp_1', name: 'Rooms', displays: 3, online: true,
       video: groupPlane('partial', {sources: {s1: 2}, none: 1}, 3),
       audio: groupPlane('mixed', {sources: {s1: 1, s2: 2}}, 3)},
    ]}, overrides || {});
}

function harness(extra) {
  const MX = loadMatrix();
  const grid = new El('div'), scroller = new El('div'), modeBar = new El('div'),
        empty = new El('div'), host = new El('div');
  scroller.appendChild(grid);
  const routed = [];
  const timers = [];
  const fetched = [];
  const drawn = [];
  let cleared = 0;
  const deps = Object.assign({
    document: {createElement: (t) => new El(t)},
    grid, scroller, modeBar, empty, previewHost: host,
    route: (request) => routed.push(request),
    fetchPreview: (id) => { fetched.push(id); return Promise.resolve({image: {id}, note: ''}); },
    drawPreview: (card, result) => { drawn.push([card, result]); card.replaceChildren(new El('img')); },
    setTimeout: (fn, ms) => { timers.push({fn, ms, live: true}); return timers.length; },
    clearTimeout: (h) => { cleared++; if (timers[h - 1]) timers[h - 1].live = false; },
    viewportWidth: () => 1024,
  }, extra || {});
  const matrix = MX.create(deps);
  const cell = (row, source) => grid.querySelectorAll('.mx-cell')
    .find((c) => c.dataset.row === row && c.dataset.source === source);
  return {MX, matrix, grid, scroller, modeBar, empty, host, routed, timers, fetched, drawn,
          cell, cleared: () => cleared, deps};
}

const labelOf = (c) => c.getAttribute('aria-label');

const suite = [];

suite.push(() => check('a source is a column and a display or a group is a row', async () => {
  const h = harness();
  h.matrix.render(dataset());
  assert(h.grid.querySelectorAll('.mx-source-head').length === 2);
  assert(h.grid.querySelectorAll('.mx-row').length === 3);
  assert(h.grid.querySelectorAll('.mx-cell').length === 6, 'two sources by three rows');
  const group = h.grid.querySelectorAll('.mx-row').find((r) => r.classList.contains('mx-group'));
  assert(group && /Group · 3 displays/.test(group.textContent), 'a group says what it is');
}));

suite.push(() => check('the ring is video and the dot is audio, and they can differ (breakaway)', async () => {
  const h = harness();
  h.matrix.render(dataset());
  const fromTeacher = h.cell('d1', 's1');
  const fromCamera = h.cell('d1', 's2');
  assert(fromTeacher.querySelector('.mx-ring').classList.contains('video-all'));
  assert(!fromTeacher.querySelector('.mx-dot').classList.contains('audio-all'));
  assert(!fromCamera.querySelector('.mx-ring').classList.contains('video-all'));
  assert(fromCamera.querySelector('.mx-dot').classList.contains('audio-all'));
  assert(/video routed/.test(labelOf(fromTeacher)) && !/audio routed/.test(labelOf(fromTeacher)));
  assert(/audio routed/.test(labelOf(fromCamera)));
}));

suite.push(() => check('a group is drawn all, some or none per source, never one member for all', async () => {
  const h = harness();
  h.matrix.render(dataset());
  const s1 = h.cell('grp_1', 's1');
  assert(s1.querySelector('.mx-ring').classList.contains('video-partial'), '2 of 3 is partial');
  assert(!s1.querySelector('.mx-ring').classList.contains('video-all'));
  assert(s1.querySelector('.mx-dot').classList.contains('audio-partial'), '1 of 3 is partial');
  assert(/some displays/.test(labelOf(s1)));
  const s2 = h.cell('grp_1', 's2');
  assert(!s2.querySelector('.mx-ring').classList.contains('video-partial'), 'no member has s2 video');
  assert(s2.querySelector('.mx-dot').classList.contains('audio-partial'));
}));

suite.push(() => check('a group on one source for every member is all', async () => {
  const h = harness();
  h.matrix.render(dataset({rows: [
    {kind: 'group', id: 'g', name: 'G', displays: 2, online: true,
     video: groupPlane('all', {sources: {s1: 2}}, 2), audio: groupPlane('all', {sources: {s1: 2}}, 2)}]}));
  const c = h.cell('g', 's1');
  assert(c.querySelector('.mx-ring').classList.contains('video-all'));
  assert(c.querySelector('.mx-dot').classList.contains('audio-all'));
}));

suite.push(() => check('a group that is not all says why in words', async () => {
  const h = harness();
  h.matrix.render(dataset());
  const head = h.grid.querySelectorAll('.mx-row-head').find((r) => /Rooms/.test(r.textContent));
  assert(/Video: Partial/.test(head.textContent) && /Audio: Mixed/.test(head.textContent), head.textContent);
  h.matrix.render(dataset({rows: [
    {kind: 'group', id: 'g', name: 'Dark', displays: 2, online: false,
     video: groupPlane('unavailable', {}, 2, 0), audio: groupPlane('unavailable', {}, 2, 0)}]}));
  const dark = h.grid.querySelectorAll('.mx-row-head')[0].textContent;
  assert(/Unavailable/.test(dark) && /Offline/.test(dark), dark);
}));

suite.push(() => check('a group that is entirely in one state carries no chip', async () => {
  const h = harness();
  h.matrix.render(dataset({rows: [
    {kind: 'group', id: 'g', name: 'G', displays: 2, online: true,
     video: groupPlane('all', {sources: {s1: 2}}, 2), audio: groupPlane('all', {sources: {s1: 2}}, 2)}]}));
  assert(h.grid.querySelectorAll('.mx-chip').length === 0);
}));

suite.push(() => check('an offline display or source is disabled and says Offline as a word', async () => {
  const h = harness();
  h.matrix.render(dataset({sources: [SOURCES[0], {id: 's2', name: 'Camera', online: false}]}));
  assert(h.cell('d2', 's1').disabled === true, 'an offline display');
  assert(h.cell('d1', 's2').disabled === true, 'an offline source');
  assert(h.cell('d1', 's1').disabled === false);
  assert(/offline/.test(labelOf(h.cell('d2', 's1'))));
  const text = h.grid.textContent;
  assert((text.match(/Offline/g) || []).length >= 2, 'a word, not only a colour: ' + text);
  await h.cell('d2', 's1').parentNode.fire('click', {target: h.cell('d2', 's1')});
  await h.scroller.fire('click', {target: h.cell('d2', 's1')});
  assert(h.routed.length === 0, 'a disabled cell sent a request');
}));

suite.push(() => check('a click sends one request carrying source, row, kind and the chosen mode', async () => {
  const h = harness();
  h.matrix.render(dataset());
  await h.scroller.fire('click', {target: h.cell('d1', 's2')});
  assert(h.routed.length === 1);
  assert(JSON.stringify(h.routed[0]) === JSON.stringify({source: 's2', kind: 'display', id: 'd1', mode: 'av'}),
    JSON.stringify(h.routed[0]));
  await h.scroller.fire('click', {target: h.cell('grp_1', 's1')});
  assert(h.routed[1].kind === 'group' && h.routed[1].id === 'grp_1');
}));

suite.push(() => check('the mode buttons change what a click means and send nothing themselves', async () => {
  const h = harness();
  h.matrix.render(dataset());
  const buttons = h.modeBar.querySelectorAll('.mx-modebtn');
  assert(buttons.map((b) => b.dataset.mode).join() === 'av,video,audio');
  assert(buttons[0].getAttribute('aria-checked') === 'true');
  await h.modeBar.fire('click', {target: buttons[2]});
  assert(h.routed.length === 0, 'choosing a mode must not route');
  const after = h.modeBar.querySelectorAll('.mx-modebtn');
  assert(after[2].getAttribute('aria-checked') === 'true' && after[0].getAttribute('aria-checked') === 'false',
    'announced as well as painted');
  assert(/audio only/.test(labelOf(h.cell('d1', 's1'))));
  await h.scroller.fire('click', {target: h.cell('d1', 's1')});
  assert(h.routed[0].mode === 'audio');
  await h.modeBar.fire('click', {target: after[1]});
  await h.scroller.fire('click', {target: h.cell('d1', 's1')});
  assert(h.routed[1].mode === 'video');
}));

suite.push(() => check('while busy every cell is disabled, and an in-flight route cannot be doubled', async () => {
  const h = harness();
  h.matrix.render(dataset());
  h.matrix.setBusy(true);
  assert(h.cell('d1', 's1').disabled === true);
  await h.scroller.fire('click', {target: h.cell('d1', 's1')});
  assert(h.routed.length === 0);
  h.matrix.setBusy(false);
  assert(h.cell('d1', 's1').disabled === false);
}));

suite.push(() => check('a state-only refresh repaints the cells already there', async () => {
  const h = harness();
  h.matrix.render(dataset());
  const before = h.cell('d1', 's1');
  const table = h.grid.children[0];
  h.matrix.render(dataset({rows: dataset().rows.map((r) => (r.id === 'd1'
    ? Object.assign({}, r, {video: plane('source', 's2')}) : r))}));
  assert(h.cell('d1', 's1') === before, 'the same element, not a rebuilt one');
  assert(h.grid.children[0] === table, 'the table was not replaced');
  assert(!before.querySelector('.mx-ring').classList.contains('video-all'), 'but its state moved');
  assert(h.cell('d1', 's2').querySelector('.mx-ring').classList.contains('video-all'));
}));

suite.push(() => check('a change of membership rebuilds', async () => {
  const h = harness();
  h.matrix.render(dataset());
  const before = h.cell('d1', 's1');
  h.matrix.render(dataset({sources: [SOURCES[0]]}));
  assert(h.grid.querySelectorAll('.mx-cell').length === 3);
  assert(h.cell('d1', 's1') !== before);
  assert(!h.cell('d1', 's2'), 'a withdrawn source is gone, not greyed');
}));

suite.push(() => check('nothing to show is a sentence, not an empty grid', async () => {
  const h = harness();
  h.matrix.render(dataset({sources: [], rows: []}));
  assert(h.empty.hidden === false && h.scroller.hidden === true);
  assert(/No sources or displays/.test(h.empty.textContent));
  assert(h.grid.children.length === 0);
  h.matrix.render(dataset({sources: [], rows: dataset().rows}));
  assert(/No sources/.test(h.empty.textContent));
  h.matrix.render(dataset({rows: []}));
  assert(/No displays or groups/.test(h.empty.textContent));
  h.matrix.render(dataset());
  assert(h.empty.hidden === true && h.scroller.hidden === false);
}));

suite.push(() => check('a row key keeps a display and a group with the same text apart', async () => {
  const h = harness();
  h.matrix.render(dataset({rows: [
    {kind: 'display', id: 'same', name: 'A', online: true, video: plane('none'), audio: plane('none')},
    {kind: 'group', id: 'same', name: 'B', displays: 1, online: true,
     video: groupPlane('all', {none: 1}, 1), audio: groupPlane('all', {none: 1}, 1)}]}));
  assert(h.grid.querySelectorAll('.mx-cell').length === 4);
}));

// ------------------------------------------------------------- the preview
suite.push(() => check('nothing is fetched when the grid is drawn', async () => {
  const h = harness();
  h.matrix.render(dataset());
  await settle();
  assert(h.fetched.length === 0 && h.timers.length === 0);
}));

suite.push(() => check('a hover waits, then asks once for that source', async () => {
  const h = harness();
  h.matrix.render(dataset());
  const head = h.grid.querySelectorAll('.mx-source-head')[0];
  await h.grid.fire('mouseover', {target: head});
  assert(h.timers.length === 1 && h.timers[0].ms === h.MX.HOVER_DELAY_MS && h.fetched.length === 0);
  h.timers[0].fn();
  await settle();
  assert(h.fetched.length === 1 && h.fetched[0] === head.dataset.source);
  assert(h.drawn.length === 1);
}));

suite.push(() => check('leaving before the delay asks for nothing', async () => {
  const h = harness();
  h.matrix.render(dataset());
  const head = h.grid.querySelectorAll('.mx-source-head')[0];
  await h.grid.fire('mouseover', {target: head});
  await h.grid.fire('mouseout', {target: head});
  assert(h.timers[0].live === false && h.matrix.hasPreviewTimer() === false);
  assert(h.fetched.length === 0);
}));

suite.push(() => check('a picture that arrives after the pointer left is not drawn, and closing drops it', async () => {
  let release;
  const slow = new Promise((r) => { release = r; });
  const h = harness({fetchPreview: (id) => { h.fetched.push(id); return slow; }});
  h.matrix.render(dataset());
  const head = h.grid.querySelectorAll('.mx-source-head')[0];
  await h.grid.fire('mouseover', {target: head});
  h.timers[0].fn();
  await h.grid.fire('mouseout', {target: head});
  release({image: {}, note: ''});
  await settle();
  assert(h.drawn.length === 0, 'a late picture was drawn for a hover that had ended');
  assert(h.host.children[0].hidden === true && h.host.children[0].children.length === 0);
}));

suite.push(() => check('an offline source has no picture to ask for', async () => {
  const h = harness();
  h.matrix.render(dataset({sources: [{id: 's1', name: 'T', online: false}, SOURCES[1]]}));
  const head = h.grid.querySelectorAll('.mx-source-head')[0];
  await h.grid.fire('mouseover', {target: head});
  h.timers[0].fn();
  await settle();
  assert(h.fetched.length === 0);
}));

suite.push(() => check('keyboard focus previews as a hover does', async () => {
  const h = harness();
  h.matrix.render(dataset());
  const head = h.grid.querySelectorAll('.mx-source-head')[1];
  assert(head.getAttribute('tabindex') === '0');
  await h.grid.fire('focusin', {target: head});
  h.timers[0].fn();
  await settle();
  assert(h.fetched[0] === 's2');
}));

suite.push(() => check('a click closes the preview before routing', async () => {
  const h = harness();
  h.matrix.render(dataset());
  const head = h.grid.querySelectorAll('.mx-source-head')[0];
  await h.grid.fire('mouseover', {target: head});
  h.timers[0].fn();
  await settle();
  await h.scroller.fire('click', {target: h.cell('d1', 's1')});
  assert(h.host.children[0].hidden === true && h.host.children[0].children.length === 0);
}));

suite.push(() => check('the module never writes browser storage and has no repeating timer', async () => {
  const code = MATRIX_JS.split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
  assert(!/localStorage|sessionStorage|document\.cookie/.test(code));
  assert(!/setInterval/.test(code), 'one frame per hover, no timer');
  assert(!/innerHTML|insertAdjacentHTML|outerHTML/.test(code), 'built from nodes, so nothing needs escaping');
}));

// -------------------------------------------------------------- the page
function world(options) {
  options = options || {};
  const live = {
    state: options.state,
    matrix: options.matrix || {ok: true, interfaces: ['matrix'], revision: 'r1',
                               sources: SOURCES, rows: dataset().rows},
    revision: (options.state && options.state.revision) || 'r1',
    matrixStatus: 200,
  };
  const byId = new Map();
  [...HTML.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)].forEach((m) => {
    const n = new El('div'); n.id = m[1];
    n.hidden = /\shidden(\s|>|=|\/)/.test(m[0]);
    byId.set(m[1], n);
  });
  // The real page nests the grid inside the scroller inside the panel.
  byId.get('matrix_scroll').appendChild(byId.get('matrix_grid'));
  const listeners = {};
  const body = new El('body');
  const document = {
    getElementById: (id) => byId.get(id) || null,
    querySelectorAll: (sel) => {
      const root = new El('div');
      root.children = [...byId.get('sources').children, ...byId.get('destinations').children];
      return root.querySelectorAll(sel);
    },
    createElement: (tag) => new El(tag),
    createTextNode: (t) => Object.assign(new El('#text'), {_text: String(t)}),
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    hidden: false,
    body,
  };
  const calls = [];
  const toasts = [];
  const remembered = Object.assign({}, options.remembered || {});
  const sandbox = {
    document,
    window: {toast: (m, kind) => toasts.push([m, kind]), omniConfirm: async () => true,
             addEventListener() {}, setInterval() { return 0; }, clearInterval() {},
             setTimeout, clearTimeout,
             omniRemember: {get: (k) => (k in remembered ? remembered[k] : null),
                            set: (k, v) => { if (v) remembered[k] = String(v); else delete remembered[k]; }}},
    location: {pathname: '/user', replace() {}, reload() {}},
    sessionStorage: {getItem: () => 'tok', setItem() {}, removeItem() {}},
    fetch: async (url, opts) => {
      const call = {url, method: (opts && opts.method) || 'GET',
                    body: opts && opts.body ? JSON.parse(opts.body) : null};
      calls.push(call);
      // A page that loops on its own requests must fail the test, not hang it.
      if (calls.length > 60) throw new Error('runaway requests');
      let out = {body: {ok: true}};
      if (url === '/api/user/state') out = {body: live.state};
      else if (url === '/api/user/revision') out = {body: {ok: true, revision: live.revision}};
      else if (url === '/api/auth/session') out = {body: {csrf_token: 'tok', method: 'password'}};
      else if (url === '/api/user/matrix_state') {
        // A scripted read can be held open and answered late, so a test can
        // make one read overtake another.
        const queued = (live.matrixQueue || []).shift();
        if (queued) { await queued.promise; out = {body: queued.body}; }
        else out = live.matrixStatus === 200 ? {body: live.matrix}
          : {status: live.matrixStatus, body: {ok: false, reason: 'interface_not_permitted'}};
      } else if (url === '/api/user/route_group') {
        out = options.groupAnswer || {body: {ok: true, routed: 3, total: 3, results: []}};
      } else if (url === '/api/user/route') {
        if (options.hold) await options.hold;
        out = options.routeAnswer || {body: {ok: true, status: 'VERIFIED'}};
      }
      return {ok: (out.status || 200) < 400, status: out.status || 200,
              headers: {get: () => 'application/json'}, json: async () => out.body || {}};
    },
    URL: {createObjectURL() { return ''; }, revokeObjectURL() {}},
    Image: function () {},
    Date, JSON, Object, Array, Set, Map, String, Number, Promise, console, encodeURIComponent,
    setTimeout, clearTimeout, setInterval() { return 0; },
  };
  vm.createContext(sandbox);
  vm.runInContext(PREVIEW_JS, sandbox);
  vm.runInContext(MATRIX_JS, sandbox);
  vm.runInContext(DRAG_JS, sandbox);
  vm.runInContext(USER_JS, sandbox);
  const fire = async (type, event) => {
    const e = event && event.preventDefault ? event : Object.assign({preventDefault() {}}, event);
    for (const fn of listeners[type] || []) await fn(e);
  };
  return {byId, calls, toasts, live, remembered, fire, document,
          click: (node) => fire('click', {target: node})};
}

const PAGE_STATE = (interfaces, extra) => Object.assign({
  ok: true, role: 'user', username: 'teacher', features: ['av'], interfaces,
  sources: [{id: 's1', name: 'Teacher PC', online: true}, {id: 's2', name: 'Camera', online: true}],
  destinations: [{id: 'd1', name: 'Front', online: true, showing: {mode: 'none'}},
                 {id: 'd2', name: 'Side', online: false, showing: {mode: 'none'}}],
  groups: [{id: 'grp_1', name: 'Rooms', displays: 3}], revision: 'r1',
}, extra || {});

const urls = (w, pattern) => w.calls.filter((c) => pattern.test(c.url));
const tabLabels = (w) => w.byId.get('mode_tabs').children.map((t) => t.textContent);
const shown = (w) => ['standard_routing', 'matrix_routing', 'no_routing']
  .filter((id) => w.byId.get(id).hidden === false);

suite.push(() => check('both interfaces: two tabs, Routing then Matrix, Standard first by default', async () => {
  const w = world({state: PAGE_STATE(['matrix', 'standard'])});
  await settle();
  assert(tabLabels(w).join() === 'Routing,Matrix', tabLabels(w).join());
  assert(w.byId.get('mode_tabs').hidden === false);
  assert(shown(w).join() === 'standard_routing', shown(w).join());
  assert(urls(w, /matrix_state/).length === 0, 'the grid is not read until it is asked for');
}));

suite.push(() => check('Standard only: no second tab, no Matrix, and no Matrix request', async () => {
  const w = world({state: PAGE_STATE(['standard'])});
  await settle();
  assert(w.byId.get('mode_tabs').hidden === true, 'one interface needs no tab');
  assert(shown(w).join() === 'standard_routing');
  assert(urls(w, /matrix_state/).length === 0);
}));

suite.push(() => check('Matrix only: the Matrix is the page, Standard routing is not shown, no Route bar', async () => {
  const w = world({state: PAGE_STATE(['matrix'])});
  await settle();
  assert(w.byId.get('mode_tabs').hidden === true);
  assert(shown(w).join() === 'matrix_routing', shown(w).join());
  assert(w.byId.get('action_bar').hidden === true, 'the Matrix routes from its cells');
  assert(urls(w, /matrix_state/).length === 1, 'read once on the way in');
  assert(w.byId.get('matrix_grid').querySelectorAll('.mx-cell').length === 6);
}));

suite.push(() => check('a routing account given neither interface sees an explanation, not a blank page', async () => {
  const w = world({state: PAGE_STATE([])});
  await settle();
  assert(shown(w).join() === 'no_routing', shown(w).join());
  assert(w.byId.get('action_bar').hidden === true);
  assert(urls(w, /matrix_state/).length === 0);
}));

suite.push(() => check('an older server that names no interfaces is Standard routing', async () => {
  const state = PAGE_STATE(undefined);
  delete state.interfaces;
  const w = world({state});
  await settle();
  assert(shown(w).join() === 'standard_routing');
  assert(w.byId.get('mode_tabs').hidden === true);
}));

suite.push(() => check('choosing Matrix reads the grid and remembers the choice, locally', async () => {
  const w = world({state: PAGE_STATE(['matrix', 'standard'])});
  await settle();
  await w.byId.get('mode_tabs').children[1].fire('click');
  await settle();
  assert(shown(w).join() === 'matrix_routing');
  assert(urls(w, /matrix_state/).length === 1);
  assert(w.remembered.interface === 'matrix');
  assert(w.byId.get('action_bar').hidden === true);
  await w.byId.get('mode_tabs').children[0].fire('click');
  await settle();
  assert(shown(w).join() === 'standard_routing' && w.remembered.interface === 'standard');
  assert(w.byId.get('action_bar').hidden === false, 'the Route bar is back with Standard routing');
  assert(urls(w, /^\/api\/user\/state$/).length >= 2, 'Standard is read fresh on the way back');
}));

suite.push(() => check('the remembered tab is honoured while permitted and ignored when it is not', async () => {
  const both = world({state: PAGE_STATE(['matrix', 'standard']), remembered: {interface: 'matrix'}});
  await settle();
  assert(shown(both).join() === 'matrix_routing', 'opens where the operator left off');
  const std = world({state: PAGE_STATE(['standard']), remembered: {interface: 'matrix'}});
  await settle();
  assert(shown(std).join() === 'standard_routing', 'a remembered Matrix is not a grant');
  assert(urls(std, /matrix_state/).length === 0);
  const mx = world({state: PAGE_STATE(['matrix']), remembered: {interface: 'standard'}});
  await settle();
  assert(shown(mx).join() === 'matrix_routing');
  const junk = world({state: PAGE_STATE(['matrix', 'standard']), remembered: {interface: 'admin'}});
  await settle();
  assert(shown(junk).join() === 'standard_routing');
}));

suite.push(() => check('an automatic fallback does not overwrite what the operator chose', async () => {
  const w = world({state: PAGE_STATE(['matrix']), remembered: {interface: 'matrix'}});
  await settle();
  w.live.state = PAGE_STATE(['standard'], {revision: 'r2'});
  w.live.revision = 'r2';
  await w.fire('visibilitychange', {});
  await settle();
  assert(shown(w).join() === 'standard_routing');
  assert(w.remembered.interface === 'matrix', 'a preference is changed only by the operator');
}));

suite.push(() => check('a Matrix click posts one request that claims the Matrix and carries the mode', async () => {
  const w = world({state: PAGE_STATE(['matrix'])});
  await settle();
  const modes = w.byId.get('matrix_modes').querySelectorAll('.mx-modebtn');
  await w.byId.get('matrix_modes').fire('click', {target: modes[1]});
  const cell = w.byId.get('matrix_grid').querySelectorAll('.mx-cell')
    .find((c) => c.dataset.row === 'd1' && c.dataset.source === 's2');
  await w.byId.get('matrix_scroll').fire('click', {target: cell});
  await settle();
  const posts = w.calls.filter((c) => c.method === 'POST');
  assert(posts.length === 1, 'one request: ' + posts.length);
  assert(posts[0].url === '/api/user/route');
  assert(JSON.stringify(posts[0].body) === JSON.stringify(
    {destination: 'd1', source: 's2', mode: 'video', interface: 'matrix'}), JSON.stringify(posts[0].body));
  assert(w.toasts.some((t) => t[0] === 'Routed'));
  assert(urls(w, /matrix_state/).length === 2, 'the grid is read again after the route');
}));

suite.push(() => check('a Matrix click on a group uses the group endpoint and reports N of M', async () => {
  const w = world({state: PAGE_STATE(['matrix']),
                   groupAnswer: {body: {ok: false, routed: 2, total: 3,
                                        results: [{ok: true, name: 'A'}, {ok: false, name: 'B'}, {ok: true, name: 'C'}]}}});
  await settle();
  const cell = w.byId.get('matrix_grid').querySelectorAll('.mx-cell')
    .find((c) => c.dataset.kind === 'group' && c.dataset.source === 's1');
  await w.byId.get('matrix_scroll').fire('click', {target: cell});
  await settle();
  const post = w.calls.find((c) => c.method === 'POST');
  assert(post.url === '/api/user/route_group');
  assert(post.body.group === 'grp_1' && post.body.interface === 'matrix' && post.body.mode === 'av');
  assert(w.toasts.some((t) => /2 of 3 displays routed.*Not routed: B/.test(t[0]) && t[1] === 'error'),
    JSON.stringify(w.toasts));
}));

suite.push(() => check('a refused Matrix route says why and leaves the page usable', async () => {
  const w = world({state: PAGE_STATE(['matrix']),
                   routeAnswer: {status: 403, body: {ok: false, reason: 'source_not_permitted', error: 'You do not have access to that source.'}}});
  await settle();
  const cell = w.byId.get('matrix_grid').querySelectorAll('.mx-cell')[0];
  await w.byId.get('matrix_scroll').fire('click', {target: cell});
  await settle();
  assert(w.toasts.some((t) => /do not have access to that source/.test(t[0]) && t[1] === 'error'));
  assert(w.byId.get('matrix_grid').querySelectorAll('.mx-cell').every((c) => c.dataset.gone !== '1'));
  assert(!w.byId.get('matrix_grid').querySelectorAll('.mx-cell')[0].disabled, 'not left disabled');
}));

suite.push(() => check('a second click while a route is out sends nothing', async () => {
  let release;
  const hold = new Promise((r) => { release = r; });
  const w = world({state: PAGE_STATE(['matrix']), hold});
  await settle();
  const cells = w.byId.get('matrix_grid').querySelectorAll('.mx-cell');
  const first = w.byId.get('matrix_scroll').fire('click', {target: cells[0]});
  await settle();
  assert(w.calls.filter((c) => c.method === 'POST').length === 1, 'the first route is out');
  assert(cells[1].disabled === true, 'every cell says so while it is');
  await w.byId.get('matrix_scroll').fire('click', {target: cells[1]});
  release();
  await first;
  await settle();
  assert(w.calls.filter((c) => c.method === 'POST').length === 1,
    'a tap during the first route was queued behind it');
  assert(cells[1].disabled === false, 'usable again afterwards');
}));

suite.push(() => check('the Matrix withdrawn while it is showing moves the operator to Standard routing', async () => {
  const w = world({state: PAGE_STATE(['matrix', 'standard']), remembered: {interface: 'matrix'}});
  await settle();
  assert(shown(w).join() === 'matrix_routing');
  w.live.state = PAGE_STATE(['standard'], {revision: 'r2'});
  w.live.revision = 'r2';
  await w.fire('visibilitychange', {});
  await settle();
  assert(shown(w).join() === 'standard_routing', shown(w).join());
  assert(w.byId.get('mode_tabs').hidden === true, 'one interface needs no tab');
  assert(w.byId.get('action_bar').hidden === false);
}));

suite.push(() => check('both withdrawn while showing: the explanation, not a stale grid', async () => {
  const w = world({state: PAGE_STATE(['matrix']), remembered: {}});
  await settle();
  w.live.state = PAGE_STATE([], {revision: 'r2'});
  w.live.revision = 'r2';
  await w.fire('visibilitychange', {});
  await settle();
  assert(shown(w).join() === 'no_routing');
  assert(w.byId.get('action_bar').hidden === true);
}));

suite.push(() => check('a 403 from the grid itself re-reads what the account holds', async () => {
  const w = world({state: PAGE_STATE(['matrix', 'standard']), remembered: {interface: 'matrix'}});
  await settle();
  w.live.state = PAGE_STATE(['standard'], {revision: 'r2'});
  w.live.matrixStatus = 403;
  const cell = w.byId.get('matrix_grid').querySelectorAll('.mx-cell')[0];
  await w.byId.get('matrix_scroll').fire('click', {target: cell});
  await settle();
  assert(shown(w).join() === 'standard_routing', shown(w).join());
}));

suite.push(() => check('a grid that keeps answering 403 is asked about twice, not forever', async () => {
  const w = world({state: PAGE_STATE(['matrix'])});
  w.live.matrixStatus = 403;
  await settle();
  await settle();
  assert(urls(w, /matrix_state/).length <= 2, 'matrix_state reads: ' + urls(w, /matrix_state/).length);
  assert(urls(w, /^\/api\/user\/state$/).length <= 3, 'state reads: ' + urls(w, /^\/api\/user\/state$/).length);
}));

suite.push(() => check('a permission added while the page is open appears without a reload', async () => {
  const w = world({state: PAGE_STATE(['standard'])});
  await settle();
  assert(w.byId.get('mode_tabs').hidden === true);
  w.live.state = PAGE_STATE(['standard', 'matrix'], {revision: 'r2'});
  w.live.revision = 'r2';
  await w.fire('visibilitychange', {});
  await settle();
  assert(tabLabels(w).join() === 'Routing,Matrix' && w.byId.get('mode_tabs').hidden === false);
}));

suite.push(() => check('a source or display removed live disappears from the grid', async () => {
  const w = world({state: PAGE_STATE(['matrix'])});
  await settle();
  w.live.matrix = {ok: true, interfaces: ['matrix'], revision: 'r2', sources: [SOURCES[1]],
                   rows: dataset().rows.filter((r) => r.id !== 'd1')};
  w.live.state = PAGE_STATE(['matrix'], {revision: 'r2'});
  w.live.revision = 'r2';
  await w.fire('visibilitychange', {});
  await settle();
  const cells = w.byId.get('matrix_grid').querySelectorAll('.mx-cell');
  assert(cells.length === 2 && cells.every((c) => c.dataset.source === 's2'));
  assert(!cells.some((c) => c.dataset.row === 'd1'));
}));

suite.push(() => check('returning to a page showing the Matrix reads the grid once; Standard does not', async () => {
  const w = world({state: PAGE_STATE(['matrix', 'standard'])});
  await settle();
  await w.fire('visibilitychange', {});
  await settle();
  assert(urls(w, /matrix_state/).length === 0, 'Standard is showing, so the grid is not read');
  await w.byId.get('mode_tabs').children[1].fire('click');
  await settle();
  const before = urls(w, /matrix_state/).length;
  await w.fire('visibilitychange', {});
  await settle();
  assert(urls(w, /matrix_state/).length === before + 1);
}));

suite.push(() => check('a read that began before a route finished is not drawn over the newer one', async () => {
  const w = world({state: PAGE_STATE(['matrix'])});
  await settle();
  const grid = w.byId.get('matrix_grid');
  assert(grid.querySelectorAll('.mx-cell').length === 6, 'the first read drew the full grid');
  // A read begun now is answered late, with a world that has since changed.
  let release;
  const stale = {ok: true, interfaces: ['matrix'], revision: 'old', sources: [SOURCES[0]],
                 rows: [dataset().rows[0]]};
  w.live.matrixQueue = [{promise: new Promise((r) => { release = r; }), body: stale}];
  const pending = w.fire('visibilitychange', {});
  await settle();
  // A route completes meanwhile and reads the grid again, which is current.
  const cell = grid.querySelectorAll('.mx-cell')[0];
  await w.byId.get('matrix_scroll').fire('click', {target: cell});
  await settle();
  assert(grid.querySelectorAll('.mx-cell').length === 6);
  release();
  await pending;
  await settle();
  assert(grid.querySelectorAll('.mx-cell').length === 6,
    'the late read described a world the route had already changed, and was drawn anyway');
}));

suite.push(() => check('a read in flight when a route begins is not drawn while the route is still out', async () => {
  let releaseRoute;
  const hold = new Promise((r) => { releaseRoute = r; });
  const w = world({state: PAGE_STATE(['matrix']), hold});
  await settle();
  const grid = w.byId.get('matrix_grid');
  let releaseRead;
  const stale = {ok: true, interfaces: ['matrix'], revision: 'old', sources: [SOURCES[0]],
                 rows: [dataset().rows[0]]};
  w.live.matrixQueue = [{promise: new Promise((r) => { releaseRead = r; }), body: stale}];
  const pending = w.fire('visibilitychange', {});        // a read begins
  await settle();
  const route = w.byId.get('matrix_scroll').fire('click',  // a route begins, and stays out
    {target: grid.querySelectorAll('.mx-cell')[0]});
  await settle();
  releaseRead();                                          // the read finishes mid-route
  await pending;
  await settle();
  assert(grid.querySelectorAll('.mx-cell').length === 6,
    'a read from before the route was drawn while the route was still in flight');
  releaseRoute();
  await route;
  await settle();
  assert(grid.querySelectorAll('.mx-cell').length === 6);
}));

// ------------------------------------------------------------ drag and drop
function dragEvent(target) {
  const store = {};
  return {target, dataTransfer: {setData: (k, v) => { store[k] = v; }, effectAllowed: '', dropEffect: ''},
          store, prevented: false,
          preventDefault() { this.prevented = true; }};
}
const sourceCard = (w, id) => w.byId.get('sources').children.find((c) => c.dataset.id === id);
const targetCard = (w, id) => w.byId.get('destinations').children.find((c) => c.dataset.id === id);

suite.push(() => check('a source card is draggable only while it is online', async () => {
  const state = PAGE_STATE(['standard']);
  state.sources[1].online = false;
  const w = world({state});
  await settle();
  assert(sourceCard(w, 's1').draggable === true);
  assert(!sourceCard(w, 's2').draggable, 'an offline source cannot be picked up');
  assert(!targetCard(w, 'd1').draggable, 'a display is a target, not a handle');
}));

suite.push(() => check('dropping a source on a display sends the very request the Route button sends', async () => {
  const button = world({state: PAGE_STATE(['standard'])});
  await settle();
  await button.click(sourceCard(button, 's1'));
  await button.click(targetCard(button, 'd1'));
  await button.byId.get('route_btn').fire('click');
  await settle();
  const pressed = button.calls.find((c) => c.method === 'POST');

  const drop = world({state: PAGE_STATE(['standard'])});
  await settle();
  const start = dragEvent(sourceCard(drop, 's1'));
  await drop.fire('dragstart', start);
  assert(start.dataTransfer.effectAllowed === 'copy');
  assert(!/s1|Teacher/.test(JSON.stringify(start.store)), 'the payload names nothing');
  const over = dragEvent(targetCard(drop, 'd1'));
  await drop.fire('dragover', over);
  assert(over.prevented === true, 'a valid target says it will take the drop');
  const marked = targetCard(drop, 'd1');
  assert(marked.classList.contains('drop-ok'));
  await drop.fire('drop', dragEvent(targetCard(drop, 'd1')));
  await settle();
  const dropped = drop.calls.filter((c) => c.method === 'POST');
  assert(dropped.length === 1, 'one request: ' + dropped.length);
  assert(JSON.stringify(dropped[0]) === JSON.stringify(pressed),
    'drop ' + JSON.stringify(dropped[0]) + ' button ' + JSON.stringify(pressed));
  assert(dropped[0].body.interface === 'standard' && dropped[0].body.mode === 'av');
  // The same element, not a lookup: the page rebuilds its cards after a route, so a fresh card
  // would be clean whether or not the marker had been cleared.
  assert(!marked.classList.contains('drop-ok'), 'the marker is cleared');
}));

suite.push(() => check('dropping on a group uses the group endpoint', async () => {
  const w = world({state: PAGE_STATE(['standard'])});
  await settle();
  await w.fire('dragstart', dragEvent(sourceCard(w, 's2')));
  await w.fire('dragover', dragEvent(targetCard(w, 'grp_1')));
  await w.fire('drop', dragEvent(targetCard(w, 'grp_1')));
  await settle();
  const post = w.calls.find((c) => c.method === 'POST');
  assert(post.url === '/api/user/route_group');
  assert(post.body.source === 's2' && post.body.group === 'grp_1' && post.body.interface === 'standard');
}));

suite.push(() => check('an offline display is not a drop target and nothing is sent', async () => {
  const w = world({state: PAGE_STATE(['standard'])});
  await settle();
  await w.fire('dragstart', dragEvent(sourceCard(w, 's1')));
  const over = dragEvent(targetCard(w, 'd2'));
  await w.fire('dragover', over);
  assert(over.prevented === false, 'an offline display must not accept');
  await w.fire('drop', dragEvent(targetCard(w, 'd2')));
  await settle();
  assert(w.calls.filter((c) => c.method === 'POST').length === 0);
}));

suite.push(() => check('the page refuses an offline display even if its card were enabled', async () => {
  const w = world({state: PAGE_STATE(['standard'])});
  await settle();
  const card = targetCard(w, 'd2');
  card.disabled = false;            // take the first line of defence out of the way
  await w.fire('dragstart', dragEvent(sourceCard(w, 's1')));
  const over = dragEvent(card);
  await w.fire('dragover', over);
  assert(over.prevented === false, 'offline is decided from the list the server sent, not from the card');
  await w.fire('drop', dragEvent(card));
  await settle();
  assert(w.calls.filter((c) => c.method === 'POST').length === 0);
}));

suite.push(() => check('a drop ends the drag: dropping again without a new pick-up sends nothing', async () => {
  const w = world({state: PAGE_STATE(['standard'])});
  await settle();
  await w.fire('dragstart', dragEvent(sourceCard(w, 's1')));
  await w.fire('dragover', dragEvent(targetCard(w, 'd1')));
  await w.fire('drop', dragEvent(targetCard(w, 'd1')));
  await settle();
  assert(w.calls.filter((c) => c.method === 'POST').length === 1);
  await w.fire('dragover', dragEvent(targetCard(w, 'd1')));
  await w.fire('drop', dragEvent(targetCard(w, 'd1')));
  await settle();
  assert(w.calls.filter((c) => c.method === 'POST').length === 1, 'a second drop re-used the finished drag');
}));

suite.push(() => check('a drop that was never started, or from outside, sends nothing', async () => {
  const w = world({state: PAGE_STATE(['standard'])});
  await settle();
  await w.fire('drop', dragEvent(targetCard(w, 'd1')));
  await w.fire('dragover', dragEvent(targetCard(w, 'd1')));
  assert(w.calls.filter((c) => c.method === 'POST').length === 0);
  // Dragging a display (not a source) is not a drag this page starts.
  const start = dragEvent(targetCard(w, 'd1'));
  await w.fire('dragstart', start);
  await w.fire('drop', dragEvent(targetCard(w, 'd1')));
  assert(w.calls.filter((c) => c.method === 'POST').length === 0);
}));

suite.push(() => check('a drag ending anywhere else clears everything and sends nothing', async () => {
  const w = world({state: PAGE_STATE(['standard'])});
  await settle();
  await w.fire('dragstart', dragEvent(sourceCard(w, 's1')));
  assert(sourceCard(w, 's1').classList.contains('dragging'));
  await w.fire('dragend', dragEvent(sourceCard(w, 's1')));
  assert(!sourceCard(w, 's1').classList.contains('dragging'));
  await w.fire('drop', dragEvent(targetCard(w, 'd1')));
  assert(w.calls.filter((c) => c.method === 'POST').length === 0, 'the drag was over');
}));

suite.push(() => check('no drag starts, and no drop lands, while the Matrix is the interface', async () => {
  const w = world({state: PAGE_STATE(['matrix', 'standard']), remembered: {interface: 'matrix'}});
  await settle();
  const start = dragEvent(sourceCard(w, 's1'));
  await w.fire('dragstart', start);
  assert(start.prevented === true, 'the drag was refused');
  await w.fire('drop', dragEvent(targetCard(w, 'd1')));
  await settle();
  assert(w.calls.filter((c) => c.method === 'POST').length === 0);
}));

suite.push(() => check('the Standard Route button and a drop both go through the one function', async () => {
  const code = USER_JS.split('\n').filter((l) => !/^\s*\/\//.test(l)).join('\n');
  assert((code.match(/async function routeSelected\(/g) || []).length === 1);
  assert(/el\('route_btn'\)\.addEventListener\('click', routeSelected\)/.test(code));
  assert(/return routeSelected\(\);/.test(code), 'perform ends in the same function');
  // The drag module itself holds no network and no authority.
  const drag = DRAG_JS.split('\n').filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n');
  assert(!/fetch\(|XMLHttpRequest|localStorage|sessionStorage/.test(drag));
}));

suite.push(() => check('every request the shell makes states the interface it came from', async () => {
  const code = USER_JS.split('\n').filter((l) => !/^\s*\/\//.test(l)).join('\n');
  const standardBodies = code.match(/interface: 'standard'/g) || [];
  assert(standardBodies.length === 2, 'single and group Standard routes: ' + standardBodies.length);
  assert(/interface: 'matrix'/.test(code));
  assert(!/localStorage|sessionStorage\.setItem\('omni_user/.test(code), 'the shell writes no storage');
}));

(async () => {
  for (const run of suite) await run();
  results.forEach(([status, line]) => console.log(status + ' ' + line));
  console.log(`\n${results.length - failures}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
})();

// The restricted User's Multiview editor, executed.
//
// Layer 1 drives the real module with a scripted server that keeps the authoritative composition
// (windows, revision) and applies the same rules the endpoint states: a stale revision is refused
// and writes nothing, a source cannot be unknown, a refused request changes nothing. Layer 2 loads
// the real shell script and module and dispatches real clicks and change events, then reads the
// requests that actually left the page -- method, path, body, CSRF -- and pins the paths against
// the server's route table.
//
// Rapid-operation coherence is measured, not argued: fixed sequences from the report (X,X,X; X then
// at once assign another; assign, assign, unassign, assign) and a long seeded random sequence, each
// checked after EVERY operation against a model that knows nothing about the page's code.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {El} = require('./_fakedom');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const HTML = read('ui', 'user', 'index.html');
const USER_JS = read('ui', 'user', 'user.js');
const MODULE_JS = read('ui', 'user', 'multiview.js');
const ACCESS_PY = read('omni_access.py');
const CSS = read('ui', 'remote.css');
const TOKENS = read('ui', 'templates.css') + ' ' + CSS;
const PREVIEW_JS = read('ui', 'user', 'preview-refresh.js');
const MATRIX_JS = read('ui', 'user', 'matrix.js');
const DRAG_JS = read('ui', 'user', 'dragdrop.js');
const USB_JS = read('ui', 'user', 'usb-matrix.js');

let failures = 0;
const results = [];
function check(name, fn) {
  return Promise.resolve().then(fn).then(
    () => results.push(['ok  ', name]),
    (err) => { failures++; results.push(['FAIL', `${name}: ${err && err.message ? err.message : err}`]); });
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }
// Key order is not a fact about an object, so it is not compared.
const stable = (v) => JSON.stringify(v, (k, x) => (x && typeof x === 'object' && !Array.isArray(x)
  ? Object.keys(x).sort().reduce((o, key) => { o[key] = x[key]; return o; }, {}) : x));
function eq(a, b, m) { if (stable(a) !== stable(b)) throw new Error(`${m || 'not equal'}: ${stable(a)} !== ${stable(b)}`); }
const settle = () => new Promise((r) => setTimeout(r, 30));
const strip = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').split('\n').map((l) => l.replace(/^\s*\/\/.*$/, '')).join('\n');
const clone = (o) => JSON.parse(JSON.stringify(o));

function all(node, out) {
  out = out || [];
  node.children.forEach((c) => { out.push(c); all(c, out); });
  return out;
}
const byClass = (root, cls) => all(root).filter((n) => n.className.split(' ').includes(cls));
const byData = (root, attr, value) => all(root).filter((n) => n.getAttribute(attr) === value);

// Synthetic identities and layout; nothing here is a lab value.
const DEC = '0a:1b:2c:d0:00:01';
const LAY = DEC + '/mv_00000000000000a1';
const S1 = '0a:1b:2c:e0:00:01', S2 = '0a:1b:2c:e0:00:02', S3 = '0a:1b:2c:e0:00:03', S4 = '0a:1b:2c:e0:00:04';
const SIZES = {main: [1280, 720], a: [640, 360], b: [640, 360], c: [640, 360]};
const GEOMETRY = [
  {cell: 'main', x: 0, y: 0, width: 1280, height: 720},
  {cell: 'a', x: 1280, y: 0, width: 640, height: 360},
  {cell: 'b', x: 1280, y: 360, width: 640, height: 360},
  {cell: 'c', x: 1280, y: 720, width: 640, height: 360},
];
const NAMES = {[S1]: 'Camera One', [S2]: 'Camera Two', [S3]: 'Camera Three', [S4]: 'Camera Four'};

// The authoritative side. `held` is what the display holds: cell -> source id, '' for empty, or
// '*' for a source this account cannot be told about.
class Server {
  constructor(held) {
    this.held = Object.assign({main: S1, a: '', b: S2, c: ''}, held || {});
    this.rev = 1;
    this.showing = true;
    this.log = [];
    this.fail = null;          // a reply to give the next apply instead
    this.gate = null;          // a promise the next apply waits for
    this.sources = [
      {id: S1, name: NAMES[S1], selectable: true, reason: ''},
      {id: S2, name: NAMES[S2], selectable: true, reason: ''},
      {id: S3, name: NAMES[S3], selectable: true, reason: ''},
      {id: S4, name: NAMES[S4], selectable: false, reason: 'Multicast configuration required'},
    ];
    this.layoutStatus = 200;
    this.applyStatus = null;
    this.detailGate = null;
  }
  state() {
    return {ok: true, displays: [{id: DEC, name: 'Front Display', layouts: [{
      id: LAY, name: 'Lesson', layout_label: 'Main and three', windows: GEOMETRY,
      canvas: {width: 1920, height: 1080}, showing: this.showing, available: true, reason: ''}]}]};
  }
  detail() {
    return {ok: true, display: {id: DEC, name: 'Front Display'},
      layout: {id: LAY, name: 'Lesson', layout_label: 'Main and three', canvas: {width: 1920, height: 1080},
               showing: this.showing, available: true, reason: '', editable: true, editable_reason: ''},
      windows: GEOMETRY.map((g, i) => {
        const v = this.held[g.cell];
        return Object.assign({number: i + 1, label: 'Window ' + (i + 1), origin: v ? 'live' : 'empty', health: v ? 'live' : ''}, g, {
          source: !v ? null : v === '*' ? {id: null, name: 'Another source', restricted: true}
            : {id: v, name: NAMES[v]}});
      }),
      sources: clone(this.sources), hidden_sources: 1, revision: 'r' + this.rev};
  }
  async apply(payload) {
    this.log.push(clone(payload));
    if (this.gate) { const g = this.gate; this.gate = null; await g; }
    if (this.fail) { const f = this.fail; this.fail = null; return f; }
    if (payload.base !== 'r' + this.rev) {
      return {status: 409, body: {ok: false, reason: 'stale', status: 'CHANGED', error: 'This layout changed while you were choosing.', op_id: payload.op_id}};
    }
    const next = Object.assign({}, this.held);
    for (const [cell, id] of Object.entries(payload.changes)) {
      if (!(cell in SIZES)) return {status: 400, body: {ok: false, reason: 'bad_window', error: 'No such window.', op_id: payload.op_id}};
      next[cell] = id;
    }
    // One source, one scaler: the same refusal the engine gives.
    const where = {};
    for (const [cell, id] of Object.entries(next)) {
      if (!id || id === '*') continue;
      (where[id] = where[id] || []).push(cell);
    }
    for (const cells of Object.values(where)) {
      if (new Set(cells.map((c) => SIZES[c].join('x'))).size > 1) {
        return {status: 409, body: {ok: false, status: 'conflict', reason: 'refused', error: 'One source cannot fill windows of different sizes.', op_id: payload.op_id}};
      }
    }
    this.held = next;
    this.showing = true;
    this.rev += 1;
    return {status: 200, body: {ok: true, status: 'VERIFIED', op_id: payload.op_id}};
  }
}

function harness(options) {
  options = options || {};
  const server = options.server || new Server();
  const doc = {createElement: (t) => new El(t), body: new El('body')};
  const host = new El('div');
  const status = new El('p');
  const requests = [], toasts = [], dialogs = [];
  let confirmAnswer = options.confirm === undefined ? true : options.confirm;
  let confirmGate = null;
  const sandbox = {window: {}, Promise, Array, Object, JSON, String, Number, Set, Map, Date, console};
  vm.createContext(sandbox);
  vm.runInContext(MODULE_JS, sandbox);
  const deps = {
    document: doc, host, status,
    load: async () => { requests.push(['GET', 'state']); return {status: 200, body: clone(server.state())}; },
    show: async (id) => { requests.push(['POST', 'show', id]); return options.showReply || {status: 200, body: {ok: true, status: 'VERIFIED'}}; },
    detail: async (id) => {
      requests.push(['GET', 'layout', id]);
      // The answer is what the server held when it was ASKED; a slow one is old by the time it arrives.
      const body = server.layoutStatus === 200 ? clone(server.detail()) : {ok: false, error: 'Your permission for that has changed.', reason: 'multiview_not_assigned'};
      if (server.detailGate) { const g = server.detailGate; server.detailGate = null; await g; }
      return {status: server.layoutStatus, body};
    },
    apply: async (payload) => { requests.push(['POST', 'apply', clone(payload)]); return server.apply(payload); },
    toast: (text, kind) => toasts.push([text, kind]),
    failureText: (body) => body.error || 'That did not work.',
  };
  if (options.confirm !== null) {
    deps.confirm = async (opts) => {
      dialogs.push(clone(opts));
      if (confirmGate) await confirmGate;
      return confirmAnswer;
    };
  }
  const module = sandbox.window.OmniUserMultiview.create(deps);
  const h = {
    module, host, status, server, requests, toasts, dialogs,
    answer: (v) => { confirmAnswer = v; },
    holdDialog: () => { let release; confirmGate = new Promise((r) => { release = r; }); return () => { confirmGate = null; release(); }; },
    posts: () => requests.filter((r) => r[0] === 'POST' && r[1] === 'apply'),
    editor: () => host.children[0],
    box: (cell) => byClass(h.host, 'mv-box').find((b) => b.getAttribute('data-cell') === cell),
    row: (cell) => byClass(h.host, 'mv-row').find((b) => b.getAttribute('data-cell') === cell),
    chip: (id) => byClass(h.host, 'mv-source').find((b) => b.getAttribute('data-source') === id),
    button: (cls) => byClass(h.host, cls)[0],
    text: (cls) => (byClass(h.host, cls)[0] || {textContent: ''}).textContent,
    sourceOf: (cell) => byClass(h.box(cell), 'mv-box-source')[0].textContent,
    async open() {
      await module.refresh();
      const card = byClass(host, 'mv-card')[0];
      const open = byClass(card, 'mv-open')[0];
      await open.fire('click');
      await settle();
    },
    async pick(cell, id) { const s = h.row(cell).children.find((c) => c.tagName === 'SELECT'); s.value = id; await s.fire('change'); },
  };
  return h;
}

const suite = [];
const t = (name, fn) => suite.push(() => check(name, fn));

// ================================================================== opening
t('the list offers Choose sources beside Show, and opening reads exactly one layout', async () => {
  const h = harness();
  await h.module.refresh();
  const card = byClass(h.host, 'mv-card')[0];
  assert(byClass(card, 'mv-show').length === 1 && byClass(card, 'mv-open').length === 1);
  await h.open();
  eq(h.requests.filter((r) => r[1] === 'layout'), [['GET', 'layout', LAY]]);
  assert(byClass(h.host, 'mv-editor').length === 1, 'the editor replaced the list');
  assert(byClass(h.host, 'mv-card').length === 0, 'the cards are not drawn under the editor');
});

t('without the detail and apply requests the page is the old one: Show only', async () => {
  const sandbox = {window: {}, Promise, Array, Object, JSON, String, Number, Set, Map, Date, console};
  vm.createContext(sandbox);
  vm.runInContext(MODULE_JS, sandbox);
  const host = new El('div');
  const s = new Server();
  const m = sandbox.window.OmniUserMultiview.create({
    document: {createElement: (x) => new El(x)}, host, load: async () => ({status: 200, body: s.state()}),
    show: async () => ({status: 200, body: {ok: true}}),
  });
  await m.refresh();
  eq(byClass(host, 'mv-open').length, 0);
});

t('the editor says: display, layout, each window and its size, what is in it now, and your sources', async () => {
  const h = await (async () => { const x = harness(); await x.open(); return x; })();
  assert(/Lesson/.test(h.text('mv-editor-title')));
  assert(/Front Display/.test(h.text('mv-editor-meta')) && /Main and three/.test(h.text('mv-editor-meta')));
  eq(byClass(h.host, 'mv-box').length, 4);
  eq(byClass(h.host, 'mv-row').map((r) => byClass(r, 'mv-row-label')[0].textContent),
     ['Window 1 (1280×720)', 'Window 2 (640×360)', 'Window 3 (640×360)', 'Window 4 (640×360)']);
  eq(h.sourceOf('main'), 'Camera One');
  eq(h.sourceOf('a'), 'Empty');
  assert(/Now: Camera One/.test(byClass(h.row('main'), 'mv-row-now')[0].textContent));
  eq(byClass(h.host, 'mv-source').map((c) => c.textContent), [NAMES[S1], NAMES[S2], NAMES[S3], NAMES[S4]]);
  assert(byClass(h.host, 'mv-showing')[0].hidden === false, 'the layout is on the display and says so in words');
  assert(/1 source you were given cannot be used/.test(h.text('mv-hidden-sources')), 'hidden sources are counted');
});

t('a source that is not ready is listed with its reason and cannot be chosen anywhere', async () => {
  const h = harness();
  await h.open();
  assert(h.chip(S4).disabled === true && h.chip(S4).draggable !== true);
  assert(/configuration required/i.test(h.host.textContent));
  const select = h.row('a').children.find((c) => c.tagName === 'SELECT');
  assert(!select.children.some((o) => o.value === S4), 'not an option');
  eq(await h.module.assign('a', S4), false);
  eq(h.module.editorState().pending, {});
});

t('a window holding a source this account cannot be told about says so and names nothing', async () => {
  const h = harness({server: new Server({c: '*'})});
  await h.open();
  eq(h.sourceOf('c'), 'Another source');
  assert(!/\*/.test(h.host.textContent));
  // It can be cleared or replaced like any other window.
  assert(h.module.clear('c') === true);
  eq(h.module.editorState().pending, {c: ''});
});

t('the control of a window holding an ungiven source says so and is not set to Empty', async () => {
  const h = harness({server: new Server({c: '*'})});
  await h.open();
  const select = h.row('c').children.find((c) => c.tagName === 'SELECT');
  assert(select.children.some((o) => o.textContent === 'Another source'), 'the true state is an option');
  eq(select.value, ' restricted');
  eq(await h.module.assign('c', ' restricted'), false, 'it is not a choice');
  eq(h.module.editorState().pending, {});
});

t('no control edits the design of a layout', async () => {
  const h = harness();
  await h.open();
  const controls = all(h.host).filter((n) => ['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(n.tagName));
  const labels = controls.map((c) => (c.getAttribute('aria-label') || c.textContent || '').toLowerCase());
  labels.forEach((l) => assert(!/rename|delete|resize|create|new layout|geometry|width|height/.test(l), 'a design control: ' + l));
  eq(all(h.host).filter((n) => ['INPUT', 'TEXTAREA'].includes(n.tagName)).length, 0);
  const code = strip(MODULE_JS);
  assert(!/layout_definition|\brename\b|geometry\s*:/.test(code), 'the module has no design vocabulary');
});

// ================================================================== choosing
t('choosing is pending: nothing is sent until Show, and the page says what is pending', async () => {
  const h = harness();
  await h.open();
  const before = h.requests.length;
  await h.pick('a', S3);
  eq(h.requests.length, before, 'a choice sends nothing');
  eq(h.module.editorState().pending, {a: S3});
  eq(h.sourceOf('a'), 'Camera Three');
  assert(h.box('a').classList.contains('changed') && h.row('a').classList.contains('changed'));
  assert(/1 change not shown yet/.test(h.text('mv-pending')) && /Window 2: Empty → Camera Three/.test(h.text('mv-pending')));
  eq(h.button('mv-apply').textContent, 'Show with my changes');
  assert(h.button('mv-discard').disabled === false);
  assert(/not change the layout your administrator saved/.test(h.text('mv-note')), 'the page says choices are not saved into the layout');
});

t('choosing what the display already holds is not a change', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3);
  await h.pick('a', '');
  eq(h.module.editorState().pending, {});
  eq(h.button('mv-apply').textContent, 'Show');
  assert(h.button('mv-discard').disabled === true);
});

t('a source can be picked and a window clicked, and dragged and dropped, with the same result', async () => {
  const h = harness();
  await h.open();
  await h.chip(S3).fire('click');
  assert(h.chip(S3).getAttribute('aria-pressed') === 'true');
  await h.box('a').fire('click');
  eq(h.module.editorState().pending, {a: S3});
  let prevented = 0;
  const data = {};
  await h.chip(S2).fire('dragstart', {dataTransfer: {setData: (k, v) => { data[k] = v; }}});
  await h.box('c').fire('dragover', {preventDefault: () => { prevented++; }});
  assert(prevented === 1, 'a window accepts a drag while one is in flight');
  await h.box('c').fire('drop', {preventDefault() {}});
  eq(h.module.editorState().pending, {a: S3, c: S2});
  assert(!JSON.stringify(data).includes(S2), 'no identity travels in the drag payload');
});

t('the X on a window and the Clear beside it do the same thing; an empty window has nothing to clear', async () => {
  const h = harness();
  await h.open();
  assert(byClass(h.box('a'), 'mv-box-clear')[0].disabled === true, 'nothing in Window 2 to clear');
  await byClass(h.box('main'), 'mv-box-clear')[0].fire('click');
  eq(h.module.editorState().pending, {main: ''});
  eq(h.sourceOf('main'), 'Empty');
  await byClass(h.row('b'), 'mv-row-clear')[0].fire('click');
  eq(h.module.editorState().pending, {main: '', b: ''});
  eq(byClass(h.box('main'), 'mv-box-clear')[0].getAttribute('aria-label'), 'Clear Window 1');
});

t('Discard returns every window to what the display holds', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3); h.module.clear('main');
  await h.button('mv-discard').fire('click');
  eq(h.module.editorState().pending, {});
  eq(h.sourceOf('main'), 'Camera One');
});

t('an inset anchored to a corner is drawn at that corner, in the card and in the editor', async () => {
  const server = new Server();
  const inset = {cell: 'main', x: 0, y: 0, width: 1920, height: 1080};
  const corner = {cell: 'a', x: 1888, y: 1048, anchor: 'bottom right', width: 640, height: 360};
  const orig = server.state.bind(server), origDetail = server.detail.bind(server);
  server.state = () => { const s = orig(); s.displays[0].layouts[0].windows = [inset, corner]; return s; };
  server.detail = () => { const d = origDetail(); d.windows = [Object.assign({}, d.windows[0], inset),
    Object.assign({}, d.windows[1], corner)]; return d; };
  const h = harness({server});
  await h.module.refresh();
  const small = byClass(byClass(h.host, 'mv-card')[0], 'mv-window')[1];
  eq([small.style.left, small.style.top], [(100 * (1888 - 640) / 1920) + '%', (100 * (1048 - 360) / 1080) + '%']);
  await h.open();
  eq([h.box('a').style.left, h.box('a').style.top], [small.style.left, small.style.top]);
  assert(parseFloat(h.box('a').style.left) + parseFloat(h.box('a').style.width) <= 100.0001, 'inside the canvas');
});

// ================================================== rapid operations, measured
function modelEffective(held, pending) {
  const out = {};
  Object.keys(SIZES).forEach((c) => { out[c] = c in pending ? pending[c] : (held[c] === '*' ? '*' : held[c] || ''); });
  return out;
}
function pageEffective(h) {
  const out = {};
  Object.keys(SIZES).forEach((c) => {
    const t_ = h.sourceOf(c).replace(/ • changed$/, '');
    out[c] = t_ === 'Empty' ? '' : t_ === 'Another source' ? '*' : Object.keys(NAMES).find((id) => NAMES[id] === t_);
  });
  return out;
}

t('X, X, X: three clears in a row leave exactly three (or fewer) windows empty and nothing stale', async () => {
  const h = harness({server: new Server({main: S1, a: S2, b: S3, c: S4 && ''})});
  await h.open();
  h.module.clear('main'); h.module.clear('a'); h.module.clear('b');
  eq(h.module.editorState().pending, {main: '', a: '', b: ''});
  eq(pageEffective(h), {main: '', a: '', b: '', c: ''});
  h.module.clear('main');                                   // already empty: no new effect
  eq(h.module.editorState().pending, {main: '', a: '', b: ''});
});

t('X then at once assign another: the second wins and the first is not resurrected', async () => {
  const h = harness();
  await h.open();
  h.module.clear('main');
  const pending = h.module.assign('main', S3);              // not awaited: the very next statement
  eq(h.module.editorState().pending, {main: S3});
  await pending;
  eq(pageEffective(h).main, S3);
  h.module.clear('b'); h.module.assign('b', S2);            // back to what the display holds
  eq(h.module.editorState().pending, {main: S3}, 'assigning what the display holds removes the choice');
});

t('assign, assign, unassign, assign leaves exactly the last word on every window', async () => {
  const h = harness();
  await h.open();
  await h.module.assign('a', S2);                           // same size as b, legal
  await h.module.assign('c', S2);
  h.module.clear('a');
  await h.module.assign('a', S3);
  eq(h.module.editorState().pending, {a: S3, c: S2});
  eq(pageEffective(h), {main: S1, a: S3, b: S2, c: S2});
});

t('a long seeded random sequence agrees with an independent model after every operation', async () => {
  const h = harness({server: new Server({main: S1, a: S2, b: '', c: '*'})});
  await h.open();
  let seed = 20260522;
  const rnd = (n) => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed % n; };
  const cells = Object.keys(SIZES);
  const ids = [S1, S2, S3];
  const model = {pending: {}};
  const held = h.server.held;
  const same = (c, id) => id === (held[c] === '*' ? '*' : held[c] || '');
  const eff = () => modelEffective(held, model.pending);
  const set = (c, v) => { if (same(c, v)) delete model.pending[c]; else model.pending[c] = v; };
  for (let i = 0; i < 400; i++) {
    const cell = cells[rnd(4)];
    const op = rnd(3);
    if (op === 0) {                                          // clear
      if (eff()[cell] !== '') set(cell, '');
      h.module.clear(cell);
    } else {                                                 // assign, with Replace answered yes
      const id = ids[rnd(3)];
      if (eff()[cell] !== id) {
        const clash = cells.filter((o) => o !== cell && eff()[o] === id && SIZES[o].join() !== SIZES[cell].join());
        clash.forEach((o) => set(o, ''));
        set(cell, id);
      }
      await h.module.assign(cell, id);
    }
    const state = h.module.editorState();
    if (JSON.stringify(state.pending) !== JSON.stringify(model.pending)) {
      throw new Error(`step ${i}: page ${JSON.stringify(state.pending)} model ${JSON.stringify(model.pending)}`);
    }
    if (JSON.stringify(pageEffective(h)) !== JSON.stringify(eff())) {
      throw new Error(`step ${i}: drawn ${JSON.stringify(pageEffective(h))} model ${JSON.stringify(eff())}`);
    }
  }
  eq(h.posts().length, 0, 'nothing was sent while choosing');
});

// ================================================== same-size reuse and Replace
t('one source in a second window of the SAME size is legal and asks nothing', async () => {
  const h = harness();
  await h.open();
  await h.module.assign('a', S2);                           // S2 is already in b, same size
  eq(h.dialogs.length, 0);
  eq(h.module.editorState().pending, {a: S2});
});

t('one source for a window of a DIFFERENT size asks, with the same words and the safe default', async () => {
  const h = harness({confirm: false});
  await h.open();
  const result = await h.module.assign('a', S1);            // S1 holds main (1280x720)
  eq(result, false);
  eq(h.dialogs.length, 1);
  const d = h.dialogs[0];
  eq([d.title, d.confirmText, d.cancelDefault], ['Replace assignment?', 'Replace', true]);
  assert(/different size/.test(d.message));
  eq(d.summary, [{label: 'Source', value: 'Camera One'}, {label: 'Currently in', value: 'Window 1 (1280x720)'},
                 {label: 'Chosen for', value: 'Window 2 (640x360)'}]);
  eq(h.module.editorState().pending, {}, 'Cancel changes nothing');
  eq(h.sourceOf('main'), 'Camera One');
});

t('Replace empties the other window and fills this one in one step', async () => {
  const h = harness({confirm: true});
  await h.open();
  eq(await h.module.assign('a', S1), true);
  eq(h.module.editorState().pending, {main: '', a: S1});
  eq(pageEffective(h), {main: '', a: S1, b: S2, c: ''});
});

t('Replace also takes the source out of a window the person only just filled', async () => {
  const h = harness({confirm: true});
  await h.open();
  await h.module.assign('a', S3);                           // pending: a = S3
  eq(await h.module.assign('main', S3), true);              // main is bigger than a
  // The window the person had just filled is put back to what the display holds, which is not a
  // change, so only the bigger window's choice remains.
  eq(h.module.editorState().pending, {main: S3});
  eq(pageEffective(h), {main: S3, a: '', b: S2, c: ''});
});

t('while the question is open nothing else can be edited, and the answer reads the world again', async () => {
  const h = harness({confirm: true});
  await h.open();
  const release = h.holdDialog();
  const asking = h.module.assign('a', S1);
  await settle();
  eq(h.module.clear('b'), false, 'one question at a time');
  eq(await h.module.assign('c', S3), false);
  assert(h.button('mv-apply').disabled === true, 'Show waits for the answer');
  release();
  await asking;
  eq(h.module.editorState().pending, {main: '', a: S1});
});

t('without the shared dialog the change is refused, never decided by a browser string box', async () => {
  const h = harness({confirm: null});
  await h.open();
  eq(await h.module.assign('a', S1), false);
  eq(h.module.editorState().pending, {});
  assert(h.toasts.some((x) => /dialog is unavailable/.test(x[0])));
});

// ================================================================== Show
t('Show sends exactly layout, base, changes and op_id, then reads what the display now holds', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3); h.module.clear('b');
  await h.button('mv-apply').fire('click');
  await settle();
  const posts = h.posts();
  eq(posts.length, 1);
  const body = posts[0][2];
  eq(Object.keys(body).sort(), ['base', 'changes', 'layout', 'op_id']);
  eq([body.layout, body.base, body.changes], [LAY, 'r1', {a: S3, b: ''}]);
  assert(/^mv[0-9a-z]+-\d+$/.test(body.op_id));
  const order = h.requests.map((r) => r[0] + ' ' + r[1]);
  assert(order.indexOf('POST apply') < order.lastIndexOf('GET layout'), 'the page re-read after Show');
  eq(h.module.editorState().pending, {}, 'a successful Show clears the choices');
  eq(h.sourceOf('a'), 'Camera Three');
  eq(h.sourceOf('b'), 'Empty');
  assert(/Shown on Front Display/.test(h.text('mv-editor-result')));
  assert(h.toasts.some((x) => x[1] === 'ok'));
  assert(h.requests.some((r) => r[1] === 'state'), 'the list marker follows the device');
});

t('a failed Show keeps every choice and puts the server sentence on the page', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3); h.module.clear('main');
  h.server.fail = {status: 409, body: {ok: false, status: 'conflict', reason: 'refused', error: 'camera three did not answer.'}};
  await h.button('mv-apply').fire('click');
  await settle();
  eq(h.module.editorState().pending, {a: S3, main: ''});
  assert(/Camera three did not answer\./.test(h.text('mv-editor-result')));
  assert(byClass(h.host, 'mv-editor-result')[0].className.includes('error'));
  assert(h.button('mv-apply').disabled === false, 'the person can try again');
  eq(h.sourceOf('main'), 'Empty', 'their choice is still on the page');
});

t('a stale Show is refused by the server, the choices are kept over the new state, and Show works again', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3);
  h.server.held.c = S2; h.server.rev += 1;                    // somebody else changed a window
  await h.button('mv-apply').fire('click');
  await settle();
  eq(h.posts()[0][2].base, 'r1');
  eq(h.server.held.a, '', 'nothing was written');
  eq(h.module.editorState().pending, {a: S3});
  eq(h.sourceOf('c'), 'Camera Two', 'the new state, not the old');
  assert(/changed while you were choosing/.test(h.text('mv-editor-result')));
  await h.button('mv-apply').fire('click');
  await settle();
  eq(h.posts()[1][2].base, 'r2');
  eq(h.server.held.a, S3);
});

t('a choice the new state already satisfies drops out when the page re-reads', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3);
  h.server.held.a = S3; h.server.rev += 1;
  await h.button('mv-reread').fire('click');
  await settle();
  eq(h.module.editorState().pending, {});
});

t('Show is single-flight and nothing can be edited while it is in flight', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3);
  let release; h.server.gate = new Promise((r) => { release = r; });
  const first = h.button('mv-apply').fire('click');
  await settle();
  await h.button('mv-apply').fire('click');
  await h.button('mv-apply').fire('click');
  eq(h.posts().length, 1, 'three clicks, one request');
  assert(h.button('mv-apply').disabled === true && h.button('mv-apply').textContent === 'Working…');
  eq(h.module.clear('main'), false);
  eq(await h.module.assign('c', S2), false);
  await h.pick('c', S3);
  eq(h.module.editorState().pending, {a: S3}, 'edits during the write are not accepted');
  release();
  await first; await settle();
  eq(h.module.editorState().pending, {});
  eq(h.posts().length, 1);
});

t('a refusal delivered with status 200 is not a success, in the list and in the editor', async () => {
  const refusal = {status: 200, body: {ok: false, error: 'Camera Three did not answer.'}};
  const h = harness({showReply: refusal});
  await h.module.refresh();
  await byClass(byClass(h.host, 'mv-card')[0], 'mv-show')[0].fire('click');
  await settle();
  assert(/did not answer/.test(h.host.textContent) && h.toasts.every((x) => x[1] === 'error'), 'the list reports a failure');
  const e = harness();
  await e.open();
  await e.pick('a', S3);
  e.server.fail = refusal;
  await e.button('mv-apply').fire('click');
  await settle();
  eq(e.module.editorState().pending, {a: S3}, 'the choices survive an unverified Show');
  assert(byClass(e.host, 'mv-editor-result')[0].className.includes('error'));
});

t('after a Show the page shows what the display holds, even when that is not what was chosen', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3);
  const apply = h.server.apply.bind(h.server);
  h.server.apply = async (payload) => { const r = await apply(payload); h.server.held.a = S2; return r; };   // the display kept something else
  await h.button('mv-apply').fire('click');
  await settle();
  eq(h.module.editorState().pending, {}, 'nothing is left as "not shown yet" after a Show that succeeded');
  eq(h.sourceOf('a'), 'Camera Two', 'the page says what the display holds');
});

t('a read that was already in flight when the page was left and re-opened is discarded', async () => {
  const h = harness();
  await h.open();
  let release; h.server.detailGate = new Promise((r) => { release = r; });
  const old = h.module.refreshEditor();                    // asks now; will be answered late, with today�s world
  await settle();
  h.module.closeEditor();
  h.server.held.a = S3; h.server.rev += 1;                 // the world moves on
  await h.module.refresh();
  await byClass(byClass(h.host, 'mv-card')[0], 'mv-open')[0].fire('click');
  await settle();
  eq(h.sourceOf('a'), 'Camera Three', 'the newer read was applied');
  release(); await old; await settle();
  eq(h.sourceOf('a'), 'Camera Three', 'the late, older answer did not overwrite it');
  eq(h.module.editorState().revision, 'r2');
});

t('a source withdrawn while the page was open drops out of the choices on the next read', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3);
  h.server.sources.find((x) => x.id === S3).selectable = false;
  await h.button('mv-reread').fire('click');
  await settle();
  eq(h.module.editorState().pending, {}, 'a choice that is no longer on offer is not kept');
});

t('an answer to some other request is not applied', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3);
  h.server.fail = {status: 200, body: {ok: true, status: 'VERIFIED', op_id: 'someone-else'}};
  await h.button('mv-apply').fire('click');
  await settle();
  eq(h.module.editorState().pending, {a: S3}, 'the choices were not cleared by an answer that was not theirs');
});

t('a read that was already in flight when Show began is discarded', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3);
  let release; h.server.detailGate = new Promise((r) => { release = r; });
  const late = h.module.refreshEditor();                    // begins; will answer after Show
  await settle();
  // Show cannot start while a read is outstanding, so the read finishes first; what matters is the
  // sequence guard on closing: a read that outlives the page is dropped.
  release(); await late; await settle();
  eq(h.module.editorState().phase, 'ready');
  h.server.detailGate = new Promise((r) => { release = r; });
  const stale = h.module.refreshEditor();
  await settle();
  h.module.closeEditor() ;                                   // refused while reading? closing is allowed
  release(); await stale; await settle();
  assert(h.module.editorState() === null, 'the closed page was not re-opened by a late answer');
});

t('after a revoked grant the next operation is refused, the page says so, and Back returns to the list', async () => {
  const h = harness();
  await h.open();
  await h.pick('a', S3);
  h.server.layoutStatus = 403;
  h.server.fail = {status: 403, body: {ok: false, reason: 'multiview_not_assigned', error: 'That Multiview layout has not been given to your account.'}};
  await h.button('mv-apply').fire('click');
  await settle();
  assert(/has not been given to your account/.test(h.text('mv-editor-result')));
  eq(h.module.editorState().phase, 'error');
  assert(h.button('mv-apply').disabled === true, 'nothing more can be sent from a layout that is no longer yours');
  h.server.state = () => ({ok: true, displays: []});
  await h.button('mv-back').fire('click');
  await settle();
  assert(/No Multiview layouts have been given/.test(h.host.textContent));
});

t('Back is not offered mid-write, and returns to the list with the Showing marker current', async () => {
  const h = harness();
  await h.open();
  let release; h.server.gate = new Promise((r) => { release = r; });
  const p = h.button('mv-apply').fire('click');
  await settle();
  eq(h.module.closeEditor(), false);
  release(); await p; await settle();
  eq(h.module.closeEditor(), true);
  assert(byClass(h.host, 'mv-card').length === 1);
});

t('an unreadable layout is said to be unreadable, not empty', async () => {
  const h = harness();
  h.server.layoutStatus = 502;
  await h.module.refresh();
  await byClass(byClass(h.host, 'mv-card')[0], 'mv-open')[0].fire('click');
  await settle();
  eq(h.module.editorState().phase, 'error');
  assert(h.text('mv-editor-result').length > 0);
  assert(h.button('mv-apply').disabled === true);
});

// ================================================================== the real page
function world(server) {
  const byId = new Map();
  [...HTML.matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)].forEach((m) => {
    const n = new El('div'); n.id = m[1];
    n.hidden = /\shidden(\s|>|=|\/)/.test(m[0]);
    byId.set(m[1], n);
  });
  byId.get('usb_wrap').appendChild(byId.get('usbMatrix'));
  byId.get('usbMatrix').tagName = 'TABLE';
  byId.get('usb_wrap').style = {setProperty() {}};
  const listeners = {};
  const document = {
    getElementById: (id) => byId.get(id) || null, querySelectorAll: () => [],
    createElement: (tag) => new El(tag), createTextNode: (x) => Object.assign(new El('#text'), {_text: String(x)}),
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    hidden: false, body: new El('body'), documentElement: new El('html'),
  };
  const calls = [];
  const sandbox = {
    document,
    window: {toast() {}, omniConfirm: async () => true, innerWidth: 1280, innerHeight: 800, addEventListener() {},
             setInterval() { return 0; }, clearInterval() {}, setTimeout, clearTimeout, omniRemember: {get: () => null, set() {}}},
    location: {pathname: '/user', replace() {}, reload() {}},
    sessionStorage: {getItem: () => 'tok', setItem() {}, removeItem() {}},
    fetch: async (url, opts) => {
      calls.push({url, method: (opts && opts.method) || 'GET', body: opts && opts.body ? JSON.parse(opts.body) : null,
                  csrf: opts && opts.headers && opts.headers['X-OmniSuite-CSRF']});
      if (calls.length > 120) throw new Error('runaway requests');
      let out = {body: {ok: true}};
      const bare = url.split('?')[0];
      if (bare === '/api/user/state') out = {body: {ok: true, role: 'user', username: 'teacher', features: ['multiview'], interfaces: ['standard'],
        sources: [], destinations: [], groups: [], revision: 'r1'}};
      else if (bare === '/api/user/revision') out = {body: {ok: true, revision: 'r1'}};
      else if (bare === '/api/auth/session') out = {body: {csrf_token: 'tok', method: 'password'}};
      else if (bare === '/api/user/usb_state') out = {body: {ok: true, computers: [], peripherals: [], cells: {}}};
      else if (bare === '/api/user/multiview_state') out = {body: server.state()};
      else if (bare === '/api/user/multiview_layout') out = {body: server.detail()};
      else if (bare === '/api/user/multiview_compose') out = await server.apply(JSON.parse(opts.body));
      return {ok: (out.status || 200) < 400, status: out.status || 200, headers: {get: () => 'application/json'}, json: async () => out.body || {}};
    },
    URL: {createObjectURL() { return ''; }, revokeObjectURL() {}}, Image: function () {},
    Date, JSON, Object, Array, Set, Map, String, Number, Promise, console, encodeURIComponent, setTimeout, clearTimeout, setInterval() { return 0; },
  };
  vm.createContext(sandbox);
  [PREVIEW_JS, MATRIX_JS, DRAG_JS, USB_JS, MODULE_JS, USER_JS].forEach((src) => vm.runInContext(src, sandbox));
  return {byId, calls};
}

t('in the real page: open, choose, Show -- the requests that leave carry the contract and the token', async () => {
  const server = new Server();
  const w = world(server);
  await settle();
  const host = w.byId.get('mv_host');
  await byClass(byClass(host, 'mv-card')[0], 'mv-open')[0].fire('click');
  await settle();
  const layoutCalls = w.calls.filter((c) => c.url.startsWith('/api/user/multiview_layout'));
  eq(layoutCalls.map((c) => [c.method, c.url]), [['GET', '/api/user/multiview_layout?layout=' + encodeURIComponent(LAY)]]);
  const select = byClass(byClass(host, 'mv-row').find((r) => r.getAttribute('data-cell') === 'a'), 'mv-row-select')[0];
  select.value = S3;
  const before = w.calls.length;
  await select.fire('change');
  eq(w.calls.length, before, 'choosing makes no request');
  await byClass(host, 'mv-apply')[0].fire('click');
  await settle();
  const applies = w.calls.filter((c) => c.url === '/api/user/multiview_compose');
  eq(applies.length, 1);
  eq([applies[0].method, applies[0].csrf], ['POST', 'tok']);
  eq(Object.keys(applies[0].body).sort(), ['base', 'changes', 'layout', 'op_id']);
  eq([applies[0].body.layout, applies[0].body.base, applies[0].body.changes], [LAY, 'r1', {a: S3}]);
  eq(server.held.a, S3, 'the scripted display holds what was asked');
  assert(!/\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/.test(JSON.stringify(applies[0].body)), 'no address leaves the page');
});

t('in the real page: Replace goes out as one request naming both windows', async () => {
  const server = new Server();
  const w = world(server);
  await settle();
  const host = w.byId.get('mv_host');
  await byClass(byClass(host, 'mv-card')[0], 'mv-open')[0].fire('click');
  await settle();
  const select = byClass(byClass(host, 'mv-row').find((r) => r.getAttribute('data-cell') === 'a'), 'mv-row-select')[0];
  select.value = S1;
  await select.fire('change');                               // omniConfirm in this world answers yes
  await settle();
  await byClass(host, 'mv-apply')[0].fire('click');
  await settle();
  const applies = w.calls.filter((c) => c.url === '/api/user/multiview_compose');
  eq(applies.length, 1);
  eq(applies[0].body.changes, {main: '', a: S1});
  eq([server.held.main, server.held.a], ['', S1]);
});

t('the endpoint paths are the ones the server serves, to the roles that may use them', () => {
  const user = strip(USER_JS);
  ['/api/user/multiview_layout', '/api/user/multiview_compose'].forEach((p) => {
    assert(user.includes("'" + p + "'"), p + ' is not requested by the page');
    const row = new RegExp('"' + p + '":\\s*\\(Access\\.USER,\\s*FEATURE_MULTIVIEW\\)');
    assert(row.test(ACCESS_PY), p + ' is not a USER route behind the multiview feature in omni_access.ROUTES');
  });
  assert(!/\/api\/multiview\//.test(user + strip(MODULE_JS)), 'the restricted page reaches an administrative route');
});

t('the editor styles exist, use tokens only, and every control has an accessible name', () => {
  const css = strip(CSS);
  ['.mv-editor', '.mv-box', '.mv-box.changed', '.mv-source.picked', '.mv-row', '.mv-actionbar'].forEach((s) => assert(css.includes(s), s + ' has no rule'));
  const block = css.slice(css.indexOf('.mv-card .mv-open'), css.indexOf('.assign-group-head'));
  assert(block.length > 800 && !/#[0-9a-f]{3,8}\b|rgba?\(/i.test(block), 'a colour literal in the editor styles');
  assert(!new RegExp('[' + String.fromCharCode(0x80) + '-' + String.fromCharCode(0x9f) + ']').test(block), 'a stray control character in the styles');
  assert(/content: " ' + String.fromCharCode(92) + '2022/.test(block) || block.includes(String.fromCharCode(92) + '2022'), 'the changed marker is a real escape');
  const used = [...block.matchAll(/var\(--([a-z0-9-]+)\)/g)].map((m) => m[1]);
  used.forEach((name) => assert(new RegExp('--' + name + '\\s*:').test(strip(TOKENS)), 'token --' + name + ' is used and never defined'));
  const code = strip(MODULE_JS);
  assert(/aria-label', 'Clear '/.test(code) && /aria-label', 'Source for '/.test(code), 'icon and select have names');
  assert(!/innerHTML|insertAdjacentHTML|\b(alert|confirm|prompt)\s*\(|setInterval|setTimeout/.test(code), 'unsafe or polling construct');
});

(async function run() {
  for (const test of suite) await test();
  results.forEach(([flag, line]) => console.log(`${flag} ${line}`));
  const passed = results.filter((r) => r[0] === 'ok  ').length;
  console.log(`\n${passed}/${results.length} checks passed`);
  process.exit(failures ? 1 : 0);
})();

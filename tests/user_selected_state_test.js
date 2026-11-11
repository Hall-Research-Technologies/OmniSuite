// The selected state of the restricted User page's Routing | Matrix tabs and
// AV / Video / Audio buttons -- measured, not described.
//
// The defect: the selected control was painted with --selected-fill, which is a
// 12-16% tint by design (it tints table rows). On this page, which does not load
// the appearance module, that measured deltaE 8.1 in light mode and 9.9 in dark
// against the same control unselected. A selected control is FILLED.
//
// This is computed from the stylesheet and the preset table, so it fails when
// either changes. A real-browser measurement of the same element, toggled
// between states with :hover forced, across all ten presets in both modes, was
// taken alongside it and is recorded in the build notes; a headless test here
// cannot lay out CSS, so what it can prove is which tokens the rules name and
// what those tokens resolve to.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const read = (...p) => fs.readFileSync(path.join(ROOT, ...p), 'utf8').split('\r\n').join('\n');
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');

let failures = 0;
function check(name, fn) {
  try { fn(); console.log('ok   ' + name); }
  catch (err) { failures++; console.log('FAIL ' + name + ': ' + err.message); }
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }

const REMOTE = strip(read('ui', 'remote.css'));
const TEMPLATES = strip(read('ui', 'templates.css'));

// Every declaration block whose selector list contains exactly this selector.
function blocks(css, selector) {
  const out = [];
  for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const list = m[1].split(',').map((s) => s.trim().replace(/\s+/g, ' '));
    if (list.includes(selector)) out.push(m[2]);
  }
  return out;
}
function declared(css, selector, prop) {
  const values = [];
  for (const body of blocks(css, selector)) {
    for (const d of body.matchAll(new RegExp('(?:^|;|\\s)' + prop + '\\s*:\\s*([^;]+)', 'g'))) {
      values.push(d[1].trim());
    }
  }
  return values;
}

// ----- colour arithmetic --------------------------------------------------
const hex = (h) => { h = h.replace('#', ''); return [0, 2, 4].map((i) => parseInt(h.substr(i, 2), 16)); };
const lin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
const lum = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
const contrast = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
function lab(rgb) {
  const [r, g, b] = rgb.map(lin);
  const X = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047, Y = 0.2126 * r + 0.7152 * g + 0.0722 * b,
    Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883;
  const f = (t) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  return [116 * f(Y) - 16, 500 * (f(X) - f(Y)), 200 * (f(Y) - f(Z))];
}
const deltaE = (a, b) => { const A = lab(a), B = lab(b); return Math.hypot(A[0] - B[0], A[1] - B[1], A[2] - B[2]); };

// The presets and the readable-foreground rule come from the real module.
const appearance = fs.readFileSync(path.join(ROOT, 'ui', 'appearance.js'), 'utf8');
const el = () => ({ setAttribute() {}, removeAttribute() {}, classList: { toggle() {}, add() {}, remove() {} },
  style: { setProperty() {}, removeProperty() {} } });
const sandbox = { window: { addEventListener() {} }, location: { pathname: '/user', search: '' }, localStorage: { getItem() { return null; }, setItem() {} },
  document: { documentElement: el(), body: el(), addEventListener() {}, readyState: 'loading' },
  fetch: () => Promise.reject(new Error('no network in this test')) };
vm.createContext(sandbox);
vm.runInContext(appearance + '\nthis.__p = APPEARANCE_PRESETS; this.__t = readableTextOn;', sandbox);
const PRESETS = sandbox.__p;
const readableOn = sandbox.__t;

// The unselected surface in each palette, from the stylesheet's own defaults.
const darkSurface = hex(/--surface-elevated:\s*var\(--panel-2,\s*(#[0-9a-f]{6})\)/i.exec(TEMPLATES)[1]);
const lightSurface = hex(/--surface-elevated:\s*var\(--light-surface-elevated,\s*(#[0-9a-f]{6})\)/i.exec(TEMPLATES)[1]);

const SELECTED = [
  '.mode-tabs .tab[aria-selected="true"]',
  '.mx-modebtn.active',
];
const UNSELECTED = ['.mode-tabs .tab', '.mx-modebtn'];

for (const sel of SELECTED) {
  check(`${sel} is filled with the opaque accent, not the selection tint`, () => {
    const bg = declared(REMOTE, sel, 'background-color');
    assert(bg.length === 1 && bg[0] === 'var(--action-primary)',
      `background-color is ${JSON.stringify(bg)}; --selected-fill is a 12-16% tint`);
    const edge = declared(REMOTE, sel, 'border-color');
    assert(edge.length === 1 && edge[0] === 'var(--action-primary)', `border-color ${JSON.stringify(edge)}`);
  });
  check(`${sel} takes its label colour from the fill, never the accent`, () => {
    const colour = declared(REMOTE, sel, 'color');
    assert(colour.length === 1 && /^var\(--on-accent,\s*#[0-9a-f]{6}\)$/i.test(colour[0]),
      `color is ${JSON.stringify(colour)}`);
  });
}

for (const sel of UNSELECTED) {
  check(`${sel} keeps hover, press and keyboard focus`, () => {
    const hover = declared(REMOTE, sel + ':hover', 'background-image');
    const press = declared(REMOTE, sel + ':active', 'background-image');
    assert(hover.length === 1 && /control-hover-veil/.test(hover[0]), 'hover veil is gone');
    assert(press.length === 1 && /control-hover-veil/.test(press[0]), 'pressed state is gone');
    const ring = declared(REMOTE, sel + ':focus-visible', 'outline');
    assert(ring.length === 1 && /focus-ring/.test(ring[0]), 'focus-visible outline is gone');
  });
  check(`${sel} is not hovered with a brightness filter`, () => {
    for (const s of [sel, sel + ':hover', sel + ':active']) {
      assert(declared(REMOTE, s, 'filter').length === 0, `${s} uses filter`);
    }
  });
}

// The page does not load the appearance module, so with nothing applied the
// accent is the stylesheet's own default.
const DEFAULT_ACCENT = hex(/--action-primary:\s*var\(--accent,\s*(#[0-9a-f]{6})\)/i.exec(TEMPLATES)[1]);
const FALLBACK_LABEL = hex(/var\(--on-accent,\s*(#[0-9a-f]{6})\)/i.exec(declared(REMOTE, SELECTED[0], 'color')[0])[1]);

check('with no preset applied the selected tab differs from the unselected one by deltaE >= 25, in both modes', () => {
  const dark = deltaE(DEFAULT_ACCENT, darkSurface), light = deltaE(DEFAULT_ACCENT, lightSurface);
  assert(dark >= 25 && light >= 25, `dark ${dark.toFixed(1)}, light ${light.toFixed(1)}`);
});
check('the fallback label is readable on the default accent', () => {
  const ratio = contrast(FALLBACK_LABEL, DEFAULT_ACCENT);
  assert(ratio >= 4.5, `${ratio.toFixed(2)}:1`);
});

check('every preset, in both modes, separates the selected fill from the unselected surface', () => {
  assert(PRESETS.length === 10, `expected ten presets, found ${PRESETS.length}`);
  const weak = [];
  for (const p of PRESETS) {
    const accent = hex(p.colors.accent);
    for (const [mode, surface] of [['dark', darkSurface], ['light', lightSurface]]) {
      const d = deltaE(accent, surface);
      if (d < 25) weak.push(`${p.id}/${mode} ${d.toFixed(1)}`);
    }
  }
  assert(weak.length === 0, weak.join(', '));
});
check('every preset has a readable foreground on its accent fill', () => {
  const weak = [];
  for (const p of PRESETS) {
    const accent = hex(p.colors.accent);
    const ratio = contrast(hex(readableOn(p.colors.accent)), accent);
    if (ratio < 4.5) weak.push(`${p.id} ${ratio.toFixed(2)}`);
  }
  assert(weak.length === 0, weak.join(', '));
});

check('the state is announced as well as painted', () => {
  const shell = read('ui', 'user', 'user.js'), matrix = read('ui', 'user', 'matrix.js');
  assert(/setAttribute\('aria-selected', String\(chosen === id\)\)/.test(shell), 'tab aria-selected');
  assert(/setAttribute\('role', 'tab'\)/.test(shell), 'tab role');
  assert(/setAttribute\('aria-checked', String\(entry\[0\] === mode\)\)/.test(matrix), 'mode aria-checked');
  assert(/setAttribute\('role', 'radio'\)/.test(matrix), 'mode role');
});

check('no colour literal enters the selected rules except the one documented label fallback', () => {
  for (const sel of SELECTED) {
    for (const body of blocks(REMOTE, sel)) {
      const literals = body.match(/#[0-9a-f]{3,8}\b|rgba?\(/gi) || [];
      assert(literals.length <= 1, `${sel} carries ${literals.join(' ')}`);
    }
  }
});

if (failures) { console.log(failures + ' failed'); process.exit(1); }
console.log('user selected-state: all checks passed');

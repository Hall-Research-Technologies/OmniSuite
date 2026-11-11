/*
 * State-coloured TEXT stays readable in every preset, light and dark.
 *
 * A preset's green, amber, red and blue are fills. Set on the page by the preset
 * itself they measured 1.3:1 to 4.4:1 as text, and pale literals (#93c5fd, #86efac,
 * #fecaca, #fca5a5) were unreadable the moment the page went light. Text now takes
 * a derived colour: --state-*-text and --link-text, mixed toward white on a dark
 * page and toward black on a light one.
 *
 * This reads the mix percentages out of ui/templates.css and the palettes out of
 * ui/appearance.js, so the test follows what ships; it computes sRGB interpolation
 * (what color-mix(in srgb) does) and WCAG contrast. A check that cannot fail proves
 * nothing, so it first shows the raw preset colour DOES fail as text on a light
 * page -- the condition before the fix.
 *
 * Run: node tests/state_text_contrast_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');

const UI = path.join(__dirname, '..', 'ui');
const read = (rel) => fs.readFileSync(path.join(UI, rel), 'utf8').replace(/\r\n/g, '\n');

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures++; console.log(`  FAIL ${name}: ${err && err.message}`); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }

const hex = (h) => { const m = /^#([0-9a-f]{6})$/i.exec(h); assert(m, 'not a #rrggbb colour: ' + h);
  return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)); };
const mix = (a, pa, b) => a.map((v, i) => v * pa / 100 + b[i] * (100 - pa) / 100);
const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };

// ---- the palettes ----------------------------------------------------------
const appearanceSrc = read('appearance.js');
const start = appearanceSrc.indexOf('const APPEARANCE_PRESETS = [');
const end = appearanceSrc.indexOf('\n];', start);
assert(start > 0 && end > start, 'could not find the preset list');
const PRESETS = new Function(appearanceSrc.slice(start, end + 3).replace('const APPEARANCE_PRESETS =', 'return') )();
assert(PRESETS.length >= 10, 'expected the ten presets, found ' + PRESETS.length);

// ---- the token definitions -------------------------------------------------
const css = read('templates.css');
function block(selectorStart) {
  const i = css.indexOf(selectorStart);
  assert(i >= 0, 'missing block: ' + JSON.stringify(selectorStart));
  return css.slice(i, css.indexOf('}', i));
}
const DARK = block(':root,\nbody {\n  --state-success-text');
const LIGHT = block('html.light,\nbody.light {\n  --state-success-text');

function recipe(blockText, token) {
  const re = new RegExp('--' + token + ': color-mix\\(in srgb, var\\(--([a-z-]+)(?:, #1e90ff)?\\) (\\d+)%, (#[0-9a-f]{6}) (\\d+)%\\);');
  const m = re.exec(blockText);
  assert(m, 'no recipe for --' + token);
  assert(+m[2] + +m[4] === 100, `--${token} percentages sum to ${+m[2] + +m[4]}, not 100 (a mix under 100 turns translucent)`);
  return {source: m[1], pct: +m[2], toward: hex(m[3])};
}

// Surfaces the text sits on: the dark and light palettes in ui/index.html.
const SURFACES = {
  dark: ['#1d232a', '#222a33', '#262f39'].map(hex),
  light: ['#f7f8fb', '#ffffff', '#f2f5f8'].map(hex),
};
const ROLES = [['state-success-text', 'success'], ['state-warning-text', 'warning'],
               ['state-error-text', 'error'], ['state-info-text', 'info'], ['link-text', 'accent']];

check('the condition before the fix is real: a raw preset colour fails as text on a light page', () => {
  let failed = 0;
  PRESETS.forEach((p) => ['success', 'warning', 'error'].forEach((role) => {
    if (SURFACES.light.some((s) => ratio(hex(p.colors[role]), s) < 4.5)) failed++;
  }));
  assert(failed >= 10, 'expected raw preset colours to fail often on a light page, only ' + failed + ' did');
});

for (const [mode, blockText] of [['dark', DARK], ['light', LIGHT]]) {
  check(`every mix sums to 100 and is declared for ${mode} pages`, () => {
    ROLES.forEach(([token]) => recipe(blockText, token));
  });

  check(`state and link text reach 4.5:1 in all ${PRESETS.length} presets on ${mode} pages`, () => {
    const problems = [];
    PRESETS.forEach((p) => ROLES.forEach(([token, role]) => {
      const r = recipe(blockText, token);
      const text = mix(hex(p.colors[role]), r.pct, r.toward);
      SURFACES[mode].forEach((surface) => {
        const c = ratio(text, surface);
        if (c < 4.5) problems.push(`${p.id}/${token} ${c.toFixed(2)}`);
      });
    }));
    assert(!problems.length, problems.slice(0, 6).join('; ')
           + (problems.length > 6 ? ` (+${problems.length - 6} more)` : ''));
  });
}

// ---- nothing bypasses the tokens -------------------------------------------
const FILES = ['index.html', 'device-table.css', 'remote.css', 'settings.css', 'templates.css',
               'matrix/matrix.css', 'matrix/multiview.css', 'matrix/index.html', 'matrix/configure.html',
               'matrix/usb.html', 'matrix/multiview.html'].filter((f) => fs.existsSync(path.join(UI, f)));

check('no pale literal is used as a text colour', () => {
  const offenders = [];
  FILES.forEach((f) => read(f).split('\n').forEach((line, i) => {
    // A pill that paints its own solid background is a matched pair, not text on the page.
    if (/background\s*:\s*#[0-9a-f]{6}/i.test(line)) return;
    if (/[^-]color\s*:\s*#(93c5fd|86efac|fecaca|fca5a5|fde68a)\b/i.test(' ' + line)) offenders.push(`${f}:${i + 1}`);
  }));
  assert(!offenders.length, 'pale literal text colour at ' + offenders.join(', '));
});

check('state-coloured text uses the text tokens, never the raw fill token', () => {
  const offenders = [];
  FILES.forEach((f) => read(f).split('\n').forEach((line, i) => {
    if (/(^|[^-\w])color\s*:\s*var\(--state-(success|warning|error|info)\)/.test(line)) offenders.push(`${f}:${i + 1}`);
  }));
  assert(!offenders.length, 'raw state fill used as text at ' + offenders.join(', '));
});

check('the light block is not left to the dark values (restated for light pages)', () => {
  ROLES.forEach(([token]) => {
    const d = recipe(DARK, token), l = recipe(LIGHT, token);
    assert(d.toward.join() !== l.toward.join(), `--${token} mixes toward the same colour in both modes`);
  });
});

console.log(failures ? `\nstate text contrast: ${failures} failure(s)` : '\nstate text contrast test: all checks passed');
process.exit(failures ? 1 : 0);

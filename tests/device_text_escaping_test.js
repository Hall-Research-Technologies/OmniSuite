/*
 * Text a device reports is never markup.
 *
 * A rogue unit on the LAN chooses its own EDID names, input lists, link speed, USB type,
 * video-wall values and the body of its firmware-upload error. The tables are built with
 * innerHTML, so each of those reached the page as HTML and would have run script in the
 * administrator's session -- which, on the local listener, is trusted with everything.
 *
 * This lifts the real builder functions out of ui/matrix/matrix.js and ui/index.html and
 * feeds them hostile strings, then checks the markup that comes back. Structural checks
 * cover the assignments that build markup inline; comments are stripped first so a comment
 * that names the very call under test cannot satisfy them.
 *
 * Run: node tests/device_text_escaping_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.join(__dirname, '..');
const MATRIX = fs.readFileSync(path.join(ROOT, 'ui', 'matrix', 'matrix.js'), 'utf8');
const INDEX = fs.readFileSync(path.join(ROOT, 'ui', 'index.html'), 'utf8');
const SETTINGS = fs.readFileSync(path.join(ROOT, 'ui', 'settings.js'), 'utf8');

const failures = [];
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (err) { failures.push(`${name}: ${err.message}`); console.log(`  FAIL ${name}: ${err.message}`); }
}
function assert(c, m) { if (!c) throw new Error(m || 'assertion failed'); }

/** The source of `function name(...) { ... }`, by brace matching. */
function lift(source, name) {
  const start = source.search(new RegExp('\\bfunction\\s+' + name + '\\s*\\('));
  if (start < 0) throw new Error('function not found: ' + name);
  let depth = 0, i = source.indexOf('{', start);
  for (; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}' && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error('unbalanced: ' + name);
}
function load(source, names) {
  const ctx = vm.createContext({});
  vm.runInContext(names.map(n => lift(source, n)).join('\n'), ctx);
  return ctx;
}
function stripComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:\\'"`])\/\/[^\n]*/g, '$1');
}

const HOSTILE = [
  '"><img src=x onerror=alert(1)>',
  '<img src=x onerror=alert(1)>',
  "' onfocus='alert(1)' x='",
  '</option></select><script>alert(1)</script>',
];
/** No tag, and no attribute-breaking quote, survives from the hostile text. */
function inert(markup, label) {
  assert(!/<img|<script|<svg/i.test(markup), label + ': a tag came through: ' + markup);
  assert(!/"\s*(onerror|onfocus|onload)\s*=/i.test(markup), label + ': an attribute broke out: ' + markup);
}

// ---- ui/matrix/matrix.js ------------------------------------------------------------
const mx = load(MATRIX, ['escAttr', 'buildSelectOptions', 'buildInputSelectOptions',
                         'formatDecoderInputOptionLabel', 'formatEdgeModeLabel']);

for (const text of HOSTILE) {
  check('select options: ' + text.slice(0, 24), () => {
    inert(mx.buildSelectOptions([text, 'ok'], text, 'Loading'), 'option list');
    inert(mx.buildSelectOptions([], '', text), 'placeholder');
  });
  check('input options: ' + text.slice(0, 24), () => {
    inert(mx.buildInputSelectOptions([text], text, 'video', {}), 'input list');
    inert(mx.buildInputSelectOptions(['ip_input1'], 'ip_input1', 'video',
                                     { ip1_addr: text, ip1_port: text }), 'decoder address in a label');
  });
}
check('a normal option list is unchanged', () => {
  const html = mx.buildSelectOptions(['1920x1080', '3840x2160'], '1920x1080', '');
  assert(html.includes('<option value="1920x1080" selected>1920x1080</option>'), html);
  assert(html.includes('<option value="3840x2160" >3840x2160</option>'), html);
});

const matrixCode = stripComments(MATRIX);
check('video-wall and fast-switching values are escaped in their attributes', () => {
  const raw = matrixCode.match(/value="\$\{(?!escAttr)[^}]*(video_wall_[a-z_]+|fsmTimeout)[^}]*\}"/g);
  assert(!raw, 'unescaped value attribute(s): ' + (raw || []).join(' | '));
});
check('the edge-mode options are escaped', () => {
  assert(!/<option value="\$\{opt\}"/.test(matrixCode), 'an option value is interpolated raw');
  assert(!/\$\{formatEdgeModeLabel\(opt\)\}/.test(matrixCode), 'an edge-mode label is interpolated raw');
});
check('the routing grid does not interpolate an address raw', () => {
  assert(!/data-dec="\$\{d\.ip\}"/.test(matrixCode) && !/aria-label="Route video \$\{d\.ip\}/.test(matrixCode),
         'a raw address in the crosspoint cell');
});

// ---- ui/index.html ------------------------------------------------------------------
const ix = load(INDEX, ['escapeHtml', 'escapeAttr', 'linkspeedMarkup']);
for (const text of HOSTILE) {
  check('link speed: ' + text.slice(0, 24), () => inert(ix.linkspeedMarkup(text), 'link speed'));
}
check('a numeric link speed still reads as a number, and 1000 is the good colour', () => {
  assert(ix.linkspeedMarkup(1000) === '<span class="linkspeed linkspeed-ok" data-sort="1000">1000 Mbps</span>', ix.linkspeedMarkup(1000));
  assert(ix.linkspeedMarkup('100') === '<span class="linkspeed linkspeed-slow" data-sort="100">100 Mbps</span>', ix.linkspeedMarkup('100'));
});

const indexCode = stripComments(INDEX);
check('the upgrade status text is escaped wherever it is put into a cell', () => {
  assert(!/<span class="status \$\{cls\}">\$\{text\}<\/span>/.test(indexCode), 'setStatus writes raw text');
  assert(!/\$\{activeUpgrade\.text\}/.test(indexCode), 'the active status is re-injected raw');
  assert(!/\$\{cached\.statusText\}/.test(indexCode), 'the cached status is re-injected raw');
});
check('the USB role label is escaped', () => {
  assert(!/usb-pill">\$\{label\}</.test(indexCode), 'a device-reported USB type is written raw');
});
check('no link speed is built inline any more', () => {
  assert(!/<span style="color:[^>]*>\$\{(ls|u\.linkspeed|display)\} Mbps/.test(indexCode), 'a link speed is interpolated raw');
});
check('upgrade and scan errors are written as text', () => {
  assert(!/#scan_status'\)\.innerHTML = [`'](❌|❌)/.test(indexCode), 'an error is assigned to innerHTML');
});
check('an address is never put into an inline handler unless it is a plain IPv4 literal', () => {
  assert(/isEncoder && \/\^\\d\{1,3\}/.test(indexCode), 'previewUrl is not gated on an IPv4 literal');
});

// ---- ui/settings.js -----------------------------------------------------------------
check('the folder browser shows an error as text', () => {
  assert(!/innerHTML\s*=\s*'<div style="color:var\(--danger\)[^;]*\(message/.test(stripComments(SETTINGS)),
         'an error message is concatenated into markup');
});

console.log(failures.length ? `\n${failures.length} FAILED` : '\ndevice text escaping test: all checks passed');
process.exit(failures.length ? 1 : 0);

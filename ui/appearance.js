/*
 * OmniSuite appearance: layout, palette preset, and semantic colour overrides.
 *
 * Three independent dimensions, deliberately not folded into one another:
 *
 *   layout    classic | compact | modern | workspace   (information architecture)
 *   mode      light | dark                              (appearance)
 *   colours   a preset, optionally overridden per semantic role
 *
 * Four layouts times two modes is eight combinations, not eight templates, and a
 * colour choice applies to all of them. Nothing here touches routing, discovery
 * or device state.
 *
 * Persistence has two tiers on purpose: localStorage is read synchronously
 * before paint so the first frame is already right, and the server is the
 * authority that carries the choice to another browser and across a restart.
 */
'use strict';

const UI_TEMPLATES = [
  {id: 'classic', name: 'Classic', hint: 'The familiar OmniSuite layout'},
  {id: 'compact', name: 'Compact', hint: 'Denser tables, quieter controls'},
  {id: 'modern', name: 'Modern', hint: 'Grouped cards, clearer hierarchy'},
  // The rail and the inspector were removed; the hint went on describing them
  // long afterwards, so the dialog advertised two features the layout no
  // longer had. What Workspace actually is: a centred work area with a
  // maximum width, and slightly tighter spacing.
  {id: 'workspace', name: 'Workspace', hint: 'Constrained, centred work area'},
];
const UI_TEMPLATE_DEFAULT = 'classic';

// The semantic roles a preset defines. These are internal: presets set them, and
// the operator chooses a preset rather than nine separate colours. The token
// architecture stays because it is what lets one choice reach every layout; what
// went away is asking an operator to make nine decisions to get a palette.
const APPEARANCE_COLORS = [
  {id: 'accent', name: 'Accent', hint: 'Primary buttons and links',
   tokens: ['--accent', '--action-primary']},
  {id: 'selection', name: 'Selection', hint: 'Selected rows and active routes',
   tokens: ['--state-selection']},
  {id: 'success', name: 'Online', hint: 'Healthy and linked states',
   tokens: ['--state-success']},
  {id: 'warning', name: 'Warning', hint: 'Advisories worth noticing',
   tokens: ['--state-warning', '--multi-switch-warning-accent']},
  {id: 'error', name: 'Critical', hint: 'Failures and offline devices',
   tokens: ['--state-error', '--danger']},
  {id: 'info', name: 'Information', hint: 'Neutral informational highlights',
   tokens: ['--state-info']},
  {id: 'daisy', name: 'Daisy chain', hint: 'Devices reached through another device',
   tokens: ['--daisy-topology-border', '--daisy-topology-fg']},
  {id: 'minority', name: 'Other switch', hint: 'Devices on a different switch',
   tokens: ['--alternate-switch-border', '--alternate-switch-fg']},
  {id: 'surface', name: 'Panel accent', hint: 'Panel edges and dividers',
   tokens: ['--edge', '--border']},
];

// Presets are complete palettes, not a single hue. "Warm" is the softer
// aesthetic: rose, plum and warm neutrals, chosen to stay legible and
// professional rather than to signal anything about who is using it.
// Ten palettes, each a complete set: a preset that only changed the accent would
// leave the states looking like the previous one. Semantic meaning is held
// constant across all of them -- critical always reads as critical, online always
// reads as healthy -- so a palette changes how the application looks and never
// what a colour means.
//
// The softer options are named for their colours. A palette is not a
// demographic, and nothing here says who should pick one.
const APPEARANCE_PRESETS = [
  {id: 'default', name: 'Default', hint: 'OmniSuite blue',
   swatch: ['#1e90ff', '#4ade80', '#eab308', '#e5534b'],
   colors: {accent: '#1e90ff', selection: '#1e90ff', success: '#4ade80', warning: '#eab308',
            error: '#e5534b', info: '#38bdf8', daisy: '#2dd4bf', minority: '#818cf8',
            surface: '#4a5561'}},
  {id: 'slate', name: 'Slate', hint: 'Neutral corporate grey',
   swatch: ['#5b7186', '#4d9375', '#b58b3c', '#b8524f'],
   colors: {accent: '#5b7186', selection: '#7c94aa', success: '#4d9375', warning: '#b58b3c',
            error: '#b8524f', info: '#6b8fa8', daisy: '#5f9c9c', minority: '#7b83a8',
            surface: '#4a5561'}},
  {id: 'cool', name: 'Cool', hint: 'Teal and slate',
   swatch: ['#0e9f9f', '#10b981', '#d9a441', '#e05252'],
   colors: {accent: '#0e9f9f', selection: '#14b8a6', success: '#10b981', warning: '#d9a441',
            error: '#e05252', info: '#38bdf8', daisy: '#2dd4bf', minority: '#818cf8',
            surface: '#3f5560'}},
  {id: 'ocean', name: 'Ocean', hint: 'Deep blue and cyan',
   swatch: ['#2563eb', '#06b6d4', '#e0a63a', '#dc4c4c'],
   colors: {accent: '#2563eb', selection: '#3b82f6', success: '#0891b2', warning: '#e0a63a',
            error: '#dc4c4c', info: '#06b6d4', daisy: '#22b8cf', minority: '#6366f1',
            surface: '#3c4f6b'}},
  {id: 'forest', name: 'Forest', hint: 'Green and moss',
   swatch: ['#3f8f5f', '#5aa469', '#c99b3f', '#c0524a'],
   colors: {accent: '#3f8f5f', selection: '#5aa469', success: '#4c9a5a', warning: '#c99b3f',
            error: '#c0524a', info: '#5f9e8f', daisy: '#4fa88c', minority: '#7d93b5',
            surface: '#4a5a4d'}},
  {id: 'warm', name: 'Warm', hint: 'Amber and clay',
   swatch: ['#c07a3e', '#7fa05a', '#d99a5b', '#c0524a'],
   colors: {accent: '#c07a3e', selection: '#d09a63', success: '#7fa05a', warning: '#d99a5b',
            error: '#c0524a', info: '#a8875f', daisy: '#b58f6b', minority: '#a08a9e',
            surface: '#665549'}},
  {id: 'plum', name: 'Plum', hint: 'Violet and aubergine',
   swatch: ['#8b5cb8', '#5f9e7d', '#d0a04e', '#c0526b'],
   colors: {accent: '#8b5cb8', selection: '#a77fcc', success: '#5f9e7d', warning: '#d0a04e',
            error: '#c0526b', info: '#8f7bb5', daisy: '#a67fb5', minority: '#7d8fc4',
            surface: '#5b4a66'}},
  {id: 'rose', name: 'Rose', hint: 'Rose and warm neutrals',
   swatch: ['#b4587a', '#5f9e7d', '#d99a5b', '#c0526b'],
   colors: {accent: '#b4587a', selection: '#c88ba6', success: '#5f9e7d', warning: '#d99a5b',
            error: '#c0526b', info: '#8f7bb5', daisy: '#b07f9e', minority: '#8f7bb5',
            surface: '#6b5560'}},
  {id: 'soft', name: 'Soft', hint: 'Muted, low contrast',
   swatch: ['#6b8fb5', '#7fa88c', '#c4a875', '#b58080'],
   colors: {accent: '#6b8fb5', selection: '#8fa9c4', success: '#7fa88c', warning: '#c4a875',
            error: '#b58080', info: '#8fa9c4', daisy: '#8fb5ad', minority: '#9a94bd',
            surface: '#5a6570'}},
  {id: 'contrast', name: 'High contrast', hint: 'Maximum legibility',
   swatch: ['#0a84ff', '#00b34a', '#ffb000', '#ff3b30'],
   colors: {accent: '#0a84ff', selection: '#0a84ff', success: '#00b34a', warning: '#ffb000',
            error: '#ff3b30', info: '#00c2d1', daisy: '#00d1b8', minority: '#7d7dff',
            surface: '#9aa7b4'}},
];
const APPEARANCE_PRESET_DEFAULT = 'default';

// The one colour the operator sets by hand: the light-mode application
// background. Panels, table headers and borders are derived from it in CSS so
// they keep their separation instead of the whole page becoming one flat colour.
// It is a light-mode value and never reaches dark mode.
const LIGHT_BACKGROUND_DEFAULT = '#f7f8fb';

const APPEARANCE_KEY = 'omniAppearance';
const HEX = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

function isKnownTemplate(id) {
  return UI_TEMPLATES.some(template => template.id === id);
}

// Nothing that is not a hex colour ever reaches a stylesheet. A custom property
// is a CSS injection point, so the value is validated rather than escaped.
function isValidColor(value) {
  return typeof value === 'string' && HEX.test(value.trim());
}

function normalizeColor(value) {
  if (!isValidColor(value)) return null;
  let hex = value.trim().toLowerCase();
  if (hex.length === 4) hex = '#' + [...hex.slice(1)].map(c => c + c).join('');
  return hex;
}

// Relative luminance, so text placed on a chosen colour is legible without the
// operator having to think about it. The colour they picked is kept; what is
// chosen for them is only whether what sits on top is light or dark.
function relativeLuminance(hex) {
  const value = normalizeColor(hex);
  if (!value) return 0;
  const channel = n => {
    const c = parseInt(value.substr(n, 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}

function contrastRatio(a, b) {
  const la = relativeLuminance(a), lb = relativeLuminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

function readableTextOn(hex) {
  return contrastRatio(hex, '#ffffff') >= contrastRatio(hex, '#101418') ? '#ffffff' : '#101418';
}

// Is this colour usable as a solid fill behind text at all? Reported, not
// enforced: the operator is told, and the colour is still theirs.
function colorReadability(hex) {
  const onIt = readableTextOn(hex);
  const ratio = contrastRatio(hex, onIt);
  return {textOn: onIt, ratio: Math.round(ratio * 100) / 100, usable: ratio >= 4.5};
}

function currentPageKey() {
  const path = location.pathname;
  if (path.startsWith('/matrix/usb')) return 'usb';
  if (path.startsWith('/matrix/configure')) return 'configure';
  if (path === '/matrix') return 'av';
  return 'devices';
}

const appearance = {
  template: UI_TEMPLATE_DEFAULT,
  preset: APPEARANCE_PRESET_DEFAULT,
  lightBackground: LIGHT_BACKGROUND_DEFAULT,
};

function presetById(id) {
  return APPEARANCE_PRESETS.find(preset => preset.id === id) || APPEARANCE_PRESETS[0];
}

// The colours in force: entirely the preset's. Per-role overrides were removed
// as an operator-facing idea; the roles remain because they are how one choice
// reaches every layout.
function effectiveColors(state) {
  return {...presetById((state || appearance).preset).colors};
}

function applyAppearance(state) {
  const next = state || appearance;
  appearance.template = isKnownTemplate(next.template) ? next.template : UI_TEMPLATE_DEFAULT;
  appearance.preset = presetById(next.preset).id;
  appearance.lightBackground = normalizeColor(next.lightBackground) || LIGHT_BACKGROUND_DEFAULT;

  const root = document.documentElement;
  root.setAttribute('data-template', appearance.template);
  root.setAttribute('data-preset', appearance.preset);
  // Which page this is, so a layout can give the right content the width it
  // needs. An attribute, not a DOM rearrangement: nothing is moved, so there is
  // exactly one of every element and nothing to leave behind.
  root.setAttribute('data-page', currentPageKey());

  // Applied to <body> as well as <html>. The light palette is a class on <body>
  // that re-declares several of these tokens, and a class rule on an element
  // beats an inherited value from its parent -- so a root-level override alone
  // reaches everything except light mode, which is where it would be noticed.
  const hosts = [root, document.body].filter(Boolean);
  // Clear every role first, so removing an override actually removes it rather
  // than leaving the last value stuck on the element.
  hosts.forEach(host => APPEARANCE_COLORS.forEach(entry => {
    entry.tokens.forEach(token => host.style.removeProperty(token));
    host.style.removeProperty(`--on-${entry.id}`);
  }));
  hosts.forEach(host => host.style.setProperty('--light-bg', appearance.lightBackground));
  // The light palette is a class on <body>, but the page canvas is painted from
  // <html>, which that class never reaches -- so light mode left a dark strip
  // around the page. Mirroring the class onto the root element lets the palette
  // own the whole viewport. Each page's own theme toggle stays the source of
  // truth; this only follows it.
  if (document.body) {
    root.classList.toggle('light', document.body.classList.contains('light'));
    // Density is what the Compact layout means, and every page's density rules
    // are written as `body.compact`. Mirroring the template here is what makes
    // Compact reach all of them: this bridge used to live in index.html's own
    // initDensity(), so it existed on exactly one page and the three Matrix
    // pages ignored the layout entirely -- visible as the header changing
    // height, and the Settings icon moving, whenever you left Device Info.
    document.body.classList.toggle('compact', appearance.template === 'compact');
  }
  const colours = effectiveColors(appearance);
  Object.entries(colours).forEach(([role, value]) => {
    const entry = APPEARANCE_COLORS.find(item => item.id === role);
    if (!entry) return;
    hosts.forEach(host => {
      entry.tokens.forEach(token => host.style.setProperty(token, value));
      // A matching foreground travels with the colour, so a button never ends up
      // with unreadable text because of a palette choice.
      host.style.setProperty(`--on-${entry.id}`, readableTextOn(value));
    });
  });
  return appearance;
}

function readStoredAppearance() {
  try {
    const raw = JSON.parse(localStorage.getItem(APPEARANCE_KEY) || 'null');
    if (raw && typeof raw === 'object') {
      // A previous build stored per-role overrides. Those controls are gone, so
      // the values are ignored here and dropped on the next save; leaving them
      // applied would tint the UI from a control that no longer exists.
      const {colors, ...kept} = raw;
      return kept;
    }
  } catch (err) { /* storage disabled or corrupt: defaults are still a valid page */ }
  try {
    // Carried over from when the layout was the only stored preference, and from
    // the separate density control the layout selector replaced.
    const template = localStorage.getItem('uiTemplate');
    const density = localStorage.getItem('viewDensity');
    if (isKnownTemplate(template)) return {template};
    if (density === 'compact') return {template: 'compact'};
  } catch (err) { /* as above */ }
  return {};
}

function storeAppearance() {
  try {
    localStorage.setItem(APPEARANCE_KEY, JSON.stringify({
      template: appearance.template, preset: appearance.preset,
      lightBackground: appearance.lightBackground,
    }));
    localStorage.setItem('uiTemplate', appearance.template);
  } catch (err) { /* not fatal */ }
}

// Applied before anything renders. <body> does not exist yet at that point, so
// the same values are re-applied to it as soon as it does.
applyAppearance(readStoredAppearance());
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => applyAppearance(appearance));
}

// Reconcile with the server on every page, not only where the Settings dialog
// happens to live. Without this, a preference set in another browser -- or any
// page loaded after local storage was cleared -- kept the default layout on
// Configure, the Matrix and the USB Matrix while Device Info showed the real
// choice. The fast path stays local; this is the authority catching up.
function reconcileAppearance() {
  if (typeof loadAppearance === 'function') loadAppearance().catch(() => {});
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', reconcileAppearance);
} else {
  reconcileAppearance();
}

async function loadAppearance() {
  try {
    // A non-2xx reply is an error page, not preferences. Parsing it and acting
    // on whatever it happens to contain is how a failed request could repaint
    // the application from a body that never described an appearance.
    const res = await fetch('/api/ui_preferences');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const prefs = await res.json();
    if (prefs && prefs.ok) {
      applyAppearance({template: prefs.template, preset: prefs.preset,
                       lightBackground: prefs.light_background});
      storeAppearance();
    }
  } catch (err) {
    console.error('Appearance preferences unavailable:', err);
  }
  return appearance;
}

async function saveAppearance(partial) {
  applyAppearance({...appearance, ...(partial || {})});
  storeAppearance();
  try {
    // The status is the only evidence the preference was stored. Ignoring it
    // reported a rejected save as a successful one, so the choice reverted on
    // the next page load with nothing logged to say why.
    const res = await fetch('/api/ui_preferences', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({template: appearance.template, preset: appearance.preset,
                            light_background: appearance.lightBackground}),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    console.error('Could not save appearance:', err);
  }
  return appearance;
}

const setUiTemplate = template => saveAppearance({template});
const setAppearancePreset = preset => saveAppearance({preset});
const setLightBackground = value => {
  const colour = normalizeColor(value);
  return colour ? saveAppearance({lightBackground: colour}) : Promise.resolve(appearance);
};

// Appearance only. Discovered units, routes, scan settings, USB state, topology
// acknowledgements and every credential are not appearance and are untouched.
async function resetUiPreferences() {
  try {
    const res = await fetch('/api/ui_preferences/reset', {method: 'POST'});
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    console.error('Could not reset appearance:', err);
  }
  try {
    localStorage.removeItem(APPEARANCE_KEY);
    localStorage.removeItem('uiTemplate');
    localStorage.removeItem('viewDensity');
    // Dark is the application default, so an appearance reset returns to it.
    localStorage.setItem('dark', 'true');
  } catch (err) { /* not fatal */ }
  document.body.classList.remove('light', 'compact');
  applyAppearance({template: UI_TEMPLATE_DEFAULT, preset: APPEARANCE_PRESET_DEFAULT,
                   lightBackground: LIGHT_BACKGROUND_DEFAULT});
  return appearance;
}

function renderTemplateChoice(host, onChange) {
  if (!host) return;
  host.className = 'template-choice';
  host.innerHTML = UI_TEMPLATES.map(template => `
    <button type="button" data-template-id="${template.id}"
            aria-pressed="${template.id === appearance.template}">
      <span class="template-name">${template.name}</span>
      <span class="template-hint">${template.hint}</span>
    </button>`).join('');
  host.querySelectorAll('button[data-template-id]').forEach(button => {
    button.addEventListener('click', async () => {
      await setUiTemplate(button.getAttribute('data-template-id'));
      host.querySelectorAll('button[data-template-id]').forEach(other => {
        other.setAttribute('aria-pressed', String(other.getAttribute('data-template-id') === appearance.template));
      });
      if (typeof onChange === 'function') onChange(appearance.template);
    });
  });
}

function renderPresetChoice(host, onChange) {
  if (!host) return;
  host.className = 'preset-choice';
  host.innerHTML = APPEARANCE_PRESETS.map(preset => `
    <button type="button" data-preset-id="${preset.id}" title="${preset.hint}"
            aria-pressed="${preset.id === appearance.preset}">
      <span class="preset-swatches">${preset.swatch
        .map(colour => `<i style="background:${colour}"></i>`).join('')}</span>
      <span class="preset-name">${preset.name}</span>
    </button>`).join('');
  host.querySelectorAll('button[data-preset-id]').forEach(button => {
    const id = button.getAttribute('data-preset-id');
    // Previewing on hover costs nothing and is the quickest way to understand a
    // palette; leaving restores whatever is actually selected.
    button.addEventListener('mouseenter', () => applyAppearance({...appearance, preset: id}));
    button.addEventListener('mouseleave', () => applyAppearance(appearance));
    button.addEventListener('click', async () => {
      await setAppearancePreset(id);
      renderPresetChoice(host, onChange);
      if (typeof onChange === 'function') onChange(appearance.preset);
    });
  });
}

// One picker. It sets the light-mode application background; the surfaces around
// it are derived in CSS so panels and headers keep their separation.
function renderLightBackground(host, onChange) {
  if (!host) return;
  const isLight = document.body.classList.contains('light');
  host.className = 'light-bg-choice';
  host.innerHTML = `
    <input type="color" id="cfg_light_bg_picker" value="${appearance.lightBackground}"
           aria-label="Light mode background">
    <input type="text" id="cfg_light_bg_hex" class="color-hex" spellcheck="false"
           value="${appearance.lightBackground}" aria-label="Light mode background hex value">
    <span class="light-bg-note">${isLight
      ? 'Applies to Light theme.'
      : 'Saved for Light theme. Dark is unaffected.'}</span>`;
  const picker = host.querySelector('#cfg_light_bg_picker');
  const hex = host.querySelector('#cfg_light_bg_hex');
  const preview = value => {
    const colour = normalizeColor(value);
    if (colour) applyAppearance({...appearance, lightBackground: colour});
  };
  const commit = async value => {
    if (!isValidColor(value)) {
      const note = host.querySelector('.light-bg-note');
      if (note) note.textContent = 'Not a colour. Use a hex value such as #eef2f5.';
      applyAppearance(appearance);
      return;
    }
    await setLightBackground(value);
    renderLightBackground(host, onChange);
    if (typeof onChange === 'function') onChange(appearance.lightBackground);
  };
  picker.addEventListener('input', () => preview(picker.value));
  picker.addEventListener('change', () => commit(picker.value));
  hex.addEventListener('change', () => commit(hex.value.trim()));
}

// The theme can be toggled at any time by the page's own control; follow it.
function watchThemeClass() {
  if (!document.body) return;
  const sync = () => document.documentElement.classList.toggle(
    'light', document.body.classList.contains('light'));
  sync();
  new MutationObserver(sync).observe(document.body, {attributes: true, attributeFilter: ['class']});
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', watchThemeClass);
} else {
  watchThemeClass();
}

window.addEventListener('storage', event => {
  if (event.key === APPEARANCE_KEY || event.key === 'uiTemplate') {
    applyAppearance(readStoredAppearance());
  }
});

if (typeof globalThis !== 'undefined') {
  Object.assign(globalThis, {
    UI_TEMPLATES, UI_TEMPLATE_DEFAULT, APPEARANCE_COLORS, APPEARANCE_PRESETS,
    APPEARANCE_PRESET_DEFAULT, appearance, isKnownTemplate, isValidColor, normalizeColor,
    relativeLuminance, contrastRatio, readableTextOn, colorReadability, effectiveColors,
    applyAppearance, readStoredAppearance, loadAppearance, saveAppearance, setUiTemplate,
    setAppearancePreset, setLightBackground, resetUiPreferences, renderTemplateChoice,
    renderPresetChoice, renderLightBackground, LIGHT_BACKGROUND_DEFAULT, currentPageKey,
    // Kept so existing callers of the previous module keep working.
    uiTemplateIsKnown: isKnownTemplate, loadUiTemplate: loadAppearance,
  });
}

document.addEventListener('DOMContentLoaded', function() {
  // Reload Cache Button Handler
  const reloadBtn = document.getElementById('reloadCacheBtn');
  if (reloadBtn) {
    reloadBtn.addEventListener('click', async function() {
      try {
        const res = await fetch('/api/reload_cache', {method:'POST'});
        const data = await res.json();
        if (data.ok) {
          toast('Cache reloaded from file', true);
          await refresh();
        } else {
          toast('Reload failed: ' + (data.error || 'Unknown error'), false);
        }
      } catch (e) {
        toast('Reload failed: ' + e.message, false);
      }
    });
  }
  const encBulkConfigExportBtn = document.getElementById('encBulkConfigExportBtn');
  if (encBulkConfigExportBtn) {
    encBulkConfigExportBtn.addEventListener('click', () => {
      window.location = '/api/unit_config/export_bulk?role=encoders';
    });
  }
  const decBulkConfigExportBtn = document.getElementById('decBulkConfigExportBtn');
  if (decBulkConfigExportBtn) {
    decBulkConfigExportBtn.addEventListener('click', () => {
      window.location = '/api/unit_config/export_bulk?role=decoders';
    });
  }
});
const qs = (s)=>document.querySelector(s);
let routeMode = 'av';
let previewEnabled = true;
let activeCodecSelectIp = '';

let _matrixPreviewLoadingUrl = null;
let _matrixPreviewRefreshInterval = null;

function _ensureMatrixHoverPreview(){
  let box = document.getElementById('matrix_hover_preview');
  if (box) return box;
  box = document.createElement('div');
  box.id = 'matrix_hover_preview';
  box.className = 'hover-preview';
  box.innerHTML = '<img id="matrix_hover_preview_img" alt="preview"/>';
  document.body.appendChild(box);
  return box;
}

function _refreshMatrixPreviewImage(url){
  const img = document.getElementById('matrix_hover_preview_img');
  if (!img || _matrixPreviewLoadingUrl !== url) return;
  const freshUrl = url + '?t=' + Date.now();
  const tempImg = new Image();
  tempImg.onload = () => {
    if (_matrixPreviewLoadingUrl === url) {
      img.src = freshUrl;
      img.style.opacity = '1';
    }
  };
  tempImg.onerror = () => {};
  tempImg.src = freshUrl;
}

function showMatrixHoverPreview(el, evt){
  if (!previewEnabled) return;
  const url = el.getAttribute('data-preview-url');
  if (!url) return;

  const box = _ensureMatrixHoverPreview();
  const img = document.getElementById('matrix_hover_preview_img');
  _matrixPreviewLoadingUrl = url;

  if (_matrixPreviewRefreshInterval) {
    clearInterval(_matrixPreviewRefreshInterval);
    _matrixPreviewRefreshInterval = null;
  }

  img.src = '';
  img.style.opacity = '0.5';
  box.style.display = 'block';

  const rect = el.getBoundingClientRect();
  box.style.right = (window.innerWidth - rect.left + 12) + 'px';
  box.style.left = 'auto';
  box.style.top = Math.max(10, rect.top - 40) + 'px';

  _refreshMatrixPreviewImage(url);
  _matrixPreviewRefreshInterval = setInterval(() => _refreshMatrixPreviewImage(url), 2000);
}

function hideMatrixHoverPreview(){
  const box = document.getElementById('matrix_hover_preview');
  if (box) box.style.display = 'none';
  _matrixPreviewLoadingUrl = null;
  if (_matrixPreviewRefreshInterval) {
    clearInterval(_matrixPreviewRefreshInterval);
    _matrixPreviewRefreshInterval = null;
  }
}

// ===== Preview Toggle =====
function initPreviewToggle(){
  const previewSwitch = document.getElementById('preview_switch');
  const previewToggle = document.getElementById('preview_toggle');

  const applyPreview = (enabled)=>{
    previewEnabled = enabled;
    if(previewSwitch) previewSwitch.classList.toggle('on', enabled);
    if(previewToggle) previewToggle.checked = enabled;
    if(!enabled) hideMatrixHoverPreview();
  };

  applyPreview(localStorage.getItem('matrixPreviewEnabled') !== 'false');

  const label = document.getElementById('preview_toggle_label');
  if(label){
    const toggle = ()=>{
      const nextEnabled = !previewEnabled;
      applyPreview(nextEnabled);
      localStorage.setItem('matrixPreviewEnabled', nextEnabled.toString());
    };
    label.addEventListener('click', (e)=>{ e.preventDefault(); toggle(); });
    if(previewToggle) previewToggle.addEventListener('change', ()=> toggle());
    if(previewSwitch) previewSwitch.addEventListener('click', (e)=>{ e.preventDefault(); toggle(); });
  }

  window.addEventListener('storage', (e)=>{
    if(e.key === 'matrixPreviewEnabled'){
      applyPreview(e.newValue !== 'false');
    }
  });
}

// ===== Sticky Headers Toggle =====
function initStickyHeaders(){
  const stickySwitch = document.getElementById('sticky_switch');
  const stickyToggle = document.getElementById('sticky_headers_toggle');
  const matrixTable = document.getElementById('matrix');

  const applySticky = (isSticky)=>{
    if(matrixTable) matrixTable.classList.toggle('sticky-enabled', isSticky);
    if(stickySwitch) stickySwitch.classList.toggle('on', isSticky);
    if(stickyToggle) stickyToggle.checked = isSticky;
  };

  applySticky(localStorage.getItem('stickyHeaders') === 'true');

  const label = document.getElementById('sticky_headers_label');
  if(label){
    const toggle = ()=>{
      const nowSticky = matrixTable && matrixTable.classList.contains('sticky-enabled');
      const nextSticky = !nowSticky;
      applySticky(nextSticky);
      localStorage.setItem('stickyHeaders', nextSticky.toString());
    };
    label.addEventListener('click', (e)=>{ e.preventDefault(); toggle(); });
    if(stickyToggle) stickyToggle.addEventListener('change', ()=> toggle());
    if(stickySwitch) stickySwitch.addEventListener('click', (e)=>{ e.preventDefault(); toggle(); });
  }

  // Sync with other tabs/pages
  window.addEventListener('storage', (e)=>{
    if(e.key === 'stickyHeaders'){
      applySticky(e.newValue === 'true');
    }
  });
}

// ===== Theme Toggle =====
function initTheme(){
  const darkSwitch = document.getElementById('dark_switch');
  const darkToggle = document.getElementById('dark_mode_toggle');

  const applyTheme = (isDark)=>{
    document.body.classList.toggle('light', !isDark);
    if(darkSwitch) darkSwitch.classList.toggle('on', isDark);
    if(darkToggle) darkToggle.checked = isDark;
  };

  applyTheme(localStorage.getItem('dark') !== 'false');

  const label = document.getElementById('header_dark_label');
  if(label){
    const toggle = ()=>{
      const nowDark = !document.body.classList.contains('light');
      const nextDark = !nowDark;
      applyTheme(nextDark);
      localStorage.setItem('dark', nextDark.toString());
    };
    label.addEventListener('click', (e)=>{ e.preventDefault(); toggle(); });
    if(darkToggle) darkToggle.addEventListener('change', ()=> toggle());
    if(darkSwitch) darkSwitch.addEventListener('click', (e)=>{ e.preventDefault(); toggle(); });
  }

  // Sync with other tabs/pages
  window.addEventListener('storage', (e)=>{
    if(e.key === 'dark'){
      applyTheme(e.newValue !== 'false');
    }
  });
}

// ===== Density Toggle =====
function initDensity(){
  const densitySwitch = document.getElementById('density_switch');
  const densityToggle = document.getElementById('density_toggle');

  const applyDensity = (isCompact)=>{
    document.body.classList.toggle('compact', isCompact);
    if(densitySwitch) densitySwitch.classList.toggle('on', isCompact);
    if(densityToggle) densityToggle.checked = isCompact;
  };

  applyDensity(localStorage.getItem('viewDensity') === 'compact');

  const label = document.getElementById('density_toggle_label');
  if(label){
    const toggle = ()=>{
      const nextCompact = !document.body.classList.contains('compact');
      applyDensity(nextCompact);
      localStorage.setItem('viewDensity', nextCompact ? 'compact' : 'comfortable');
    };
    label.addEventListener('click', (e)=>{ e.preventDefault(); toggle(); });
    if(densityToggle) densityToggle.addEventListener('change', ()=> toggle());
    if(densitySwitch) densitySwitch.addEventListener('click', (e)=>{ e.preventDefault(); toggle(); });
  }

  window.addEventListener('storage', (e)=>{
    if(e.key === 'viewDensity'){
      applyDensity(e.newValue === 'compact');
    }
  });
}

function setMode(m){
  routeMode = m;
  document.querySelectorAll('.modebtn').forEach(b=>b.classList.toggle('active', b.dataset.mode===m));
  const modeLabel = document.getElementById('mode_label');
  if(modeLabel){
    const upper = (m||'').toUpperCase();
    const label = upper==='AV' ? 'AV (Audio + Video)' : (upper==='VIDEO' ? 'Video Only' : 'Audio Only');
    modeLabel.textContent = label;
  }
}
document.addEventListener('click', (e)=>{
  const b = e.target.closest('.modebtn'); if(!b) return;
  setMode(b.dataset.mode);
});

async function getJSON(u){
  const r = await fetch(u); if(!r.ok) throw new Error(await r.text()); return r.json();
}
async function postJSON(u, body){
  const r = await fetch(u, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const t = await r.text();
  if(!r.ok){
    try {
      const data = JSON.parse(t);
      throw new Error(data.error || data.message || t || r.statusText);
    } catch(parseErr) {
      if(parseErr instanceof SyntaxError) throw new Error(t || r.statusText);
      throw parseErr;
    }
  }
  try { return JSON.parse(t);} catch { return {ok:false, error:t}; }
}

function toast(msg, good=false){
  const el = document.createElement('div');
  el.className = 'toast'+(good?' ok':'');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(()=>el.classList.add('show'),10);
  setTimeout(()=>el.classList.remove('show'), 1800);
  setTimeout(()=>el.remove(), 2200);
}

const liveWriteQueues = new Map();
const liveWriteLatest = new Map();
const liveWritePrevious = new Map();

function liveWriteKey(kind, ip, field){
  return `${kind}:${ip}:${field}`;
}

function isLiveFieldPending(kind, ip, field){
  return liveWriteLatest.has(liveWriteKey(kind, ip, field));
}

function markLiveControl(control, state){
  if(!control) return;
  control.classList.toggle('pending-write', state === 'pending');
  control.classList.toggle('failed-write', state === 'failed');
}

let matrixRenderDeferred = false;

function requestMatrixRender(force=false){
  if(!lastState) return;
  if(!force && isMatrixEditActive()){
    matrixRenderDeferred = true;
    return;
  }
  matrixRenderDeferred = false;
  render(lastState);
}

function flushDeferredMatrixRender(){
  if(!matrixRenderDeferred || isMatrixEditActive()) return;
  requestMatrixRender(true);
}

function getLiveDevice(kind, ip){
  const listKey = kind === 'encoder' ? '_rawEncoders' : '_rawDecoders';
  const viewKey = kind === 'encoder' ? 'encoders' : 'decoders';
  const raw = (lastState?.[listKey] || []).find(d => d.ip === ip);
  const view = (lastState?.[viewKey] || []).find(d => d.ip === ip);
  return raw || view || null;
}

function setLiveDeviceField(kind, ip, field, value){
  if(!lastState) return;
  const listKeys = kind === 'encoder' ? ['encoders', '_rawEncoders'] : ['decoders', '_rawDecoders'];
  listKeys.forEach(listKey => {
    const dev = (lastState[listKey] || []).find(d => d.ip === ip);
    if(dev) dev[field] = value;
  });
}

function enqueueLiveWrite({kind, ip, field, value, control, payload, endpoint, sync, successMessage, failureMessage, previousValue}){
  const key = liveWriteKey(kind, ip, field);
  const token = Symbol(key);
  if(!liveWritePrevious.has(key)) liveWritePrevious.set(key, previousValue);
  const verifiedPreviousValue = liveWritePrevious.get(key);
  liveWriteLatest.set(key, token);
  setLiveDeviceField(kind, ip, field, value);
  markLiveControl(control, 'pending');

  const prior = liveWriteQueues.get(ip) || Promise.resolve();
  const run = prior.catch(()=>{}).then(async () => {
    if(liveWriteLatest.get(key) !== token) return;
    try {
      const res = await postJSON(endpoint, payload);
      if(!res.ok) throw new Error(res.error || 'Write failed');
      if(liveWriteLatest.get(key) !== token) return;
      if(sync) sync(res);
      liveWriteLatest.delete(key);
      liveWritePrevious.delete(key);
      markLiveControl(control, '');
      requestMatrixRender();
      if(successMessage) toast(successMessage, true);
    } catch(err) {
      if(liveWriteLatest.get(key) !== token) return;
      liveWriteLatest.delete(key);
      liveWritePrevious.delete(key);
      setLiveDeviceField(kind, ip, field, verifiedPreviousValue);
      if(control){
        if(control.type === 'checkbox') control.checked = !!verifiedPreviousValue;
        else control.value = verifiedPreviousValue ?? '';
      }
      markLiveControl(control, 'failed');
      setTimeout(()=>markLiveControl(control, ''), 2500);
      requestMatrixRender();
      toast((failureMessage || 'Update failed') + ': ' + err.message, false);
    }
  }).finally(() => {
    if(liveWriteQueues.get(ip) === run) liveWriteQueues.delete(ip);
  });
  liveWriteQueues.set(ip, run);
  return run;
}

function omniConfirm(options = {}) {
  let backdrop = document.getElementById('matrix_confirm_backdrop');
  if (!backdrop) {
    backdrop = document.createElement('div');
    backdrop.id = 'matrix_confirm_backdrop';
    backdrop.className = 'confirm-backdrop hidden';
    backdrop.innerHTML = `<div class="confirm-card" role="dialog" aria-modal="true">
      <h3 class="confirm-title"></h3>
      <div class="confirm-message"></div>
      <div class="confirm-summary" style="display:none;"></div>
      <div class="confirm-actions">
        <button type="button" class="confirm-cancel">Cancel</button>
        <button type="button" class="confirm-ok">Continue</button>
      </div>
    </div>`;
    document.body.appendChild(backdrop);
  }
  const titleEl = backdrop.querySelector('.confirm-title');
  const messageEl = backdrop.querySelector('.confirm-message');
  const summaryEl = backdrop.querySelector('.confirm-summary');
  const okBtn = backdrop.querySelector('.confirm-ok');
  const cancelBtn = backdrop.querySelector('.confirm-cancel');
  titleEl.textContent = options.title || 'Confirm Action';
  messageEl.textContent = options.message || '';
  const summary = options.summary || [];
  if (summary.length) {
    summaryEl.innerHTML = summary.map(row => `
      <div class="confirm-summary-row">
        <span>${escAttr(row.label || '')}</span>
        <strong>${escAttr(row.value || '')}</strong>
      </div>
    `).join('');
    summaryEl.style.display = 'block';
  } else {
    summaryEl.innerHTML = '';
    summaryEl.style.display = 'none';
  }
  okBtn.textContent = options.confirmText || 'Continue';
  okBtn.classList.toggle('confirm-danger', !!options.danger);
  backdrop.classList.remove('hidden');

  return new Promise(resolve => {
    const cleanup = (result) => {
      backdrop.classList.add('hidden');
      okBtn.removeEventListener('click', onOk);
      cancelBtn.removeEventListener('click', onCancel);
      backdrop.removeEventListener('click', onBackdrop);
      document.removeEventListener('keydown', onKeydown);
      resolve(result);
    };
    const onOk = () => cleanup(true);
    const onCancel = () => cleanup(false);
    const onBackdrop = (event) => { if (event.target === backdrop) cleanup(false); };
    const onKeydown = (event) => { if (event.key === 'Escape') cleanup(false); };
    okBtn.addEventListener('click', onOk);
    cancelBtn.addEventListener('click', onCancel);
    backdrop.addEventListener('click', onBackdrop);
    document.addEventListener('keydown', onKeydown);
    okBtn.focus();
  });
}

// ---- IP sort helpers ----
function ipNum(ip){
  const m = (ip||'').trim().match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if(!m) return Number.MAX_SAFE_INTEGER;
  return (+m[1]<<24) + (+m[2]<<16) + (+m[3]<<8) + (+m[4]);
}
function sortByIpAsc(arr){ return [...arr].sort((a,b)=>ipNum(a.ip)-ipNum(b.ip)); }

let lastState = null;
const DECODER_POLL_MS = 5000;
const ENCODER_SETTINGS_POLL_MS = 15000;
const VIDEO_WALL_PIXEL_SOURCES = {
  '4k': {label: '4K (3840x2160)', width: 3840, height: 2160},
  '1080p': {label: '1080p (1920x1080)', width: 1920, height: 1080},
};

// Track pending routes so UI stays stable until feedback arrives.
const pendingRoutes = new Map();
const PENDING_ROUTE_MIN_MS = 3000;
const routeQueues = new Map();
const routeLatest = new Map();
const configImportBusy = new Map();
const configImportPollTimers = new Map();
const CONFIG_IMPORT_MIN_MS = 8000;
const CONFIG_IMPORT_MAX_MS = 180000;

function isConfigImportBusy(ip) {
  const started = configImportBusy.get(ip);
  if (!started) return false;
  if (Date.now() - started > CONFIG_IMPORT_MAX_MS) {
    configImportBusy.delete(ip);
    return false;
  }
  return true;
}

function setConfigImportBusy(ip, busy) {
  if (!ip) return;
  if (busy) {
    configImportBusy.set(ip, Date.now());
    startConfigImportPoll(ip);
  } else {
    configImportBusy.delete(ip);
    stopConfigImportPoll(ip);
  }
  if (lastState) requestMatrixRender();
}

function clearConfigImportBusyOnPoll(ip) {
  if (!ip || !configImportBusy.has(ip)) return false;
  if (Date.now() - configImportBusy.get(ip) < CONFIG_IMPORT_MIN_MS) return false;
  configImportBusy.delete(ip);
  stopConfigImportPoll(ip);
  toast(`Configuration import complete on ${ip}`, true);
  return true;
}

async function pollConfigImportUnit(ip) {
  const pollRole = async (endpoint, key, syncFn) => {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({[key]: [ip]}),
    });
    const data = await res.json().catch(() => ({}));
    const fields = data?.results?.[ip];
    if (data.ok && fields && !fields.error) return syncFn(ip, fields);
    return false;
  };
  const changed = await Promise.allSettled([
    pollRole('/api/poll_encoders', 'encoders', syncEncoderFields),
    pollRole('/api/poll_decoders', 'decoders', syncDecoderFields),
  ]);
  if (changed.some(r => r.status === 'fulfilled' && r.value) && lastState) {
    requestMatrixRender();
  }
}

function startConfigImportPoll(ip) {
  stopConfigImportPoll(ip);
  const tick = () => {
    if (!isConfigImportBusy(ip)) {
      setConfigImportBusy(ip, false);
      return;
    }
    pollConfigImportUnit(ip).catch(() => {});
  };
  configImportPollTimers.set(ip, setInterval(tick, 3000));
  setTimeout(tick, 1000);
}

function stopConfigImportPoll(ip) {
  const timer = configImportPollTimers.get(ip);
  if (timer) clearInterval(timer);
  configImportPollTimers.delete(ip);
}

function videoMatchesEncoder(dec, enc){
  return (dec.ip1_addr === enc.v_mcast) && (Number(dec.ip1_port) === Number(enc.v_port));
}

function audioMatchesEncoder(dec, enc){
  return (dec.ip3_addr === enc.a_mcast) && (Number(dec.ip3_port) === Number(enc.a_port));
}

function recordPendingRoute(decIp, encIp, mode){
  if (!lastState) return;
  const rawDecoders = lastState._rawDecoders || lastState.decoders || [];
  const dec = rawDecoders.find(d => d.ip === decIp) || {};
  const pending = {
    encoderIp: encIp,
    mode,
    prevVideo: null,
    prevAudio: null,
    ts: Date.now(),
  };
  if (mode === 'video' || mode === 'av') {
    pending.prevVideo = {ip1_addr: dec.ip1_addr || '', ip1_port: dec.ip1_port || ''};
  }
  if (mode === 'audio' || mode === 'av') {
    pending.prevAudio = {ip3_addr: dec.ip3_addr || '', ip3_port: dec.ip3_port || ''};
  }
  pendingRoutes.set(decIp, pending);
}

function resolvePendingRoutes(state){
  if (!state) return;
  const rawEncoders = state._rawEncoders || state.encoders || [];
  const rawDecoders = state._rawDecoders || state.decoders || [];
  const encMap = new Map(rawEncoders.map(e => [e.ip, e]));
  for (const [decIp, pending] of pendingRoutes.entries()) {
    const dec = rawDecoders.find(d => d.ip === decIp);
    const enc = encMap.get(pending.encoderIp);
    if (!dec || !enc) {
      pendingRoutes.delete(decIp);
      continue;
    }
    const elapsedMs = Date.now() - pending.ts;
    if (elapsedMs < PENDING_ROUTE_MIN_MS) {
      continue;
    }
    const videoMatches = videoMatchesEncoder(dec, enc);
    const audioMatches = audioMatchesEncoder(dec, enc);
    const videoChanged = pending.prevVideo ?
      (dec.ip1_addr !== pending.prevVideo.ip1_addr || Number(dec.ip1_port) !== Number(pending.prevVideo.ip1_port)) :
      false;
    const audioChanged = pending.prevAudio ?
      (dec.ip3_addr !== pending.prevAudio.ip3_addr || Number(dec.ip3_port) !== Number(pending.prevAudio.ip3_port)) :
      false;

    const videoFeedback = (pending.mode === 'video' || pending.mode === 'av') && (videoMatches || videoChanged);
    const audioFeedback = (pending.mode === 'audio' || pending.mode === 'av') && (audioMatches || audioChanged);
    let feedbackReceived = false;
    if (pending.mode === 'video') feedbackReceived = videoFeedback;
    else if (pending.mode === 'audio') feedbackReceived = audioFeedback;
    else feedbackReceived = videoFeedback && audioFeedback;

    if (feedbackReceived || elapsedMs >= PENDING_ROUTE_MIN_MS) pendingRoutes.delete(decIp);
  }
}

function enqueueRouteWrite(decoderIp, encoderIp, mode) {
  const token = Symbol(`${decoderIp}:${encoderIp}:${mode}`);
  routeLatest.set(decoderIp, token);
  const prior = routeQueues.get(decoderIp) || Promise.resolve();
  const run = prior.catch(()=>{}).then(async () => {
    if(routeLatest.get(decoderIp) !== token) return {ok: true, skipped: true, decoderIp};
    const res = await postJSON('/api/route', {decoder: decoderIp, encoder: encoderIp, mode});
    if(routeLatest.get(decoderIp) !== token) return {ok: true, skipped: true, decoderIp};
    routeLatest.delete(decoderIp);
    if(res.decoder && lastState && Array.isArray(lastState.decoders)) {
      const updateDecoder = (list) => {
        const idx = (list || []).findIndex(d => d.ip === res.decoder.ip);
        if(idx >= 0) list[idx] = {...list[idx], ...res.decoder};
      };
      updateDecoder(lastState.decoders);
      updateDecoder(lastState._rawDecoders);
    }
    return {...res, decoderIp};
  }).catch(err => {
    if(routeLatest.get(decoderIp) === token) routeLatest.delete(decoderIp);
    throw err;
  }).finally(() => {
    if(routeQueues.get(decoderIp) === run) routeQueues.delete(decoderIp);
  });
  routeQueues.set(decoderIp, run);
  return run;
}

// Group selection state for decoders
let selectedDecoders = new Set();

// Filter state
let encFilterValue = '';
let decFilterValue = '';
let configureFilterValue = '';
let openVideoWallConfigDecoderIp = null;
const videoWallPixelSourceByDecoder = new Map();

const sectionColumnVisibilityDefaults = {
  encUnitInfo: true,
  encInput: true,
  encOutput: true,
  encEncoding: true,
  decUnitInfo: true,
  decOutput: true,
  decResolution: true,
  decFastSwitching: true,
  decVideoWall: true,
};

let sectionColumnVisibility = {...sectionColumnVisibilityDefaults};
try {
  const savedColumnVisibility = JSON.parse(localStorage.getItem('matrixSectionColumns') || '{}');
  sectionColumnVisibility = {...sectionColumnVisibility, ...savedColumnVisibility};
} catch {}

function isSectionVisible(key) {
  return sectionColumnVisibility[key] !== false;
}

function sectionClass(key) {
  return isSectionVisible(key) ? '' : ' section-hidden';
}

function colTh(label, key) {
  return `<th class="${sectionClass(key)}" data-section-group="${key}">${label}</th>`;
}

function colTd(content, key) {
  return `<td class="${sectionClass(key)}" data-section-group="${key}">${content}</td>`;
}

function saveSectionColumnVisibility() {
  localStorage.setItem('matrixSectionColumns', JSON.stringify(sectionColumnVisibility));
}

function applySectionFilterControls() {
  document.querySelectorAll('.matrix-section-filter').forEach(label => {
    const key = label.getAttribute('data-section-group');
    const visible = isSectionVisible(key);
    const checkbox = label.querySelector('.section-filter-checkbox');
    const switchEl = label.querySelector('.switch');
    const labelText = label.querySelector('.label');
    const groupLabel = label.getAttribute('data-section-label') || labelText?.textContent || '';
    if (checkbox) checkbox.checked = visible;
    if (switchEl) switchEl.classList.toggle('on', visible);
    if (labelText) labelText.textContent = `${visible ? 'Hide' : 'Show'} ${groupLabel}`;
  });
}

function initSectionFilterControls() {
  document.querySelectorAll('.matrix-section-filter').forEach(label => {
    const key = label.getAttribute('data-section-group');
    const toggle = () => {
      sectionColumnVisibility[key] = !isSectionVisible(key);
      saveSectionColumnVisibility();
      requestMatrixRender();
    };
    label.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      toggle();
    });
  });
  applySectionFilterControls();
}

function deviceMatchesFilter(dev, filter, type) {
  if (!filter) return true;
  filter = filter.toLowerCase();
  let stream = '';
  // For encoders, use v_mcast, a_mcast, host, ip, model, serial
  // For decoders, use ip1_addr, ip3_addr, host, ip, model, serial
  if (type === 'enc') {
    stream = (dev.v_mcast||'') + ' ' + (dev.a_mcast||'');
  } else if (type === 'dec') {
    stream = (dev.ip1_addr||'') + ' ' + (dev.ip3_addr||'');
  }
  return (
    (dev.host && dev.host.toLowerCase().includes(filter)) ||
    (dev.ip && dev.ip.toLowerCase().includes(filter)) ||
    (stream && stream.toLowerCase().includes(filter)) ||
    (dev.model && dev.model.toLowerCase().includes(filter)) ||
    (dev.serial && dev.serial.toLowerCase().includes(filter))
  );
}

async function refresh(){
  // Show loading overlay
  const overlay = document.getElementById('matrix_loading_overlay');
  if (overlay) overlay.classList.remove('hidden');
  
  try {
    const s = await getJSON('/api/state');
    // sort encoders left->right and decoders top->bottom by IP
    const rawEncoders = sortByIpAsc(s.encoders||[]);
    const rawDecoders = sortByIpAsc(s.decoders||[]);
    // Apply filters
    const usingConfigureFilter = !!document.getElementById('configureFilterInput');
    const encoders = rawEncoders.filter(e => deviceMatchesFilter(e, usingConfigureFilter ? configureFilterValue : encFilterValue, 'enc'));
    const decoders = rawDecoders.filter(d => deviceMatchesFilter(d, usingConfigureFilter ? configureFilterValue : decFilterValue, 'dec'));
    lastState = {...s, encoders, decoders, _rawEncoders: rawEncoders, _rawDecoders: rawDecoders};
    resolvePendingRoutes(lastState);
    requestMatrixRender();
  } finally {
    // Hide loading overlay when done
    if (overlay) overlay.classList.add('hidden');
  }
}

async function pollDecoderInputs(decoderIpsOverride = null, options = {}){
  const force = !!options.force;
  if (openVideoWallConfigDecoderIp && !force) {
    return;
  }
  if (!lastState || !lastState.decoders || lastState.decoders.length === 0) {
    return;
  }
  const decoderIps = Array.isArray(decoderIpsOverride) && decoderIpsOverride.length ? decoderIpsOverride : lastState.decoders.map(d => d.ip);
  try {
    const result = await fetch('/api/poll_decoders', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({decoders: decoderIps})
    });
    const data = await result.json();
    if (data.ok && data.results) {
      // Update decoder inputs in lastState
      let changed = false;
      for (const [ip, fields] of Object.entries(data.results)) {
        if (fields.error) continue;
        changed = syncDecoderFields(ip, fields) || changed;
      }
      // Re-render with updated inputs
      resolvePendingRoutes(lastState);
      if (changed) {
        requestMatrixRender(force);
      }
      console.log(`[POLL] Updated ${data.updated || 0} decoders`);
    }
  } catch(err) {
    console.error('[POLL] Failed to poll decoders:', err);
  }
}

async function refreshVideoWallDecoder(decoderIp) {
  if (!decoderIp) return;
  openVideoWallConfigDecoderIp = decoderIp;
  await pollDecoderInputs([decoderIp], {force: true});
}

function syncEncoderFields(ip, fields) {
  if (!lastState || !fields || fields.error) return false;
  let changed = clearConfigImportBusyOnPoll(ip);
  const applyFields = (enc) => {
    if (!enc) return;
    for (const key of [
      'host', 'hostname', 'fw', 'version', 'firmwareversion',
      'v_mcast', 'v_port', 'a_mcast', 'a_port',
      'session1_name', 'session1_video_mcast', 'session1_video_port', 'session1_audio_mcast', 'session1_audio_port',
      'session2_name', 'session2_video_mcast', 'session2_video_port', 'session2_audio_mcast', 'session2_audio_port',
      'input_auto_switch', 'active_input', 'input_status', 'cable_present', 'edid', 'edid_options',
      'hdcp_encrypted', 'hdcp_negotiated_version', 'hdcp_support_version', 'hdcp_supported_versions'
    ]) {
      if (isLiveFieldPending('encoder', ip, key)) continue;
      if (fields[key] !== undefined) {
        enc[key] = fields[key];
        changed = true;
      }
    }
  };
  applyFields((lastState.encoders || []).find(e => e.ip === ip));
  applyFields((lastState._rawEncoders || []).find(e => e.ip === ip));
  return changed;
}

async function pollEncoderInputs(){
  if (!lastState || !lastState.encoders || lastState.encoders.length === 0) {
    return;
  }
  const encoderIps = lastState.encoders.map(e => e.ip);
  if (encoderIps.length === 0) {
    return;
  }
  try {
    const result = await fetch('/api/poll_encoders', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({encoders: encoderIps})
    });
    const data = await result.json();
    if (data.ok && data.results) {
      let changed = false;
      for (const [ip, fields] of Object.entries(data.results)) {
        changed = syncEncoderFields(ip, fields) || changed;
      }
      if (changed) {
        requestMatrixRender();
      }
      console.log(`[POLL] Updated ${data.updated || 0} encoders`);
    }
  } catch(err) {
    console.error('[POLL] Failed to poll encoders:', err);
  }
}

async function pollMatrixDevices(){
  await Promise.all([
    pollDecoderInputs(),
    pollEncoderInputs(),
  ]);
}

function buildSelectOptions(options, currentValue, placeholder) {
  const list = Array.isArray(options) ? [...options] : [];
  if (currentValue && !list.includes(currentValue)) {
    list.unshift(currentValue);
  }
  if (list.length === 0) {
    const label = placeholder || 'Loading...';
    return `<option value="">${label}</option>`;
  }
  return list.map(opt => `<option value="${opt}" ${opt === currentValue ? 'selected' : ''}>${opt}</option>`).join('');
}

function escAttr(value) {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function optionList(values, currentValue, labeler = value => value) {
  const out = [];
  const seen = new Set();
  [...(values || []), currentValue].forEach(value => {
    if (value === undefined || value === null) return;
    const key = String(value);
    if (seen.has(key)) return;
    seen.add(key);
    out.push(value);
  });
  return out.map(value => `<option value="${escAttr(value)}" ${String(value) === String(currentValue ?? '') ? 'selected' : ''}>${escAttr(labeler(value))}</option>`).join('');
}

function unitIp(unit) {
  return String(unit?.ip || '').trim();
}

function unitHost(unit) {
  return String(unit?.host || unit?.hostname || '').trim();
}

function unitDisplayLabel(unit) {
  const ip = unitIp(unit);
  const host = unitHost(unit);
  return host ? `${ip} / ${host}` : ip;
}

function uniqueUnitsByIp(units) {
  const seen = new Map();
  (units || []).forEach(unit => {
    const ip = unitIp(unit);
    if (ip && !seen.has(ip)) seen.set(ip, unit);
  });
  return sortByIpAsc([...seen.values()]);
}

function getAvailableEncoderUnits() {
  if (!lastState) return [];
  return uniqueUnitsByIp(lastState._rawEncoders || lastState.encoders || []);
}

function hasVideoWallCapability(decoder) {
  return !!decoder && (
    decoder.video_wall_enabled !== undefined ||
    decoder.video_wall_unit !== undefined ||
    decoder.video_wall_width !== undefined ||
    decoder.video_wall_height !== undefined ||
    (Array.isArray(decoder.video_wall_unit_options) && decoder.video_wall_unit_options.length > 0) ||
    (Array.isArray(decoder.video_wall_rotation_options) && decoder.video_wall_rotation_options.length > 0) ||
    (Array.isArray(decoder.video_wall_edge_mode_options) && decoder.video_wall_edge_mode_options.length > 0)
  );
}

function getVideoWallDecoderUnits() {
  if (!lastState) return [];
  const decoders = uniqueUnitsByIp(lastState._rawDecoders || lastState.decoders || []);
  const capable = decoders.filter(hasVideoWallCapability);
  return capable.length ? capable : decoders;
}

function buildUnitSelectOptions(units, selectedIp) {
  const options = uniqueUnitsByIp(units);
  if (!options.length) return '<option value="">No units</option>';
  return options.map(unit => {
    const ip = unitIp(unit);
    return `<option value="${escAttr(ip)}" ${ip === selectedIp ? 'selected' : ''}>${escAttr(unitDisplayLabel(unit))}</option>`;
  }).join('');
}

function setModalUnitSelectOptions(modal, selector, units, selectedIp) {
  const select = modal.querySelector(selector);
  if (!select) return;
  select.innerHTML = buildUnitSelectOptions(units, selectedIp);
  select.value = selectedIp || '';
  select.disabled = !units || units.length <= 1;
}

const CODEC_OPTIONS = [
  ['Colibri', 'VCx'],
  ['VC2/LeGall', 'VC-2 Video'],
  ['VC2/Haar', 'VC-2 PC application'],
];

function codecLabel(systemMode) {
  const found = CODEC_OPTIONS.find(([value]) => value === systemMode);
  return found ? found[1] : (systemMode || '');
}

function codecClass(systemMode) {
  return systemMode === 'Colibri' ? 'codec-ok' : 'codec-bad';
}

function isCodecConfigurable(unit) {
  if (unit?.codec_configurable !== undefined) return !!unit.codec_configurable;
  const model = String(unit?.model || '').trim().toLowerCase();
  return !!model && !model.startsWith('hw-omni');
}

function buildCodecControl(unit) {
  const mode = unit?.system_mode || '';
  if (!isCodecConfigurable(unit)) {
    return `<span class="codec-pill ${codecClass(mode)}" data-system-mode="${escAttr(mode)}">${escAttr(codecLabel(mode) || 'Unknown')}</span>`;
  }
  const supported = Array.isArray(unit.supported_system_modes) && unit.supported_system_modes.length
    ? unit.supported_system_modes
    : CODEC_OPTIONS.map(([value]) => value);
  const options = supported.map(value => `<option value="${escAttr(value)}" ${value === mode ? 'selected' : ''}>${escAttr(codecLabel(value) || value)}</option>`).join('');
  return `<select class="codec-select ${codecClass(mode)}" data-ip="${escAttr(unit.ip || '')}" data-last-value="${escAttr(mode)}" data-system-mode="${escAttr(mode)}">${options}</select>`;
}

function syncCodecFields(ip, systemMode, supportedModes = null) {
  if (!lastState) return;
  const apply = unit => {
    if (!unit) return;
    unit.system_mode = systemMode;
    unit.codec = codecLabel(systemMode);
    if (Array.isArray(supportedModes)) unit.supported_system_modes = supportedModes;
  };
  apply((lastState.encoders || []).find(e => e.ip === ip));
  apply((lastState._rawEncoders || []).find(e => e.ip === ip));
  apply((lastState.decoders || []).find(d => d.ip === ip));
  apply((lastState._rawDecoders || []).find(d => d.ip === ip));
}

function getMatrixCodecModes(encoders, decoders) {
  return [...(encoders || []), ...(decoders || [])]
    .map(unit => unit.system_mode)
    .filter(Boolean);
}

function updateCodecMismatchWarning(encoders, decoders) {
  let warning = document.getElementById('codec_mismatch_warning');
  if (!warning) {
    const host = document.querySelector('.matrix-area') || document.querySelector('.configure-area');
    if (!host) return;
    warning = document.createElement('div');
    warning.id = 'codec_mismatch_warning';
    warning.className = 'codec-warning hidden';
    host.prepend(warning);
  }
  const unique = [...new Set(getMatrixCodecModes(encoders, decoders))];
  if (unique.length > 1) {
    warning.textContent = `Codec mismatch detected: ${unique.map(codecLabel).join(', ')}`;
    warning.classList.remove('hidden');
  } else {
    warning.classList.add('hidden');
  }
}

function routeCodecCompatible(encoder, decoder) {
  const encMode = String(encoder?.system_mode || '').trim();
  const decMode = String(decoder?.system_mode || '').trim();
  return !encMode || !decMode || encMode === decMode;
}

function routeCodecMismatchMessage(encoder, decoder) {
  return `Codec mismatch: encoder ${codecLabel(encoder?.system_mode) || 'Unknown'} cannot route to decoder ${codecLabel(decoder?.system_mode) || 'Unknown'}`;
}

function isValidHostname(value) {
  return /^[A-Za-z0-9.-]+$/.test(value);
}

function buildHostnameInput(unit, extraClass = '') {
  return `<input type="text" class="matrix-hostname-edit ${extraClass}" data-ip="${escAttr(unit.ip || '')}" data-last-value="${escAttr(unit.host || '')}" value="${escAttr(unit.host || '')}" spellcheck="false" title="Letters, numbers, hyphen, and period only">`;
}

function syncHostnameFields(ip, hostname) {
  if (!lastState) return;
  const apply = (unit) => {
    if (!unit) return;
    unit.host = hostname;
    unit.hostname = hostname;
  };
  apply((lastState.encoders || []).find(e => e.ip === ip));
  apply((lastState._rawEncoders || []).find(e => e.ip === ip));
  apply((lastState.decoders || []).find(d => d.ip === ip));
  apply((lastState._rawDecoders || []).find(d => d.ip === ip));
}

function isMatrixEditActive() {
  const active = document.activeElement;
  if (openVideoWallConfigDecoderIp) return true;
  if (activeCodecSelectIp) return true;
  if (!active?.classList) return false;
  if (active.closest('.encoder-output-modal:not(.hidden), .video-wall-modal:not(.hidden), .video-wall-config-modal:not(.hidden)')) return true;
  if (active.matches('[data-path], .modal-unit-select')) return true;
  return active.matches([
    '.matrix-hostname-edit',
    '.codec-select',
    '.enc-auto-switch-toggle',
    '.enc-active-input-select',
    '.enc-edid-select',
    '.enc-hdcp-select',
    '.dec-sap-input-toggle',
    '.dec-input-session-select',
    '.dec-hdcp-select',
    '.dec-video-input-select',
    '.dec-audio-input-select',
    '.dec-stretch-crop-select',
    '.dec-resolution-select',
    '.dec-framerate-select',
    '.dec-fsm-enabled-toggle',
    '.dec-fsm-timeout-input',
    '.dec-fsm-colorspace-select'
  ].join(','));
}

function handleMatrixHostnameKey(event) {
  if (event.key === 'Enter') {
    event.preventDefault();
    event.target.blur();
  } else if (event.key === 'Escape') {
    event.preventDefault();
    event.target.value = event.target.dataset.lastValue || '';
    event.target.blur();
  }
}

async function submitMatrixHostnameEdit(input) {
  const ip = input.getAttribute('data-ip');
  const nextHostname = input.value.trim();
  const previousHostname = input.dataset.lastValue ?? input.defaultValue ?? '';
  if (!ip || nextHostname === previousHostname) return;
  if (!nextHostname || !isValidHostname(nextHostname)) {
    input.classList.add('invalid');
    input.value = previousHostname;
    toast('Hostname may only contain letters, numbers, hyphen, and period.', false);
    return;
  }

  input.classList.remove('invalid');
  input.classList.add('saving');
  input.disabled = true;
  try {
    const data = await postJSON('/api/hostname', {ip, hostname: nextHostname});
    if (!data.ok) throw new Error(data.error || 'Hostname update failed');
    const savedHostname = data.hostname || nextHostname;
    syncHostnameFields(ip, savedHostname);
    input.dataset.lastValue = savedHostname;
    input.defaultValue = savedHostname;
    input.value = savedHostname;
    toast('Hostname updated', true);
    requestMatrixRender();
  } catch (err) {
    input.value = previousHostname;
    toast('Hostname update failed: ' + (err.message || err), false);
  } finally {
    input.disabled = false;
    input.classList.remove('saving');
  }
}

const DSCP_OPTIONS = [
  {value: 0, label: 'Best effort'},
  {value: 10, label: 'AF11'},
  {value: 18, label: 'AF21'},
  {value: 26, label: 'AF31'},
  {value: 34, label: 'AF41'},
  {value: 8, label: 'CS1'},
  {value: 16, label: 'CS2'},
  {value: 24, label: 'CS3'},
  {value: 32, label: 'CS4'},
  {value: 40, label: 'CS5'},
  {value: 48, label: 'CS6'},
  {value: 56, label: 'CS7'},
  {value: 46, label: 'EF'},
];

function dscpLabel(value) {
  const found = DSCP_OPTIONS.find(opt => Number(opt.value) === Number(value));
  return found ? found.label : value;
}

function dscpOptions(currentValue) {
  const values = DSCP_OPTIONS.map(opt => opt.value);
  if (currentValue !== undefined && currentValue !== null && !values.some(value => Number(value) === Number(currentValue))) {
    values.push(currentValue);
  }
  return values.map(value => `<option value="${escAttr(value)}" ${Number(value) === Number(currentValue ?? 0) ? 'selected' : ''}>${escAttr(dscpLabel(value))}</option>`).join('');
}

function encoderLabel(value) {
  if (!value) return 'Not used';
  return String(value).replace(/^vc2_/, '').replace('_encoder', '');
}

function audioSourceLabel(value) {
  if (!value) return 'Not used';
  if (value === 'audio_generator1') return 'Audio generator';
  return value;
}

function auxSourceLabel(value) {
  if (!value) return 'Commands';
  return value;
}

function sessionInterfaceOptions(sessions, currentValue) {
  return optionList(['eth1', 'lan0', 'sfp0', ...(sessions || []).map(s => s.interface).filter(Boolean)], currentValue);
}

function buildInputSelectOptions(options, currentValue, kind, decoder) {
  const current = (currentValue ?? '') === '' ? 'notused' : currentValue;
  const rawList = ['notused', ...(Array.isArray(options) ? options : [])];
  const list = [];
  const seen = new Set();
  rawList.forEach(opt => {
    const value = (opt ?? '') === '' ? 'notused' : opt;
    const key = String(value).toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    list.push(value);
  });
  if (!list.includes(current)) {
    list.unshift(current);
  }
  if (list.length === 0) {
    return '<option value="">No inputs</option>';
  }
  return list.map(opt => {
    const value = (opt ?? '') === '' ? 'notused' : opt;
    const label = formatDecoderInputOptionLabel(value, kind, decoder);
    return `<option value="${value}" ${value === current ? 'selected' : ''}>${label}</option>`;
  }).join('');
}

function formatDecoderInputOptionLabel(value, kind, decoder) {
  if (value === '' || String(value).toLowerCase() === 'notused') return 'Not used';
  if (value === 'generator') return kind === 'audio' ? 'Audio generator' : 'Video generator';
  if (value === 'ip_input1' && decoder?.ip1_addr) return `${value} (${decoder.ip1_addr}:${decoder.ip1_port || ''})`;
  if (value === 'ip_input3' && decoder?.ip3_addr) return `${value} (${decoder.ip3_addr}:${decoder.ip3_port || ''})`;
  return value;
}

function isE4521Encoder(encoder) {
  return ((encoder?.model || '').trim().toLowerCase() === 'hw-omni-e4521');
}

function getActiveInputOptions(encoder) {
  const options = Array.isArray(encoder?.input_status) ? encoder.input_status.map(item => item?.name).filter(Boolean) : [];
  if (encoder?.active_input && !options.includes(encoder.active_input)) {
    options.unshift(encoder.active_input);
  }
  return options;
}

function computeCablePresent(encoder) {
  if (encoder?.cable_present !== undefined && encoder?.cable_present !== null) {
    return !!encoder.cable_present;
  }
  const statuses = Array.isArray(encoder?.input_status) ? encoder.input_status : [];
  if (encoder?.active_input) {
    const active = statuses.find(item => item?.name === encoder.active_input);
    if (active && active.cabledetect !== undefined && active.cabledetect !== null) {
      return !!active.cabledetect;
    }
  }
  return statuses.some(item => !!item?.cabledetect);
}

function buildEncoderInputSection(encoder) {
  const edidOptions = buildSelectOptions(encoder.edid_options, encoder.edid, 'Loading EDIDs...');
  const hdcpOptions = buildSelectOptions(encoder.hdcp_supported_versions, encoder.hdcp_support_version, 'Loading versions...');
  const edidDisabled = (!Array.isArray(encoder.edid_options) || encoder.edid_options.length === 0) ? 'disabled' : '';
  const hdcpDisabled = (!Array.isArray(encoder.hdcp_supported_versions) || encoder.hdcp_supported_versions.length === 0) ? 'disabled' : '';
  const cableOn = computeCablePresent(encoder);
  const cableCell = colTd(`<span class="cable-light ${cableOn ? 'on' : 'off'}" title="${cableOn ? 'Cable Present' : 'Cable Not Present'}"></span>`, 'encInput');
  let autoSwitchCell = colTd('N/A', 'encInput');
  let activeInputCell = colTd('N/A', 'encInput');
  if (isE4521Encoder(encoder)) {
    const autoSwitchChecked = encoder.input_auto_switch ? 'checked' : '';
    const activeInputOptions = buildSelectOptions(getActiveInputOptions(encoder), encoder.active_input, 'Loading inputs...');
    const activeInputDisabled = encoder.input_auto_switch || getActiveInputOptions(encoder).length === 0 ? 'disabled' : '';
    autoSwitchCell = colTd(`<input type="checkbox" class="enc-auto-switch-toggle" data-enc-ip="${encoder.ip}" ${autoSwitchChecked}>`, 'encInput');
    activeInputCell = colTd(`<select class="enc-active-input-select" data-enc-ip="${encoder.ip}" ${activeInputDisabled}>${activeInputOptions}</select>`, 'encInput');
  }
  return `${cableCell}
        ${autoSwitchCell}
        ${activeInputCell}
        ${colTd(`<select class="enc-edid-select" data-enc-ip="${encoder.ip}" ${edidDisabled}>${edidOptions}</select>`, 'encInput')}
        ${colTd(`<select class="enc-hdcp-select" data-enc-ip="${encoder.ip}" ${hdcpDisabled}>${hdcpOptions}</select>`, 'encInput')}`;
}

function buildEncoderOutputLink(encoder) {
  return colTd(`<button type="button" class="text-link enc-output-link" data-enc-ip="${encoder.ip}">Session</button>`, 'encOutput');
}

function buildEncoderEncodingLink(encoder) {
  return colTd(`<button type="button" class="text-link enc-encoding-link" data-enc-ip="${encoder.ip}">Encoding</button>`, 'encEncoding');
}

function buildUnitConfigActions(unit, groupKey) {
  const ip = escAttr(unit?.ip || '');
  const busy = isConfigImportBusy(unit?.ip || '');
  const indicator = busy ? '<span class="config-import-indicator" title="Configuration import is applying"><span class="config-import-spinner"></span>Importing</span>' : '';
  return colTd(`<button type="button" class="text-link unit-config-export-link" data-unit-ip="${ip}">Export</button>
        <span style="color:var(--muted);padding:0 4px;">/</span>
        <button type="button" class="text-link unit-config-import-link" data-unit-ip="${ip}">Import</button>
        ${indicator}`, groupKey);
}

function ensureUnitConfigImportModal() {
  let modal = document.getElementById('unit_config_import_modal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'unit_config_import_modal';
  modal.className = 'encoder-output-modal hidden';
  modal.innerHTML = `<div class="encoder-output-card" style="width:min(92vw,520px);">
      <div class="encoder-output-head">
        <div>
          <h4>Import Unit Configuration</h4>
          <span class="encoder-output-subtitle unit-config-import-subtitle"></span>
        </div>
        <button type="button" class="unit-config-import-close" aria-label="Close">x</button>
      </div>
      <div class="encoder-output-body" style="display:block;">
        <div class="encoder-output-grid" style="display:block;">
          <label style="display:grid;grid-template-columns:96px minmax(0,1fr);gap:10px;align-items:center;margin-bottom:12px;">
            <span>JSON file</span>
            <input type="file" class="unit-config-import-file" accept=".json,application/json">
          </label>
          <div class="encoder-output-error unit-config-import-error" style="display:none;"></div>
          <div class="encoder-output-loading unit-config-import-status" style="display:none;"></div>
        </div>
      </div>
      <div class="encoder-output-actions">
        <button type="button" class="unit-config-import-cancel">Cancel</button>
        <button type="button" class="unit-config-import-submit">Import</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click', evt => {
    if (evt.target === modal) modal.classList.add('hidden');
  });
  modal.querySelector('.unit-config-import-close').addEventListener('click', () => modal.classList.add('hidden'));
  modal.querySelector('.unit-config-import-cancel').addEventListener('click', () => modal.classList.add('hidden'));
  modal.querySelector('.unit-config-import-submit').addEventListener('click', submitUnitConfigImport);
  return modal;
}

function openUnitConfigImportModal(ip) {
  const modal = ensureUnitConfigImportModal();
  modal.setAttribute('data-unit-ip', ip);
  modal.querySelector('.unit-config-import-subtitle').textContent = ip;
  modal.querySelector('.unit-config-import-file').value = '';
  modal.querySelector('.unit-config-import-error').style.display = 'none';
  modal.querySelector('.unit-config-import-error').textContent = '';
  modal.querySelector('.unit-config-import-status').style.display = 'none';
  modal.querySelector('.unit-config-import-status').textContent = '';
  modal.classList.remove('hidden');
}

async function submitUnitConfigImport() {
  const modal = ensureUnitConfigImportModal();
  const ip = modal.getAttribute('data-unit-ip');
  const fileInput = modal.querySelector('.unit-config-import-file');
  const errorEl = modal.querySelector('.unit-config-import-error');
  const statusEl = modal.querySelector('.unit-config-import-status');
  const submitBtn = modal.querySelector('.unit-config-import-submit');
  const file = fileInput?.files?.[0];
  errorEl.style.display = 'none';
  errorEl.textContent = '';
  statusEl.style.display = 'none';
  statusEl.textContent = '';
  if (!ip) {
    errorEl.textContent = 'Unit IP is missing.';
    errorEl.style.display = 'block';
    return;
  }
  if (!file) {
    errorEl.textContent = 'Choose a .json configuration file.';
    errorEl.style.display = 'block';
    return;
  }
  if (!file.name.toLowerCase().endsWith('.json')) {
    errorEl.textContent = 'Configuration import only accepts .json files.';
    errorEl.style.display = 'block';
    return;
  }
  if (!await omniConfirm({
    title: 'Import Configuration',
    message: `Importing a configuration file to ${ip} can overwrite unit settings.`,
    confirmText: 'Import',
    danger: true,
    summary: [
      {label: 'Device', value: ip},
      {label: 'File', value: file.name}
    ]
  })) return;

  const form = new FormData();
  form.append('ip', ip);
  form.append('file', file);
  submitBtn.disabled = true;
  statusEl.textContent = 'Uploading configuration...';
  statusEl.style.display = 'block';
  try {
    const res = await fetch('/api/unit_config/import', {method: 'POST', body: form});
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      const stage = data.stage ? `${data.stage}: ` : '';
      throw new Error(`${stage}${data.error || 'Configuration import failed'}`);
    }
    setConfigImportBusy(ip, true);
    toast(data.pending ? 'Configuration import sent; unit is applying it' : 'Configuration imported', true);
    modal.classList.add('hidden');
  } catch (err) {
    errorEl.textContent = err.message || String(err);
    errorEl.style.display = 'block';
  } finally {
    submitBtn.disabled = false;
    statusEl.style.display = 'none';
  }
}

function ensureEncoderOutputModal() {
  let modal = document.getElementById('encoder_output_modal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'encoder_output_modal';
  modal.className = 'encoder-output-modal hidden';
  modal.innerHTML = `<div class="encoder-output-card">
      <div class="encoder-output-head">
        <div>
          <h4>Encoder Output</h4>
          <span class="encoder-output-subtitle"></span>
        </div>
        <div class="encoder-output-head-actions">
          <select class="modal-unit-select encoder-output-unit-select" aria-label="Select encoder"></select>
          <button type="button" class="encoder-output-close" aria-label="Close">x</button>
        </div>
      </div>
      <div class="encoder-output-body"></div>
      <div class="encoder-output-actions">
        <button type="button" class="encoder-output-footer-close">Close</button>
        <button type="button" class="encoder-output-refresh">Refresh</button>
        <button type="button" class="encoder-output-save">Save</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click', evt => {
    if (evt.target === modal) modal.classList.add('hidden');
  });
  modal.querySelector('.encoder-output-close').addEventListener('click', () => modal.classList.add('hidden'));
  modal.querySelector('.encoder-output-footer-close').addEventListener('click', () => modal.classList.add('hidden'));
  modal.querySelector('.encoder-output-refresh').addEventListener('click', () => {
    const encoderIp = modal.getAttribute('data-enc-ip');
    if (encoderIp) openEncoderOutputModal(encoderIp);
  });
  modal.querySelector('.encoder-output-unit-select').addEventListener('change', evt => {
    const encoderIp = evt.target.value;
    if (encoderIp) openEncoderOutputModal(encoderIp);
  });
  modal.querySelector('.encoder-output-save').addEventListener('click', saveEncoderOutputModal);
  return modal;
}

async function openEncoderOutputModal(encoderIp) {
  const modal = ensureEncoderOutputModal();
  modal.setAttribute('data-enc-ip', encoderIp);
  modal.querySelector('.encoder-output-subtitle').textContent = encoderIp;
  setModalUnitSelectOptions(modal, '.encoder-output-unit-select', getAvailableEncoderUnits(), encoderIp);
  modal.querySelector('.encoder-output-body').innerHTML = '<div class="encoder-output-loading">Loading...</div>';
  modal.classList.remove('hidden');
  try {
    const data = await getJSON(`/api/encoder_output?encoder=${encodeURIComponent(encoderIp)}`);
    modal._sessions = JSON.parse(JSON.stringify(data.sessions || []));
    renderEncoderOutputSessions(modal, modal._sessions);
  } catch (err) {
    modal.querySelector('.encoder-output-body').innerHTML = `<div class="encoder-output-error">Failed to load output settings: ${escAttr(err.message || err)}</div>`;
  }
}

function streamFields(path, stream) {
  const s = stream || {};
  const fec = s.fec || {};
  const rtcp = s.rtcp || {};
  return `<div class="encoder-output-grid">
      <label><span>Enabled</span><input type="checkbox" data-path="${path}.enabled" ${s.enabled ? 'checked' : ''}></label>
      <label><span>Address</span><input type="text" data-path="${path}.destination_address" value="${escAttr(s.destination_address)}"></label>
      <label><span>Port</span><input type="number" data-path="${path}.destination_port" min="0" max="65535" value="${escAttr(s.destination_port)}"></label>
      <label><span>DSCP</span><select data-path="${path}.dscp" data-number="true">${dscpOptions(s.dscp)}</select></label>
      <label><span>TTL</span><input type="number" data-path="${path}.ttl" min="0" max="255" value="${escAttr(s.ttl)}"></label>
      <label><span>RTCP</span><input type="checkbox" data-path="${path}.rtcp.enabled" ${rtcp.enabled ? 'checked' : ''}></label>
      <label><span>FEC</span><input type="checkbox" data-path="${path}.fec.enabled" ${fec.enabled ? 'checked' : ''}></label>
      <label><span>FEC rows</span><input type="number" data-path="${path}.fec.rows" min="0" value="${escAttr(fec.rows)}"></label>
      <label><span>FEC columns</span><input type="number" data-path="${path}.fec.columns" min="0" value="${escAttr(fec.columns)}"></label>
    </div>`;
}

function renderEncoderOutputSessions(modal, sessions) {
  const body = modal.querySelector('.encoder-output-body');
  if (!sessions.length) {
    body.innerHTML = '<div class="encoder-output-empty">No output sessions found.</div>';
    return;
  }
  const visibleSessions = sessions.slice(0, 2);
  body.innerHTML = visibleSessions.map((session, idx) => {
    const sap = session.sap || {};
    const scrambling = session.scrambling || {};
    const group = session.encodergroup || {};
    const audio = session.audio || {};
    const aes67 = audio.aes67 || {};
    const aux = session.aux || {};
    const bidi = aux.bidirectional || {};
    const video = session.video || {};
    return `<section class="encoder-output-session" data-session-index="${idx}">
        <button type="button" class="encoder-output-session-title">${escAttr(session.name || `session${idx + 1}`)}</button>
        <div class="encoder-output-session-body">
          <div class="encoder-output-two">
            <fieldset>
              <legend>SAP</legend>
              <div class="encoder-output-grid">
                <label><span>Enabled</span><input type="checkbox" data-path="sap.enabled" ${sap.enabled ? 'checked' : ''}></label>
                <label><span>Name</span><input type="text" data-path="sap.name" value="${escAttr(sap.name)}"></label>
                <label><span>Originator</span><input type="text" data-path="sap.originator" value="${escAttr(sap.originator)}"></label>
                <label><span>Category</span><input type="text" data-path="sap.categorisation" value="${escAttr(sap.categorisation)}"></label>
                <label><span>Frequency</span><input type="number" data-path="sap.frequency" min="1" value="${escAttr(sap.frequency)}"></label>
                <label><span>Audio always</span><input type="checkbox" data-path="sap.audio_always" ${sap.audio_always ? 'checked' : ''}></label>
                <label class="encoder-output-wide"><span>Description</span><input type="text" data-path="sap.description" value="${escAttr(sap.description)}"></label>
              </div>
            </fieldset>
            <fieldset>
              <legend>Session</legend>
              <div class="encoder-output-grid">
                <label><span>Interface</span><select data-path="interface">${sessionInterfaceOptions(sessions, session.interface)}</select></label>
                <label><span>Scrambling</span><input type="checkbox" data-path="scrambling.enabled" ${scrambling.enabled ? 'checked' : ''}></label>
                <label><span>Key</span><input type="text" data-path="scrambling.key" value="${escAttr(scrambling.key)}"></label>
                <label><span>Key type</span><input type="text" data-path="scrambling.keytype" value="${escAttr(scrambling.keytype)}"></label>
                <label><span>Group</span><input type="checkbox" data-path="encodergroup.enabled" ${group.enabled ? 'checked' : ''}></label>
                <label><span>Trigger</span><input type="text" data-path="encodergroup.trigger" value="${escAttr(group.trigger)}"></label>
              </div>
            </fieldset>
          </div>
          <fieldset>
            <legend>Video Stream</legend>
            <div class="encoder-output-grid encoder-output-encoder-row">
              <label><span>Encoder</span><select data-path="video.encoder">${optionList(['', 'vc2_encoder1', 'vc2_encoder2'], video.encoder, encoderLabel)}</select></label>
            </div>
            ${streamFields('video.stream', video.stream)}
          </fieldset>
          <fieldset>
            <legend>Audio Stream</legend>
            <div class="encoder-output-grid encoder-output-encoder-row">
              <label><span>Source</span><select data-path="audio.encoder">${optionList(['', ...(audio.available_inputs || [])], audio.encoder, audioSourceLabel)}</select></label>
              <label><span>AES67</span><input type="checkbox" data-path="audio.aes67.enable" ${aes67.enable ? 'checked' : ''}></label>
              <label><span>Downmixing</span><select data-path="audio.aes67.downmix">${optionList(['none', 'stereo', 'mono'], aes67.downmix || 'none')}</select></label>
            </div>
            ${streamFields('audio.stream', audio.stream)}
          </fieldset>
          <fieldset>
            <legend>AUX Stream</legend>
            <div class="encoder-output-grid encoder-output-encoder-row">
              <label><span>Source</span><select data-path="aux.encoder">${optionList([''], aux.encoder || '', auxSourceLabel)}</select></label>
              <label><span>Bidirectional</span><input type="checkbox" data-path="aux.bidirectional.enabled" ${bidi.enabled ? 'checked' : ''}></label>
              <label><span>Listen port</span><input type="number" data-path="aux.bidirectional.listen_port" min="0" max="65535" value="${escAttr(bidi.listen_port)}"></label>
            </div>
            ${streamFields('aux.stream', aux.stream)}
          </fieldset>
          <div class="encoder-output-session-actions">
            <button type="button" class="encoder-output-session-save">Save ${escAttr(session.name || `session${idx + 1}`)}</button>
          </div>
        </div>
      </section>`;
  }).join('');

  body.querySelectorAll('.encoder-output-session-title').forEach(btn => {
    btn.addEventListener('click', () => {
      btn.closest('.encoder-output-session')?.classList.toggle('collapsed');
    });
  });
  body.querySelectorAll('.encoder-output-session-save').forEach(btn => {
    btn.addEventListener('click', saveEncoderOutputModal);
  });
}

function setNestedValue(target, path, value) {
  const parts = path.split('.');
  let cur = target;
  parts.slice(0, -1).forEach(part => {
    if (!cur[part] || typeof cur[part] !== 'object') cur[part] = {};
    cur = cur[part];
  });
  cur[parts[parts.length - 1]] = value;
}

function collectEncoderOutputSessions(modal) {
  const sessions = JSON.parse(JSON.stringify(modal._sessions || []));
  modal.querySelectorAll('.encoder-output-session').forEach(section => {
    const idx = Number(section.getAttribute('data-session-index'));
    const session = sessions[idx];
    if (!session) return;
    section.querySelectorAll('[data-path]').forEach(input => {
      let value;
      if (input.type === 'checkbox') value = input.checked;
      else if (input.tagName === 'SELECT' && input.getAttribute('data-number') === 'true') value = Number(input.value);
      else if (input.type === 'number') value = input.value === '' ? '' : Number(input.value);
      else value = input.value;
      setNestedValue(session, input.getAttribute('data-path'), value);
    });
  });
  return sessions;
}

async function saveEncoderOutputModal() {
  const modal = ensureEncoderOutputModal();
  const encoderIp = modal.getAttribute('data-enc-ip');
  if (!encoderIp) return;
  const saveBtn = modal.querySelector('.encoder-output-save');
  saveBtn.disabled = true;
  try {
    const sessions = collectEncoderOutputSessions(modal);
    const res = await postJSON('/api/encoder_output', {encoder: encoderIp, sessions});
    if (!res.ok) throw new Error(res.error || 'Failed to save encoder output');
    modal._sessions = JSON.parse(JSON.stringify(res.sessions || sessions));
    renderEncoderOutputSessions(modal, modal._sessions);
    toast('Encoder output updated', true);
    refresh();
  } catch (err) {
    toast('Encoder output update failed: ' + err.message, false);
  } finally {
    saveBtn.disabled = false;
  }
}

function slateLogoOptions(currentValue) {
  return optionList(['OmniStream_Encoder'], currentValue || 'OmniStream_Encoder');
}

function scalerValue(scaler) {
  const s = scaler || {};
  if (!s.enable) return 'disable';
  const w = Number(s.width);
  const h = Number(s.height);
  if (w > 0 && h > 0) return `${w}x${h}`;
  return 'enable';
}

function scalerOptions(currentValue, encoderIndex) {
  const encoder1Options = [
    'disable',
    '3840x2160',
    '2880x1584',
    '2592x1440',
    '2560x1440',
    '1920x1104',
    '1920x1080',
    '1920x1072',
    '1792x960',
    '1728x960',
    '1440x816',
    '1280x736',
    '1280x720',
    '960x544',
    '960x528',
    '864x480',
    '640x368',
    '640x360',
    '480x272'
  ];
  const encoder2Options = [
    '1920x1080',
    '1920x1072',
    '1792x960',
    '1728x960',
    '1440x816',
    '1280x736',
    '1280x720',
    '960x544',
    '960x528',
    '864x480',
    '640x368',
    '640x360',
    '480x272'
  ];
  return optionList(encoderIndex === 1 ? encoder2Options : encoder1Options, currentValue, value => value === 'disable' ? 'disable' : value);
}

function ensureEncoderEncodingModal() {
  let modal = document.getElementById('encoder_encoding_modal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'encoder_encoding_modal';
  modal.className = 'encoder-output-modal hidden';
  modal.innerHTML = `<div class="encoder-output-card">
      <div class="encoder-output-head">
        <div>
          <h4>Encoder Encoding</h4>
          <span class="encoder-encoding-subtitle"></span>
        </div>
        <div class="encoder-output-head-actions">
          <select class="modal-unit-select encoder-encoding-unit-select" aria-label="Select encoder"></select>
          <button type="button" class="encoder-encoding-close" aria-label="Close">x</button>
        </div>
      </div>
      <div class="encoder-encoding-body encoder-output-body"></div>
      <div class="encoder-output-actions">
        <button type="button" class="encoder-encoding-footer-close">Close</button>
        <button type="button" class="encoder-encoding-refresh">Refresh</button>
        <button type="button" class="encoder-encoding-save">Save</button>
      </div>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click', evt => {
    if (evt.target === modal) modal.classList.add('hidden');
  });
  modal.querySelector('.encoder-encoding-close').addEventListener('click', () => modal.classList.add('hidden'));
  modal.querySelector('.encoder-encoding-footer-close').addEventListener('click', () => modal.classList.add('hidden'));
  modal.querySelector('.encoder-encoding-refresh').addEventListener('click', () => {
    const encoderIp = modal.getAttribute('data-enc-ip');
    if (encoderIp) openEncoderEncodingModal(encoderIp);
  });
  modal.querySelector('.encoder-encoding-unit-select').addEventListener('change', evt => {
    const encoderIp = evt.target.value;
    if (encoderIp) openEncoderEncodingModal(encoderIp);
  });
  modal.querySelector('.encoder-encoding-save').addEventListener('click', saveEncoderEncodingModal);
  return modal;
}

async function openEncoderEncodingModal(encoderIp) {
  const modal = ensureEncoderEncodingModal();
  modal.setAttribute('data-enc-ip', encoderIp);
  modal.querySelector('.encoder-encoding-subtitle').textContent = encoderIp;
  setModalUnitSelectOptions(modal, '.encoder-encoding-unit-select', getAvailableEncoderUnits(), encoderIp);
  modal.querySelector('.encoder-encoding-body').innerHTML = '<div class="encoder-output-loading">Loading...</div>';
  modal.classList.remove('hidden');
  try {
    const data = await getJSON(`/api/encoder_encoding?encoder=${encodeURIComponent(encoderIp)}`);
    modal._encoders = JSON.parse(JSON.stringify(data.encoders || []));
    modal._inputOptions = data.input_options || [];
    renderEncoderEncodingPanels(modal, modal._encoders, modal._inputOptions, encoderIp);
  } catch (err) {
    modal.querySelector('.encoder-encoding-body').innerHTML = `<div class="encoder-output-error">Failed to load encoding settings: ${escAttr(err.message || err)}</div>`;
  }
}

function renderEncoderEncodingPanels(modal, encoders, inputOptions, encoderIp) {
  const body = modal.querySelector('.encoder-encoding-body');
  if (!encoders.length) {
    body.innerHTML = '<div class="encoder-output-empty">No encoders found.</div>';
    return;
  }
  const visibleEncoders = encoders.slice(0, 2);
  body.innerHTML = visibleEncoders.map((encoder, idx) => {
    const scaler = encoder.scaler || {};
    const slate = encoder.slate || {};
    const thumbnail = encoder.thumbnail || {};
    const currentScaler = scalerValue(scaler);
    const thumbnailUrl = `http://${encoderIp}/thumbnail/thumbnail${idx + 1}.jpg`;
    const showThumbnail = idx === 0 || encoder.thumbnail;
    return `<section class="encoder-output-session encoder-encoding-panel" data-encoder-index="${idx}">
        <button type="button" class="encoder-output-session-title">${escAttr(`Encoder ${idx + 1}`)}</button>
        <div class="encoder-output-session-body">
          <fieldset>
            <legend>Encoding</legend>
            <div class="encoder-output-grid">
              <label><span>Input</span><select data-path="input">${optionList(['', ...(inputOptions || [])], encoder.input, value => value || 'Not used')}</select></label>
              <label><span>Max bit rate</span><input type="number" data-path="bitrate" min="0" value="${escAttr(encoder.bitrate)}"></label>
              <label><span>Max bit depth</span><select data-path="bitdepth" data-number="true">${optionList([8, 10, 12], encoder.bitdepth, value => `${value}-bit`)}</select></label>
              <label><span>Max subsampling</span><select data-path="subsampling">${optionList(['420', '422', '444'], encoder.subsampling, value => String(value).replace(/(\\d)(\\d)(\\d)/, '$1:$2:$3'))}</select></label>
              <label><span>Force YUV</span><input type="checkbox" data-path="force_yuv" ${encoder.force_yuv ? 'checked' : ''}></label>
              <label><span>Slate mode</span><select data-path="slate.mode">${optionList(['off', 'auto', 'manual'], slate.mode || 'auto')}</select></label>
              <label><span>Slate logo</span><select data-path="slate.logo">${slateLogoOptions(slate.logo)}</select></label>
              <label><span>Scaler</span><select data-path="_scaler_value">${scalerOptions(currentScaler, idx)}</select></label>
            </div>
          </fieldset>
          ${showThumbnail ? `<fieldset>
            <legend>Thumbnail</legend>
            <div class="encoder-output-grid">
              <label><span>Enable</span><input type="checkbox" data-path="thumbnail.enable" ${thumbnail.enable ? 'checked' : ''}></label>
            </div>
            <div class="encoder-thumbnail-preview-row">
              <button type="button" class="encoder-thumbnail-copy" data-copy-url="${escAttr(thumbnailUrl)}">Copy URI</button>
              <img src="${escAttr(thumbnailUrl)}?t=${Date.now()}" alt="Encoder ${idx + 1} thumbnail preview">
            </div>
          </fieldset>` : ''}
          <div class="encoder-output-session-actions">
            <button type="button" class="encoder-encoding-panel-save">Save Encoder ${idx + 1}</button>
          </div>
        </div>
      </section>`;
  }).join('');

  body.querySelectorAll('.encoder-encoding-panel-save').forEach(btn => {
    btn.addEventListener('click', saveEncoderEncodingModal);
  });
  body.querySelectorAll('.encoder-thumbnail-copy').forEach(btn => {
    btn.addEventListener('click', async () => {
      const url = btn.getAttribute('data-copy-url') || '';
      try {
        await navigator.clipboard.writeText(url);
        toast('Thumbnail URI copied', true);
      } catch {
        toast(url, true);
      }
    });
  });
}

function applyScalerValue(encoder, value) {
  if (!encoder.scaler || typeof encoder.scaler !== 'object') encoder.scaler = {};
  if (value === 'disable') {
    encoder.scaler.enable = false;
    return;
  }
  encoder.scaler.enable = true;
  const match = String(value || '').match(/^(\d+)x(\d+)$/);
  if (match) {
    encoder.scaler.width = Number(match[1]);
    encoder.scaler.height = Number(match[2]);
  }
}

function collectEncoderEncodingSettings(modal) {
  const encoders = JSON.parse(JSON.stringify(modal._encoders || []));
  modal.querySelectorAll('.encoder-encoding-panel').forEach(section => {
    const idx = Number(section.getAttribute('data-encoder-index'));
    const encoder = encoders[idx];
    if (!encoder) return;
    section.querySelectorAll('[data-path]').forEach(input => {
      const path = input.getAttribute('data-path');
      let value;
      if (input.type === 'checkbox') value = input.checked;
      else if (input.tagName === 'SELECT' && input.getAttribute('data-number') === 'true') value = Number(input.value);
      else if (input.type === 'number') value = input.value === '' ? '' : Number(input.value);
      else value = input.value;
      if (path === '_scaler_value') applyScalerValue(encoder, value);
      else setNestedValue(encoder, path, value);
    });
  });
  return encoders;
}

async function saveEncoderEncodingModal() {
  const modal = ensureEncoderEncodingModal();
  const encoderIp = modal.getAttribute('data-enc-ip');
  if (!encoderIp) return;
  const saveBtn = modal.querySelector('.encoder-encoding-save');
  saveBtn.disabled = true;
  try {
    const encoders = collectEncoderEncodingSettings(modal);
    const res = await postJSON('/api/encoder_encoding', {encoder: encoderIp, encoders});
    if (!res.ok) throw new Error(res.error || 'Failed to save encoding settings');
    modal._encoders = JSON.parse(JSON.stringify(res.encoders || encoders));
    modal._inputOptions = res.input_options || modal._inputOptions || [];
    renderEncoderEncodingPanels(modal, modal._encoders, modal._inputOptions, encoderIp);
    toast('Encoder encoding updated', true);
    refresh();
  } catch (err) {
    toast('Encoder encoding update failed: ' + err.message, false);
  } finally {
    saveBtn.disabled = false;
  }
}

function isSapCapableDecoder(decoder) {
  const model = ((decoder?.model || '').trim().toLowerCase());
  if (model === 'hw-omni-d4111' || model === 'at-omni-d4111') {
    return true;
  }
  return model === 'hw-omni-d4511' ||
    model === 'hw-omni-d4521' ||
    model === 'at-omni-d4511' ||
    model === 'at-omni-d4521';
}

function supportsFsColorspace(decoder) {
  const model = ((decoder?.model || '').trim().toLowerCase());
  return model === 'hw-omni-d4111' ||
    model === 'at-omni-d4111' ||
    model === 'hw-omni-d4511' ||
    model === 'at-omni-d4511';
}

function formatEdgeModeLabel(mode) {
  return String(mode || '').replace(/_/g, ' ');
}

function normalizeEdgeMode(mode) {
  return String(mode || '').toLowerCase().replace(/_/g, ' ').trim();
}

function clampVideoWallSize(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 1;
  return Math.max(1, Math.min(5, Math.trunc(n)));
}

function isPixelVideoWallUnit(unit) {
  return String(unit || '').trim().toLowerCase() === 'pixels';
}

function getVideoWallPixelSource(key) {
  return VIDEO_WALL_PIXEL_SOURCES[key] || VIDEO_WALL_PIXEL_SOURCES['4k'];
}

function inferVideoWallPixelSource(decoder) {
  const stored = videoWallPixelSourceByDecoder.get(decoder?.ip);
  if (stored && VIDEO_WALL_PIXEL_SOURCES[stored]) return stored;
  const width = Number(decoder?.video_wall_width);
  if (Number.isFinite(width) && width > 0) {
    return width <= 960 ? '1080p' : '4k';
  }
  return '4k';
}

function videoWallPixelSourceOptions(selectedKey) {
  return Object.entries(VIDEO_WALL_PIXEL_SOURCES)
    .map(([key, source]) => `<option value="${escAttr(key)}" ${key === selectedKey ? 'selected' : ''}>${escAttr(source.label)}</option>`)
    .join('');
}

function readVideoWallNumber(modal, selector, label, minValue) {
  const input = modal.querySelector(selector);
  const value = Number(input?.value);
  if (!input || !Number.isFinite(value) || value < minValue) {
    input?.focus();
    throw new Error(`${label} must be ${minValue > 0 ? 'greater than 0' : '0 or greater'}`);
  }
  return value;
}

function collectVideoWallConfig(modal) {
  const decoderIp = modal.getAttribute('data-dec-ip');
  if (!decoderIp) throw new Error('Decoder is missing');
  const unit = modal.querySelector('.dec-vw-unit-select')?.value || '';
  if (isPixelVideoWallUnit(unit)) {
    videoWallPixelSourceByDecoder.set(decoderIp, modal.querySelector('.dec-vw-pixel-source-select')?.value || '4k');
  }
  const payload = {
    decoder: decoderIp,
    video_wall_unit: unit,
    video_wall_width: readVideoWallNumber(modal, '.dec-vw-width-input', 'Width', 0.01),
    video_wall_height: readVideoWallNumber(modal, '.dec-vw-height-input', 'Height', 0.01),
    video_wall_horizontal: readVideoWallNumber(modal, '.dec-vw-horizontal-input', 'Horizontal', 0),
    video_wall_vertical: readVideoWallNumber(modal, '.dec-vw-vertical-input', 'Vertical', 0),
    video_wall_rotation: Number(modal.querySelector('.dec-vw-rotation-select')?.value || 0),
    video_wall_edge_mode: modal.querySelector('.dec-vw-edge-mode-select')?.value || '',
    video_wall_edge_top: readVideoWallNumber(modal, '.dec-vw-edge-top-input', 'Top edge compensation', 0),
    video_wall_edge_bottom: readVideoWallNumber(modal, '.dec-vw-edge-bottom-input', 'Bottom edge compensation', 0),
    video_wall_edge_left: readVideoWallNumber(modal, '.dec-vw-edge-left-input', 'Left edge compensation', 0),
    video_wall_edge_right: readVideoWallNumber(modal, '.dec-vw-edge-right-input', 'Right edge compensation', 0),
  };
  if (!isPixelVideoWallUnit(unit)) {
    payload.video_wall_total_width = readVideoWallNumber(modal, '.dec-vw-total-width-input', 'Total display width', 0.01);
    payload.video_wall_total_height = readVideoWallNumber(modal, '.dec-vw-total-height-input', 'Total display height', 0.01);
  }
  return payload;
}

function setVideoWallInputValue(modal, selector, value) {
  const input = modal.querySelector(selector);
  if (input) input.value = value;
}

function inferVideoWallLayoutFromFields(modal) {
  const unit = modal.querySelector('.dec-vw-unit-select')?.value || '';
  const source = getVideoWallPixelSource(modal.querySelector('.dec-vw-pixel-source-select')?.value);
  const width = Number(modal.querySelector('.dec-vw-width-input')?.value);
  const height = Number(modal.querySelector('.dec-vw-height-input')?.value);
  if (isPixelVideoWallUnit(unit) && Number.isFinite(width) && width > 0 && Number.isFinite(height) && height > 0) {
    modal.dataset.vwLayoutWidth = String(Math.max(1, Math.min(5, Math.round(source.width / width))));
    modal.dataset.vwLayoutHeight = String(Math.max(1, Math.min(5, Math.round(source.height / height))));
  }
}

function getVideoWallLayoutSize(modal) {
  const width = Number(modal.dataset.vwLayoutWidth);
  const height = Number(modal.dataset.vwLayoutHeight);
  return {
    width: Number.isFinite(width) && width > 0 ? width : 2,
    height: Number.isFinite(height) && height > 0 ? height : 2,
  };
}

function setVideoWallPixelSizeFields(modal) {
  const unit = modal.querySelector('.dec-vw-unit-select')?.value || '';
  if (!isPixelVideoWallUnit(unit)) return;
  const source = getVideoWallPixelSource(modal.querySelector('.dec-vw-pixel-source-select')?.value);
  const layout = getVideoWallLayoutSize(modal);
  setVideoWallInputValue(modal, '.dec-vw-width-input', Math.round(source.width / layout.width));
  setVideoWallInputValue(modal, '.dec-vw-height-input', Math.round(source.height / layout.height));
}

function syncVideoWallUnitRows(modal) {
  const unit = modal.querySelector('.dec-vw-unit-select')?.value || '';
  const pixels = isPixelVideoWallUnit(unit);
  modal.querySelectorAll('.dec-vw-total-size-row').forEach(row => {
    row.classList.toggle('is-hidden', pixels);
  });
  modal.querySelectorAll('.dec-vw-pixel-source-row').forEach(row => {
    row.classList.toggle('is-hidden', !pixels);
  });
  modal.querySelectorAll('.dec-vw-width-input,.dec-vw-height-input').forEach(input => {
    input.readOnly = pixels;
  });
  setVideoWallPixelSizeFields(modal);
}

function ensureVideoWallModal() {
  let modal = document.getElementById('video_wall_picker_modal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'video_wall_picker_modal';
  modal.className = 'video-wall-modal hidden';
  modal.innerHTML = `<div class="video-wall-modal-card">
      <div class="video-wall-modal-head">
        <h4>Video Wall Layout</h4>
        <button type="button" class="video-wall-modal-close" aria-label="Close">x</button>
      </div>
      <div class="video-wall-modal-controls">
        <label>Columns
          <select class="video-wall-size-width">
            ${[1,2,3,4,5].map(n => `<option value="${n}">${n}</option>`).join('')}
          </select>
        </label>
        <label>Rows
          <select class="video-wall-size-height">
            ${[1,2,3,4,5].map(n => `<option value="${n}">${n}</option>`).join('')}
          </select>
        </label>
      </div>
      <div class="video-wall-grid" role="grid" aria-label="Video wall position grid"></div>
      <p class="video-wall-hint">Select a cell to set Horizontal/Vertical position.</p>
    </div>`;
  document.body.appendChild(modal);
  modal.addEventListener('click', (evt) => {
    if (evt.target === modal) {
      modal.classList.add('hidden');
    }
  });
  modal.querySelector('.video-wall-modal-close').addEventListener('click', () => {
    modal.classList.add('hidden');
  });
  return modal;
}

function openVideoWallPicker(decoderIp, widthValue, heightValue, onPick) {
  const modal = ensureVideoWallModal();
  const widthSelect = modal.querySelector('.video-wall-size-width');
  const heightSelect = modal.querySelector('.video-wall-size-height');
  const grid = modal.querySelector('.video-wall-grid');

  const renderGrid = () => {
    const cols = clampVideoWallSize(widthSelect.value);
    const rows = clampVideoWallSize(heightSelect.value);
    grid.innerHTML = '';
    grid.style.setProperty('--vw-grid-cols', String(cols));
    for (let y = 0; y < rows; y += 1) {
      for (let x = 0; x < cols; x += 1) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'video-wall-grid-cell';
        btn.textContent = `${x},${y}`;
        btn.title = `Set position ${x}, ${y}`;
        btn.addEventListener('click', async () => {
          modal.classList.add('hidden');
          await onPick({decoderIp, gridWidth: cols, gridHeight: rows, gridX: x, gridY: y});
        });
        grid.appendChild(btn);
      }
    }
  };

  widthSelect.value = String(clampVideoWallSize(widthValue));
  heightSelect.value = String(clampVideoWallSize(heightValue));
  widthSelect.onchange = renderGrid;
  heightSelect.onchange = renderGrid;
  renderGrid();
  modal.classList.remove('hidden');
}

function syncDecoderFields(ip, fields) {
  if (!lastState || !fields || fields.error) return false;
  let changed = clearConfigImportBusyOnPoll(ip);
  const applyFields = (dec) => {
    if (!dec) return;
    for (const key of [
      'host', 'hostname', 'fw', 'version', 'firmwareversion',
      'ip1_addr', 'ip1_port', 'ip3_addr', 'ip3_port',
      'sap_input_enabled', 'input_session', 'input_session_options',
      'hdcp_support_version', 'hdcp_supported_versions',
      'video_input', 'audio_input', 'video_input_options', 'audio_input_options',
      'stretch_crop_mode', 'stretch_crop_mode_options',
      'resolution', 'resolution_options',
      'framerate', 'framerate_options',
      'fast_switching_enabled', 'fast_switching_timeout', 'fast_switching_colorspace', 'fast_switching_colorspace_options',
      'video_wall_enabled', 'video_wall_unit', 'video_wall_unit_options',
      'video_wall_total_width', 'video_wall_total_height',
      'video_wall_grid_width', 'video_wall_grid_height', 'video_wall_grid_x', 'video_wall_grid_y',
      'video_wall_width', 'video_wall_height', 'video_wall_horizontal', 'video_wall_vertical',
      'video_wall_rotation', 'video_wall_rotation_options',
      'video_wall_edge_mode', 'video_wall_edge_mode_options',
      'video_wall_edge_top', 'video_wall_edge_bottom', 'video_wall_edge_left', 'video_wall_edge_right',
    ]) {
      if (isLiveFieldPending('decoder', ip, key)) continue;
      if (fields[key] !== undefined) {
        dec[key] = fields[key];
        changed = true;
      }
    }
  };
  applyFields((lastState.decoders || []).find(d => d.ip === ip));
  applyFields((lastState._rawDecoders || []).find(d => d.ip === ip));
  return changed;
}

function buildDecoderInputSection(decoder) {
  const sapCapable = isSapCapableDecoder(decoder);
  const hasFsColorspace = supportsFsColorspace(decoder);
  const enabled = !!decoder.sap_input_enabled;
  const checked = enabled ? 'checked' : '';
  const sessionOptions = buildSelectOptions(decoder.input_session_options, decoder.input_session, 'No sessions');
  const sessionDisabled = enabled ? '' : 'disabled';
  const avInputDisabled = enabled ? 'disabled' : '';
  const hdcpOptions = buildSelectOptions(decoder.hdcp_supported_versions, decoder.hdcp_support_version, 'No versions');
  const videoInputOptions = buildInputSelectOptions(decoder.video_input_options, decoder.video_input, 'video', decoder);
  const audioInputOptions = buildInputSelectOptions(decoder.audio_input_options, decoder.audio_input, 'audio', decoder);
  const stretchCropOptions = buildSelectOptions(decoder.stretch_crop_mode_options, decoder.stretch_crop_mode, 'No modes');
  const fastSwitchingEnabled = !!decoder.fast_switching_enabled;
  const videoWallEnabled = !!decoder.video_wall_enabled;
  const decoderResolutionOptions = (Array.isArray(decoder.resolution_options) ? decoder.resolution_options : [])
    .filter(opt => (!fastSwitchingEnabled && !videoWallEnabled) || String(opt).trim().toLowerCase() !== 'input');
  const decoderResolution = (fastSwitchingEnabled || videoWallEnabled) && String(decoder.resolution || '').trim().toLowerCase() === 'input'
    ? 'auto'
    : decoder.resolution;
  const resolutionOptions = buildSelectOptions(decoderResolutionOptions, decoderResolution, 'No resolutions');
  const framerateOptions = buildSelectOptions(decoder.framerate_options, decoder.framerate, 'No rates');
  const fsmColorOptions = buildSelectOptions(decoder.fast_switching_colorspace_options, decoder.fast_switching_colorspace, 'No colorspaces');
  const fsmChecked = fastSwitchingEnabled ? 'checked' : '';
  const fsmTimeout = decoder.fast_switching_timeout ?? '';
  const vwEnabled = videoWallEnabled;
  const vwEnabledChecked = vwEnabled ? 'checked' : '';
  const vwUnitOptions = buildSelectOptions(decoder.video_wall_unit_options, decoder.video_wall_unit, 'No units');
  const vwRotationOptions = buildSelectOptions(decoder.video_wall_rotation_options, decoder.video_wall_rotation, 'No rotation');
  const currentEdgeModeNorm = normalizeEdgeMode(decoder.video_wall_edge_mode);
  const vwEdgeModeOptions = (Array.isArray(decoder.video_wall_edge_mode_options) ? decoder.video_wall_edge_mode_options : [])
    .map(opt => `<option value="${opt}" ${normalizeEdgeMode(opt) === currentEdgeModeNorm ? 'selected' : ''}>${formatEdgeModeLabel(opt)}</option>`)
    .join('') || `<option value="">No modes</option>`;
  const vwEdgeHidden = (currentEdgeModeNorm === 'none') ? 'is-hidden' : '';
  const vwIsPixels = isPixelVideoWallUnit(decoder.video_wall_unit);
  const vwTotalSizeHidden = vwIsPixels ? 'is-hidden' : '';
  const vwPixelSourceHidden = vwIsPixels ? '' : 'is-hidden';
  const vwPixelSourceKey = inferVideoWallPixelSource(decoder);
  const vwPixelReadonly = vwIsPixels ? 'readonly' : '';

  const videoWallSection = colTd(`
      <div class="dec-vw-section">
        <div class="dec-vw-enable-row">
          <label>
            <input type="checkbox" class="dec-vw-enabled-toggle" data-dec-ip="${decoder.ip}" ${vwEnabledChecked}> Enable
          </label>
          <button type="button" class="dec-vw-config-btn ${vwEnabled ? '' : 'is-hidden'}" data-dec-ip="${decoder.ip}" title="Configure Video Wall">Configure</button>
        </div>
        <div class="video-wall-config-modal hidden" data-dec-ip="${decoder.ip}">
          <div class="video-wall-config-card">
            <div class="video-wall-config-head">
              <div>
                <h4>Video Wall</h4>
                <select class="modal-unit-select video-wall-decoder-select" data-dec-ip="${decoder.ip}" aria-label="Select decoder">${buildUnitSelectOptions(getVideoWallDecoderUnits(), decoder.ip)}</select>
              </div>
              <button type="button" class="video-wall-config-close" data-dec-ip="${decoder.ip}" aria-label="Close">x</button>
            </div>
            <div class="dec-vw-fields" data-dec-ip="${decoder.ip}">
              <div class="dec-vw-row">
                <span>Unit</span>
                <select class="dec-vw-unit-select" data-dec-ip="${decoder.ip}">${vwUnitOptions}</select>
              </div>
              <div class="dec-vw-row dec-vw-pixel-source-row ${vwPixelSourceHidden}">
                <span>Source resolution</span>
                <select class="dec-vw-pixel-source-select" data-dec-ip="${decoder.ip}">${videoWallPixelSourceOptions(vwPixelSourceKey)}</select>
              </div>
              <div class="dec-vw-row dec-vw-total-size-row ${vwTotalSizeHidden}">
                <span>Total display width</span>
                <input type="number" class="dec-vw-total-width-input" data-dec-ip="${decoder.ip}" min="0.01" step="0.01" value="${decoder.video_wall_total_width ?? ''}">
              </div>
              <div class="dec-vw-row dec-vw-total-size-row ${vwTotalSizeHidden}">
                <span>Total display height</span>
                <input type="number" class="dec-vw-total-height-input" data-dec-ip="${decoder.ip}" min="0.01" step="0.01" value="${decoder.video_wall_total_height ?? ''}">
              </div>
              <div class="dec-vw-row">
                <span>Width</span>
                <input type="number" class="dec-vw-width-input" data-dec-ip="${decoder.ip}" min="0.01" step="0.01" value="${decoder.video_wall_width ?? ''}" ${vwPixelReadonly}>
              </div>
              <div class="dec-vw-row">
                <span>Height</span>
                <input type="number" class="dec-vw-height-input" data-dec-ip="${decoder.ip}" min="0.01" step="0.01" value="${decoder.video_wall_height ?? ''}" ${vwPixelReadonly}>
              </div>
              <div class="dec-vw-row">
                <span>Horizontal</span>
                <input type="number" class="dec-vw-horizontal-input" data-dec-ip="${decoder.ip}" min="0" step="0.01" value="${decoder.video_wall_horizontal ?? ''}">
              </div>
              <div class="dec-vw-row">
                <span>Vertical</span>
                <input type="number" class="dec-vw-vertical-input" data-dec-ip="${decoder.ip}" min="0" step="0.01" value="${decoder.video_wall_vertical ?? ''}">
              </div>
              <div class="dec-vw-row">
                <span>Rotation</span>
                <select class="dec-vw-rotation-select" data-dec-ip="${decoder.ip}">${vwRotationOptions}</select>
              </div>
              <div class="dec-vw-row">
                <span>Edge Comp</span>
                <select class="dec-vw-edge-mode-select" data-dec-ip="${decoder.ip}">${vwEdgeModeOptions}</select>
              </div>
              <div class="dec-vw-edge-fields ${vwEdgeHidden}" data-dec-ip="${decoder.ip}">
                <div class="dec-vw-row"><span>Top</span><input type="number" class="dec-vw-edge-top-input" data-dec-ip="${decoder.ip}" min="0" step="0.01" value="${decoder.video_wall_edge_top ?? 0}"></div>
                <div class="dec-vw-row"><span>Bottom</span><input type="number" class="dec-vw-edge-bottom-input" data-dec-ip="${decoder.ip}" min="0" step="0.01" value="${decoder.video_wall_edge_bottom ?? 0}"></div>
                <div class="dec-vw-row"><span>Left</span><input type="number" class="dec-vw-edge-left-input" data-dec-ip="${decoder.ip}" min="0" step="0.01" value="${decoder.video_wall_edge_left ?? 0}"></div>
                <div class="dec-vw-row"><span>Right</span><input type="number" class="dec-vw-edge-right-input" data-dec-ip="${decoder.ip}" min="0" step="0.01" value="${decoder.video_wall_edge_right ?? 0}"></div>
              </div>
              <div class="dec-vw-row">
                <button type="button" class="dec-vw-picker-btn" data-dec-ip="${decoder.ip}">Video Wall Layout</button>
              </div>
            </div>
            <div class="video-wall-config-actions">
              <button type="button" class="dec-vw-save-btn" data-dec-ip="${decoder.ip}">Save</button>
              <button type="button" class="video-wall-config-close" data-dec-ip="${decoder.ip}">Close</button>
            </div>
          </div>
        </div>
      </div>
    `, 'decVideoWall');

  return `${sapCapable ? colTd(`<input type="checkbox" class="dec-sap-input-toggle" data-dec-ip="${decoder.ip}" ${checked}>`, 'decOutput') : colTd('N/A', 'decOutput')}
        ${sapCapable ? colTd(`<select class="dec-input-session-select" data-dec-ip="${decoder.ip}" ${sessionDisabled}>${sessionOptions}</select>`, 'decOutput') : colTd('N/A', 'decOutput')}
        ${colTd(`<select class="dec-hdcp-select" data-dec-ip="${decoder.ip}">${hdcpOptions}</select>`, 'decOutput')}
        ${colTd(`<select class="dec-video-input-select" data-dec-ip="${decoder.ip}" ${avInputDisabled}>${videoInputOptions}</select>`, 'decOutput')}
        ${colTd(`<select class="dec-audio-input-select" data-dec-ip="${decoder.ip}" ${avInputDisabled}>${audioInputOptions}</select>`, 'decOutput')}
        ${colTd(`<select class="dec-stretch-crop-select" data-dec-ip="${decoder.ip}">${stretchCropOptions}</select>`, 'decResolution')}
        ${colTd(`<select class="dec-resolution-select" data-dec-ip="${decoder.ip}">${resolutionOptions}</select>`, 'decResolution')}
        ${colTd(`<select class="dec-framerate-select" data-dec-ip="${decoder.ip}">${framerateOptions}</select>`, 'decResolution')}
        ${colTd(`<input type="checkbox" class="dec-fsm-enabled-toggle" data-dec-ip="${decoder.ip}" ${fsmChecked}>`, 'decFastSwitching')}
        ${colTd(`<input type="number" class="dec-fsm-timeout-input" data-dec-ip="${decoder.ip}" min="0" value="${fsmTimeout}">`, 'decFastSwitching')}
        ${hasFsColorspace ? colTd(`<select class="dec-fsm-colorspace-select" data-dec-ip="${decoder.ip}">${fsmColorOptions}</select>`, 'decFastSwitching') : colTd('N/A', 'decFastSwitching')}
        ${videoWallSection}`;
}

function attachQueuedSelect(selector, options) {
  document.querySelectorAll(selector).forEach(select => {
    select.addEventListener('change', () => {
      const ip = select.getAttribute(options.ipAttr);
      if(!ip) return;
      const previousValue = getLiveDevice(options.kind, ip)?.[options.field] ?? '';
      const nextValue = select.value;
      enqueueLiveWrite({
        kind: options.kind,
        ip,
        field: options.field,
        value: nextValue,
        control: select,
        endpoint: options.endpoint,
        payload: options.payload(ip, nextValue),
        previousValue,
        successMessage: options.successMessage,
        failureMessage: options.failureMessage,
        sync: res => options.sync(ip, res),
      });
    });
  });
}

function attachQueuedToggle(selector, options) {
  document.querySelectorAll(selector).forEach(toggle => {
    toggle.addEventListener('change', () => {
      const ip = toggle.getAttribute(options.ipAttr);
      if(!ip) return;
      const previousValue = !!getLiveDevice(options.kind, ip)?.[options.field];
      const nextValue = !!toggle.checked;
      enqueueLiveWrite({
        kind: options.kind,
        ip,
        field: options.field,
        value: nextValue,
        control: toggle,
        endpoint: options.endpoint,
        payload: options.payload(ip, nextValue),
        previousValue,
        successMessage: options.successMessage,
        failureMessage: options.failureMessage,
        sync: res => options.sync(ip, res),
      });
    });
  });
}

const liveFieldControlSelectors = {
  'encoder:input_auto_switch': ['.enc-auto-switch-toggle', 'data-enc-ip'],
  'encoder:active_input': ['.enc-active-input-select', 'data-enc-ip'],
  'encoder:edid': ['.enc-edid-select', 'data-enc-ip'],
  'encoder:hdcp_support_version': ['.enc-hdcp-select', 'data-enc-ip'],
  'decoder:sap_input_enabled': ['.dec-sap-input-toggle', 'data-dec-ip'],
  'decoder:input_session': ['.dec-input-session-select', 'data-dec-ip'],
  'decoder:hdcp_support_version': ['.dec-hdcp-select', 'data-dec-ip'],
  'decoder:video_input': ['.dec-video-input-select', 'data-dec-ip'],
  'decoder:audio_input': ['.dec-audio-input-select', 'data-dec-ip'],
  'decoder:stretch_crop_mode': ['.dec-stretch-crop-select', 'data-dec-ip'],
  'decoder:resolution': ['.dec-resolution-select', 'data-dec-ip'],
  'decoder:framerate': ['.dec-framerate-select', 'data-dec-ip'],
  'decoder:fast_switching_enabled': ['.dec-fsm-enabled-toggle', 'data-dec-ip'],
  'decoder:fast_switching_timeout': ['.dec-fsm-timeout-input', 'data-dec-ip'],
  'decoder:fast_switching_colorspace': ['.dec-fsm-colorspace-select', 'data-dec-ip'],
};

function applyLivePendingControlStates() {
  liveWriteLatest.forEach((token, key) => {
    const [kind, ip, field] = key.split(':');
    const entry = liveFieldControlSelectors[`${kind}:${field}`];
    if(!entry) return;
    const [selector, attr] = entry;
    document.querySelectorAll(`${selector}[${attr}="${CSS.escape(ip)}"]`).forEach(control => markLiveControl(control, 'pending'));
  });
}

function render(s){
  const enc = s.encoders||[], dec = s.decoders||[];
  updateCodecMismatchWarning(enc, dec);
  const t = document.querySelector('#matrix');
  if (t) {
  // Master checkbox for select all
  const allChecked = dec.length > 0 && dec.every(d => selectedDecoders.has(d.ip));
  const someChecked = dec.some(d => selectedDecoders.has(d.ip));
  const head = `<tr><th class="row-head"><input type="checkbox" id="group-master-checkbox" ${allChecked ? 'checked' : ''} ${!allChecked && someChecked ? 'indeterminate' : ''}></th><th class="row-head">Decoders \\ Encoders</th>` + enc.map(e=>
    `<th class="enc-head"><div class="col-header"><span class="enc-ip"><a href="http://${e.ip}" target="_blank" style="color:inherit;text-decoration:none;cursor:pointer;" title="Open ${e.ip} in new tab">${e.ip}</a></span><small class="enc-host">${escAttr(e.host || '')}</small></div></th>`
  ).join('') + '</tr>';
  const rows = dec.map(d=>{
    const checkedGroup = selectedDecoders.has(d.ip) ? 'checked' : '';
    const cells = enc.map(e=>{
      const codecCompatible = routeCodecCompatible(e, d);
      const pending = pendingRoutes.get(d.ip);
      const videoMatch = (pending && (pending.mode === 'video' || pending.mode === 'av')) ?
        (e.ip === pending.encoderIp) :
        videoMatchesEncoder(d, e);
      const audioMatch = (pending && (pending.mode === 'audio' || pending.mode === 'av')) ?
        (e.ip === pending.encoderIp) :
        audioMatchesEncoder(d, e);
      const checked = videoMatch ? 'checked' : '';
      const audioCls = audioMatch ? ' audio-on' : '';
      const disabledCls = codecCompatible ? '' : ' codec-blocked';
      const disabledAttr = codecCompatible ? '' : ' disabled';
      const title = codecCompatible ? '' : ` title="${escAttr(routeCodecMismatchMessage(e, d))}"`;
      return `<td class="cell${disabledCls}" data-dec="${d.ip}" data-enc="${e.ip}"${title}>
                <span class="radio-wrap">
                  <input type="radio" name="video-${d.ip}" ${checked}${disabledAttr} aria-label="Route video ${d.ip} -> ${e.ip}" data-preview-url="http://${e.ip}/thumbnail/thumbnail1.jpg"/>
                  <span class="dot${audioCls}" aria-hidden="true"></span>
                </span>
              </td>`;
    }).join('');
    return `<tr><td style="width:28px;min-width:28px;max-width:28px;padding:0 1px;"><input type="checkbox" class="group-checkbox" data-dec-ip="${d.ip}" style="width:14px;height:14px;vertical-align:middle;" ${checkedGroup}></td><th class="row-head"><a href="http://${d.ip}" target="_blank" style="color:inherit;text-decoration:none;cursor:pointer;" title="Open ${d.ip} in new tab">${d.ip}</a><br/><small>${escAttr(d.host || '')}</small></th>${cells}</tr>`;
  }).join('');
  t.innerHTML = head + rows;

  t.querySelectorAll('.radio-wrap').forEach(wrap => {
    const radio = wrap.querySelector('input[type="radio"]');
    if (!radio) return;
    wrap.addEventListener('mouseenter', (evt) => showMatrixHoverPreview(radio, evt));
    wrap.addEventListener('mouseleave', () => hideMatrixHoverPreview());
  });

  t.querySelectorAll('td.cell').forEach(cell=>{
    cell.addEventListener('click', async (e)=>{
      const dec = cell.getAttribute('data-dec');
      const enc = cell.getAttribute('data-enc');
      const mode = routeMode;
      const encoderUnit = (lastState?._rawEncoders || lastState?.encoders || []).find(e => e.ip === enc);
      const decoderUnit = (lastState?._rawDecoders || lastState?.decoders || []).find(d => d.ip === dec);
      if (!routeCodecCompatible(encoderUnit, decoderUnit)) {
        toast(routeCodecMismatchMessage(encoderUnit, decoderUnit), false);
        e.stopPropagation();
        return;
      }

      // If any group checkboxes are checked, do group routing
      const checkedDecoders = Array.from(document.querySelectorAll('.group-checkbox:checked')).map(cb => cb.getAttribute('data-dec-ip'));
      const groupRoute = checkedDecoders.length > 1 && checkedDecoders.includes(dec);
      const requestedTargets = groupRoute ? checkedDecoders : [dec];
      const targets = requestedTargets.filter(targetDec => {
        const targetUnit = (lastState?._rawDecoders || lastState?.decoders || []).find(d => d.ip === targetDec);
        return routeCodecCompatible(encoderUnit, targetUnit);
      });
      const blockedTargets = requestedTargets.filter(targetDec => !targets.includes(targetDec));
      if (!targets.length) {
        toast('No route applied: selected decoder codec does not match the encoder codec', false);
        e.stopPropagation();
        return;
      }
      if (blockedTargets.length) {
        toast(`Skipped ${blockedTargets.length} decoder(s) with codec mismatch`, false);
      }

      targets.forEach(targetDec => recordPendingRoute(targetDec, enc, mode));

      // Set radios and UI for all targets
      for (const targetDec of targets) {
        if(mode === 'video' || mode === 'av'){
          const radio = t.querySelector(`td.cell[data-dec="${targetDec}"][data-enc="${enc}"] input[type=radio]`);
          if(radio){
            const name = radio.getAttribute('name');
            t.querySelectorAll(`input[name="${name}"]`).forEach(r=>r.checked=false);
            radio.checked = true;
          }
        }
        if(mode === 'av' || mode === 'audio'){
          const rowCells = t.querySelectorAll(`td.cell[data-dec="${targetDec}"] .dot`);
          rowCells.forEach(d=>d.classList.remove('audio-on'));
          const dot = t.querySelector(`td.cell[data-dec="${targetDec}"][data-enc="${enc}"] .dot`);
          if(dot) dot.classList.add('audio-on');
        }
      }

      // Queue route requests per decoder. Different decoders can run in
      // parallel, while rapid clicks on the same decoder collapse to latest.
      try {
        const results = await Promise.all(targets.map(targetDec =>
          enqueueRouteWrite(targetDec, enc, mode)
            .then(res => { console.log('[ROUTE RESPONSE]', {targetDec, res}); return res; })
        ));
        let errors = [];
        let updated = false;
        let applied = 0;
        results.forEach((res, i) => {
          if(res?.skipped) return;
          if(!res.ok) {
            errors.push(targets[i] + ': ' + (res.error || 'Route failed'));
          } else if(res.decoder) {
            updated = true;
            applied++;
          }
        });
        if (updated) requestMatrixRender();
        if (errors.length > 0) {
          toast('Some routes failed: ' + errors.join('; '), false);
        } else if (applied > 0) {
          toast(`Route ${mode.toUpperCase()} applied`, true);
        }
        // Fetch updated decoder inputs without full refresh (no loading overlay)
        setTimeout(()=>{ pollDecoderInputs().catch(()=>{}); }, 700);
      } catch(err){
        toast('Route error: '+err.message, false);
      }
      e.stopPropagation();
    });
  });

  // Handle group checkbox changes
  t.querySelectorAll('.group-checkbox').forEach(cb => {
    cb.addEventListener('change', function() {
      const ip = cb.getAttribute('data-dec-ip');
      if (cb.checked) selectedDecoders.add(ip);
      else selectedDecoders.delete(ip);
      // Update master checkbox state
      const master = document.getElementById('group-master-checkbox');
      const all = Array.from(t.querySelectorAll('.group-checkbox'));
      if (master) {
        master.checked = all.every(c => c.checked);
        master.indeterminate = !master.checked && all.some(c => c.checked);
      }
    });
  });
  // Master checkbox logic
  const masterCheckbox = document.getElementById('group-master-checkbox');
  if (masterCheckbox) {
    const all = Array.from(t.querySelectorAll('.group-checkbox'));
    masterCheckbox.checked = all.length > 0 && all.every(c => c.checked);
    masterCheckbox.indeterminate = !masterCheckbox.checked && all.some(c => c.checked);
    masterCheckbox.addEventListener('change', function() {
      all.forEach(cb => {
        cb.checked = masterCheckbox.checked;
        const ip = cb.getAttribute('data-dec-ip');
        if (masterCheckbox.checked) selectedDecoders.add(ip);
        else selectedDecoders.delete(ip);
      });
    });
  }
  }

  const encTbl = document.querySelector('#encTbl');
  const decTbl = document.querySelector('#decTbl');
  if (encTbl && decTbl) {
  encTbl.innerHTML = `<tr>
      <th>IP</th><th>Hostname</th><th>Codec</th>
      ${colTh('Model', 'encUnitInfo')}${colTh('FW', 'encUnitInfo')}${colTh('Serial', 'encUnitInfo')}${colTh('Video', 'encUnitInfo')}${colTh('Audio', 'encUnitInfo')}
      ${colTh('Cable Present', 'encInput')}${colTh('Input Auto-Switch', 'encInput')}${colTh('Active Input', 'encInput')}${colTh('EDID', 'encInput')}${colTh('HDCP Version', 'encInput')}
      ${colTh('Session', 'encOutput')}
      ${colTh('Encoding', 'encEncoding')}
      ${colTh('Configuration', 'encConfiguration')}
    </tr>` +
    enc.map(e=>{
      return `<tr>
        <td><a href="http://${escAttr(e.ip)}" target="_blank" class="text-link" title="Open ${escAttr(e.ip)}">${escAttr(e.ip)}</a></td>
        <td>${buildHostnameInput(e)}</td>
        <td>${buildCodecControl(e)}</td>
        ${colTd(e.model||'', 'encUnitInfo')}
        ${colTd(e.fw||'', 'encUnitInfo')}
        ${colTd(e.serial||'', 'encUnitInfo')}
        ${colTd((e.v_mcast||'')+':'+(e.v_port||''), 'encUnitInfo')}
        ${colTd((e.a_mcast||'')+':'+(e.a_port||''), 'encUnitInfo')}
        ${buildEncoderInputSection(e)}
        ${buildEncoderOutputLink(e)}
        ${buildEncoderEncodingLink(e)}
        ${buildUnitConfigActions(e, 'encConfiguration')}
      </tr>`;
    }).join('');
  decTbl.innerHTML = `<tr>
      <th>IP</th><th>Hostname</th><th>Codec</th>
      ${colTh('Model', 'decUnitInfo')}${colTh('FW', 'decUnitInfo')}${colTh('Serial', 'decUnitInfo')}${colTh('ip_input1', 'decUnitInfo')}${colTh('ip_input3', 'decUnitInfo')}
      ${colTh('SAP Input', 'decOutput')}${colTh('Input Session', 'decOutput')}${colTh('HDCP Version', 'decOutput')}${colTh('Video Input', 'decOutput')}${colTh('Audio Input', 'decOutput')}
      ${colTh('Stretch/Crop Mode', 'decResolution')}${colTh('Resolution', 'decResolution')}${colTh('Framerate', 'decResolution')}
      ${colTh('Fast Switching', 'decFastSwitching')}${colTh('FS Timeout', 'decFastSwitching')}${colTh('FS Colorspace', 'decFastSwitching')}
      ${colTh('Video Wall', 'decVideoWall')}
      ${colTh('Configuration', 'decConfiguration')}
    </tr>` +
    dec.map(d=>`<tr><td><a href="http://${escAttr(d.ip)}" target="_blank" class="text-link" title="Open ${escAttr(d.ip)}">${escAttr(d.ip)}</a></td><td>${buildHostnameInput(d)}</td><td>${buildCodecControl(d)}</td>${colTd(d.model||'', 'decUnitInfo')}${colTd(d.fw||'', 'decUnitInfo')}${colTd(d.serial||'', 'decUnitInfo')}${colTd((d.ip1_addr||'')+':'+(d.ip1_port||''), 'decUnitInfo')}${colTd((d.ip3_addr||'')+':'+(d.ip3_port||''), 'decUnitInfo')}${buildDecoderInputSection(d)}${buildUnitConfigActions(d, 'decConfiguration')}</tr>`).join('');
  }

  applySectionFilterControls();

  document.querySelectorAll('.matrix-hostname-edit').forEach(input => {
    input.addEventListener('click', evt => evt.stopPropagation());
    input.addEventListener('keydown', handleMatrixHostnameKey);
    input.addEventListener('blur', () => submitMatrixHostnameEdit(input));
  });

  document.querySelectorAll('.codec-select').forEach(select => {
    const markActive = () => { activeCodecSelectIp = select.getAttribute('data-ip') || ''; };
    const clearActive = () => {
      const ip = select.getAttribute('data-ip') || '';
      setTimeout(() => {
        if (activeCodecSelectIp === ip && document.activeElement !== select) activeCodecSelectIp = '';
      }, 250);
    };
    select.addEventListener('focus', markActive);
    select.addEventListener('pointerdown', markActive);
    select.addEventListener('blur', clearActive);
    select.addEventListener('change', async () => {
      const ip = select.getAttribute('data-ip');
      const previous = select.getAttribute('data-last-value') || '';
      const next = select.value;
      if (!ip || next === previous) return;
      if (!await omniConfirm({
        title: 'Change Codec',
        message: `Changing codec on ${ip} to ${codecLabel(next)} will reboot the unit.`,
        confirmText: 'Change and Reboot',
        danger: true,
        summary: [
          {label: 'Device', value: ip},
          {label: 'New Codec', value: codecLabel(next)}
        ]
      })) {
        select.value = previous;
        return;
      }
      select.disabled = true;
      try {
        const res = await postJSON('/api/codec', {ip, system_mode: next});
        if (!res.ok) throw new Error(res.error || 'Codec update failed');
        syncCodecFields(ip, res.system_mode || next, res.supported_system_modes || null);
        activeCodecSelectIp = '';
        toast('Codec updated; unit will reboot', true);
        requestMatrixRender();
      } catch (err) {
        select.value = previous;
        toast('Codec update failed: ' + err.message, false);
      } finally {
        select.disabled = false;
      }
    });
  });

  document.querySelectorAll('.dec-vw-config-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const decoderIp = btn.getAttribute('data-dec-ip');
      openVideoWallConfigDecoderIp = decoderIp;
      const modal = document.querySelector(`.video-wall-config-modal[data-dec-ip="${decoderIp}"]`);
      if (modal) {
        inferVideoWallLayoutFromFields(modal);
        syncVideoWallUnitRows(modal);
        modal.classList.remove('hidden');
      }
      try {
        await refreshVideoWallDecoder(decoderIp);
      } catch (err) {
        toast('Video wall refresh failed: ' + err.message, false);
      }
    });
  });

  if (openVideoWallConfigDecoderIp) {
    const openModal = document.querySelector(`.video-wall-config-modal[data-dec-ip="${openVideoWallConfigDecoderIp}"]`);
    if (openModal) {
      inferVideoWallLayoutFromFields(openModal);
      syncVideoWallUnitRows(openModal);
      openModal.classList.remove('hidden');
    }
    else openVideoWallConfigDecoderIp = null;
  }

  document.querySelectorAll('.video-wall-config-modal').forEach(modal => {
    modal.addEventListener('click', evt => {
      if (evt.target === modal) {
        openVideoWallConfigDecoderIp = null;
        modal.classList.add('hidden');
      }
    });
  });

  document.querySelectorAll('.video-wall-config-close').forEach(btn => {
    btn.addEventListener('click', () => {
      const modal = btn.closest('.video-wall-config-modal');
      openVideoWallConfigDecoderIp = null;
      if (modal) modal.classList.add('hidden');
    });
  });

  document.querySelectorAll('.video-wall-decoder-select').forEach(select => {
    select.disabled = getVideoWallDecoderUnits().length <= 1;
    select.addEventListener('change', async () => {
      const decoderIp = select.value;
      if (!decoderIp) return;
      openVideoWallConfigDecoderIp = decoderIp;
      try {
        await refreshVideoWallDecoder(decoderIp);
      } catch (err) {
        requestMatrixRender();
        toast('Video wall refresh failed: ' + err.message, false);
      }
    });
  });

  attachQueuedToggle('.enc-auto-switch-toggle', {
    kind: 'encoder',
    ipAttr: 'data-enc-ip',
    field: 'input_auto_switch',
    endpoint: '/api/encoder_input',
    payload: (ip, value) => ({encoder: ip, input_auto_switch: value}),
    sync: (ip, res) => syncEncoderFields(ip, res.encoder || {}),
    successMessage: 'Input auto-switch updated',
    failureMessage: 'Input auto-switch update failed',
  });

  attachQueuedSelect('.enc-active-input-select', {
    kind: 'encoder',
    ipAttr: 'data-enc-ip',
    field: 'active_input',
    endpoint: '/api/encoder_input',
    payload: (ip, value) => ({encoder: ip, active_input: value}),
    sync: (ip, res) => syncEncoderFields(ip, res.encoder || {}),
    successMessage: 'Active input updated',
    failureMessage: 'Active input update failed',
  });

  attachQueuedSelect('.enc-edid-select', {
    kind: 'encoder',
    ipAttr: 'data-enc-ip',
    field: 'edid',
    endpoint: '/api/encoder_input',
    payload: (ip, value) => ({encoder: ip, edid: value}),
    sync: (ip, res) => syncEncoderFields(ip, res.encoder || {}),
    successMessage: 'EDID updated',
    failureMessage: 'EDID update failed',
  });

  attachQueuedSelect('.enc-hdcp-select', {
    kind: 'encoder',
    ipAttr: 'data-enc-ip',
    field: 'hdcp_support_version',
    endpoint: '/api/encoder_input',
    payload: (ip, value) => ({encoder: ip, hdcp_support_version: value}),
    sync: (ip, res) => syncEncoderFields(ip, res.encoder || {}),
    successMessage: 'HDCP version updated',
    failureMessage: 'HDCP update failed',
  });

  document.querySelectorAll('.enc-output-link').forEach(btn => {
    btn.addEventListener('click', () => {
      const encoderIp = btn.getAttribute('data-enc-ip');
      if (encoderIp) openEncoderOutputModal(encoderIp);
    });
  });

  document.querySelectorAll('.enc-encoding-link').forEach(btn => {
    btn.addEventListener('click', () => {
      const encoderIp = btn.getAttribute('data-enc-ip');
      if (encoderIp) openEncoderEncodingModal(encoderIp);
    });
  });

  document.querySelectorAll('.unit-config-export-link').forEach(btn => {
    btn.addEventListener('click', () => {
      const ip = btn.getAttribute('data-unit-ip');
      if (ip) window.location = `/api/unit_config/export?ip=${encodeURIComponent(ip)}`;
    });
  });

  document.querySelectorAll('.unit-config-import-link').forEach(btn => {
    btn.addEventListener('click', () => {
      const ip = btn.getAttribute('data-unit-ip');
      if (ip) openUnitConfigImportModal(ip);
    });
  });

  attachQueuedToggle('.dec-sap-input-toggle', {
    kind: 'decoder',
    ipAttr: 'data-dec-ip',
    field: 'sap_input_enabled',
    endpoint: '/api/decoder_input',
    payload: (ip, value) => ({decoder: ip, sap_input_enabled: value}),
    sync: (ip, res) => syncDecoderFields(ip, res.decoder || {}),
    successMessage: 'SAP input updated',
    failureMessage: 'SAP input update failed',
  });

  attachQueuedSelect('.dec-input-session-select', {
    kind: 'decoder',
    ipAttr: 'data-dec-ip',
    field: 'input_session',
    endpoint: '/api/decoder_input',
    payload: (ip, value) => ({decoder: ip, input_session: value}),
    sync: (ip, res) => syncDecoderFields(ip, res.decoder || {}),
    successMessage: 'Input session updated',
    failureMessage: 'Input session update failed',
  });

  attachQueuedSelect('.dec-hdcp-select', {
    kind: 'decoder',
    ipAttr: 'data-dec-ip',
    field: 'hdcp_support_version',
    endpoint: '/api/decoder_input',
    payload: (ip, value) => ({decoder: ip, hdcp_support_version: value}),
    sync: (ip, res) => syncDecoderFields(ip, res.decoder || {}),
    successMessage: 'HDCP version updated',
    failureMessage: 'HDCP version update failed',
  });

  attachQueuedSelect('.dec-video-input-select', {
    kind: 'decoder',
    ipAttr: 'data-dec-ip',
    field: 'video_input',
    endpoint: '/api/decoder_input',
    payload: (ip, value) => ({decoder: ip, video_input: value}),
    sync: (ip, res) => syncDecoderFields(ip, res.decoder || {}),
    successMessage: 'Video input updated',
    failureMessage: 'Video input update failed',
  });

  attachQueuedSelect('.dec-audio-input-select', {
    kind: 'decoder',
    ipAttr: 'data-dec-ip',
    field: 'audio_input',
    endpoint: '/api/decoder_input',
    payload: (ip, value) => ({decoder: ip, audio_input: value}),
    sync: (ip, res) => syncDecoderFields(ip, res.decoder || {}),
    successMessage: 'Audio input updated',
    failureMessage: 'Audio input update failed',
  });

  attachQueuedSelect('.dec-stretch-crop-select', {
    kind: 'decoder',
    ipAttr: 'data-dec-ip',
    field: 'stretch_crop_mode',
    endpoint: '/api/decoder_input',
    payload: (ip, value) => ({decoder: ip, stretch_crop_mode: value}),
    sync: (ip, res) => syncDecoderFields(ip, res.decoder || {}),
    successMessage: 'Stretch/crop mode updated',
    failureMessage: 'Stretch/crop mode update failed',
  });

  attachQueuedSelect('.dec-resolution-select', {
    kind: 'decoder',
    ipAttr: 'data-dec-ip',
    field: 'resolution',
    endpoint: '/api/decoder_input',
    payload: (ip, value) => ({decoder: ip, resolution: value}),
    sync: (ip, res) => syncDecoderFields(ip, res.decoder || {}),
    successMessage: 'Resolution updated',
    failureMessage: 'Resolution update failed',
  });

  attachQueuedSelect('.dec-framerate-select', {
    kind: 'decoder',
    ipAttr: 'data-dec-ip',
    field: 'framerate',
    endpoint: '/api/decoder_input',
    payload: (ip, value) => ({decoder: ip, framerate: value}),
    sync: (ip, res) => syncDecoderFields(ip, res.decoder || {}),
    successMessage: 'Framerate updated',
    failureMessage: 'Framerate update failed',
  });

  attachQueuedToggle('.dec-fsm-enabled-toggle', {
    kind: 'decoder',
    ipAttr: 'data-dec-ip',
    field: 'fast_switching_enabled',
    endpoint: '/api/decoder_input',
    payload: (ip, value) => ({decoder: ip, fast_switching_enabled: value}),
    sync: (ip, res) => syncDecoderFields(ip, res.decoder || {}),
    successMessage: 'Fast switching updated',
    failureMessage: 'Fast switching update failed',
  });

  document.querySelectorAll('.dec-fsm-timeout-input').forEach(input => {
    let lastSubmittedValue = null;
    const submitTimeout = () => {
      const decoderIp = input.getAttribute('data-dec-ip');
      const prevValue = getLiveDevice('decoder', decoderIp)?.fast_switching_timeout;
      const nextValue = Number(input.value);
      if (!Number.isFinite(nextValue) || nextValue < 0) {
        input.value = prevValue ?? '';
        return;
      }
      if (lastSubmittedValue === nextValue) return;
      lastSubmittedValue = nextValue;
      enqueueLiveWrite({
        kind: 'decoder',
        ip: decoderIp,
        field: 'fast_switching_timeout',
        value: nextValue,
        control: input,
        endpoint: '/api/decoder_input',
        payload: {decoder: decoderIp, fast_switching_timeout: nextValue},
        previousValue: prevValue,
        successMessage: 'Fast switching timeout updated',
        failureMessage: 'Fast switching timeout update failed',
        sync: res => syncDecoderFields(decoderIp, res.decoder || {}),
      });
    };
    input.addEventListener('change', submitTimeout);
    input.addEventListener('blur', submitTimeout);
  });

  attachQueuedSelect('.dec-fsm-colorspace-select', {
    kind: 'decoder',
    ipAttr: 'data-dec-ip',
    field: 'fast_switching_colorspace',
    endpoint: '/api/decoder_input',
    payload: (ip, value) => ({decoder: ip, fast_switching_colorspace: value}),
    sync: (ip, res) => syncDecoderFields(ip, res.decoder || {}),
    successMessage: 'Fast switching colorspace updated',
    failureMessage: 'Fast switching colorspace update failed',
  });

  document.querySelectorAll('.dec-vw-enabled-toggle').forEach(toggle => {
    toggle.addEventListener('change', async () => {
      const decoderIp = toggle.getAttribute('data-dec-ip');
      const prevValue = !!(lastState._rawDecoders || lastState.decoders || []).find(d => d.ip === decoderIp)?.video_wall_enabled;
      const configBtn = toggle.closest('.dec-vw-section')?.querySelector('.dec-vw-config-btn');
      if (configBtn) configBtn.classList.toggle('is-hidden', !toggle.checked);
      if (!toggle.checked && openVideoWallConfigDecoderIp === decoderIp) {
        openVideoWallConfigDecoderIp = null;
      }
      try {
        const payload = {decoder: decoderIp, video_wall_enabled: toggle.checked};
        if (toggle.checked) {
          payload.resolution = '1920x1080';
        }
        const res = await postJSON('/api/decoder_input', payload);
        if (!res.ok) throw new Error(res.error || 'Failed to set video wall enable');
        syncDecoderFields(decoderIp, res.decoder || {});
        requestMatrixRender();
        toast('Video wall enable updated', true);
      } catch (err) {
        toggle.checked = prevValue;
        if (configBtn) configBtn.classList.toggle('is-hidden', !prevValue);
        toast('Video wall enable update failed: ' + err.message, false);
      }
    });
  });

  document.querySelectorAll('.dec-vw-edge-mode-select').forEach(select => {
    select.addEventListener('change', () => {
      const edgeFields = select.closest('.dec-vw-fields')?.querySelector('.dec-vw-edge-fields');
      if (edgeFields) {
        edgeFields.classList.toggle('is-hidden', normalizeEdgeMode(select.value) === 'none');
      }
    });
  });

  document.querySelectorAll('.dec-vw-unit-select').forEach(select => {
    select.addEventListener('change', () => {
      const modal = select.closest('.video-wall-config-modal');
      if (modal) syncVideoWallUnitRows(modal);
    });
  });

  document.querySelectorAll('.dec-vw-pixel-source-select').forEach(select => {
    select.addEventListener('change', () => {
      const modal = select.closest('.video-wall-config-modal');
      if (modal) {
        const decoderIp = modal.getAttribute('data-dec-ip');
        if (decoderIp) videoWallPixelSourceByDecoder.set(decoderIp, select.value);
        setVideoWallPixelSizeFields(modal);
      }
    });
  });

  document.querySelectorAll('.dec-vw-save-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const modal = btn.closest('.video-wall-config-modal');
      if (!modal) return;
      btn.disabled = true;
      try {
        const payload = collectVideoWallConfig(modal);
        const res = await postJSON('/api/decoder_input', payload);
        if (!res.ok) throw new Error(res.error || 'Failed to save video wall settings');
        syncDecoderFields(payload.decoder, res.decoder || {});
        toast('Video wall settings saved', true);
        await refreshVideoWallDecoder(payload.decoder);
      } catch (err) {
        toast('Video wall save failed: ' + err.message, false);
      } finally {
        btn.disabled = false;
      }
    });
  });

  document.querySelectorAll('.dec-vw-picker-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const decoderIp = btn.getAttribute('data-dec-ip');
      const current = (lastState._rawDecoders || lastState.decoders || []).find(d => d.ip === decoderIp) || {};
      openVideoWallPicker(
        decoderIp,
        current.video_wall_grid_width ?? current.video_wall_width ?? 1,
        current.video_wall_grid_height ?? current.video_wall_height ?? 1,
        ({decoderIp: decIp, gridWidth, gridHeight, gridX, gridY}) => {
          const configModal = document.querySelector(`.video-wall-config-modal[data-dec-ip="${decIp}"]`);
          if (!configModal) return;
          const unit = configModal.querySelector('.dec-vw-unit-select')?.value || '';
          let sourceWidth = Number(configModal.querySelector('.dec-vw-total-width-input')?.value);
          let sourceHeight = Number(configModal.querySelector('.dec-vw-total-height-input')?.value);
          let decimals = 4;
          if (isPixelVideoWallUnit(unit)) {
            const source = getVideoWallPixelSource(configModal.querySelector('.dec-vw-pixel-source-select')?.value);
            sourceWidth = source.width;
            sourceHeight = source.height;
            decimals = 0;
            configModal.dataset.vwLayoutWidth = String(gridWidth);
            configModal.dataset.vwLayoutHeight = String(gridHeight);
          }
          const cellWidth = Number.isFinite(sourceWidth) && sourceWidth > 0 ? sourceWidth / gridWidth : gridWidth;
          const cellHeight = Number.isFinite(sourceHeight) && sourceHeight > 0 ? sourceHeight / gridHeight : gridHeight;
          const formatWallValue = value => Number(value.toFixed(decimals));
          setVideoWallInputValue(configModal, '.dec-vw-width-input', formatWallValue(cellWidth));
          setVideoWallInputValue(configModal, '.dec-vw-height-input', formatWallValue(cellHeight));
          setVideoWallInputValue(configModal, '.dec-vw-horizontal-input', formatWallValue(cellWidth * gridX));
          setVideoWallInputValue(configModal, '.dec-vw-vertical-input', formatWallValue(cellHeight * gridY));
          configModal.classList.remove('hidden');
          openVideoWallConfigDecoderIp = decIp;
          toast('Video wall position staged. Click Save to send.', true);
        }
      );
    });
  });
  applyLivePendingControlStates();
}

const refreshButton = qs('#refreshBtn');
if (refreshButton) {
  refreshButton.onclick = async ()=>{
    try { await refresh(); toast('Refreshed', true); }
    catch(err){ alert('Refresh error: '+err.message); }
  };
}

// Filter input logic (set up ONCE)
document.addEventListener('DOMContentLoaded', function() {
  const encFilterInput = document.getElementById('encFilterInput');
  const decFilterInput = document.getElementById('decFilterInput');
  const encFilterClearBtn = document.getElementById('encFilterClearBtn');
  const decFilterClearBtn = document.getElementById('decFilterClearBtn');
  const configureFilterInput = document.getElementById('configureFilterInput');
  const configureFilterClearBtn = document.getElementById('configureFilterClearBtn');
  if (encFilterInput) {
    encFilterInput.addEventListener('input', function() {
      encFilterValue = encFilterInput.value;
      refresh();
    });
  }
  if (decFilterInput) {
    decFilterInput.addEventListener('input', function() {
      decFilterValue = decFilterInput.value;
      refresh();
    });
  }
  if (encFilterClearBtn && encFilterInput) {
    encFilterClearBtn.addEventListener('click', function() {
      encFilterInput.value = '';
      encFilterValue = '';
      refresh();
    });
  }
  if (decFilterClearBtn && decFilterInput) {
    decFilterClearBtn.addEventListener('click', function() {
      decFilterInput.value = '';
      decFilterValue = '';
      refresh();
    });
  }
  if (configureFilterInput) {
    configureFilterInput.addEventListener('input', function() {
      configureFilterValue = configureFilterInput.value;
      refresh();
    });
  }
  if (configureFilterClearBtn && configureFilterInput) {
    configureFilterClearBtn.addEventListener('click', function() {
      configureFilterInput.value = '';
      configureFilterValue = '';
      refresh();
    });
  }
});

setMode('av');
initStickyHeaders();
initPreviewToggle();
initTheme();
initDensity();
initSectionFilterControls();

document.addEventListener('focusout', () => {
  setTimeout(flushDeferredMatrixRender, 150);
});

// Set up polling with preference sync
let matrixPollingEnabled = localStorage.getItem('pollUnits') === 'true';
let matrixPollingInterval = null;
let encoderSettingsPollingInterval = null;
let matrixPollRunning = false;
let encoderSettingsPollRunning = false;

function startMatrixPolling() {
  if (matrixPollingInterval) return; // Already polling
  // When matrix polling is enabled, keep both decoder routes and encoder input fields fresh.
  matrixPollingInterval = setInterval(async () => {
    if (matrixPollRunning) return;
    matrixPollRunning = true;
    try { await pollMatrixDevices(); }
    finally { matrixPollRunning = false; }
  }, DECODER_POLL_MS);
  console.log('[MATRIX_POLL] Polling started');
}

function stopMatrixPolling() {
  if (matrixPollingInterval) {
    clearInterval(matrixPollingInterval);
    matrixPollingInterval = null;
    console.log('[MATRIX_POLL] Polling stopped');
  }
}

function startEncoderSettingsPolling() {
  if (encoderSettingsPollingInterval) return;
  encoderSettingsPollingInterval = setInterval(async () => {
    if (encoderSettingsPollRunning) return;
    encoderSettingsPollRunning = true;
    try { await pollEncoderInputs(); }
    finally { encoderSettingsPollRunning = false; }
  }, ENCODER_SETTINGS_POLL_MS);
  console.log('[ENCODER_POLL] Settings polling started');
}

function stopEncoderSettingsPolling() {
  if (encoderSettingsPollingInterval) {
    clearInterval(encoderSettingsPollingInterval);
    encoderSettingsPollingInterval = null;
    console.log('[ENCODER_POLL] Settings polling stopped');
  }
}

function syncPollingMode() {
  if (matrixPollingEnabled) {
    startMatrixPolling();
    stopEncoderSettingsPolling();
  } else {
    stopMatrixPolling();
    // Keep encoder input-related fields updated even when matrix route polling is disabled.
    startEncoderSettingsPolling();
  }
}

refresh().then(() => {
  // Fetch current routing and encoder settings after initial load.
  pollMatrixDevices();
  syncPollingMode();
});

// Sync polling preference from other tabs/device manager page
window.addEventListener('storage', (e) => {
  if (e.key === 'pollUnits') {
    matrixPollingEnabled = e.newValue === 'true';
    console.log('[MATRIX_POLL] Got storage event, polling now:', matrixPollingEnabled);
    syncPollingMode();
  }
});

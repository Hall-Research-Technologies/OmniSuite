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
});
const qs = (s)=>document.querySelector(s);
let routeMode = 'av';
let previewEnabled = true;

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
  if(!r.ok) throw new Error(t||r.statusText);
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

// ---- IP sort helpers ----
function ipNum(ip){
  const m = (ip||'').trim().match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if(!m) return Number.MAX_SAFE_INTEGER;
  return (+m[1]<<24) + (+m[2]<<16) + (+m[3]<<8) + (+m[4]);
}
function sortByIpAsc(arr){ return [...arr].sort((a,b)=>ipNum(a.ip)-ipNum(b.ip)); }

let lastState = null;

// Track pending routes so UI stays stable until feedback arrives.
const pendingRoutes = new Map();
const PENDING_ROUTE_MIN_MS = 3000;

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

// Group selection state for decoders
let selectedDecoders = new Set();

// Filter state
let encFilterValue = '';
let decFilterValue = '';

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
    const encoders = rawEncoders.filter(e => deviceMatchesFilter(e, encFilterValue, 'enc'));
    const decoders = rawDecoders.filter(d => deviceMatchesFilter(d, decFilterValue, 'dec'));
    lastState = {...s, encoders, decoders, _rawEncoders: rawEncoders, _rawDecoders: rawDecoders};
    resolvePendingRoutes(lastState);
    render(lastState);
  } finally {
    // Hide loading overlay when done
    if (overlay) overlay.classList.add('hidden');
  }
}

async function pollDecoderInputs(){
  if (!lastState || !lastState.decoders || lastState.decoders.length === 0) {
    return;
  }
  const decoderIps = lastState.decoders.map(d => d.ip);
  try {
    const result = await fetch('/api/poll_decoders', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({decoders: decoderIps})
    });
    const data = await result.json();
    if (data.ok && data.results) {
      // Update decoder inputs in lastState
      for (const [ip, fields] of Object.entries(data.results)) {
        if (fields.error) continue;
        const dec = lastState.decoders.find(d => d.ip === ip);
        if (dec) {
          if (fields.ip1_addr !== undefined) dec.ip1_addr = fields.ip1_addr;
          if (fields.ip1_port !== undefined) dec.ip1_port = fields.ip1_port;
          if (fields.ip3_addr !== undefined) dec.ip3_addr = fields.ip3_addr;
          if (fields.ip3_port !== undefined) dec.ip3_port = fields.ip3_port;
        }
        const rawDec = (lastState._rawDecoders || []).find(d => d.ip === ip);
        if (rawDec) {
          if (fields.ip1_addr !== undefined) rawDec.ip1_addr = fields.ip1_addr;
          if (fields.ip1_port !== undefined) rawDec.ip1_port = fields.ip1_port;
          if (fields.ip3_addr !== undefined) rawDec.ip3_addr = fields.ip3_addr;
          if (fields.ip3_port !== undefined) rawDec.ip3_port = fields.ip3_port;
        }
      }
      // Re-render with updated inputs
      resolvePendingRoutes(lastState);
      render(lastState);
      console.log(`[POLL] Updated ${data.updated || 0} decoders`);
    }
  } catch(err) {
    console.error('[POLL] Failed to poll decoders:', err);
  }
}

function render(s){
  const enc = s.encoders||[], dec = s.decoders||[];
  const t = document.querySelector('#matrix');
  // Master checkbox for select all
  const allChecked = dec.length > 0 && dec.every(d => selectedDecoders.has(d.ip));
  const someChecked = dec.some(d => selectedDecoders.has(d.ip));
  const head = `<tr><th class="row-head"><input type="checkbox" id="group-master-checkbox" ${allChecked ? 'checked' : ''} ${!allChecked && someChecked ? 'indeterminate' : ''}></th><th class="row-head">Decoders \\ Encoders</th>` + enc.map(e=>
    `<th class="enc-head"><div class="col-header"><span class="enc-ip"><a href="http://${e.ip}" target="_blank" style="color:inherit;text-decoration:none;cursor:pointer;" title="Open ${e.ip} in new tab">${e.ip}</a></span><small class="enc-host">${e.host||''}</small></div></th>`
  ).join('') + '</tr>';
  const rows = dec.map(d=>{
    const checkedGroup = selectedDecoders.has(d.ip) ? 'checked' : '';
    const cells = enc.map(e=>{
      const pending = pendingRoutes.get(d.ip);
      const videoMatch = (pending && (pending.mode === 'video' || pending.mode === 'av')) ?
        (e.ip === pending.encoderIp) :
        videoMatchesEncoder(d, e);
      const audioMatch = (pending && (pending.mode === 'audio' || pending.mode === 'av')) ?
        (e.ip === pending.encoderIp) :
        audioMatchesEncoder(d, e);
      const checked = videoMatch ? 'checked' : '';
      const audioCls = audioMatch ? ' audio-on' : '';
      return `<td class="cell" data-dec="${d.ip}" data-enc="${e.ip}">
                <span class="radio-wrap">
                  <input type="radio" name="video-${d.ip}" ${checked} aria-label="Route video ${d.ip} -> ${e.ip}" data-preview-url="http://${e.ip}/thumbnail/thumbnail1.jpg"/>
                  <span class="dot${audioCls}" aria-hidden="true"></span>
                </span>
              </td>`;
    }).join('');
    return `<tr><td style="width:28px;min-width:28px;max-width:28px;padding:0 1px;"><input type="checkbox" class="group-checkbox" data-dec-ip="${d.ip}" style="width:14px;height:14px;vertical-align:middle;" ${checkedGroup}></td><th class="row-head"><a href="http://${d.ip}" target="_blank" style="color:inherit;text-decoration:none;cursor:pointer;" title="Open ${d.ip} in new tab">${d.ip}</a><br/><small>${d.host||''}</small></th>${cells}</tr>`;
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

      // If any group checkboxes are checked, do group routing
      const checkedDecoders = Array.from(document.querySelectorAll('.group-checkbox:checked')).map(cb => cb.getAttribute('data-dec-ip'));
      const groupRoute = checkedDecoders.length > 1 && checkedDecoders.includes(dec);
      const targets = groupRoute ? checkedDecoders : [dec];

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

      // Send all route requests in parallel
      try {
        const results = await Promise.all(targets.map(targetDec =>
          postJSON('/api/route', {decoder: targetDec, encoder: enc, mode})
            .then(res => { console.log('[ROUTE RESPONSE]', {targetDec, res}); return res; })
        ));
        let errors = [];
        let updated = false;
        results.forEach((res, i) => {
          if(!res.ok) {
            errors.push(targets[i] + ': ' + (res.error || 'Route failed'));
          } else if(res.decoder && lastState && Array.isArray(lastState.decoders)) {
            const idx = lastState.decoders.findIndex(d=>d.ip===res.decoder.ip);
            if(idx >= 0){
              lastState.decoders[idx] = {...lastState.decoders[idx], ...res.decoder};
              updated = true;
            }
          }
        });
        if (updated) render(lastState);
        if (errors.length > 0) {
          toast('Some routes failed: ' + errors.join('; '), false);
        } else {
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

  const encTbl = document.querySelector('#encTbl');
  const decTbl = document.querySelector('#decTbl');
  encTbl.innerHTML = '<tr><th>IP</th><th>Hostname</th><th>Model</th><th>FW</th><th>Serial</th><th>Video</th><th>Audio</th></tr>' +
    enc.map(e=>`<tr><td>${e.ip}</td><td>${e.host||''}</td><td>${e.model||''}</td><td>${e.fw||''}</td><td>${e.serial||''}</td><td>${e.v_mcast||''}:${e.v_port||''}</td><td>${e.a_mcast||''}:${e.a_port||''}</td></tr>`).join('');
  decTbl.innerHTML = '<tr><th>IP</th><th>Hostname</th><th>Model</th><th>FW</th><th>Serial</th><th>ip_input1</th><th>ip_input3</th></tr>' +
    dec.map(d=>`<tr><td>${d.ip}</td><td>${d.host||''}</td><td>${d.model||''}</td><td>${d.fw||''}</td><td>${d.serial||''}</td><td>${(d.ip1_addr||'')+':'+(d.ip1_port||'')}</td><td>${(d.ip3_addr||'')+':'+(d.ip3_port||'')}</td></tr>`).join('');
}

qs('#refreshBtn').onclick = async ()=>{
  try { await refresh(); toast('Refreshed', true); }
  catch(err){ alert('Refresh error: '+err.message); }
};

// Filter input logic (set up ONCE)
document.addEventListener('DOMContentLoaded', function() {
  const encFilterInput = document.getElementById('encFilterInput');
  const decFilterInput = document.getElementById('decFilterInput');
  const encFilterClearBtn = document.getElementById('encFilterClearBtn');
  const decFilterClearBtn = document.getElementById('decFilterClearBtn');
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
});

setMode('av');
initStickyHeaders();
initPreviewToggle();
initTheme();

// Set up polling with preference sync
let matrixPollingEnabled = localStorage.getItem('pollUnits') === 'true';
let matrixPollingInterval = null;

function startMatrixPolling() {
  if (matrixPollingInterval) return; // Already polling
  matrixPollingInterval = setInterval(pollDecoderInputs, 5000);
  console.log('[MATRIX_POLL] Polling started');
}

function stopMatrixPolling() {
  if (matrixPollingInterval) {
    clearInterval(matrixPollingInterval);
    matrixPollingInterval = null;
    console.log('[MATRIX_POLL] Polling stopped');
  }
}

refresh().then(() => {
  // Poll decoder inputs after initial load to get current routes
  pollDecoderInputs();
  // Set up periodic polling if enabled
  if (matrixPollingEnabled) {
    startMatrixPolling();
  }
});

// Sync polling preference from other tabs/device manager page
window.addEventListener('storage', (e) => {
  if (e.key === 'pollUnits') {
    matrixPollingEnabled = e.newValue === 'true';
    console.log('[MATRIX_POLL] Got storage event, polling now:', matrixPollingEnabled);
    if (matrixPollingEnabled) {
      startMatrixPolling();
    } else {
      stopMatrixPolling();
    }
  }
});

// Collapsible sections
document.querySelectorAll('.collapsible .header').forEach(header=>{
  header.addEventListener('click', ()=>{
    const section = header.closest('.collapsible');
    section.classList.toggle('collapsed');
  });
});

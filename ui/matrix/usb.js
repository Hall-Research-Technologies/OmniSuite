const qs = (s)=>document.querySelector(s);

// Single HTML-escaping helper for this module. Every device-supplied value
// (hostnames, addresses, labels, tooltip text) goes through it before being
// interpolated into markup.
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// ===== Sticky Headers Toggle =====
function initStickyHeaders(){
  const stickySwitch = document.getElementById('sticky_switch');
  const stickyToggle = document.getElementById('sticky_headers_toggle');
  const matrixTable = document.getElementById('usbMatrix');

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
// Light and dark are applied by appearance.js, which owns every appearance
// dimension and is loaded by all four pages. This file used to carry its own
// copy, bound to controls that no longer exist.


// ===== Density =====
// Applied by appearance.js for every page. This file's copy read the
// retired `viewDensity` key and cleared body.compact from it.

async function getJSON(u){
  const r = await fetch(u); 
  if(!r.ok) throw new Error(await r.text()); 
  return r.json();
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

// toast() is provided by ui/toast.js, which every page loads. This file and
// usb.js carried byte-identical copies while Device Info had none and fell back
// to native alert().

// ---- IP sort helpers ----
function ipNum(ip){
  const m = (ip||'').trim().match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if(!m) return Number.MAX_SAFE_INTEGER;
  return (+m[1]<<24) + (+m[2]<<16) + (+m[3]<<8) + (+m[4]);
}
function sortByIpAsc(arr){ return [...arr].sort((a,b)=>ipNum(a.ip)-ipNum(b.ip)); }

// Same deterministic rule the server applies: integrated first, then numeric
// IPv4, then canonical MAC. Applied again here so the axes can never depend on
// response ordering even if an older server build is in front of this page.
function sortMatrixAxis(arr){
  return [...(arr||[])].sort((a,b)=>{
    const ka = (a.kind||'integrated')==='integrated' ? 0 : 1;
    const kb = (b.kind||'integrated')==='integrated' ? 0 : 1;
    if(ka!==kb) return ka-kb;
    const ia = ipNum(a.ip), ib = ipNum(b.ip);
    if(ia!==ib) return ia-ib;
    return String(a.mac||'').localeCompare(String(b.mac||''));
  });
}

// Structure is the membership and identity of the axes; live state is
// everything else. A state-only refresh updates cells in place so nothing moves.
function matrixSignature(lex, rex){
  return JSON.stringify([lex.map(l=>[l.usb_key,l.ip,l.mac,l.model||l.host||'']),
                         rex.map(r=>[r.usb_key,r.ip,r.mac,r.model||r.host||''])]);
}
let lastMatrixSignature = null;

let lastState = null;
let isFirstSession = true;
let usbRenderDeferred = false;

// Poll responses are not ordered. `/api/usb_state` performs live, pairing and
// link refreshes, so one call can easily outlive a route change issued after it,
// and applying it would put the old owner back on screen -- which is what made
// the Matrix look stuck until the page was reloaded.
//
// Two counters settle it. `usbStateSeq`/`usbStateApplied` discard any response
// overtaken by a newer one. `usbStateEpoch` is bumped by every completed route
// mutation, and a response already in flight when that happened describes a
// world that no longer exists, so it is dropped whatever its sequence number.
let usbStateSeq = 0;
let usbStateApplied = 0;
let usbStateEpoch = 0;

// Cells with a route request in flight, keyed by the pair they represent, so a
// re-render during the request cannot lose the pending affordance and a rebuilt
// cell picks it back up.
const usbPendingCells = new Set();
function usbPendingKey(rex, lex){ return `${rex} -> ${lex}`; }
function markUsbCellPending(cell, rex, lex){
  usbPendingCells.add(usbPendingKey(rex, lex));
  cell.classList.add('pending');
  cell.setAttribute('aria-busy', 'true');
}
function clearUsbCellPending(cell, rex, lex){
  usbPendingCells.delete(usbPendingKey(rex, lex));
  cell.classList.remove('pending');
  cell.removeAttribute('aria-busy');
}
function applyUsbPendingCells(table){
  if(!table || !usbPendingCells.size) return;
  table.querySelectorAll('td.cell').forEach(cell=>{
    const key = usbPendingKey(cell.getAttribute('data-rex'), cell.getAttribute('data-lex'));
    if(usbPendingCells.has(key)){
      cell.classList.add('pending');
      cell.setAttribute('aria-busy', 'true');
    }
  });
}

function isUsbLiveFieldActive() {
  const active = document.activeElement;
  return !!(active && active.closest && active.closest('#lexTbl, #rexTbl') && active.matches('select,textarea,input[type="text"],input[type="number"]'));
}

function requestUsbRender(force = false) {
  if (!lastState) return;
  if (!force && isUsbLiveFieldActive()) {
    usbRenderDeferred = true;
    return;
  }
  usbRenderDeferred = false;
  render(lastState);
}

function flushDeferredUsbRender() {
  if (!usbRenderDeferred || isUsbLiveFieldActive()) return;
  requestUsbRender(true);
}
const usbActionQueues = new Map();
const usbActionLatest = new Map();

function enqueueUsbAction(rex, action){
  const token = Symbol(`${rex}:${action.label || 'usb'}`);
  usbActionLatest.set(rex, token);
  const prior = usbActionQueues.get(rex) || Promise.resolve();
  const run = prior.catch(()=>{}).then(async ()=>{
    if(usbActionLatest.get(rex) !== token) return {ok:true, skipped:true};
    try {
      const res = await action.run();
      if(usbActionLatest.get(rex) !== token) return {ok:true, skipped:true};
      usbActionLatest.delete(rex);
      return res;
    } catch(err) {
      if(usbActionLatest.get(rex) === token) usbActionLatest.delete(rex);
      throw err;
    }
  }).finally(()=>{
    if(usbActionQueues.get(rex) === run) usbActionQueues.delete(rex);
  });
  usbActionQueues.set(rex, run);
  return run;
}

function ensureUsbPairing(rex){
  if(!lastState) return null;
  if(!lastState.pairings) lastState.pairings = {};
  if(!lastState.pairings[rex]) lastState.pairings[rex] = {active: null, available: []};
  if(!Array.isArray(lastState.pairings[rex].available)) lastState.pairings[rex].available = [];
  return lastState.pairings[rex];
}

function applyOptimisticUsbPair(rex, lex){
  const pairing = ensureUsbPairing(rex);
  if(!pairing) return;
  pairing.active = null;
  pairing.available = [lex];
  requestUsbRender(true);
}

function applyConfirmedUsbPairing(rex, pairing){
  if(!pairing) return false;
  const target = ensureUsbPairing(rex);
  if(!target) return false;
  target.active = pairing.active || null;
  target.available = Array.isArray(pairing.available) ? pairing.available : [];
  requestUsbRender(true);
  return true;
}

function applyOptimisticUsbUnpair(rex, lex){
  const pairing = ensureUsbPairing(rex);
  if(!pairing) return;
  if(pairing.active === lex) pairing.active = null;
  pairing.available = (pairing.available || []).filter(ip => ip !== lex);
  requestUsbRender(true);
}

function scheduleUsbVerifyRefresh(){
  setTimeout(()=>{ refresh(true).catch(()=>{}); }, 700);
  setTimeout(()=>{ refresh(true).catch(()=>{}); }, 2500);
  setTimeout(()=>{ refresh(true).catch(()=>{}); }, 6000);
}

// Every completed route operation, successful or not, invalidates any state read
// that was already in flight and forces the grid to reconcile immediately rather
// than waiting for the next poll.
function noteUsbRouteMutation(){ usbStateEpoch += 1; }

function usbLexPeerCounts(pairings){
  const counts = {};
  Object.values(pairings || {}).forEach(pairing => {
    if(!pairing) return;
    const peers = [];
    if(pairing.active) peers.push(pairing.active);
    (pairing.available || []).forEach(ip => peers.push(ip));
    [...new Set(peers)].forEach(ip => {
      counts[ip] = (counts[ip] || 0) + 1;
    });
  });
  return counts;
}

function subnet24(ip){
  const m = String(ip || '').trim().match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if(!m) return '';
  return `${m[1]}.${m[2]}.${m[3]}`;
}

function isDifferentSubnet(rexIp, lexIp){
  const rexSubnet = subnet24(rexIp);
  const lexSubnet = subnet24(lexIp);
  return !!rexSubnet && !!lexSubnet && rexSubnet !== lexSubnet;
}

let usbRefreshRunning = false;

async function refresh(force = false){
  usbRefreshRunning = true;
  if (window.omniDiag) omniDiag.count('poll.usbState.run');
  // Show loading overlay only on first session
  const overlay = document.getElementById('usb_loading_overlay');
  if (isFirstSession && overlay) overlay.classList.remove('hidden');
  
  const seq = ++usbStateSeq;
  const epoch = usbStateEpoch;
  try {
    const s = await getJSON('/api/usb_state');
    if (seq <= usbStateApplied) {
      console.log('USB state response superseded; discarded');
      return;
    }
    if (epoch !== usbStateEpoch) {
      console.log('USB state response predates a route change; discarded');
      return;
    }
    usbStateApplied = seq;
    console.log('USB state received:', s);
    // sort LEX left->right and REX top->bottom by IP
    s.lex = sortByIpAsc(s.lex||[]);
    s.rex = sortByIpAsc(s.rex||[]);
    s.matrix_lex = sortMatrixAxis(s.matrix_lex || s.lex);
    s.matrix_rex = sortMatrixAxis(s.matrix_rex || s.rex);
    console.log('After sorting - LEX:', s.lex.length, 'REX:', s.rex.length);
    lastState = s;
    // A refresh that follows a route change renders unconditionally: deferring
    // it because a dropdown happens to hold focus is how a completed change ends
    // up invisible until the operator reloads.
    requestUsbRender(force);
  } catch(err) {
    console.error('Refresh error:', err);
    toast('Refresh error: '+err.message);
  } finally {
    usbRefreshRunning = false;
    // Hide loading overlay when done and mark session as no longer first
    if (overlay) overlay.classList.add('hidden');
    isFirstSession = false;
  }
}

// Standalone AT-OMNI-311/324 inventory row.
//
// Inventory display only: these rows are appended to the LEX/REX unit lists and
// are deliberately absent from the routing grid above, which is still built from
// s.lex / s.rex alone. Standalone routing stays hardware gated, so nothing here
// is clickable or editable.
//
// Host Port is a fixed physical connector on a standalone unit, and the
// AT-OMNI-311/324 API does not establish device filtering, so neither is shown
// as a control. Peers are only stated when the server reports a fresh Advanced
// Query; cached pairing counts are never presented as authoritative.
function standaloneRow(u){
  const muted = t => `<span style="color:var(--muted)">${esc(t)}</span>`;
  // A useful state rather than a bare "Unknown": the server distinguishes a fresh
  // Advanced Query count from never-read and stale pairing information.
  const peers = u.pairing_state_fresh && u.peer_count !== null && u.peer_count !== undefined
    ? String(u.peer_count)
    : muted(u.peers_label || 'Not read');
  // Same derived liveness Configure > USB renders; there is no second calculation.
  const seen = u.last_seen ? new Date(u.last_seen * 1000).toLocaleString() : 'never this session';
  const status = u.online
    ? ''
    : `<div style="color:var(--muted);font-size:11px;" title="Last seen ${esc(seen)}">Offline (stale)</div>`;
  return `<tr data-standalone="1" data-mac="${esc(u.mac)}">`
    + `<td>${esc(u.ip||'')}${status}</td>`
    + `<td>${esc(u.host||'')}<div style="color:var(--muted);font-size:11px;">Standalone</div></td>`
    + `<td>${esc(u.usb_ip||'')}</td>`
    + `<td>${esc(u.mac||'')}</td>`
    + `<td>${esc(u.revision||'')}</td>`
    + `<td>${esc(u.protocol||'IP')}</td>`
    + `<td>${esc(u.type||'')}</td>`
    + `<td>${muted(u.type === 'LEX' ? 'Fixed / N/A' : 'N/A')}</td>`
    + `<td>${muted('Not established')}</td>`
    + `<td>${peers}</td></tr>`;
}

// ---- styled cell tooltip ---------------------------------------------------
// Replaces the native title attribute, which rendered detailed capability text
// as a single very wide browser tooltip across the grid. Bounded width, kept
// inside the viewport, and driven by hover and keyboard focus.
const usbTip = (() => {
  let el = null, hideTimer = null;
  function node(){
    if(el) return el;
    el = document.createElement('div');
    el.className = 'usb-cell-tip';
    el.setAttribute('role', 'tooltip');
    el.style.cssText = 'position:fixed;z-index:65000;max-width:320px;width:max-content;'
      + 'background:var(--card,#222a33);color:var(--text,#e9eef5);border:1px solid var(--border,rgba(255,255,255,.14));'
      + 'border-radius:6px;padding:7px 9px;font-size:12px;line-height:1.35;'
      + 'box-shadow:0 6px 20px rgba(0,0,0,.35);pointer-events:none;display:none;white-space:normal;overflow-wrap:anywhere;';
    document.body.appendChild(el);
    return el;
  }
  function show(cell){
    const label = cell.getAttribute('data-tip');
    if(!label) return;
    const detail = cell.getAttribute('data-tip-detail') || '';
    const tip = node();
    clearTimeout(hideTimer);
    // textContent, not innerHTML: getAttribute returns the decoded original, so
    // interpolating it as markup would reintroduce an injection path.
    tip.textContent = '';
    const head = document.createElement('div');
    head.style.fontWeight = '600';
    head.textContent = label;
    tip.appendChild(head);
    if(detail){
      const sub = document.createElement('div');
      sub.style.cssText = 'margin-top:3px;color:var(--muted,#9aa7b4);';
      sub.textContent = detail;
      tip.appendChild(sub);
    }
    tip.style.display = 'block';
    // Measure, then clamp inside the viewport rather than overflowing it.
    const rect = cell.getBoundingClientRect();
    const box = tip.getBoundingClientRect();
    const margin = 8;
    let left = rect.left + rect.width / 2 - box.width / 2;
    left = Math.max(margin, Math.min(left, window.innerWidth - box.width - margin));
    let top = rect.bottom + 6;
    if(top + box.height > window.innerHeight - margin) top = rect.top - box.height - 6;
    if(top < margin) top = margin;
    tip.style.left = `${Math.round(left)}px`;
    tip.style.top = `${Math.round(top)}px`;
  }
  function hide(){
    if(!el) return;
    hideTimer = setTimeout(()=>{ if(el) el.style.display = 'none'; }, 60);
  }
  return {show, hide};
})();

function bindUsbCellTips(table){
  if(!table || table.dataset.tipsBound === '1') return;
  table.dataset.tipsBound = '1';
  const target = ev => ev.target && ev.target.closest ? ev.target.closest('td.cell[data-tip]') : null;
  table.addEventListener('mouseover', ev => { const c = target(ev); if(c) usbTip.show(c); });
  table.addEventListener('mouseout', ev => { if(target(ev)) usbTip.hide(); });
  table.addEventListener('focusin', ev => { const c = target(ev); if(c) usbTip.show(c); });
  table.addEventListener('focusout', ev => { if(target(ev)) usbTip.hide(); });
  window.addEventListener('scroll', () => usbTip.hide(), {passive:true});
}

// Route capability for one cell, resolved by the server from both endpoint
// classifications. The UI never decides which control plane owns a route.
function cellCapability(s, rexKey, lexKey){
  return ((s.capabilities||{})[rexKey]||{})[lexKey] || {state:'UNSUPPORTED_MIXED', control_path:'', enabled:false, note:'Not validated.'};
}

function render(s){
  console.log('Render called with:', s);
  // Matrix axes carry both families; fall back to the integrated-only arrays.
  // Sorted here as well as in refresh(): render() must never depend on its
  // caller having ordered the axes.
  const lex = sortMatrixAxis(s.matrix_lex || s.lex || []), rex = sortMatrixAxis(s.matrix_rex || s.rex || []);
  const pairings = s.pairings||{}; // {rex_ip: {active: lex_ip, available: [lex_ip1, lex_ip2, ...]}}
  const standaloneRoutes = s.standalone_routes || {};
  const lexPeerCounts = usbLexPeerCounts(pairings);
  // A standalone AT-OMNI-311 may own several AT-OMNI-324 peers, so its column is
  // counted separately and is not limited by the integrated five-REX rule.
  const standalonePeerCounts = {};
  // Keyed by canonical MAC so an integrated LEX in a mixed route counts too.
  Object.values(standaloneRoutes).forEach(r=>{
    if(r && r.active){
      const key = String(r.active).toUpperCase();
      standalonePeerCounts[key] = (standalonePeerCounts[key]||0)+1;
    }
  });
  console.log('LEX units:', lex.length, lex);
  console.log('REX units:', rex.length, rex);
  console.log('Pairings:', pairings);
  
  const t = document.querySelector('#usbMatrix');
  
  // Build header row with LEX IPs across top. Model, hostname and address
  // are reported by the device and this string is assigned to innerHTML
  // below, so an unescaped hostname containing markup would execute in the
  // operator's browser.
  const head = '<tr><th class="row-head">REX \\\\ LEX</th>' + lex.map(l=>
    `<th class="enc-head"><div class="col-header"><span class="lex-label"><a href="http://${esc(l.ip)}" target="_blank" style="color:inherit;text-decoration:none;cursor:pointer;" title="Open ${esc(l.ip)} in new tab">${esc(l.ip)}</a></span><small class="enc-host">${esc(l.kind==='standalone'?(l.model||'HW-OMNI-311'):(l.host||''))}</small></div></th>`
  ).join('') + '</tr>';
  
  // Build rows: each REX can select one LEX; each LEX can feed up to 5 REX.
  const rows = rex.map(r=>{
    const rexKey = r.usb_key || r.ip;
    const standaloneRex = r.kind === 'standalone';
    const rexPairing = standaloneRex ? (standaloneRoutes[rexKey] || {active:null, available:[]})
                                     : (pairings[r.ip] || {active: null, available: []});
    const activeLex = rexPairing.active;
    const availableLex = rexPairing.available || [];

    const cells = lex.map(l=>{
      const lexKey = l.usb_key || l.ip;
      const cap = cellCapability(s, rexKey, lexKey);
      const standaloneRoute = cap.control_path === 'standalone_udp';
      // A UDP-controlled cell reads its route from the UDP-observed state and
      // matches on canonical MAC, because the row's display key is a control IP
      // when the endpoint is integrated. usb_icron still owns integrated rows.
      const udpRoute = standaloneRoute ? standaloneRoutes[rexKey] : null;
      const lexMac = (l.usb_mac || '').toUpperCase();
      const isActive = udpRoute
        ? !!(udpRoute.active && lexMac && String(udpRoute.active).toUpperCase() === lexMac)
        : activeLex === lexKey;
      const isAvailable = udpRoute ? false : (availableLex || []).includes(lexKey);
      const isPaired = isActive || isAvailable;

      // Integrated routing keeps its established rules unchanged.
      const subnetBlocked = !standaloneRoute && !isPaired && isDifferentSubnet(r.ip, l.ip);
      const peerLimit = standaloneRoute ? 7 : 5;
      const peerCount = standaloneRoute ? (standalonePeerCounts[lexMac] || 0) : (lexPeerCounts[lexKey] || 0);
      const overLimit = !isPaired && peerCount >= peerLimit;

      const canPair = cap.enabled && (isPaired || (!subnetBlocked && !overLimit));
      const checked = isPaired ? 'checked' : '';
      const innerCls = isActive ? ' audio-on' : '';
      const disabledAttr = canPair ? '' : 'disabled';
      const disabledClass = canPair ? '' : ' disabled';
      // Concise operator-facing text. The backend enum stays in data-state for
      // logs and tests; a long native title would render as a 900px browser
      // tooltip across the grid, so the styled tooltip renders these instead.
      let tipLabel = '', tipDetail = '';
      const experimental = cap.enabled && cap.mixed && cap.data_plane_verified === false;
      if(!cap.enabled){ tipLabel = cap.label || 'Not routable'; tipDetail = cap.detail || ''; }
      else if(subnetBlocked){ tipLabel = 'Not routable on current network';
                              tipDetail = `${r.ip} and ${l.ip} are on different subnets.`; }
      else if(overLimit){ tipLabel = 'Peer limit reached';
                          tipDetail = `${l.ip} already has its maximum of ${peerLimit} peers.`; }
      else if(isPaired){
        tipLabel = 'Connected';
        tipDetail = experimental ? 'Mixed USB route — data transport validation pending. Click to remove this route.'
                                 : 'Click to remove this route.';
      }
      else {
        tipLabel = 'Available';
        tipDetail = experimental ? 'Mixed USB route — data transport not yet validated. Click to create this route.'
                                 : 'Click to create this route.';
      }
      const staleRoute = standaloneRoute && (udpRoute ? udpRoute.fresh === false : rexPairing.fresh === false);
      if(staleRoute) tipDetail = (tipDetail ? tipDetail + ' ' : '') + 'Pairing state stale; awaiting refresh.';
      const tipAttr = ` data-tip="${esc(tipLabel)}"` + (tipDetail ? ` data-tip-detail="${esc(tipDetail)}"` : '');
      const staleAttr = (staleRoute ? ' data-stale="1"' : '') + (experimental ? ' data-experimental="1"' : '');

      // data-rex/data-lex are display keys (a control IP for an integrated unit).
      // data-*-mac is the routing identity and is the only thing routing may use.
      const macAttr = ` data-lex-mac="${esc(l.usb_mac || '')}" data-rex-mac="${esc(r.usb_mac || '')}"`;
      return `<td class="cell${disabledClass}" data-rex="${rexKey}" data-lex="${lexKey}"${macAttr} data-active="${isActive}" data-paired="${isPaired}" data-path="${cap.control_path}" data-state="${cap.state}"${staleAttr}${tipAttr}>
                <span class="radio-wrap">
                  <input type="radio" name="usb-${rexKey}" ${checked} ${disabledAttr} aria-label="USB pair ${rexKey} to ${lexKey}"/>
                  <span class="dot${innerCls}" aria-hidden="true"></span>
                </span>
              </td>`;
    }).join('');

    // Device-supplied model, address and hostname, rendered into innerHTML.
    const rexLabel = standaloneRex
      ? `${esc(r.model||'HW-OMNI-324')}<br/><small>${esc(r.ip)}</small>`
      : `<a href="http://${esc(r.ip)}" target="_blank" style="color:inherit;text-decoration:none;cursor:pointer;" title="Open ${esc(r.ip)} in new tab">${esc(r.ip)}</a><br/><small>${esc(r.host||'')}</small>`;
    return `<tr><th class="row-head rex-label">${rexLabel}</th>${cells}</tr>`;
  }).join('');
  
  // Rebuild the grid only when membership or identity actually changed; a
  // state-only refresh updates the existing cells so nothing moves or flickers.
  const signature = matrixSignature(lex, rex);
  const structureChanged = signature !== lastMatrixSignature || !t.querySelector('td.cell');
  if(structureChanged){
    t.innerHTML = head + rows;
    lastMatrixSignature = signature;
  } else {
    const fresh = document.createElement('tbody');
    fresh.innerHTML = rows;
    const updated = fresh.querySelectorAll('td.cell');
    const existing = t.querySelectorAll('td.cell');
    if(updated.length !== existing.length){
      t.innerHTML = head + rows;              // safety net: shapes disagree
      lastMatrixSignature = signature;
    } else {
      existing.forEach((cell, i)=>{
        const next = updated[i];
        if(cell.getAttribute('data-rex') !== next.getAttribute('data-rex') ||
           cell.getAttribute('data-lex') !== next.getAttribute('data-lex')){
          t.innerHTML = head + rows;          // ordering drifted; rebuild once
          lastMatrixSignature = signature;
          return;
        }
        if(cell.innerHTML !== next.innerHTML) cell.innerHTML = next.innerHTML;
        // className is replaced wholesale, so a request in flight would lose its
        // pending marker to any poll that happened to land mid-operation.
        const pending = cell.classList.contains('pending');
        cell.className = next.className;
        if(pending) cell.classList.add('pending');
        ['data-active','data-paired','data-path','data-state','data-stale','data-experimental','data-lex-mac','data-rex-mac','data-tip','data-tip-detail'].forEach(attr=>{
          const value = next.getAttribute(attr);
          if(value === null) cell.removeAttribute(attr);
          else if(cell.getAttribute(attr) !== value) cell.setAttribute(attr, value);
        });
      });
    }
  }

  bindUsbCellTips(t);
  // A rebuild replaces the cell nodes; re-apply pending to whatever represents
  // the same pair now.
  applyUsbPendingCells(t);

  // One delegated handler for the whole grid, bound once. Binding per cell on
  // every render accumulated a handler on nodes the render deliberately reuses:
  // after an hour one click ran hundreds of them, each clearing the pending
  // marker belonging to the real request and each issuing its own refresh.
  if(t.dataset.cellClickBound !== '1'){
    t.dataset.cellClickBound = '1';
    t.addEventListener('click', async (e)=>{
      const cell = e.target && e.target.closest ? e.target.closest('td.cell:not(.disabled)') : null;
      if(!cell || !t.contains(cell)) return;
      const rex = cell.getAttribute('data-rex');
      const lex = cell.getAttribute('data-lex');
      const isPaired = cell.getAttribute('data-paired') === 'true';
      const isActive = cell.getAttribute('data-active') === 'true';
      const controlPath = cell.getAttribute('data-path');
      // From current state, not from whichever render bound this handler.
      const rexPairing = ((lastState && lastState.pairings) || {})[rex] || {active: null, available: []};
      const existingLex = rexPairing.active || (rexPairing.available || [])[0] || '';
      const hasExistingPair = !!existingLex;

      console.log(`Clicked cell: REX=${rex}, LEX=${lex}, isPaired=${isPaired}, isActive=${isActive}, path=${controlPath}`);

      // Standalone AT-OMNI-311/324 routing uses the validated UDP transaction
      // service. Route state is never painted optimistically: the server
      // verifies both endpoints and we re-read authoritative state either way.
      if(controlPath === 'standalone_udp'){
        // This path addresses devices by MAC. The display key is an IP for an
        // integrated unit, so it must never be substituted for an identity.
        const lexMac = cell.getAttribute('data-lex-mac') || '';
        const rexMac = cell.getAttribute('data-rex-mac') || '';
        if(!lexMac || !rexMac){
          const missing = [!lexMac ? `LEX ${lex}` : null, !rexMac ? `REX ${rex}` : null].filter(Boolean).join(' and ');
          console.error('USB route blocked: no canonical USB MAC for', missing);
          toast('USB endpoint identity unavailable.');
          return;                                  // fail closed: send nothing
        }
        // The cell is a request, not an outcome: mark it pending and let the
        // verified read-back decide what it finally shows.
        markUsbCellPending(cell, rex, lex);
        try {
          const res = await enqueueUsbAction(rex, {
            label: `${isPaired?'unpair':'pair'}:${lexMac}`,
            run: () => postJSON(isPaired ? '/api/usb_route/unpair' : '/api/usb_route/pair',
                                {lex_mac: lexMac, rex_mac: rexMac})
          });
          if(res && res.skipped) return;
          if(!res || !res.ok) throw new Error((res && res.error) || 'USB route failed');
          const moved = res.reassigned_from;
          toast(isPaired ? 'Route removed and verified'
                         : (moved ? 'Route moved and verified' : 'Route created and verified'), true);
        } catch(err) {
          // No fallback: a failure here is reported, never retried through the
          // usb_icron path, which addresses different devices by a different key.
          console.error('USB route error:', err);
          toast(err.message || 'USB route failed');
        } finally {
          clearUsbCellPending(cell, rex, lex);
          // Whatever happened, state read before this point is now obsolete.
          noteUsbRouteMutation();
        }
        await refresh(true);
        return;
      }

      markUsbCellPending(cell, rex, lex);
      try {
        let res;
        // Clicking any paired bubble unpairs that REX/LEX relationship.
        if(isPaired){
          console.log('Unpairing paired route');
          res = await enqueueUsbAction(rex, {
            label: `unpair:${lex}`,
            run: () => postJSON('/api/usb_unpair', {rex, lex})
          });
          if(res.skipped) return;
          console.log('Unpair response:', res);
          if(!res.ok) throw new Error(res.error || 'Unpair failed');
          if(!applyConfirmedUsbPairing(rex, res.pairing)){
            applyOptimisticUsbUnpair(rex, lex);
          }
          toast('Pairing cleared', true);
        } else {
          console.log('Pairing new route');
          res = await enqueueUsbAction(rex, {
            label: `pair:${lex}`,
            run: () => postJSON('/api/usb_pair', {
              rex,
              lex,
              makeActive: false,
              replaceExisting: true
            })
          });
          if(res.skipped) return;
          console.log('Pair response:', res);
          if(!res.ok) throw new Error(res.error || 'Pair failed');
          if(!applyConfirmedUsbPairing(rex, res.pairing)){
            applyOptimisticUsbPair(rex, lex);
          }
          toast(hasExistingPair ? `Pairing moved from ${existingLex}` : 'Pairing set', true);
        }
        
        scheduleUsbVerifyRefresh();
      } catch(err){
        console.error('Pairing error:', err);
        toast('Pairing error: '+err.message);
      } finally {
        clearUsbCellPending(cell, rex, lex);
        noteUsbRouteMutation();
        // The optimistic paint above is a guess; this is what makes the grid
        // agree with the device without the operator reloading the page.
        refresh(true).catch(()=>{});
      }
      e.stopPropagation();
    });
  }
  
  // Populate unit lists at bottom
  const lexTbl = document.querySelector('#lexTbl');
  const rexTbl = document.querySelector('#rexTbl');

  // One row per physical USB endpoint. The server deduplicates by canonical USB
  // MAC and states each row's capabilities, so a standalone unit is never
  // rendered through the integrated template and never appears twice.
  const inventoryLex = s.inventory_lex || [];
  const inventoryRex = s.inventory_rex || [];

  const invHead = '<tr><th>Device IP</th><th>Name</th><th>USB IP</th><th>USB MAC</th>' +
                  '<th>Firmware</th><th>Protocol</th><th>Type</th><th>Host Port</th>' +
                  '<th>Device Filtering</th><th>Peers</th><th>Link</th></tr>';

  function inventoryRow(u, role){
    const key = esc(u.usb_key || u.device_ip || '');
    const typeCell = u.type_configurable
      ? `<select class="type-select" data-device="${key}" style="padding:4px;border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:4px;">
           <option value="LEX" ${role==='LEX'?'selected':''}>LEX</option>
           <option value="REX" ${role==='REX'?'selected':''}>REX</option>
         </select>`
      : esc(u.type || role);
    let portCell;
    if(u.host_port_configurable){
      const current = u.host_port || 'FollowVideo';
      portCell = `<select class="port-select" data-lex="${key}" style="padding:4px;border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:4px;">
          ${['FollowVideo','USB-C','USB-B'].map(o=>`<option value="${o}" ${o===current?'selected':''}>${o}</option>`).join('')}
        </select>`;
    } else {
      portCell = `<span class="muted">${esc(u.host_port || 'N/A')}</span>`;
    }
    let filterCell;
    if(u.filter_configurable){
      const current = u.filter || 'Allow_All';
      const options = ['Allow_All','Allow_Hid_Hub','Allow_Hid_Hub_Smartcard','Block_Isochronous','Block_MassStorage','Block_Isochronous_MassStorage'];
      filterCell = `<select class="filter-select" data-device="${key}" style="padding:4px;border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:4px;">
          ${options.map(o=>`<option value="${o}" ${o===current?'selected':''}>${o}</option>`).join('')}
        </select>`;
    } else {
      // "Not established" is not "Not supported": the reason travels with the
      // row so the distinction is legible rather than merely careful wording.
      const why = u.filter_reason ? ` data-tip="Device Filtering" data-tip-detail="${esc(u.filter_reason)}"` : '';
      filterCell = `<span class="muted"${why}>${esc(u.filter || 'Not established')}</span>`;
    }
    // Peers is the configured pairing. Link is what the device reports now.
    const peers = u.kind === 'standalone'
      ? (u.pairing_state_fresh ? String((u.paired_macs||[]).length) : 'Unknown')
      : String(lexPeerCounts[u.usb_key] !== undefined ? lexPeerCounts[u.usb_key]
              : ((pairings[u.usb_key]||{}).active ? 1 : 0) + (((pairings[u.usb_key]||{}).available)||[]).length);
    const link = esc(u.link_label || 'Unknown');
    return `<tr><td>${esc(u.device_ip||'')}</td><td>${esc(u.name||'')}</td><td>${esc(u.usb_ip||'')}</td>` +
           `<td>${esc(u.usb_mac||'')}</td><td>${esc(u.firmware||'')}</td><td>${esc(u.protocol||'')}</td>` +
           `<td>${typeCell}</td><td>${portCell}</td><td>${filterCell}</td><td>${peers}</td><td>${link}</td></tr>`;
  }

  lexTbl.innerHTML = invHead + inventoryLex.map(u=>inventoryRow(u,'LEX')).join('');
  rexTbl.innerHTML = invHead + inventoryRex.map(u=>inventoryRow(u,'REX')).join('');

  document.querySelectorAll('.port-select').forEach(select => {
    select.addEventListener('change', async (e) => {
      const lexIp = select.getAttribute('data-lex');
      const newPort = select.value;
      console.log(`Changing LEX ${lexIp} host port to ${newPort}`);
      
      try {
        const res = await postJSON('/api/usb_set_port', {lex: lexIp, port: newPort});
        if(!res.ok) throw new Error(res.error || 'Failed to set port');
        toast('Host port updated', true);
      } catch(err) {
        toast(err.message || 'Host port change failed');
        // Revert dropdown
        await refresh();
      }
    });
  });
  
  // Add event listeners for type selection
  document.querySelectorAll('.type-select').forEach(select => {
    select.addEventListener('change', async (e) => {
      const deviceIp = select.getAttribute('data-device');
      const newType = select.value;
      console.log(`Changing device ${deviceIp} type to ${newType}`);
      
      try {
        const res = await postJSON('/api/usb_set_type', {device: deviceIp, type: newType});
        if(!res.ok) throw new Error(res.error || 'Failed to set type');
        toast('Device type updated', true);
        // Refresh to update matrix layout
        setTimeout(()=>{ refresh().catch(()=>{}); }, 500);
      } catch(err) {
        toast(err.message || 'Device type change failed');
        // Revert dropdown
        await refresh();
      }
    });
  });

  // Add event listeners for device filtering
  document.querySelectorAll('.filter-select').forEach(select => {
    select.addEventListener('change', async (e) => {
      const deviceIp = select.getAttribute('data-device');
      const newFilter = select.value;
      console.log(`Changing device ${deviceIp} filtering to ${newFilter}`);
      try {
        const res = await postJSON('/api/usb_set_filter', {device: deviceIp, filter: newFilter});
        if(!res.ok) throw new Error(res.error || 'Failed to set filter');
        toast('Device filtering updated', true);
        setTimeout(()=>{ refresh().catch(()=>{}); }, 500);
      } catch(err) {
        toast(err.message || 'Device filtering change failed');
        await refresh();
      }
    });
  });
}

qs('#refreshBtn').onclick = async ()=>{
  try { await refresh(); toast('Refreshed', true); }  catch(err){ toast(err.message || 'Refresh failed'); }
};

initStickyHeaders();
document.addEventListener('focusout', () => {
  setTimeout(flushDeferredUsbRender, 150);
});
refresh();

// Collapsible sections
document.querySelectorAll('.collapsible .header').forEach(header=>{
  header.addEventListener('click', ()=>{
    const section = header.closest('.collapsible');
    section.classList.toggle('collapsed');
  });
});
// Auto-refresh / Polling
let pollTimer = null;
const pollToggle = document.querySelector('#auto_poll_toggle');
const pollSwitch = document.querySelector('#poll_switch');
const pollIntervalInput = document.querySelector('#poll_interval');

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  const interval = Math.max(1, parseInt(pollIntervalInput.value) || 5) * 1000;
  // Single-flight. /api/usb_state performs live, pairing and link refreshes
  // across every endpoint, so it can comfortably outlast a five-second
  // interval; without this the polls stack and each one sweeps the hardware
  // again for a result the newest response would supersede anyway.
  pollTimer = setInterval(() => {
    if (usbRefreshRunning) return;
    refresh().catch(err => console.error('Auto-refresh error:', err));
  }, interval);
  if (window.omniDiag) omniDiag.loop('usbState', interval);
  pollSwitch.classList.add('on');
  localStorage.setItem('usbAutoRefresh', 'true');
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
  pollSwitch.classList.remove('on');
  localStorage.setItem('usbAutoRefresh', 'false');
}

// Load saved settings
const savedAutoRefresh = localStorage.getItem('usbAutoRefresh') !== 'false';
const savedInterval = localStorage.getItem('usbPollInterval') || '5';
pollToggle.checked = savedAutoRefresh;
pollIntervalInput.value = savedInterval;
if (savedAutoRefresh) {
  pollSwitch.classList.add('on');
  startPolling();
}

// Toggle event
pollSwitch.addEventListener('click', (e) => {
  e.preventDefault();
  pollToggle.checked = !pollToggle.checked;
  if (pollToggle.checked) {
    startPolling();
  } else {
    stopPolling();
  }
});

pollToggle.addEventListener('change', () => {
  if (pollToggle.checked) {
    startPolling();
  } else {
    stopPolling();
  }
});

// Interval change event
pollIntervalInput.addEventListener('change', () => {
  const val = Math.max(1, Math.min(60, parseInt(pollIntervalInput.value) || 5));
  pollIntervalInput.value = val;
  localStorage.setItem('usbPollInterval', val.toString());
  // Restart polling if active
  if (pollToggle.checked) {
    startPolling();
  }
});

const qs = (s)=>document.querySelector(s);

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

async function getJSON(u){
  const r = await fetch(u); 
  if(!r.ok) throw new Error(await r.text()); 
  return r.json();
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
let isFirstSession = true;

async function refresh(){
  // Show loading overlay only on first session
  const overlay = document.getElementById('usb_loading_overlay');
  if (isFirstSession && overlay) overlay.classList.remove('hidden');
  
  try {
    const s = await getJSON('/api/usb_state');
    console.log('USB state received:', s);
    // sort LEX left->right and REX top->bottom by IP
    s.lex = sortByIpAsc(s.lex||[]);
    s.rex = sortByIpAsc(s.rex||[]);
    console.log('After sorting - LEX:', s.lex.length, 'REX:', s.rex.length);
    lastState = s;
    render(s);
  } catch(err) {
    console.error('Refresh error:', err);
    toast('Refresh error: '+err.message);
  } finally {
    // Hide loading overlay when done and mark session as no longer first
    if (overlay) overlay.classList.add('hidden');
    isFirstSession = false;
  }
}

function render(s){
  console.log('Render called with:', s);
  const lex = s.lex||[], rex = s.rex||[];
  const pairings = s.pairings||{}; // {rex_ip: {active: lex_ip, available: [lex_ip1, lex_ip2, ...]}}
  console.log('LEX units:', lex.length, lex);
  console.log('REX units:', rex.length, rex);
  console.log('Pairings:', pairings);
  
  const t = document.querySelector('#usbMatrix');
  
  // Build header row with LEX IPs across top
  const head = '<tr><th class="row-head">REX \\\\ LEX</th>' + lex.map(l=>
    `<th class="enc-head"><div class="col-header"><span class="lex-label"><a href="http://${l.ip}" target="_blank" style="color:inherit;text-decoration:none;cursor:pointer;" title="Open ${l.ip} in new tab">${l.ip}</a></span><small class="enc-host">${l.host||''}</small></div></th>`
  ).join('') + '</tr>';
  
  // Build rows: each REX row can have up to 4 LEX paired, only 1 active
  const rows = rex.map(r=>{
    const rexPairing = pairings[r.ip] || {active: null, available: []};
    const activeLex = rexPairing.active;
    const availableLex = rexPairing.available || [];
    
    const cells = lex.map(l=>{
      const isActive = activeLex === l.ip;
      const isAvailable = (availableLex || []).includes(l.ip);
      const isPaired = isActive || isAvailable;
      
      // Check if REX already has 4 LEX paired (can't add more)
      const pairCount = (availableLex || []).length + (activeLex ? 1 : 0);
      const canPair = pairCount < 4 || isPaired;
      
      const checked = isPaired ? 'checked' : '';
      const innerCls = isActive ? ' audio-on' : '';
      const disabledAttr = canPair ? '' : 'disabled';
      const disabledClass = canPair ? '' : ' disabled';
      
      return `<td class="cell${disabledClass}" data-rex="${r.ip}" data-lex="${l.ip}" data-active="${isActive}" data-paired="${isPaired}">
                <span class="radio-wrap">
                  <input type="radio" name="usb-${r.ip}" ${checked} ${disabledAttr} aria-label="USB pair ${r.ip} ↔ ${l.ip}"/>
                  <span class="dot${innerCls}" aria-hidden="true"></span>
                </span>
              </td>`;
    }).join('');
    
    return `<tr><th class="row-head rex-label"><a href="http://${r.ip}" target="_blank" style="color:inherit;text-decoration:none;cursor:pointer;" title="Open ${r.ip} in new tab">${r.ip}</a><br/><small>${r.host||''}</small></th>${cells}</tr>`;
  }).join('');
  
  t.innerHTML = head + rows;
  
  // Add click handlers to cells
  t.querySelectorAll('td.cell:not(.disabled)').forEach(cell=>{
    cell.addEventListener('click', async (e)=>{
      const rex = cell.getAttribute('data-rex');
      const lex = cell.getAttribute('data-lex');
      const isPaired = cell.getAttribute('data-paired') === 'true';
      const isActive = cell.getAttribute('data-active') === 'true';
      
      console.log(`Clicked cell: REX=${rex}, LEX=${lex}, isPaired=${isPaired}, isActive=${isActive}`);
      
      try {
        let res;
        // If this cell is the currently active pairing, unpair it (clear route)
        if(isActive){
          console.log('Unpairing active route');
          res = await postJSON('/api/usb_unpair', {rex, lex});
          console.log('Unpair response:', res);
          if(!res.ok) throw new Error(res.error || 'Unpair failed');
          toast('Route cleared', true);
        } else {
          // Otherwise, pair this LEX to REX (exclusive mode will replace any existing pairing)
          console.log('Pairing new route (will replace existing if any)');
          res = await postJSON('/api/usb_pair', {rex, lex, makeActive: false});
          console.log('Pair response:', res);
          if(!res.ok) throw new Error(res.error || 'Pair failed');
          toast('Route set', true);
        }
        
        // Device needs 3-4 seconds to apply pairing configuration
        setTimeout(()=>{ refresh().catch(()=>{}); }, 4000);
      } catch(err){ 
        console.error('Pairing error:', err);
        alert('Pairing error: '+err.message); 
      }
      e.stopPropagation();
    });
  });
  
  // Populate unit lists at bottom
  const lexTbl = document.querySelector('#lexTbl');
  const rexTbl = document.querySelector('#rexTbl');
  
  lexTbl.innerHTML = '<tr><th>Device IP</th><th>Hostname</th><th>USB IP</th><th>MAC</th><th>Revision</th><th>Protocol</th><th>Type</th><th>Host Port</th><th>Device Filtering</th><th>Peers</th></tr>' +
    lex.map(l=>{
      const currentPort = l.host_port || 'FollowVideo';
      const portOptions = ['FollowVideo', 'USB-C', 'USB-B'];
      const portDropdown = `<select class="port-select" data-lex="${l.ip}" style="padding:4px;border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:4px;">
        ${portOptions.map(opt => `<option value="${opt}" ${opt===currentPort?'selected':''}>${opt}</option>`).join('')}
      </select>`;
      const typeDropdown = `<select class="type-select" data-device="${l.ip}" style="padding:4px;border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:4px;">
        <option value="LEX" selected>LEX</option>
        <option value="REX">REX</option>
      </select>`;
      const currentFilter = l.filter || 'Allow_All';
      const filterOptions = ['Allow_All','Allow_Hid_Hub','Allow_Hid_Hub_Smartcard','Block_Isochronous','Block_MassStorage','Block_Isochronous_MassStorage'];
      const filterDropdown = `<select class="filter-select" data-device="${l.ip}" style="padding:4px;border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:4px;">
        ${filterOptions.map(opt => `<option value="${opt}" ${opt===currentFilter?'selected':''}>${opt}</option>`).join('')}
      </select>`;
      // Count how many REX devices have this LEX in their pairing info
      let peerCount = 0;
      for (const rexIp in pairings) {
        const p = pairings[rexIp];
        if (p.active === l.ip) peerCount++;
        else if (p.available && p.available.includes(l.ip)) peerCount++;
      }
      return `<tr><td>${l.ip}</td><td>${l.host||''}</td><td>${l.usb_ip||''}</td><td>${l.mac||''}</td><td>${l.revision||''}</td><td>${l.protocol||''}</td><td>${typeDropdown}</td><td>${portDropdown}</td><td>${filterDropdown}</td><td>${peerCount}</td></tr>`;
    }).join('');
  
  rexTbl.innerHTML = '<tr><th>Device IP</th><th>Hostname</th><th>USB IP</th><th>MAC</th><th>Revision</th><th>Protocol</th><th>Type</th><th>Host Port</th><th>Device Filtering</th><th>Peers</th></tr>' +
    rex.map(r=>{
      const rexPairing = pairings[r.ip] || {active: null, available: []};
      const peerCount = (rexPairing.active ? 1 : 0) + (rexPairing.available || []).length;
      // Check if this is an encoder (4521) that can change type, or a decoder (4511) that cannot
      const isEncoder = (r.host && r.host.includes('4521')) || (r.model && r.model.includes('4521'));
      const typeField = isEncoder 
        ? `<select class="type-select" data-device="${r.ip}" style="padding:4px;border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:4px;">
             <option value="LEX">LEX</option>
             <option value="REX" selected>REX</option>
           </select>`
        : 'N/A';
      const currentFilter = r.filter || 'Allow_All';
      const filterOptions = ['Allow_All','Allow_Hid_Hub','Allow_Hid_Hub_Smartcard','Block_Isochronous','Block_MassStorage','Block_Isochronous_MassStorage'];
      const filterDropdown = `<select class="filter-select" data-device="${r.ip}" style="padding:4px;border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:4px;">
        ${filterOptions.map(opt => `<option value="${opt}" ${opt===currentFilter?'selected':''}>${opt}</option>`).join('')}
      </select>`;
      return `<tr><td>${r.ip}</td><td>${r.host||''}</td><td>${r.usb_ip||''}</td><td>${r.mac||''}</td><td>${r.revision||''}</td><td>${r.protocol||''}</td><td>${typeField}</td><td>${r.host_port||''}</td><td>${filterDropdown}</td><td>${peerCount}</td></tr>`;
    }).join('');
  
  // Add event listeners for port selection
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
        alert('Port change error: '+err.message);
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
        alert('Type change error: '+err.message);
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
        alert('Filtering change error: '+err.message);
        await refresh();
      }
    });
  });
}

qs('#refreshBtn').onclick = async ()=>{
  try { await refresh(); toast('Refreshed', true); }  catch(err){ alert('Refresh error: '+err.message); }
};

initStickyHeaders();
initTheme();
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
  pollTimer = setInterval(() => {
    refresh().catch(err => console.error('Auto-refresh error:', err));
  }, interval);
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
const savedAutoRefresh = localStorage.getItem('usbAutoRefresh') === 'true';
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
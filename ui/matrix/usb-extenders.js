(() => {
  // Configure > USB is an inventory and configuration view only. Discovery and
  // Clear live on Device Info, so this page never scans; it renders the state the
  // server already holds and issues per-device actions.
  const $ = s => document.querySelector(s), esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let state={devices:[]}, timer;
  const toast=(m,ok=false)=>{ const e=document.createElement('div'); e.className='toast'+(ok?' ok':'');e.textContent=m;document.body.append(e);setTimeout(()=>e.classList.add('show'),10);setTimeout(()=>e.remove(),2400); };
  const post=async(u,b)=>{const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});const d=await r.json().catch(()=>({error:'Request failed'}));if(!r.ok)throw Error(d.error||'Request failed');return d};
  const na=t=>`<span class="usb-na">${esc(t)}</span>`;

  // A refresh may not empty the table. An error page, or a body whose `devices`
  // is absent or not an array, was assigned to `state` unchecked, so one failed
  // poll of the 10 s loop replaced a good inventory with "No USB endpoints
  // discovered" until a later poll happened to succeed. Validate the shape
  // first and keep what is rendered when it fails.
  //
  // Responses are not ordered either: a poll overlapping a slow read could apply
  // the older body last. `usbExtStateSeq`/`usbExtStateApplied` discard a response
  // already overtaken by a newer one, the same counters usb.js uses.
  let usbExtStateSeq=0, usbExtStateApplied=0, usbExtRefreshRunning=false;
  async function refresh(){
    // Single-flight. Discarding a stale answer is not the same as not asking
    // the question twice: this endpoint sweeps hardware, so an overlapping poll
    // doubles the traffic and the work for a result that is then thrown away.
    if(usbExtRefreshRunning) return;
    usbExtRefreshRunning=true;
    if(window.omniDiag) omniDiag.count('poll.usbExtenders.run');
    const seq=++usbExtStateSeq;
    try {
      const r=await fetch('/api/usb_extenders?pairing=1');
      if(!r.ok) throw Error(`HTTP ${r.status}`);
      const d=await r.json();
      if(!d||!Array.isArray(d.devices)) throw Error('malformed response');
      if(seq<=usbExtStateApplied) return;
      usbExtStateApplied=seq;
      state=d;
      render();
    } catch(e){toast('USB extender state unavailable: '+e.message)}
    finally { usbExtRefreshRunning=false; }
  }

  // Pairing shown here is discovery state. It is only authoritative when the
  // server reports it was re-read this session; otherwise it is labelled cached.
  // Integrated endpoints show their parent's usb_icron relationship instead.
  // Pairing information. Integrated endpoints stay authoritative from usb_icron;
  // standalone endpoints show their Advanced Query state with an explicit age, so
  // a stale peer is never presented as though it were current.
  function peerName(mac){
    const p=(state.devices||[]).find(x=>x.mac===mac);
    return p ? `${esc(p.display_model||'USB device')} \u2014 ${esc(p.ip||'')}` : `Unknown USB device (${esc(mac)})`;
  }
  function pairInfo(d){
    if(d.pairing_source==='usb_icron'){
      const n=(d.parent_paired_count===undefined||d.parent_paired_count===null)?null:d.parent_paired_count;
      const who=esc(d.parent_hostname||d.parent_ip||'parent device');
      return `<span class="small">${n===null?'Managed by':'Paired devices: '+n+'<br>Managed by'} ${who}`
           + `<br>See USB Matrix for pairing.</span>`;
    }
    if(d.classification==='UNCONFIRMED') return na('Pending association');
    if(!d.paired_macs) return na('Pairing state unavailable');
    const age=relativeAge(d.pairing_last_read);
    if(!d.pairing_state_fresh){
      return `${na('Pairing state stale')}<div class="small">${d.pairing_last_read?'Last confirmed '+esc(age):'Not read yet'}</div>`;
    }
    const peers=d.paired_macs||[];
    const isHost=d.device_type==='AT-OMNI-311';
    if(!peers.length) return `<span class="small">${isHost?'Paired devices: 0':'No paired host'}<div class="small">Updated ${esc(age||'just now')}</div></span>`;
    const list=peers.map(peerName).join('<br>');
    const head=isHost?`Paired devices: ${peers.length}`:'Paired host:';
    return `<span class="small">${head}<br>${list}<div class="small">Updated ${esc(age||'just now')}</div></span>`;
  }

  // Operationally useful status from the shared live-state model: a device that
  // answered the live poll reads Online with a relative age, not "Offline
  // (stale)" because its original discovery timestamp is old.
  function relativeAge(ts){
    if(!ts) return null;
    const secs=Math.max(0, Math.round(Date.now()/1000 - ts));
    if(secs < 5) return 'just now';
    if(secs < 60) return `${secs} s ago`;
    if(secs < 3600) return `${Math.round(secs/60)} min ago`;
    if(secs < 86400) return `${Math.round(secs/3600)} h ago`;
    return new Date(ts*1000).toLocaleString();
  }
  // Styled tooltip, same contract as the Matrix: a bounded, viewport-clamped
  // panel driven by `data-tip` / `data-tip-detail`, never the native title
  // attribute. Configure does not load usb.js, so it carries its own binder.
  const tipPanel=(()=>{
    let el=null, timer=null;
    function node(){
      if(el) return el;
      el=document.createElement('div');
      el.className='usb-cell-tip';
      el.setAttribute('role','tooltip');
      el.style.cssText='position:fixed;z-index:65000;max-width:320px;width:max-content;'
        +'background:var(--card,#222a33);color:var(--text,#e9eef5);border:1px solid var(--border,rgba(255,255,255,.14));'
        +'border-radius:6px;padding:7px 9px;font-size:12px;line-height:1.35;'
        +'box-shadow:0 6px 20px rgba(0,0,0,.35);pointer-events:none;display:none;white-space:normal;overflow-wrap:anywhere;';
      document.body.appendChild(el);
      return el;
    }
    function show(host){
      const label=host.getAttribute('data-tip');
      if(!label) return;
      const tip=node();
      clearTimeout(timer);
      // textContent, never innerHTML: the attribute holds the decoded original.
      tip.textContent='';
      const head=document.createElement('div');
      head.style.fontWeight='600';
      head.textContent=label;
      tip.appendChild(head);
      const detail=host.getAttribute('data-tip-detail')||'';
      if(detail){
        const sub=document.createElement('div');
        sub.style.cssText='margin-top:3px;color:var(--muted,#9aa7b4);';
        sub.textContent=detail;
        tip.appendChild(sub);
      }
      tip.style.display='block';
      const rect=host.getBoundingClientRect(), box=tip.getBoundingClientRect(), margin=8;
      let left=rect.left+rect.width/2-box.width/2;
      left=Math.max(margin, Math.min(left, window.innerWidth-box.width-margin));
      let top=rect.bottom+6;
      if(top+box.height > window.innerHeight-margin) top=rect.top-box.height-6;
      tip.style.left=`${Math.round(left)}px`;
      tip.style.top=`${Math.round(Math.max(margin, top))}px`;
    }
    function hide(){ if(el) timer=setTimeout(()=>{ if(el) el.style.display='none'; }, 60); }
    return {show, hide};
  })();

  function bindTips(root){
    if(!root || root.dataset.tipsBound==='1') return;
    root.dataset.tipsBound='1';
    const find=ev => ev.target && ev.target.closest ? ev.target.closest('[data-tip]') : null;
    root.addEventListener('mouseover', ev=>{ const h=find(ev); if(h) tipPanel.show(h); });
    root.addEventListener('mouseout', ev=>{ if(find(ev)) tipPanel.hide(); });
    root.addEventListener('focusin', ev=>{ const h=find(ev); if(h) tipPanel.show(h); });
    root.addEventListener('focusout', ev=>{ if(find(ev)) tipPanel.hide(); });
    window.addEventListener('scroll', ()=>tipPanel.hide(), {passive:true});
  }

  // Status answers one question: can OmniSuite talk to this endpoint right now.
  // Which backend provider observed it last is a diagnostic, not a status, and
  // showing it here made an idle endpoint look like it kept changing.
  function statusCell(d){
    const live=d.live_seen||null, seen=d.last_seen||null;
    const detail=sourceDetail(d);
    if(d.online){
      const age=relativeAge(live||seen);
      return `<span class="usb-status" ${detail}>Online</span><div class="small">Seen ${esc(age||'just now')}</div>`;
    }
    if(seen) return `<span class="usb-status offnet" ${detail}>Offline</span><div class="small">Last response ${esc(relativeAge(seen))}</div>`;
    return `${na('Not seen')}<div class="small">No response yet</div>`;
  }

  // Per-field provenance, on the tooltip rather than in the cell. Each fact says
  // where it came from; no single source speaks for the whole endpoint.
  const SOURCE_NAMES={usb_icron:'parent (usb_icron)', udp_query:'directed UDP query',
                      udp_link_status:'directed UDP Link Status', parent_net:'parent net API',
                      standalone_udp:'directed UDP', advanced_query:'UDP Advanced Query',
                      omnistream:'OmniStream unit', udp_basic_query:'UDP Basic Query'};
  function sourceDetail(d){
    const authority=d.field_authority||{};
    const lines=Object.entries({Liveness:authority.liveness, Pairing:authority.pairing,
                                Network:authority.network, Link:authority.link,
                                Firmware:authority.firmware})
      .filter(([,v])=>v)
      .map(([k,v])=>`${k}: ${SOURCE_NAMES[v]||v}`);
    if(!lines.length) return '';
    return `data-tip="Where each fact came from" data-tip-detail="${esc(lines.join(' \u00b7 '))}"`;
  }

  function row(d){
    const off=d.network_relation==='OFF_NET';
    const deviceIp=d.integrated?(d.parent_ip||''):(d.ip||'');
    // The server decides the displayed name. `device_type` is the protocol
    // identity and is still what the role checks compare against; showing it
    // here is what put AT-OMNI-311 in the Hostname column.
    const hostname=d.integrated?(d.parent_hostname||''):(d.display_hostname||d.display_model||'');
    const owner=esc(d.parent_hostname||d.parent_ip||'its parent device');
    // Both families can now be configured: standalone over UDP, integrated
    // through the parent OmniStream network API.
    const netBtn='<button data-action="network">Network</button>';
    const rebootBtn=d.reboot_supported
      ? ' <button class="danger" data-action="reboot">Reboot</button>'
      : ` <button class="danger" data-action="reboot" disabled title="Reboot ${owner} from Device Info. The standalone extender reboot is not sent to an integrated USB endpoint.">Reboot</button>`;
    const idBtn=d.identify_via
      ? '<button data-action="identify">Identify</button>'
      : '<button data-action="identify" disabled title="This endpoint has not been associated yet. Run a scan from Device Info.">Identify</button>';
    // Identity first, and frozen: Device IP and Hostname stay visible while the
    // rest of this wide table scrolls behind them.
    return `<tr data-mac="${esc(d.mac)}">`
      + `<td>${esc(deviceIp)}</td>`
      + `<td title="${esc(hostname)}">${esc(hostname)}</td>`
      + `<td>${esc(d.display_model||'Unknown')}<div class="usb-parent">${d.classification==='INTEGRATED'?'Integrated USB endpoint':(d.classification==='STANDALONE'?'Standalone':'Not yet associated')}</div></td>`
      + `<td>${esc(d.usb_role||'')}</td>`
      + `<td>${esc(d.usb_ip||d.ip||'')}</td>`
      + `<td>${esc(d.mac)}</td>`
      + `<td>${esc(d.usb_firmware||'N/A')}</td>`
      + `<td>${esc(d.network_mode||'')}</td>`
      + `<td><span class="usb-status">${esc(d.network_relation_label||'Unknown')}</span>${off?'<div class="small">Reached through a router, outside the selected interface subnet.</div>':''}</td>`
      + `<td>${statusCell(d)}</td>`
      + `<td>${linkCell(d)}</td>`
      + `<td>${pairInfo(d)}</td>`
      + `<td>${idBtn} ${netBtn}${rebootBtn}</td></tr>`;
  }

  // Link is what the device reports about the extender link right now, summarised
  // by the server from the per-peer state array so every surface reads the same
  // derived object. Integrated endpoints are included: they answer Link Status
  // over directed UDP even though their control belongs to the parent.
  const LINK_STRONG={LINKED:1, PARTIAL:1};
  function linkCell(d){
    const label=d.link_label||'Unknown', detail=d.link_detail||'';
    const peers=(d.link_states||[]).map(s=>`${s.mac}: ${(s.state||'').toLowerCase().replace('_',' ')}`);
    const tip=peers.length ? `data-tip="Per-peer link state" data-tip-detail="${esc(peers.join(' \u00b7 '))}"` : '';
    const cls=LINK_STRONG[d.link_summary_state] ? 'usb-status' : 'small';
    return `<span class="${cls}" ${tip}>${esc(label)}</span>`
      + (detail?`<div class="small">${esc(detail)}</div>`:'');
  }

  // USB discovery networks used to be configured here, as a second list of
  // routed subnets. It was never a different search space: Device Info > Scan
  // already hands its Targets to directed USB discovery, so the only thing this
  // panel added was remembering the value. Device Info now remembers Targets
  // itself and that one definition drives both protocols, so a routed network is
  // entered once. The /api/usb_extenders/ranges store still holds it -- it is
  // what the scanner reads -- and Device Info is its input.

  // ---- inventory filter --------------------------------------------------
  // Only fields the operator can actually see are searchable. `device_type` in
  // particular is excluded: for an integrated endpoint it holds the UDP-reported
  // USB role name, so an E4521 endpoint reports "AT-OMNI-311" and a D4511
  // reports "AT-OMNI-324". It is diagnostic data and is never displayed, and
  // searching it made "311" match every E4521 row.
  function usbSearchFields(d){
    return {
      model: d.display_model || '',
      role: d.usb_role || '',
      ip: d.parent_ip || d.ip || '',
      hostname: d.parent_hostname || d.display_hostname || d.display_model || '',
      usb_ip: d.usb_ip || d.ip || '',
      mac: d.mac || '',
      firmware: d.firmware || d.product_revision || '',
      network: d.network_mode || '',
      status: d.online ? 'online' : 'offline',
      link: d.link_state_fresh ? (d.link_state === 'LINKED' ? 'linked' : 'not linked') : 'unknown',
    };
  }

  // "AT-OMNI-311", "omni-311", "-311" and "311" all describe one model, so model
  // comparison ignores punctuation. This is applied to the model field only, so
  // a bare number cannot sweep in unrelated values from other columns.
  const usbLoose = v => String(v ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');

  function usbRowMatches(d, query){
    const q = String(query ?? '').trim().toLowerCase();
    if(!q) return true;
    const fields = usbSearchFields(d);
    // Every whitespace-separated term must match somewhere (AND), so a search
    // can be narrowed by adding words.
    return q.split(/\s+/).filter(Boolean).every(term=>{
      const prefix = term.match(/^([a-z_]+):(.*)$/);
      if(prefix && Object.prototype.hasOwnProperty.call(fields, prefix[1])){
        const name = prefix[1], value = prefix[2];
        if(!value) return true;
        if(name === 'model') return usbLoose(fields.model).includes(usbLoose(value));
        return String(fields[name]).toLowerCase().includes(value);
      }
      if(usbLoose(fields.model).includes(usbLoose(term))) return true;
      return Object.entries(fields).some(([name, value])=>
        name !== 'model' && String(value).toLowerCase().includes(term));
    });
  }

  // Exposed for the filter test; the page itself uses the local binding.
  if(typeof globalThis !== 'undefined'){
    globalThis.usbRowMatches = usbRowMatches;
    globalThis.usbSearchFields = usbSearchFields;
  }

  function render(){
    const host=$('#usbExtenderRows'); if(!host)return;
    const f=($('#usbDeviceFilter')?.value||'').toLowerCase();
    const devices=(state.devices||[]).filter(d=>usbRowMatches(d,f));
    host.innerHTML=devices.length?devices.map(row).join('')
      :'<tr><td colspan="13" class="usb-empty">No USB endpoints discovered. Run a scan from Device Info.</td></tr>';
    bindTips(host);
  }


  // ---- OmniSuite modal ----------------------------------------------------
  // Reuses the application's existing dialog structure and classes
  // (encoder-output-modal / -card / -head / -body / -actions) so this looks like
  // every other OmniSuite dialog. No native alert/confirm/prompt is used.
  function ensureModal(){
    let modal=document.getElementById('usb_extender_modal');
    if(modal) return modal;
    modal=document.createElement('div');
    modal.id='usb_extender_modal';
    modal.className='encoder-output-modal hidden';
    modal.innerHTML=`<div class="encoder-output-card" style="width:min(92vw,440px);">
        <div class="encoder-output-head">
          <div><h4 class="usb-modal-title"></h4><span class="encoder-output-subtitle usb-modal-subtitle"></span></div>
          <button type="button" class="usb-modal-close" aria-label="Close">x</button>
        </div>
        <div class="encoder-output-body" style="display:block;">
          <div class="encoder-output-grid usb-modal-body" style="display:block;"></div>
          <div class="encoder-output-error usb-modal-error" style="display:none;"></div>
        </div>
        <div class="encoder-output-actions usb-modal-actions"></div>
      </div>`;
    document.body.appendChild(modal);
    return modal;
  }

  // Resolves to the chosen button value, or null when dismissed. Dismissing by
  // the close button, the backdrop, or Escape always means cancel.
  function showModal({title, subtitle, bodyHtml='', buttons, onOpen}){
    return new Promise(resolve=>{
      const modal=ensureModal();
      modal.querySelector('.usb-modal-title').textContent=title;
      modal.querySelector('.usb-modal-subtitle').textContent=subtitle||'';
      modal.querySelector('.usb-modal-body').innerHTML=bodyHtml;
      const err=modal.querySelector('.usb-modal-error');
      err.style.display='none'; err.textContent='';
      const actions=modal.querySelector('.usb-modal-actions');
      actions.innerHTML='';
      let done=false;
      const finish=value=>{
        if(done) return; done=true;
        document.removeEventListener('keydown', onKey);
        modal.classList.add('hidden');
        resolve(value);
      };
      const onKey=ev=>{ if(ev.key==='Escape') finish(null); };
      buttons.forEach(btn=>{
        const el=document.createElement('button');
        el.type='button'; el.textContent=btn.label;
        if(btn.danger) el.className='danger';
        el.addEventListener('click', ()=>{
          if(btn.validate){
            const problem=btn.validate(modal);
            if(problem){ err.textContent=problem; err.style.display='block'; return; }
          }
          finish(btn.value);
        });
        actions.appendChild(el);
      });
      modal.querySelector('.usb-modal-close').onclick=()=>finish(null);
      modal.onclick=ev=>{ if(ev.target===modal) finish(null); };
      document.addEventListener('keydown', onKey);
      modal.classList.remove('hidden');
      if(onOpen) onOpen(modal);
    });
  }

  const netField=(label,cls,value)=>`<label style="display:grid;grid-template-columns:120px minmax(0,1fr);gap:10px;align-items:center;margin-bottom:10px;">
        <span>${label}</span><input type="text" class="${cls}" value="${esc(value||'')}" spellcheck="false"></label>`;


  // Integrated E4521/D4511 USB endpoint. The modes are exactly those the parent
  // OmniStream network API accepts for its icron interface, and are supplied by
  // the server rather than hard-coded here. Static fields are editable only for
  // the static mode, matching the parent device's own behaviour.
  const MODE_LABELS={broadcast:'Broadcast', dhcp:'DHCP', static:'Static', disabled:'Disabled'};
  const MODE_NOTES={
    broadcast:'The USB endpoint uses the broadcast addressing mode reported by the parent device.',
    dhcp:'The USB endpoint requests an address by DHCP. Its address may change.',
    static:'The USB endpoint uses the address you specify below.',
    disabled:'The USB network interface is turned off. The endpoint will not be reachable over the network until it is re-enabled from the parent device.'
  };
  async function integratedNetworkDialog(d,current){
    const modes=(current.modes&&current.modes.length?current.modes:['dhcp','static']);
    const subtitle=`${d.display_model||''}  \u00b7  parent ${current.parent_hostname||current.parent_ip||''}`;
    const summary=`<div class="small" style="margin-bottom:8px;">
        Parent device: <b>${esc(current.parent_hostname||'')}</b> ${esc(current.parent_ip||'')}<br>
        USB endpoint: <b>${esc(current.ipaddress||'')}</b> &nbsp; mask ${esc(current.subnetmask||'')} &nbsp; gw ${esc(current.gateway||'')}<br>
        USB MAC: ${esc(current.macaddress||d.mac||'')} &nbsp;&nbsp; current mode: <b>${esc(MODE_LABELS[current.mode]||current.mode||'unknown')}</b>
      </div><div>Select network mode for this integrated USB endpoint:</div>`;
    const buttons=modes.map(m=>({label:MODE_LABELS[m]||m, value:m}));
    buttons.push({label:'Cancel', value:null});
    const mode=await showModal({title:'USB Network Configuration', subtitle, bodyHtml:summary, buttons});
    if(!mode) return null;

    if(mode==='static'){
      let result=null;
      const go=await showModal({
        title:'Static USB Network Configuration',
        subtitle,
        bodyHtml: netField('IP Address','usb-net-ip',current.ipaddress)
                + netField('Subnet Mask','usb-net-mask',current.subnetmask||'255.255.255.0')
                + netField('Default Gateway','usb-net-gw',current.gateway)
                + `<div class="small" style="margin-top:6px;">${esc(MODE_NOTES.static)}</div>`,
        buttons:[{label:'Apply',value:'apply',validate:modal=>{
                    const ip=modal.querySelector('.usb-net-ip').value.trim();
                    const mask=modal.querySelector('.usb-net-mask').value.trim();
                    const gw=modal.querySelector('.usb-net-gw').value.trim();
                    if(!validIp(ip)) return 'Enter a valid IPv4 address.';
                    if(!validIp(mask)) return 'Enter a valid subnet mask.';
                    if(!validIp(gw)) return 'Enter a valid default gateway.';
                    const bits=mask.split('.').map(o=>Number(o).toString(2).padStart(8,'0')).join('');
                    if(!/^1*0*$/.test(bits)) return 'That subnet mask is not contiguous.';
                    const net=(a,m)=>a.split('.').map((o,i)=>Number(o)&Number(m.split('.')[i])).join('.');
                    if(net(ip,mask)!==net(gw,mask)) return 'The gateway must be inside the same subnet as the address.';
                    result={mode:'static',address:ip,netmask:mask,gateway:gw};
                    return null;
                  }},
                 {label:'Cancel',value:null}]
      });
      return go ? result : null;
    }

    // Every non-static mode is confirmed, with the consequence spelled out.
    const danger = mode==='disabled';
    const go=await showModal({
      title:`Set USB network to ${MODE_LABELS[mode]||mode}`,
      subtitle,
      bodyHtml:`<div>Set the USB endpoint of <b>${esc(current.parent_hostname||current.parent_ip||'this device')}</b>
                to <b>${esc(MODE_LABELS[mode]||mode)}</b>?</div>
                <div class="small" style="margin-top:8px;">${esc(MODE_NOTES[mode]||'')}</div>`,
      buttons:[{label:'Apply',value:'apply',danger:danger},{label:'Cancel',value:null}]
    });
    return go ? {mode} : null;
  }

  async function networkDialog(d,current){
    // Three explicit choices. Cancel means cancel; it is never overloaded.
    const mode=await showModal({
      title:'Network Configuration',
      subtitle:`${d.display_model||''}  ${d.ip||''}  ${d.mac||''}`,
      bodyHtml:'<div style="margin-bottom:6px;">Select network mode for this standalone USB extender:</div>',
      buttons:[{label:'DHCP',value:'dhcp'},{label:'Static',value:'static'},{label:'Cancel',value:null}]
    });
    if(!mode) return null;
    if(mode==='dhcp'){
      const go=await showModal({
        title:'Set to DHCP',
        subtitle:`${d.display_model||''}  ${d.mac||''}`,
        bodyHtml:`<div>Set <b>${esc(d.display_model||'this device')}</b> at <b>${esc(d.ip||'')}</b> to DHCP?</div>
                  <div class="small" style="margin-top:8px;">The address may change. The device is tracked by MAC, so it is re-resolved after the change.</div>`,
        buttons:[{label:'Apply',value:'apply'},{label:'Cancel',value:null}]
      });
      return go ? {mode:'dhcp'} : null;
    }
    let result=null;
    const go=await showModal({
      title:'Static Network Configuration',
      subtitle:`${d.display_model||''}  ${d.mac||''}`,
      bodyHtml: netField('IP Address','usb-net-ip',(current&&current.ipaddress)||d.ip)
              + netField('Subnet Mask','usb-net-mask',(current&&current.subnetmask)||d.subnet_mask||'255.255.255.0')
              + netField('Default Gateway','usb-net-gw',(current&&current.gateway)||''),
      buttons:[{label:'Apply',value:'apply',validate:modal=>{
                  const ip=modal.querySelector('.usb-net-ip').value.trim();
                  const mask=modal.querySelector('.usb-net-mask').value.trim();
                  const gw=modal.querySelector('.usb-net-gw').value.trim();
                  if(!validIp(ip)) return 'Enter a valid IPv4 address.';
                  if(!validIp(mask)) return 'Enter a valid subnet mask.';
                  if(!validIp(gw)) return 'Enter a valid default gateway.';
                  const bits=mask.split('.').map(o=>Number(o).toString(2).padStart(8,'0')).join('');
                  if(!/^1*0*$/.test(bits)) return 'That subnet mask is not contiguous.';
                  const net=(a,m)=>a.split('.').map((o,i)=>Number(o)&Number(m.split('.')[i])).join('.');
                  if(net(ip,mask)!==net(gw,mask)) return 'The gateway must be inside the same subnet as the address.';
                  if(ip===gw) return 'The gateway cannot be the same as the address.';
                  result={mode:'static',address:ip,netmask:mask,gateway:gw};
                  return null;
                }},
               {label:'Cancel',value:null}]
    });
    return go ? result : null;
  }

  function validIp(ip){return /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/.test(ip)}

  async function deviceAction(e){
    const b=e.target.closest('button[data-action]'); if(!b||b.disabled)return;
    const mac=b.closest('tr').dataset.mac, act=b.dataset.action;
    const d=(state.devices||[]).find(x=>x.mac===mac);
    if(act==='identify'){
      b.disabled=true;
      try{const r=await post('/api/usb_extenders/identify',{mac});
        toast(r.identify_via==='parent'?`Identifying ${d?.parent_hostname||r.parent_ip||'parent device'}`:'Identifying device',true);
      }catch(x){toast(x.message)}finally{b.disabled=false}
      return;
    }
    if(act==='reboot'){
      if(!d) return;
      const go=await showModal({
        title:'Reboot USB Extender',
        subtitle:`${d.display_model||''}  ${d.mac||''}`,
        bodyHtml:`<div>Reboot <b>${esc(d.display_model||'this device')}</b> at <b>${esc(d.ip||'')}</b>?</div>`,
        buttons:[{label:'Reboot',value:'go',danger:true},{label:'Cancel',value:null}]
      });
      if(!go) return;
      try{await post('/api/usb_extenders/reboot',{mac});toast('Reboot accepted',true);setTimeout(refresh,1200)}catch(x){toast(x.message)}
      return;
    }
    // Network configuration. The server refuses integrated endpoints; the button
    // is disabled for them, and this guard keeps the two consistent.
    if(!d) return;
    // Authoritative current configuration is read on demand so the dialog shows
    // real values rather than discovery leftovers.
    let current=null;
    try{ current=await (await fetch(`/api/usb_extenders/network_config?mac=${encodeURIComponent(mac)}`)).json(); }
    catch(x){ current=null; }
    if(!current||!current.ok) return toast((current&&current.error)||'Could not read the current network configuration.');

    if(d.integrated){
      const choice=await integratedNetworkDialog(d,current);
      if(!choice) return;                     // Cancel: no request, no state change
      try{const r=await post('/api/usb_extenders/network',{mac,...choice});
        toast(r.status==='configuration_verified'
              ? 'Network configuration verified'
              : 'Network configuration accepted. Waiting for the device to report back.',true);
        await refresh();
      }catch(x){toast(x.message)}
      return;
    }

    if(!d.interface_ip||!d.subnet_mask)return toast('Re-run discovery from Device Info before changing this device’s network.');
    const choice=await networkDialog(d,current);
    if(!choice) return;                       // Cancel: no packet, no state change
    const body={interface_ip:d.interface_ip,subnet_mask:d.subnet_mask,mac,...choice};
    try{const r=await post('/api/usb_extenders/network',body);
      toast(r.status==='rediscovery_verified'?'Network configuration verified':'Network configuration accepted. Waiting for device rediscovery.',true);
      await refresh();
    }catch(x){toast(x.message)}
  }

  document.addEventListener('DOMContentLoaded',async()=>{
    if(!$('#usbExtenderRows'))return;
    await refresh();
    $('#usbExtenderRows').onclick=deviceAction;
    $('#usbDeviceFilter').oninput=render;
    timer=setInterval(refresh,10000);
    if(window.omniDiag) omniDiag.loop('usbExtenders', 10000);
    window.addEventListener('beforeunload',()=>clearInterval(timer));
  });
})();

/*
 * LLDP switch topology for Device Info.
 *
 * Derived entirely from the inventory the page already fetched: no request of
 * its own, no device traffic, and no timer. It is recomputed when units render
 * and at no other time.
 *
 * Two things live here and must not be conflated:
 *
 *   * the banner, which the operator acknowledges once for the lifetime of the
 *     discovered device inventory;
 *   * the minority-switch highlighting, which describes current topology and is
 *     not affected by that acknowledgement at all.
 *
 * Dismissing the banner is an acknowledgement. It is not a statement about the
 * network, so it silences the banner and nothing else.
 */
'use strict';

// One escaping helper at module scope, so every string this module renders goes
// through the same one regardless of which function builds it.
function lldpEsc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Functional state. `acknowledged` is taken from the server with the inventory
// it belongs to and is deliberately never read back out of the DOM: whether the
// banner happens to be visible is a consequence of this flag, never its source.
const switchTopology = {
  acknowledged: false,
  minorityIps: new Set(),
  groups: [],
  switchCount: 0,
  tied: false,
  multiSwitch: false,
};

const ALTERNATE_SWITCH_NOTE =
  'LLDP indicates this device is connected through a different switch than the majority of discovered devices.';

// A daisy chain is a separate condition from a second switch, with a separate
// risk and therefore a separate acknowledgement: one is about an uplink between
// switches, the other about a link shared with the devices in front of you.
// Dismissing either must never silence the other.
const daisyTopology = {
  acknowledged: false,
  chainedIps: new Set(),
  multiStreamIps: new Set(),
  devices: {},
  present: false,
};

const DAISY_NOTE =
  'This device is reached through another OmniStream device rather than directly from the switch. '
  + 'Traffic for it shares that upstream link.';

// Advisory wording. Multiple switches are not presented as a fault -- the point
// is that traffic may cross an uplink, so uplink capacity is worth checking *if*
// problems appear.
function switchWarningText(topology) {
  const groups = (topology && topology.groups) || [];
  if (groups.length === 2 && !(topology && topology.tied)) {
    const primary = groups[0], other = groups[1];
    const are = n => (n === 1 ? 'device is' : 'devices are');
    return {
      title: 'Multiple network switches detected.',
      detail: `${primary.count} ${are(primary.count)} connected through the primary switch and `
        + `${other.count} ${are(other.count)} connected through another switch. `
        + 'If USB or AV-over-IP performance issues occur, verify uplink bandwidth between switches.',
    };
  }
  // Three or more switches, or a tie in which no group is the primary one.
  // Summarise without designating any group: with equal counts there is no basis
  // for calling one of them the odd one out.
  return {
    title: `Devices were detected across ${(topology && topology.switch_count) || groups.length} network switches.`,
    detail: 'If performance issues occur, verify inter-switch/uplink bandwidth and configuration.',
  };
}

// Whether the banner should be on screen right now. Separate from rendering so
// the decision can be asserted directly rather than inferred from markup.
function shouldShowSwitchWarning(topology, acknowledged) {
  return !!(topology && topology.multi_switch) && !acknowledged;
}

// Advisory, not a diagnosis. A daisy chain is a supported way to cable a system;
// what it changes is that one link now carries traffic for everything behind it,
// and OmniSuite has no bitrate from any device with which to say whether that is
// currently a problem. So it says what could happen, never what is happening.
function daisyWarningText(topology) {
  const chained = (topology && topology.devices_daisy_chained) || [];
  const multi = (topology && topology.daisy_chained_multi_stream) || [];
  if (multi.length) {
    const many = multi.length > 1;
    return {
      title: 'Bandwidth advisory: daisy-chained decoder with multiple streams.',
      detail: `${many ? `${multi.length} daisy-chained decoders are` : 'A daisy-chained decoder is'} `
        + 'subscribed to more than one stream. Those streams may share a 1 Gb upstream path through '
        + 'another OmniStream device. Verify the aggregate stream bandwidth for that path.',
    };
  }
  const many = chained.length > 1;
  return {
    title: 'Daisy-chained network connection detected.',
    detail: `${many ? `${chained.length} devices are` : 'A device is'} connected through another `
      + 'OmniStream device rather than directly to a switch. Traffic for downstream devices shares '
      + 'the upstream link. If multiple high-bandwidth streams are used, verify that link has '
      + 'sufficient capacity.',
  };
}

function shouldShowDaisyWarning(topology, acknowledged) {
  return !!(topology && topology.daisy_chained) && !acknowledged;
}

function applyDaisyTopology(topology, acknowledged) {
  topology = topology || {};
  daisyTopology.acknowledged = !!acknowledged;
  daisyTopology.chainedIps = new Set(topology.devices_daisy_chained || []);
  daisyTopology.multiStreamIps = new Set(topology.daisy_chained_multi_stream || []);
  daisyTopology.devices = topology.devices || {};
  daisyTopology.present = !!topology.daisy_chained;
  return daisyTopology;
}

function renderDaisyWarning(topology, acknowledged) {
  applyDaisyTopology(topology, acknowledged);
  const banner = document.getElementById('daisy_chain_warning');
  if (!banner) return daisyTopology;
  if (!shouldShowDaisyWarning(topology, daisyTopology.acknowledged)) {
    banner.classList.add('hidden');
    return daisyTopology;
  }
  const text = daisyWarningText(topology);
  banner.querySelector('.daisy-warning-title').textContent = text.title;
  banner.querySelector('.daisy-warning-detail').textContent = text.detail;
  banner.classList.remove('hidden');
  return daisyTopology;
}

async function acknowledgeDaisyChain() {
  daisyTopology.acknowledged = true;
  const banner = document.getElementById('daisy_chain_warning');
  if (banner) banner.classList.add('hidden');
  try {
    const res = await fetch('/api/lldp_topology/acknowledge', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({acknowledged: true, scope: 'daisy_chain'}),
    });
    const body = await res.json();
    if (!body || !body.ok) throw new Error((body && body.error) || 'acknowledgement was not recorded');
  } catch (err) {
    daisyTopology.acknowledged = false;
    console.error('Could not record the daisy-chain acknowledgement:', err);
  }
  return daisyTopology.acknowledged;
}

function isDaisyChainedDevice(ip) {
  return daisyTopology.chainedIps.has(ip);
}

function daisyCellAttributes(unit) {
  return daisyTopology.chainedIps.has(unit && unit.ip) ? ' daisy-chained' : '';
}

function daisyCellNote(unit) {
  const record = daisyTopology.devices[unit && unit.ip];
  if (!record || !record.is_daisy_chained) return '';
  const via = record.daisy_chain_via_device || record.daisy_chain_via_ip || 'another device';
  return `<span class="daisy-note">Daisy chained via ${lldpEsc(via)}</span>`;
}

// What the hover panel leads with: the path in words, so nobody has to infer it
// from a chassis ID.
function lldpConnectionSummary(ip) {
  const record = daisyTopology.devices[ip];
  if (!record) return null;
  const switchInfo = record.resolved_upstream_switch || {};
  const switchName = switchInfo.chassis_name || switchInfo.chassis_id_raw || switchInfo.chassis_id || '';
  if (record.is_daisy_chained) {
    const via = record.daisy_chain_via_device || record.daisy_chain_via_ip || 'another OmniStream device';
    const viaIp = record.daisy_chain_via_ip && record.daisy_chain_via_ip !== via
      ? ` (${record.daisy_chain_via_ip})` : '';
    return {
      kind: 'daisy',
      heading: 'Daisy chained through',
      through: `${via}${viaIp}`,
      upstream: switchName || 'Upstream switch could not be resolved',
      port: switchInfo.port_id || '',
      hops: (record.daisy_chain_hops || []).length,
    };
  }
  if (!switchName) return {kind: 'unknown', heading: 'Upstream switch', upstream: 'Not determined'};
  return {
    kind: 'direct',
    heading: 'Connected to',
    upstream: switchName,
    port: switchInfo.port_id || record.immediate_neighbor?.port_id || '',
  };
}

function applySwitchTopology(topology, acknowledged) {
  topology = topology || {};
  switchTopology.acknowledged = !!acknowledged;
  switchTopology.groups = topology.groups || [];
  switchTopology.switchCount = topology.switch_count || 0;
  switchTopology.tied = !!topology.tied;
  switchTopology.multiSwitch = !!topology.multi_switch;
  // Highlighting follows current topology and nothing else. A tie has no
  // minority, so the server sends none and none is invented here.
  switchTopology.minorityIps = new Set(topology.minority_ips || []);
  return switchTopology;
}

function renderSwitchTopology(topology, acknowledged) {
  applySwitchTopology(topology, acknowledged);
  const banner = document.getElementById('switch_topology_warning');
  if (!banner) return switchTopology;
  if (!shouldShowSwitchWarning(topology, switchTopology.acknowledged)) {
    banner.classList.add('hidden');
    return switchTopology;
  }
  const text = switchWarningText(topology);
  banner.querySelector('.switch-warning-title').textContent = text.title;
  banner.querySelector('.switch-warning-detail').textContent = text.detail;
  banner.classList.remove('hidden');
  return switchTopology;
}

async function acknowledgeSwitchTopology() {
  // Hide at once so the control feels responsive, but the server is what stores
  // the flag: if recording it fails the state goes back, and the banner returns
  // on the next render rather than pretending an acknowledgement was kept.
  switchTopology.acknowledged = true;
  const banner = document.getElementById('switch_topology_warning');
  if (banner) banner.classList.add('hidden');
  try {
    const res = await fetch('/api/lldp_topology/acknowledge', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({acknowledged: true}),
    });
    const body = await res.json();
    if (!body || !body.ok) throw new Error((body && body.error) || 'acknowledgement was not recorded');
  } catch (err) {
    switchTopology.acknowledged = false;
    console.error('Could not record the switch topology acknowledgement:', err);
  }
  return switchTopology.acknowledged;
}

// The LLDP/MAC portion of a row whose device is reached through a different
// switch than the majority. Subtle by design: this says "different switch", not
// "broken device", and it stays visible for as long as the topology warrants it.
function macCellAttributes(unit) {
  return switchTopology.minorityIps.has(unit && unit.ip) ? ' class="alternate-switch"' : '';
}

function macCellNote(unit) {
  if (!switchTopology.minorityIps.has(unit && unit.ip)) return '';
  const name = (unit && (unit.lldp_chassis_name || unit.lldp_chassis_id_raw)) || '';
  return `<span class="switch-note">Different switch${name ? ': ' + lldpEsc(name) : ''}</span>`;
}

function isAlternateSwitchDevice(ip) {
  return switchTopology.minorityIps.has(ip);
}

if (typeof globalThis !== 'undefined') {
  globalThis.switchTopology = switchTopology;
  globalThis.switchWarningText = switchWarningText;
  globalThis.shouldShowSwitchWarning = shouldShowSwitchWarning;
  globalThis.applySwitchTopology = applySwitchTopology;
  globalThis.renderSwitchTopology = renderSwitchTopology;
  globalThis.acknowledgeSwitchTopology = acknowledgeSwitchTopology;
  globalThis.macCellAttributes = macCellAttributes;
  globalThis.macCellNote = macCellNote;
  globalThis.isAlternateSwitchDevice = isAlternateSwitchDevice;
  globalThis.ALTERNATE_SWITCH_NOTE = ALTERNATE_SWITCH_NOTE;
  globalThis.daisyTopology = daisyTopology;
  globalThis.DAISY_NOTE = DAISY_NOTE;
  globalThis.daisyWarningText = daisyWarningText;
  globalThis.shouldShowDaisyWarning = shouldShowDaisyWarning;
  globalThis.applyDaisyTopology = applyDaisyTopology;
  globalThis.renderDaisyWarning = renderDaisyWarning;
  globalThis.acknowledgeDaisyChain = acknowledgeDaisyChain;
  globalThis.isDaisyChainedDevice = isDaisyChainedDevice;
  globalThis.daisyCellAttributes = daisyCellAttributes;
  globalThis.daisyCellNote = daisyCellNote;
  globalThis.lldpConnectionSummary = lldpConnectionSummary;
}

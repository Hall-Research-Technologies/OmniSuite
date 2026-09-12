/**
 * Device Info multi-switch warning and minority-switch highlighting.
 *
 * The two behaviours are deliberately tested apart from each other, because the
 * whole point of the design is that they are independent: acknowledging the
 * banner silences the banner and nothing else, while the highlighting keeps
 * describing whatever the current topology is.
 *
 * Run: node tests/lldp_topology_test.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = path.join(__dirname, '..', 'ui', 'lldp-topology.js');
const INDEX = path.join(__dirname, '..', 'ui', 'index.html');
const failures = [];
const pending = [];

// Async checks share one module state and one request log, so they run strictly
// one after another rather than all starting at declaration time.
let queue = Promise.resolve();
function checkSeq(name, fn) {
  queue = queue.then(async () => {
    try { await fn(); console.log(`  ok   ${name}`); }
    catch (err) { failures.push(`${name}: ${err && err.message}`); console.log(`  FAIL ${name}: ${err && err.message}`); }
  });
  pending.push(queue);
}

function check(name, fn) {
  try {
    const result = fn();
    if (result && typeof result.then === 'function') {
      pending.push(result.then(
        () => console.log(`  ok   ${name}`),
        err => { failures.push(`${name}: ${err && err.message}`); console.log(`  FAIL ${name}: ${err && err.message}`); }));
      return;
    }
    console.log(`  ok   ${name}`);
  } catch (err) {
    failures.push(`${name}: ${err && err.message}`);
    console.log(`  FAIL ${name}: ${err && err.message}`);
  }
}

// ---- a DOM stub with just the banner --------------------------------------
function makeBanner() {
  const node = (cls) => ({
    className: cls || '',
    textContent: '',
    classList: {
      add(c) { if (!node0.classes.has(c)) node0.classes.add(c); },
      remove(c) { node0.classes.delete(c); },
      contains: c => node0.classes.has(c),
    },
  });
  const node0 = {classes: new Set(['hidden'])};
  const title = {textContent: ''};
  const detail = {textContent: ''};
  const banner = {
    classes: node0.classes,
    classList: {
      add: c => node0.classes.add(c),
      remove: c => node0.classes.delete(c),
      contains: c => node0.classes.has(c),
    },
    querySelector: sel => (sel === '.switch-warning-title' ? title : detail),
    get hidden() { return node0.classes.has('hidden'); },
  };
  void node;
  return {banner, title, detail};
}

const dom = makeBanner();
const requests = [];
let acknowledgeReply = {ok: true, acknowledged: true};
const sandbox = {
  document: {getElementById: id => (id === 'switch_topology_warning' ? dom.banner : null)},
  fetch: async (url, options) => {
    requests.push({url, body: options && options.body ? JSON.parse(options.body) : null});
    return {json: async () => acknowledgeReply};
  },
  console: {error() {}, log() {}, warn() {}},
  JSON, Object, Array, String, Number, Boolean, Math, Date, RegExp, Error, Promise, Set, Map, Symbol,
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(SOURCE, 'utf8'), sandbox, {filename: 'lldp-topology.js'});

const {switchWarningText, shouldShowSwitchWarning, applySwitchTopology,
       renderSwitchTopology, acknowledgeSwitchTopology, macCellAttributes,
       macCellNote, isAlternateSwitchDevice, switchTopology,
       daisyWarningText, shouldShowDaisyWarning, applyDaisyTopology,
       renderDaisyWarning, acknowledgeDaisyChain, isDaisyChainedDevice,
       daisyCellAttributes, daisyCellNote, lldpConnectionSummary, daisyTopology} = sandbox;

// ---- fixtures: synthetic addresses and chassis ids only --------------------
const group = (id, count, first) => ({
  chassis_id: id, chassis_id_raw: id, chassis_name: `switch-${id}`,
  count, ips: Array.from({length: count}, (_, i) => `10.99.${first}.${i + 1}`),
});
function topology(groups, extra = {}) {
  const dominant = groups.length > 1 && groups[0].count > groups[1].count ? groups[0].chassis_id : '';
  return {
    switch_count: groups.length,
    groups,
    dominant_chassis_id: extra.tied ? '' : dominant,
    tied: !!extra.tied,
    minority_ips: extra.tied || !dominant ? []
      : groups.slice(1).reduce((all, g) => all.concat(g.ips), []),
    multi_switch: groups.length > 1,
    devices_without_lldp: extra.without || 0,
    ...extra,
  };
}
const TWO = topology([group('aaaa00000001', 10, 1), group('bbbb00000002', 3, 2)]);
const THREE = topology([group('aaaa00000001', 6, 1), group('bbbb00000002', 3, 2), group('cccc00000003', 2, 3)]);
const TIED = topology([group('aaaa00000001', 4, 1), group('bbbb00000002', 4, 2)], {tied: true});
const ONE = topology([group('aaaa00000001', 7, 1)]);

// ---- wording --------------------------------------------------------------
check('two switches are described as primary and another, without blame', () => {
  const text = switchWarningText(TWO);
  if (!text.detail.includes('10 devices are connected through the primary switch')) {
    throw new Error(`unexpected detail: ${text.detail}`);
  }
  if (!text.detail.includes('3 devices are connected through another switch')) {
    throw new Error(`unexpected detail: ${text.detail}`);
  }
  if (!text.detail.includes('verify uplink bandwidth between switches')) throw new Error('no uplink advice');
  const whole = `${text.title} ${text.detail}`.toLowerCase();
  for (const word of ['error', 'fault', 'problem with', 'misconfigur', 'must not', 'incorrect']) {
    if (whole.includes(word)) throw new Error(`wording implies a fault: ${word}`);
  }
});

check('three switches are summarised without naming a culprit', () => {
  const text = switchWarningText(THREE);
  if (!text.title.includes('across 3 network switches')) throw new Error(text.title);
  if (!text.detail.includes('inter-switch/uplink bandwidth')) throw new Error(text.detail);
  if (text.detail.includes('primary switch')) throw new Error('a summary must not designate a primary');
});

check('a tie is summarised, never split into primary and other', () => {
  const text = switchWarningText(TIED);
  if (text.detail.includes('primary switch')) throw new Error('a tie has no primary switch');
  if (!text.title.includes('2 network switches')) throw new Error(text.title);
});

check('singular wording for a single device on a switch', () => {
  const text = switchWarningText(topology([group('aaaa00000001', 4, 1), group('bbbb00000002', 1, 2)]));
  if (!text.detail.includes('1 device is connected through another switch')) throw new Error(text.detail);
});

// ---- when the banner shows ------------------------------------------------
check('one switch produces no warning', () => {
  if (shouldShowSwitchWarning(ONE, false)) throw new Error('a single switch is not a topology notice');
});

check('an inventory with no LLDP at all produces no warning', () => {
  const none = topology([], {without: 12});
  if (shouldShowSwitchWarning(none, false)) throw new Error('missing LLDP is not a second switch');
});

check('two switches produce a warning', () => {
  if (!shouldShowSwitchWarning(TWO, false)) throw new Error('a multi-switch inventory must warn');
});

check('an acknowledged inventory produces no warning', () => {
  if (shouldShowSwitchWarning(TWO, true)) throw new Error('acknowledgement suppresses the banner');
});

check('visibility is decided by the flag, not read back from the banner', () => {
  const source = fs.readFileSync(SOURCE, 'utf8');
  const block = source.slice(source.indexOf('function shouldShowSwitchWarning'));
  if (/classList\.contains\(['"]hidden/.test(block.slice(0, block.indexOf('function applySwitchTopology')))) {
    throw new Error('the decision reads the DOM');
  }
});

// ---- rendering and dismissal ----------------------------------------------
checkSeq('rendering shows the banner and dismissal hides it', async () => {
  requests.length = 0;
  renderSwitchTopology(TWO, false);
  if (dom.banner.hidden) throw new Error('the banner should be visible');
  if (!dom.title.textContent.includes('Multiple network switches')) throw new Error(dom.title.textContent);

  await acknowledgeSwitchTopology();
  if (!dom.banner.hidden) throw new Error('dismissing must hide the banner');
  if (switchTopology.acknowledged !== true) throw new Error('the flag was not set');
  if (requests.length !== 1 || requests[0].url !== '/api/lldp_topology/acknowledge') {
    throw new Error('the acknowledgement was not recorded server-side');
  }
  if (requests[0].body.acknowledged !== true) throw new Error('the request did not say what it acknowledged');
});

check('a dismissed banner stays dismissed across re-renders', () => {
  renderSwitchTopology(TWO, true);
  if (!dom.banner.hidden) throw new Error('a re-render resurrected the banner');
  renderSwitchTopology(THREE, true);
  if (!dom.banner.hidden) throw new Error('a third switch resurrected the banner');
});

checkSeq('a failed acknowledgement is not recorded as one', async () => {
  acknowledgeReply = {ok: false, error: 'nope'};
  renderSwitchTopology(TWO, false);
  await acknowledgeSwitchTopology();
  if (switchTopology.acknowledged !== false) throw new Error('a failure must not be treated as acknowledged');
  acknowledgeReply = {ok: true, acknowledged: true};
  renderSwitchTopology(TWO, false);
  if (dom.banner.hidden) throw new Error('the banner must come back when nothing was recorded');
});

// ---- highlighting is independent -------------------------------------------
check('minority devices are highlighted', () => {
  applySwitchTopology(TWO, false);
  if (!isAlternateSwitchDevice('10.99.2.1')) throw new Error('a minority device is not marked');
  if (isAlternateSwitchDevice('10.99.1.1')) throw new Error('a dominant-switch device must not be marked');
  const cell = macCellAttributes({ip: '10.99.2.1'});
  if (!cell.includes('alternate-switch')) throw new Error(cell);
  if (macCellAttributes({ip: '10.99.1.1'}) !== '') throw new Error('majority rows carry no treatment');
});

check('highlighting survives acknowledging the banner', () => {
  applySwitchTopology(TWO, true);
  if (!isAlternateSwitchDevice('10.99.2.1')) {
    throw new Error('acknowledging the banner must not clear the topology indication');
  }
  if (!macCellAttributes({ip: '10.99.2.1'}).includes('alternate-switch')) throw new Error('shading was lost');
});

check('highlighting follows the current topology when it changes', () => {
  applySwitchTopology(TWO, true);
  if (!isAlternateSwitchDevice('10.99.2.1')) throw new Error('setup');
  applySwitchTopology(ONE, true);
  if (isAlternateSwitchDevice('10.99.2.1')) throw new Error('a resolved topology must clear the marking');
  applySwitchTopology(THREE, true);
  if (!isAlternateSwitchDevice('10.99.3.1')) throw new Error('a new minority group must be marked');
});

check('a tie marks nobody as the minority', () => {
  applySwitchTopology(TIED, false);
  if (isAlternateSwitchDevice('10.99.1.1') || isAlternateSwitchDevice('10.99.2.1')) {
    throw new Error('with equal counts there is no basis for a minority designation');
  }
});

check('the highlight explains itself and names the switch', () => {
  applySwitchTopology(TWO, true);
  const note = macCellNote({ip: '10.99.2.1', lldp_chassis_name: 'switch-bbbb00000002'});
  if (!note.includes('Different switch')) throw new Error(note);
  if (!note.includes('switch-bbbb00000002')) throw new Error('the switch is not named');
  if (macCellNote({ip: '10.99.1.1'}) !== '') throw new Error('majority rows carry no note');
  if (!sandbox.ALTERNATE_SWITCH_NOTE.includes('different switch than the majority')) {
    throw new Error('the explanation does not say what it means');
  }
});

check('rendered strings are escaped', () => {
  applySwitchTopology(topology([group('aaaa00000001', 3, 1), group('bbbb00000002', 1, 2)]), true);
  const note = macCellNote({ip: '10.99.2.1', lldp_chassis_name: '<img src=x onerror=alert(1)>'});
  if (note.includes('<img')) throw new Error('markup from a device reached the page');
  if (!note.includes('&lt;img')) throw new Error('the value was not escaped');
});

// ---- no new polling --------------------------------------------------------
check('the module starts no timer and fetches nothing on its own', () => {
  const source = fs.readFileSync(SOURCE, 'utf8');
  for (const banned of ['setInterval', 'setTimeout', 'requestAnimationFrame']) {
    if (source.includes(banned)) throw new Error(`${banned} would be a polling loop`);
  }
  // Two advisories, so two acknowledgement requests. Every request this module
  // makes must be one of those; it reads no state of its own.
  const fetches = source.match(/fetch\('([^']+)'/g) || [];
  const targets = new Set(fetches.map(match => match.slice(7, -1)));
  if (targets.size !== 1 || !targets.has('/api/lldp_topology/acknowledge')) {
    throw new Error(`unexpected request targets: ${[...targets].join(', ')}`);
  }
});

// ---- daisy chains -----------------------------------------------------------
function daisyTopologyFixture(extra = {}) {
  return Object.assign({
    switch_count: 1,
    groups: [group('aaaa00000001', 3, 1)],
    dominant_chassis_id: 'aaaa00000001',
    tied: false,
    minority_ips: [],
    multi_switch: false,
    daisy_chained: true,
    devices_daisy_chained: ['10.99.1.3'],
    daisy_chained_multi_stream: [],
    bitrate_data_available: false,
    devices: {
      '10.99.1.1': {ip: '10.99.1.1', is_daisy_chained: false, resolution: 'direct',
                    immediate_neighbor: {chassis_name: 'core-a', port_id: 'Gi1/0/1'},
                    resolved_upstream_switch: {chassis_id: 'aaaa00000001', chassis_name: 'core-a', port_id: 'Gi1/0/1'}},
      '10.99.1.3': {ip: '10.99.1.3', is_daisy_chained: true, resolution: 'resolved_through_device',
                    daisy_chain_via_device: 'encoder-a', daisy_chain_via_ip: '10.99.1.1',
                    daisy_chain_hops: [{ip: '10.99.1.1', hostname: 'encoder-a'}],
                    immediate_neighbor: {chassis_name: 'encoder-a', management_ip: '10.99.1.1'},
                    resolved_upstream_switch: {chassis_id: 'aaaa00000001', chassis_name: 'core-a', port_id: null}},
    },
  }, extra);
}

check('a daisy chain is advised without claiming congestion', () => {
  const text = daisyWarningText(daisyTopologyFixture());
  if (!text.title.includes('Daisy-chained network connection detected')) throw new Error(text.title);
  if (!text.detail.includes('shares')) throw new Error(text.detail);
  const whole = `${text.title} ${text.detail}`.toLowerCase();
  for (const claim of ['overload', 'congest', 'exceeds', 'insufficient bandwidth', 'is saturated']) {
    if (whole.includes(claim)) throw new Error(`states a condition it cannot know: ${claim}`);
  }
  if (!whole.includes('verify')) throw new Error('gives the operator nothing to check');
});

check('a chained decoder on multiple streams gets the elevated wording', () => {
  const text = daisyWarningText(daisyTopologyFixture({daisy_chained_multi_stream: ['10.99.1.3']}));
  if (!text.title.toLowerCase().includes('bandwidth advisory')) throw new Error(text.title);
  if (!text.detail.includes('more than one stream')) throw new Error(text.detail);
  if (!text.detail.includes('1 Gb')) throw new Error('the shared path is not described');
  if (text.detail.toLowerCase().includes('will drop')) throw new Error('claims an outcome');
});

check('no daisy chain, no daisy advisory', () => {
  if (shouldShowDaisyWarning(daisyTopologyFixture({daisy_chained: false, devices_daisy_chained: []}), false)) {
    throw new Error('advised about a chain that is not there');
  }
  if (shouldShowDaisyWarning(daisyTopologyFixture(), true)) throw new Error('acknowledgement ignored');
  if (!shouldShowDaisyWarning(daisyTopologyFixture(), false)) throw new Error('a chain must be advised');
});

check('the two advisories are independent', () => {
  const both = daisyTopologyFixture({multi_switch: true, switch_count: 2,
                                     groups: [group('aaaa00000001', 3, 1), group('bbbb00000002', 1, 2)],
                                     minority_ips: ['10.99.2.1']});
  applySwitchTopology(both, true);
  applyDaisyTopology(both, false);
  if (!shouldShowDaisyWarning(both, daisyTopology.acknowledged)) {
    throw new Error('dismissing the switch banner silenced the daisy one');
  }
  applySwitchTopology(both, false);
  applyDaisyTopology(both, true);
  if (!shouldShowSwitchWarning(both, switchTopology.acknowledged)) {
    throw new Error('dismissing the daisy banner silenced the switch one');
  }
});

check('a chained device is marked, and a direct one is not', () => {
  applyDaisyTopology(daisyTopologyFixture(), false);
  if (!isDaisyChainedDevice('10.99.1.3')) throw new Error('the chained device is not marked');
  if (isDaisyChainedDevice('10.99.1.1')) throw new Error('a direct device must not be marked');
  if (!daisyCellAttributes({ip: '10.99.1.3'}).includes('daisy-chained')) throw new Error('no treatment');
  if (daisyCellAttributes({ip: '10.99.1.1'}) !== '') throw new Error('direct rows carry no treatment');
});

check('the daisy marking is distinct from the minority-switch marking', () => {
  const both = daisyTopologyFixture({multi_switch: true, minority_ips: ['10.99.2.1'],
                                     devices_daisy_chained: ['10.99.1.3']});
  applySwitchTopology(both, false);
  applyDaisyTopology(both, false);
  const daisy = daisyCellAttributes({ip: '10.99.1.3'});
  const minority = macCellAttributes({ip: '10.99.2.1'});
  if (daisy === minority) throw new Error('the two topology states render the same');
  if (minority.includes('daisy')) throw new Error('a minority device was marked as chained');
});

check('the hover panel says the path in words', () => {
  applyDaisyTopology(daisyTopologyFixture(), false);
  const chained = lldpConnectionSummary('10.99.1.3');
  if (chained.kind !== 'daisy') throw new Error(chained.kind);
  if (!chained.heading.includes('Daisy chained through')) throw new Error(chained.heading);
  if (!chained.through.includes('encoder-a')) throw new Error(chained.through);
  if (!chained.upstream.includes('core-a')) throw new Error('the upstream switch is not named');
  const direct = lldpConnectionSummary('10.99.1.1');
  if (direct.kind !== 'direct') throw new Error(direct.kind);
  if (!direct.heading.includes('Connected to')) throw new Error(direct.heading);
  if (direct.port !== 'Gi1/0/1') throw new Error('the port is not shown for a direct attachment');
});

check('the chained note names the device in front', () => {
  applyDaisyTopology(daisyTopologyFixture(), true);
  const note = daisyCellNote({ip: '10.99.1.3'});
  if (!note.includes('Daisy chained via')) throw new Error(note);
  if (!note.includes('encoder-a')) throw new Error(note);
  if (daisyCellNote({ip: '10.99.1.1'}) !== '') throw new Error('a direct row carries no note');
});

check('the page takes topology from the inventory response it already fetches', () => {
  const page = fs.readFileSync(INDEX, 'utf8');
  if (!page.includes('renderSwitchTopology(data.lldp_topology, data.topology_warning_acknowledged)')) {
    throw new Error('the banner does not consume the inventory payload');
  }
  if (page.includes("fetch('/api/lldp_topology')")) {
    throw new Error('a second request for state the inventory response already carries');
  }
});

check('both semantic states are defined for light and dark', () => {
  const page = fs.readFileSync(INDEX, 'utf8');
  const dark = page.slice(page.indexOf(':root{'), page.indexOf('.light{'));
  const light = page.slice(page.indexOf('.light{'), page.indexOf('.light{') + 900);
  for (const token of ['--multi-switch-warning-bg', '--multi-switch-warning-border',
                       '--multi-switch-warning-fg', '--alternate-switch-bg',
                       '--alternate-switch-border', '--alternate-switch-fg']) {
    if (!dark.includes(token)) throw new Error(`${token} missing from the dark palette`);
    if (!light.includes(token)) throw new Error(`${token} missing from the light palette`);
  }
  const rules = page.slice(page.indexOf('.switch-warning{'), page.indexOf('/* markup */') + 1 || undefined);
  const treatment = rules.slice(0, rules.indexOf('</style>') + 1 || rules.length);
  if (/#[0-9a-fA-F]{3,6}/.test(treatment.split('td.alternate-switch')[0].split('.switch-warning{')[1] || '')) {
    throw new Error('the banner hard-codes a colour instead of using its token');
  }
});

Promise.all(pending).then(() => {
  console.log(failures.length
    ? `\nlldp-topology.js test: ${failures.length} failure(s)\n  - ${failures.join('\n  - ')}`
    : '\nlldp-topology.js test: all checks passed');
  process.exit(failures.length ? 1 : 0);
});

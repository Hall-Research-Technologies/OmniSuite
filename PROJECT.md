# OmniSuite architecture and USB extender plan

## Product purpose

OmniSuite is a local Atlona by Hall Research OmniStream device and firmware manager with an A/V matrix, a configuration workspace, a USB matrix for compatible OmniStream endpoints, and device maintenance controls. The active application is a threaded Flask server in `OmniMatrix_upgrade_server_v7_6y.py` serving static HTML and JavaScript from `ui/matrix`.

Runtime state is file based, not database based. The server stores configuration, device cache, scan results, and CSV export in the platform data directory (`%LOCALAPPDATA%/OmniSuite` on Windows unless `OMNI_DATA_DIR` is set). It uses atomic replacement for the primary cache writes and a process-local lock for cache I/O.

## Current device types

The current normal scan recognizes only OmniStream devices whose `systeminfo` response reports an encoder or decoder type:

- Encoders: e.g. AT/HW-OMNI-E4521, E4111, and E4111-WP. The scan obtains `sessions` and records multicast session information.
- Decoders: e.g. AT/HW-OMNI-D4511 and D4111. The scan obtains `ip_input` and records input multicast information.

Normal device control uses the OmniStream WebSocket API at the configured HTTP/WebSocket port and `/wsapp/` path. The server keeps the discovered model, role, IP, hardware-derived MAC (from the license response), hostname, firmware, selected password, system information, and role-specific fields in the shared cache.

`omni_matrix_logic.py` consumes that cache to maintain encoder and decoder matrix state. `omni_matrix.py` is an older standalone/CLI-oriented matrix implementation and protocol helper; the Flask server is the active product entry point.

## Network selection

`GET /api/adapters` enumerates active private IPv4 adapters. On Windows it first parses `ipconfig`, then falls back to `psutil`, `ip`, `ifconfig`, and route output. Each adapter exposes an address, CIDR, and a scan expression. The UI supplies either the selected adapter range or a free-form target expression to `POST /api/scan`.

The application does not change the computer NIC configuration. Directed scans accept individual addresses, address ranges, and CIDR input.

## Existing discovery architecture

`POST /api/scan` expands the requested target expression, performs a short TCP preflight, and then probes reachable addresses through the OmniStream WebSocket API. It uses a per-request `ThreadPoolExecutor` with up to 96 workers. A successful probe fetches `systeminfo`, optional license and timezone data, then encoder or decoder configuration. Unknown device roles are deliberately omitted.

The scan merges results into `units_cache.json`, deduplicating by MAC when available and otherwise by IP, writes `scan_results.json`, and reloads the matrix helper. Startup also launches a delayed cache-verification task; encoder and decoder polling endpoints use small, separate thread pools.

Existing discovery is performance-sensitive. AT-OMNI-311/324 discovery must not delay existing encoder/decoder discovery. New discovery providers should operate independently and concurrently where safe. Encoder and decoder results must render as soon as they are available, without waiting for USB discovery; USB results must render without waiting for range scans.

## Existing E4521/D4511 USB implementation and matrix

The USB Matrix page (`/matrix/usb`) obtains `GET /api/usb_state`. The server filters the normal OmniStream cache to USB-capable E4521/E4511/D4511 variants, obtains each endpoint's `usb_icron` configuration via the existing OmniStream WebSocket API, and separates devices by firmware-reported `LEX` and `REX` type. The UI renders LEX columns and REX rows, permits host-port/filter/type controls where appropriate, and serializes per-REX UI actions.

`POST /api/usb_pair` and `POST /api/usb_unpair` read and modify `paired_devices` through `usb_icron` configuration writes. They use MAC-based peer membership, read-back verification, and a process-wide reentrant USB routing lock. The server rejects cross-subnet pair requests using a same-/24 check; the UI disables those cells as well. A LEX is limited to five REX peers, and replacement routing cleans prior peer membership. This behavior is established and must remain unchanged in the initial USB-extender phase.

The A/V Matrix page (`/matrix`) obtains `GET /api/state` and writes routes through `POST /api/route`; it uses cached encoder session multicast values and decoder inputs. The configuration page (`/matrix/configure`) currently has Encoder and Decoder sections. Device Info is the root page and the matrix UI currently exposes encoder/decoder lists; the requested USB count and configuration section are planned additions.

The existing E4521/D4511 GUI can pair endpoints in its `usb_icron` control plane. It does not currently discover, query, or control AT-OMNI-311/324 devices. Therefore unified routing would otherwise create competing pairing mechanisms and is explicitly out of scope for the first phase.

## Planned AT-OMNI-311/324 USB extender architecture

### Capabilities and identity

The planned capability mapping is:

| Device | Capability |
| --- | --- |
| E4521 | VIDEO_ENCODER, USB_HOST |
| D4511 | VIDEO_DECODER, USB_DEVICE |
| AT-OMNI-311 | USB_HOST |
| AT-OMNI-324 | USB_DEVICE |

Do not refactor E4521/D4511 solely to introduce these capabilities during the initial phase if it raises regression risk. AT-OMNI-311/324 records must retain MAC-based identity and separately represent `DISCOVERED`, `MANAGEABLE`, `LOCAL/OFF-NET`, and `PAIRING_ELIGIBLE`. An off-net device is not automatically offline. Devices on incompatible IP subnets are never pairing-eligible, although a reachable device may still be discovered and configured.

### Protocol scope

AT-OMNI-311/324 use a binary UDP protocol on UDP port 6137. Planned support includes broadcast discovery, direct-IP queries, Advanced Query, MAC-based identity, network configuration, pairing information, identify/blink, and reboot. Exact packet semantics and same-L2/off-net behavior are hardware-dependent and must be validated against devices or authoritative protocol documentation before control operations are enabled.

### Discovery modes and performance

Planned discovery modes are:

1. Fast automatic local broadcast discovery.
2. Configurable IP-range discovery in a background worker with bounded concurrency.
3. Direct Discover-by-IP.
4. Same-L2 off-net/recovery discovery only where actual device and OS behavior supports it.

Local UDP broadcast discovery should run concurrently with existing discovery where safe, with its own sockets, timeout, result channel, and cancellation/error handling. It must not be awaited by `/api/scan`, startup cache verification, A/V matrix rendering, or the normal Device Info rendering path. Range scans must be explicitly invoked or scheduled separately; a slow or unreachable range may not hold normal discovery and must not scan addresses serially.

### Confirmed Phase 1A implementation

`omni_usb_extender.py` now owns standalone extender packet construction/parsing, message correlation, UDP transport, MAC-keyed state, and bounded discovery workers. It persists standalone records and configured ranges in `usb_extenders.json`, separate from the OmniStream A/V cache. The server exposes read-only state, configured-range management, local broadcast discovery, direct IP discovery, and asynchronous range discovery under `/api/usb_extenders`.

The UDP provider is not called from `/api/scan`; it has a dedicated two-worker background executor, while individual range scans use at most 24 UDP workers and reject any combined range over 1,024 hosts. Local broadcast makes basic records available before Advanced Query enrichment. The standalone API now also exposes MAC-targeted DHCP/static network configuration, Blink On, Blink Off, bounded auto-off Identify, and reboot. Network commands require validated IPv4 input and ACK/NACK handling, then requery by MAC; their result distinguishes verified rediscovery from accepted-but-not-yet-verified. These operations still require physical hardware validation.
### Authoritative versus cached state model

Standalone extender state exists in two distinct forms, and the distinction is
load bearing.

*Cached* state is whatever the last successful discovery wrote to
`usb_extenders.json`. It preserves MAC identity across IP changes and drives
rendering, but it is never authoritative for a routing decision. `last_seen`
records when the record was refreshed and `advanced_query_at` records when its
pairing information was last read; `state()` derives `online`, `stale`,
`manageable`, and `pairing_state_fresh` from those timestamps rather than storing
them.

*Authoritative* state is produced only by `read_pairing(mac)`, which performs a
MAC-verified Query and a fresh Advanced Query during the current call and returns
the values it just read. It never falls back to the cache: if the Advanced Query
times out, returns a malformed payload, or reports a peer count outside the
documented limit, the read fails with `ADVANCED_QUERY_FAILED` and carries no
`paired_macs` at all.

`get_route_state(host_mac, device_mac)` calls `read_pairing` on both endpoints.
It reports `routed` only when both reads succeed and agree — the AT-OMNI-311
lists the AT-OMNI-324 *and* the AT-OMNI-324 names that AT-OMNI-311. If either
endpoint cannot be read it returns `UNVERIFIABLE`; if only one side reports the
relationship it returns `INCONSISTENT`. Neither carries a `routed` value.

A UDP ACK means the command was acknowledged, never that a route exists.
`pair_route` and `unpair_route` return `VERIFIED_SUCCESS` only after a fresh
both-endpoint read agrees with the intended end state; an ACK whose read-back
cannot be completed returns `COMMAND_ACCEPTED_UNVERIFIED`, and an ACK contradicted
by a successful read-back returns `VERIFICATION_FAILED`. `ALREADY_ROUTED` and
`ALREADY_UNROUTED` are likewise issued only from fresh both-endpoint reads.

Route transaction statuses are upper case (`OK`, `UNVERIFIABLE`, `INCONSISTENT`,
`NOT_ELIGIBLE`, `PROTOCOL_ERROR`, `MAC_MISMATCH`, `ALREADY_ROUTED`,
`ALREADY_UNROUTED`, `PEER_LIMIT`, `CONFLICT`, `REJECTED`, `COMMAND_TIMEOUT`,
`UNREACHABLE`, `INTERFACE_UNAVAILABLE`, `COMMAND_ACCEPTED_UNVERIFIED`,
`VERIFICATION_FAILED`, `VERIFIED_SUCCESS`). Discovery-layer statuses remain lower
case (`found`, `timeout`, `mac_mismatch`, `not_found`, `protocol_error`,
`invalid_address`, `interface_unknown`, `interface_unavailable`) because the
existing HTTP API and UI consume them.

### Identity, liveness, and network safety

A device record is keyed by MAC and survives restarts, but persisted history never
implies current presence: `online` requires the record to have been seen by the
running process within `ONLINE_TTL` (300 s). After a restart, known devices render
as `Offline (stale)` on the Configure page until the next discovery, and the Device
Info USB count reflects only online standalone AT-OMNI-311/324 devices. Nothing
polls the network to maintain the flag; state polling is not discovery.

Any targeted refresh of a known MAC verifies the responding MAC. If a different
device answers at a remembered address, the expected record is left untouched and
is not marked seen, the responder is recorded under its own MAC, and the operation
reports `mac_mismatch` / `MAC_MISMATCH`. No command is ever addressed to a device
under another device's identity. This applies to direct discovery, route reads,
and network-configuration verification alike.

Network eligibility fails closed. `network_relation` returns `UNKNOWN` when real
interface information is unavailable, and a device is never compared against a
network fabricated from its own address. `UNKNOWN` is never pairing eligible, and
commands whose interface context cannot be resolved return `interface_unknown`
rather than letting the routing table pick a NIC. Commands and queries bind to the
interface the device was discovered on, so a multi-NIC host does not silently
egress the wrong adapter; an interface that has disappeared reports
`interface_unavailable`.

### Discovery bounds and background work

Range input is sized arithmetically before expansion, so `10.0.0.0/8`,
`1.0.0.0-254.0.0.0`, and `0.0.0.0/0` are rejected immediately without allocating
the address space they describe, and the combined configured set is bounded the
same way. A single scan expands its ranges once and hands the target list to the
worker. Range scans run on their own single-worker executor and reject an
overlapping submission with `ScanBusyError` (HTTP 409) instead of queueing
unbounded work; broadcast enrichment keeps a separate two-worker pool so a long
range scan cannot delay it. State writes are debounced and performed outside the
state lock, so a large scan does not rewrite the file per device or block UI
polling.

The Force Pair opcode is **not** established by any specification available to
this project. The value previously used, `0x0301`, collides with
`ADVANCED_QUERY_RESPONSE`. `FORCE_PAIR` is therefore `None`, `build_force_pair()`
raises, and no production path can transmit it. Normal Pair never falls back to
Force Pair, and specific Unpair never falls back to Unpair All.

### Device Info is the single discovery and clear surface

Device Info owns discovery. Its adapter selection, Targets field, Scan button and
Clear Units button drive every provider; Configure &rsaquo; USB has no discovery
controls and is an inventory and configuration view only.

**Scan** dispatches two independent providers from the one click. `POST /api/scan`
is unchanged and still returns encoder and decoder results on its own timing.
Alongside it, and never awaited by it, the UI posts to
`/api/usb_extenders/discover`, which hands local broadcast discovery to a
dedicated single worker and any Targets expression to the bounded range worker,
then returns immediately. Encoder and decoder results render as soon as
`/api/scan` responds; USB results arrive afterwards and the USB count refreshes on
its own schedule.

```
Scan click
  |-- POST /api/scan ---------------------> encoders / decoders  (unchanged, never waits)
  \-- POST /api/usb_extenders/discover ---> local broadcast + optional targets (background)
```

The two USB scopes are independent of each other as well as of `/api/scan`: a busy
range scan, an invalid Targets expression, or an interface failure is reported in
the response's `issues` list without suppressing the other scope, and none of it
can fail or delay the OmniStream scan. Targets are expanded only by the server's
bounded `parse_ranges`; the UI never expands an address range.

USB discovery reuses the interface address of the adapter already selected on
Device Info, carried on the option element. There is no second interface picker.

**Clear Units** is the single clear action. It clears the encoder/decoder cache,
scan results and CSV export exactly as before, and additionally forgets the
standalone USB inventory through `clear_devices()`. That is inventory only: no
packet is sent, no pairing, network setting, or power state is touched, and
configured USB ranges are kept. The persisted inventory in `usb_extenders.json` is
emptied so cleared devices do not reappear on refresh; they return after the next
successful discovery.

### USB endpoint to OmniStream parent association

The standalone UDP provider also sees the USB/Icron identities belonging to
E4521 and D4511 units. Those records are kept and associated with their parent
rather than filtered out.

Correlation is an exact match of the normalized USB MAC against the `usb_mac` the
normal scan already recorded for each USB-capable unit, alongside `usb_type`
(LEX/REX), `ip`, `hostname` and `model`. It is a pure in-memory lookup over the
device cache and costs no network I/O. Correlation must never be inferred from IP
proximity, MAC similarity, address suffixes, or hostname patterns.

`_usb_parent_index()` builds the map and `_usb_extender_view()` applies it as
enrichment. The UDP provider's canonical MAC identity, IP, revision and network
mode are never overwritten; the association only adds `integrated`, `parent_ip`,
`parent_hostname`, `parent_model`, `parent_role`, `display_model`, `usb_role`,
`identify_via` and `network_config_supported`. Observed on hardware:

| USB MAC | Model | USB role | Device IP | Hostname | USB IP |
| --- | --- | --- | --- | --- | --- |
| B8:98:B0:07:85:ED | HW-OMNI-E4521 | USB Host / LEX | 192.168.100.141 | hw-omni-e4521-00002 | 192.168.100.246 |
| B8:98:B0:07:85:C7 | HW-OMNI-D4511 | USB Device / REX | 192.168.100.32 | hw-omni-d4511-085c6 | 192.168.100.108 |

The Device Info USB count counts online endpoints once per canonical MAC. State is
already keyed by MAC, so an endpoint that is also correlated to a parent is never
counted twice. Stale records are excluded. Encoder and decoder counts are
unchanged.

### Classification: discovery protocol is not control ownership

The UDP Advanced Query reports only a LEX/REX role code, which the protocol layer
names `AT-OMNI-311` / `AT-OMNI-324`. That is a **USB role, not a product
identity**: the Icron endpoint inside an E4521 reports code 0 and inside a D4511
reports code 1, exactly like a standalone unit. Treating that name as the product
is what previously displayed integrated endpoints as AT-OMNI-311/324.

Association therefore wins. When a USB MAC is authoritatively mapped to an
OmniStream parent, the parent supplies the model, hostname, device IP and USB role
for presentation, and owns every control operation. The UDP-reported value is kept
only as `udp_reported_type` / `udp_role_code` for diagnostics and is never shown.

Each endpoint carries a `classification`:

| Classification | Meaning | Standalone control |
| --- | --- | --- |
| `INTEGRATED` | USB MAC maps to an E4521/D4511 parent | Never |
| `STANDALONE` | Association is complete and this MAC matches no parent | Yes |
| `UNCONFIRMED` | Association is still incomplete, so nothing is proven | Never |

`UNCONFIRMED` exists because `usb_mac` is recorded opportunistically: a
USB-capable unit that has not been verified yet has no association, and its
endpoint would otherwise look standalone. Rather than risk aiming a standalone
reboot or network command at an E4521's USB endpoint, classification fails closed
until every USB-capable unit's endpoint is known.

### The single association pipeline

`_usb_parent_context()` is the one place USB endpoint ownership is determined.
Configure &rsaquo; USB, the USB Matrix inventory, every control endpoint, and the
bench diagnostics all consume it; none of them decides ownership independently.

It is recomputed from current runtime state on every call and assembles
association from two data-driven sources:

1. `usb_mac` / `usb_type` recorded on the cached device by the normal scan and
   cache verification.
2. `_USB_ASSOCIATION`, a runtime map of authoritative `usb_icron` observations
   keyed by parent device IP.

`_record_usb_association()` populates that map from **every** place the
application already reads `usb_icron` — the USB Matrix build, unit polling, cache
verification, and the background `_refresh_usb_parent_map()` harvest — so no
additional device traffic is introduced. `_refresh_usb_parent_map()` also writes
`usb_mac`/`usb_type` back into the device cache so the association survives a
restart. It runs in the background during discovery, never from `/api/scan` and
never from a polling read.

Because association is re-derived on each call, parent information that arrives
later automatically resolves an endpoint that was UNCONFIRMED, with **no USB
rescan**: an endpoint discovered at T0 with no parent data becomes INTEGRATED at
T1 as soon as normal OmniStream discovery learns the parent's USB MAC.

There are no per-device rules. Association is exact normalized-MAC matching only;
the pipeline branches on no address, subnet, MAC prefix, hostname pattern, or
vendor string, and a test asserts that none of the live device values appear
anywhere in production code.

`GET /api/diagnostics/usb_endpoints` renders the pipeline's current result — one
row per endpoint with its classification, parent, control owner, pairing source,
and the reason it resolved that way — plus any units still blocking
classification. It is read-only and sends nothing to any device.

### Association durability and scan_results integrity

A normal scan reports no `usb_mac`/`usb_type`, so a rebuilt cache record would
drop association learned elsewhere. Two mechanisms keep it:

- `_USB_ASSOCIATION` is persisted to `usb_associations.json` and restored at
  startup, keyed by the parent's **hardware MAC** when known (falling back to the
  address only while the MAC is still unknown). Association therefore survives a
  restart, a cache clear, and an address change.
- `_preserve_usb_association_fields()` runs in `api_scan` immediately before the
  cache is written, restoring `usb_mac`/`usb_type` onto rebuilt records matched by
  that same stable identity. It is a pure in-memory lookup with no device traffic
  -- roughly 6 ms for 2,000 units -- so `/api/scan` timing is unchanged. Values
  already present on a record are never overwritten, so new authoritative data
  always wins.

An address-keyed entry is only applied when it cannot belong to a different
physical device: once a parent MAC is recorded, a unit that merely inherited the
IP address does not inherit the metadata.

`scan_results.json` is written from several code paths. Those used a plain
truncating `open(..., "w")`, which is neither atomic nor serialized: a reader
could observe a half-written file, two writers could interleave, and an
interrupted write left the file permanently truncated. Every read and write now
goes through `_read_scan_results()` / `_save_scan_results()`, which write to a
per-process, per-thread temporary file and `os.replace` it under one lock. A
malformed file is quarantined once as `scan_results.corrupt-<timestamp>.json`,
returns `{}` to the caller, and discovery continues; the next write starts clean.
The same atomic pattern protects `usb_associations.json`.

### Control and pairing ownership

| Operation | Standalone AT-OMNI-311/324 | Integrated E4521/D4511 endpoint |
| --- | --- | --- |
| Identify | UDP Blink | Parent's OmniStream identify |
| Network | UDP DHCP/static | Refused (409), configure on the parent |
| Reboot | UDP reboot | Refused (409), reboot the parent |
| Pairing | Fresh Advanced Query | `usb_icron`, shown on the USB Matrix |

For an integrated endpoint the view sets `pairing_source: "usb_icron"` and
**removes** `paired_macs` entirely, so UDP Advanced Query pairing can never be
mistaken for authoritative state on a device that `usb_icron` owns.

### One derived liveness calculation

`_usb_extender_view()` is the single derivation of `online` / `stale` /
`last_seen`. `/api/usb_state` builds its standalone inventory from that same view
object rather than re-reading state, so the USB Matrix and Configure &rsaquo; USB
cannot disagree. Matrix rows carry `last_seen` so the client renders the same
freshness detail. Peers show a fresh Advanced Query count, or `Stale` when pairing
was read earlier but not this session, or `Not read` when it never succeeded —
never a bare `Unknown`.

### Identify dispatch

Identify must light the physical device, so it is dispatched by ownership:

```
Identify USB endpoint
  |-- standalone AT-OMNI-311/324 -> UDP extender Blink (identify_via: "extender")
  \-- integrated E4521/D4511     -> existing OmniStream identify on the parent
                                    (identify_via: "parent")
```

`_omnistream_identify(ip)` is the single implementation of the OmniStream identify
method, shared by `/api/blink` and by the USB identify route; the UDP Blink is
never aimed at a USB IP that belongs to an E4521/D4511. The OmniStream identify is
a one-shot method, so discrete blink `on`/`off` is refused for an integrated
endpoint rather than being silently mapped onto it.

### Network configuration ownership

Standalone AT-OMNI-311/324 keep the existing DHCP/static action. For an integrated
E4521/D4511 USB endpoint the standalone network command is refused with HTTP 409
naming the parent device: nothing establishes that its USB-side address may be
reconfigured that way merely because it answers discovery. The Configure page
disables the control and the server enforces it.

### Corrected network-mode semantics

The documented Query network-mode byte is `0 = DHCP` and `1 = Static`. An earlier
build inverted this, so every device displayed backwards. The fix is in the
protocol parser, not in the UI. Because the raw byte is persisted as
`network_mode_code`, `state()` re-derives `network_mode` from it, so records
written by the earlier build correct themselves without waiting for rediscovery.
The write side was inspected and is unaffected: DHCP and static are separate
documented opcodes (`IP_DHCP` / `IP_STATIC`) and never carried this field.

### Standalone units in the USB Matrix inventory

`GET /api/usb_state` reports standalone AT-OMNI-311 units in `standalone_lex` and
AT-OMNI-324 units in `standalone_rex`. The routing grid is built from `lex` and
`rex` alone, so these rows appear in the LEX/REX unit lists without creating any
route cell, and existing E4521/D4511 routing is untouched.

One physical USB endpoint appears once. A UDP record that correlates to an
OmniStream parent is excluded from the standalone arrays because that endpoint
already has a `lex`/`rex` row; the existing row is instead enriched with
`usb_discovered`, `usb_online`, and the observed `usb_ip` when firmware reported
none. Only a standalone unit with no parent becomes a new row.

Fields that are not established for standalone units are presented as read-only
rather than invented. Host Port is a fixed physical connector, shown as
`Fixed / N/A` for an AT-OMNI-311 and `N/A` for an AT-OMNI-324; no editable
selector is offered. Device Filtering is not established by the AT-OMNI-311/324
API and is shown as `Not supported`. Peers are stated only when the server reports
a fresh Advanced Query (`pairing_state_fresh`); otherwise the count is withheld and
displayed as `Unknown` rather than presenting cached pairing as authoritative.

### Confirmed Phase 1B UI

The Device Info header independently polls standalone extender state and displays a USB count that includes only discovered AT-OMNI-311/324 records. The Configure page has a third USB section with selected-interface discovery, direct discovery, persistent range management, standalone-device status/pairing display, and network, identify, and reboot actions. It does not add standalone devices to the USB Matrix or alter its routing controls.

### UI plan

Device Info will show `Encoders # | Decoders # | USB #`; USB counts only standalone discovered AT-OMNI-311 and AT-OMNI-324 devices. The Configuration page will add a USB section after Encoder and Decoder configuration, following existing table and control patterns. The initial discovery/configuration phase will not alter the USB Matrix or implement matrix routing.

An eventual, separately approved USB matrix could support E4521 <-> D4511, E4521 <-> OMNI-324, OMNI-311 <-> D4511, and OMNI-311 <-> OMNI-324. It requires a single ownership model for pairing operations, compatibility checks, rollback/read-back semantics, and hardware validation before it can replace or extend the existing `usb_icron` path.

## AT-OMNI-311/324 routing: hardware validated

Validated on the bench against a physical AT-OMNI-311 and AT-OMNI-324.

**Pair and Unpair are per-endpoint operations.** This corrected the previous
assumption. A `Pair` (`0x0302`) or `Unpair` (`0x0303`) updates only the pairing
table of the device it is addressed to; neither propagates to the peer. A
complete route therefore requires the command on **both** endpoints:

```
Pair    -> AT-OMNI-311, peer = 324 MAC
Pair    -> AT-OMNI-324, peer = 311 MAC
Unpair  -> AT-OMNI-311, peer = 324 MAC
Unpair  -> AT-OMNI-324, peer = 311 MAC
```

Sending only one command leaves a one-sided table that never converges: a
single-sided Unpair was observed stable for 40 s with the peer still listing the
other endpoint. `_dual_command()` sends both and `_rollback()` reverses the
endpoint that accepted if the second fails, so a partial failure does not leave a
half route.

**Timing.** ACK latency was 1.5-2.8 ms. There is no measurable propagation delay:
both endpoints agreed on the very first read-back in every trial
(`verification_attempts = 1`). A complete verified Pair or Unpair through the
transaction service takes about 33 ms end to end.

**Production verification policy.** `USB_ROUTE_VERIFY_ATTEMPTS = 3` and
`USB_ROUTE_VERIFY_DELAY = 0.15 s` (worst case 0.3 s). Measurement supports a
single immediate read; the retries are margin only, not a guess.

**Peer semantics.** `HOST_PEER_LIMIT = 7` and `DEVICE_PEER_LIMIT = 1` remain the
documented values. An AT-OMNI-311 may own several AT-OMNI-324 peers, so the
Matrix must not restrict a 311 to one 324; an AT-OMNI-324 has a single owner, so
each REX row remains single-select. A 324 already owned by another 311 returns
`CONFLICT` and is never taken over: Force Pair is not used, and the existing route
is never silently removed.

**Force Pair remains unsupported.** No specification establishes its opcode; the
value previously used collided with `ADVANCED_QUERY_RESPONSE`. `FORCE_PAIR` is
`None`, `build_force_pair()` raises, and nothing calls it. `UNPAIR_ALL` is never
transmitted from any server path.

## usb_icron and UDP Advanced Query observe the same pairing table

Read-only comparison across all eight integrated endpoints showed the two views
describing one underlying Icron state. For every REX, its UDP Advanced Query peer
list matched exactly the LEX that `usb_icron` reported. For example `usb_icron`
reported decoder `.151` active on encoder `.143`, and the UDP Advanced Query of
`.151`'s USB endpoint listed `.143`'s USB MAC.

One asymmetry is unexplained and remains an open question: a LEX-side Advanced
Query returned fewer peers than `usb_icron` attributed to that encoder. The REX
side was coherent in every case. Integrated pairing is therefore still presented
from `usb_icron`, which is the established control plane.

## Mixed-family results: control plane only

Both mixed combinations were tested on endpoints that had **no** existing pairing,
so no production route was disturbed, and both were restored and verified
afterwards.

| Test | Result |
| --- | --- |
| Integrated E4521 LEX to standalone AT-OMNI-324 | Pair ACKed on both endpoints, both Advanced Query tables agreed, Unpair cleared both. Parent `usb_icron` neither rejected nor overwrote the relationship. |
| Standalone AT-OMNI-311 to integrated D4511 REX | Same: both tables agreed, Unpair cleared both, `usb_icron` did not conflict. |

Both are now classified `SUPPORTED_MIXED_EXPERIMENTAL`: the pairing tables agree
and Pair/Unpair are hardware-verified, but **USB data-plane operation is still
unverified** — no peripheral enumeration test has been performed. They are
routable in the Matrix precisely so that test can be carried out; a successful
Pair is never treated as data-plane evidence.

### Mixed routing enablement

Re-verified through the production `/api/usb_route/pair` and `/api/usb_route/unpair`
endpoints rather than the bench diagnostics, so the shipping path is what was
proven:

| Combination | Control plane | Data plane |
| --- | --- | --- |
| E4521 LEX to standalone AT-OMNI-324 | Verified through the production endpoint. Pair and Unpair each ACKed on both endpoints; both Advanced Query tables agreed; `VERIFIED_SUCCESS` on the first attempt. | **Not verified** — no physical test yet |
| Standalone AT-OMNI-311 to D4511 REX | Verified through the production endpoint on operator-authorised hardware (see below). | **Verified** — physically tested in the five-peer topology |
| E4521 to D4511 | Established `usb_icron` path, unchanged | Verified (pre-existing) |
| AT-OMNI-311 to AT-OMNI-324 | Verified | **Verified** — physically tested in the five-peer topology |

The two verifications came from the operator running USB peripherals across the
five-peer topology on the AT-OMNI-311 at `192.168.100.135`
(`00:1B:13:05:50:50`), which held two standalone AT-OMNI-324 and three integrated
D4511 peers at once. That single physical test covers both `311 -> 324` and
`311 -> D4511`, because both families were carrying traffic in it.

`E4521 -> 324` is deliberately left unverified. Nothing about the other three
upgrades it, and a working Pair never has.

**Five is the number physically tested, not a discovered maximum.** The
documented model remains `HOST_PEER_LIMIT = 7`; five is simply how many peers
have been proven to carry USB simultaneously.

**AT-OMNI-311 to D4511, on authorised hardware.** The D4511 at `192.168.100.32`
was released from its `usb_icron` owner `192.168.100.143` through the established
`/api/usb_unpair` path that owns that relationship, then routed from the
AT-OMNI-311 at `192.168.100.128` through `POST /api/usb_route/pair` -- the same
endpoint a Matrix click uses. Both endpoints ACKed `0x302`, and a fresh Advanced
Query of both agreed on the first attempt:

```
AT-OMNI-311 .128   peers = ['00:1B:13:04:6A:EA', 'B8:98:B0:07:85:C7']
D4511 .32          peers = ['00:1B:13:04:E9:6E']
```

The route was left active for the operator's physical USB test. Its predecessor
(`192.168.100.143 -> 192.168.100.32`) is recorded for restoration. The E4521 at
`.143` kept its other two REX peers throughout; no unrelated route was touched.

A first attempt was rolled back by the test harness on a false negative: it
verified the integrated side using the derived view's `paired_macs`, which is
deliberately removed for an integrated endpoint. Verification of a mixed route
must use the transaction's own source -- a fresh Advanced Query of both
endpoints. The rollback itself behaved correctly, clearing both endpoints.

The original standalone route was freed for the test and restored afterwards;
restoration was confirmed by reading both endpoints (`routed=True`, owner
`00:1B:13:04:E9:6E`), and both integrated endpoints were left with empty tables.

`USB_ROUTE_DATA_PLANE_VERIFIED` is a separate table from `USB_ROUTE_CAPABILITY`
for exactly this reason: enabling a route and having evidence that USB traffic
crosses it are different claims, and only `("integrated", "integrated")` carries
the second one.

The UDP transaction serves every combination it owns because both families report
their USB role identically over Advanced Query. `E4521 to D4511` is refused by
`_standalone_route_request` — not because it cannot work, but because it belongs
to the established `usb_icron` path, and two owners for one relationship is how
state diverges.

Note that `/api/usb_state` could not display the standalone peer on the parent's
row, because its peer resolution maps peers onto known OmniStream units and a
standalone extender is not one. That is a presentation limit, not a conflict.

## USB Matrix architecture

`GET /api/usb_state` returns the established `lex`/`rex` arrays unchanged, plus:

- `matrix_lex` / `matrix_rex` — both families on the semantic LEX/HOST and
  REX/DEVICE axes. Integrated entries keep their control-IP `usb_key` so the
  proven routing path is untouched; standalone entries are keyed by MAC.
- `capabilities` — `{rex_key: {lex_key: {state, control_path, enabled, note}}}`,
  derived by `_usb_route_capability()` from **both** endpoint classifications.
- `standalone_routes` — standalone route state with an explicit `fresh` flag.

| Combination | State | Control path | Enabled |
| --- | --- | --- | --- |
| E4521 to D4511 | `SUPPORTED_USB_ICRON` | `/api/usb_pair`, `/api/usb_unpair` | yes |
| AT-OMNI-311 to AT-OMNI-324 | `SUPPORTED_STANDALONE_UDP` | `/api/usb_route/*` | yes |
| E4521 to AT-OMNI-324 | `SUPPORTED_MIXED_EXPERIMENTAL` | `/api/usb_route/*` | yes |
| AT-OMNI-311 to D4511 | `SUPPORTED_MIXED_EXPERIMENTAL` | `/api/usb_route/*` | yes |

One production contract serves every UDP-controlled combination: the client sends
two endpoint identities and the server's dispatcher selects the transaction. The
frontend decides no protocol detail beyond which identities it is naming.

Endpoint conditions override capability and always fail closed: `UNCONFIRMED`,
`OFFLINE`, and `NOT_ELIGIBLE` disable a cell regardless of combination. The UI
never chooses a control plane; it reads `control_path` from the server.

`POST /api/usb_route/pair` and `/unpair` implement production standalone routing.
They resolve both endpoints from the server's own inventory, require both to
classify `STANDALONE`, enforce AT-OMNI-311 as LEX and AT-OMNI-324 as REX, check
pairing eligibility, and refuse every other combination with HTTP 409. Existing
E4521/D4511 routing continues to use `/api/usb_pair` and `/api/usb_unpair`
unchanged; the dispatcher selects that path, it was not rewritten.

Route state is never painted from a click, an HTTP 200, or an ACK. A standalone
route is rendered active only after a fresh read of both endpoints agrees.

## Routing identity is a MAC; the axis key is not

A Matrix cell click failed with `404` and
`rex_mac 19:21:68:10:01:52 has not been discovered`. That MAC belongs to no
device. It was manufactured from an address.

`usb_key` is a **display and index key**: the OmniStream control IP for an
integrated unit, the MAC for a standalone one. It was also being sent as the
routing identity. `normalize_mac` strips separators so that dotted MAC notation
is accepted, which means a dotted-quad address contributes only hex digits; an
address whose digits happen to number twelve normalises into a plausible MAC.
The `404` was not a missing Flask route -- `/api/usb_route/pair` was registered
and reached -- it was that endpoint correctly reporting that the fabricated MAC
matched no known device. Nothing was transmitted, and no fallback ran.

The contract is now explicit in three places:

| Layer | Rule |
| --- | --- |
| `/api/usb_state` | every axis entry carries `usb_mac`, the canonical endpoint MAC, alongside `usb_key` |
| Matrix cell | carries `data-lex-mac` / `data-rex-mac`; `data-lex` / `data-rex` stay display keys |
| Route endpoints | accept MACs only; an IPv4 literal is refused as malformed input (`400`) |

For a standalone unit the identity is its own hardware MAC. For an integrated
endpoint it is the associated Icron MAC, taken from the parent's own `usb_icron`
`macaddress` -- never derived from an address, hostname, suffix or index.

`normalize_mac` now rejects an IPv4 literal outright. That is defence in depth,
not the fix: an address should never reach it as an identity in the first place.

**Fail closed.** A cell whose endpoints do not both have a canonical `usb_mac`
is disabled as `IDENTITY_UNAVAILABLE` ("USB endpoint identity unavailable"), and
the click handler abandons the request before posting if either MAC is missing.
There is no fallback from the UDP route path to `usb_icron`: they address
different devices by different keys, and retrying one as the other is how a
command reaches the wrong hardware.

`POST /api/usb_route/resolve` runs the same gate and reports what the server
resolved -- identity, classification, role, parent, capability and provider --
while transmitting nothing, so a route can be checked before it is created.

### The gate must judge liveness the way the Matrix does

An integrated USB endpoint is deliberately never polled over UDP, so its UDP
record is permanently cold. The route gate was reading `online` straight off that
record, so every integrated endpoint was refused as offline while the Matrix,
which uses the derived view, showed the cell as enabled. The gate now consumes
the same derived view. A cell that is enabled on screen and the endpoint behind
it now agree.

## usb_icron converges on a UDP-created pairing, but does not render it

A UDP `Pair` addressed to an integrated endpoint is not immediately visible in
its parent's `usb_icron` configuration. Observed on the bench with the mixed
route into the D4511 at `.32`:

```
immediately after the Pair
  UDP Advanced Query   peers = ['00:1B:13:04:E9:6E']
  usb_icron            paired_devices = {}

several minutes later
  UDP Advanced Query   peers = ['00:1B:13:04:E9:6E']
  usb_icron            paired_devices = {"00:1B:13:04:E9:6E": {
                           "ipaddress": "192.168.100.128",
                           "linked": true, "paired": true}}
```

So the two views do converge on one underlying Icron state in this direction as
well; `usb_icron` is not blind to it, it is late. The convergence interval was
not characterised and should not be relied upon.

Even after convergence the Matrix cannot render the route from `usb_icron`,
because its peer resolution maps peers onto known OmniStream units and a
standalone extender is not one -- the presentation limit already recorded above.
`pairings` for `.32` therefore still reports `active: None` while the parent's
own table lists the AT-OMNI-311.

Route state for a routable cell is therefore selected by **control path**, not by
row family. A cell whose `control_path` is `standalone_udp` reads its state from
the UDP observation, which is both immediate and resolvable, and matches on
canonical MAC -- required in both mixed directions, because an integrated row's
key is an IP. `usb_icron` still owns integrated pairing presentation: the UDP
source is reported only when a peer was actually observed, so absence there is
never presented as "no route".

The pairing sweep still never polls integrated endpoints, with one exception: an
integrated endpoint already known to carry a UDP peer is refreshed, so a mixed
route's state stays current without waiting on `usb_icron` convergence.

## AT-OMNI-311/324 read-only query set (firmware 1.9.4)

Five queries were exercised against four physical units. Every one is optional to
discovery: each has its own timeout and failure handling, and no failure marks a
device offline or fails a sibling query.

| Query | Request | Response | Observed |
| --- | --- | --- | --- |
| Basic Device Information | `0x0000` | `0x0001`, 98 bytes | every unit |
| Extended Device Information | `0x0300` | `0x0301`, 17-23 bytes | every unit |
| Full Configuration | `0x030D` | `0x030E`, 168 bytes | three of four units |
| Link Status | `0x030F` | `0x0310`, 60 bytes | every unit |
| Topology | `0x0304` | `0x0305` **or** `0x0308` | both forms seen |

**Basic Device Information** (88-byte body). The string block starts at offset
**12**, not 11, and the trailing field is 12 bytes rather than 32:

```
[0:6] local MAC   [6:10] IPv4   [10] network mode   [11] protocol byte (unverified)
[12:44] vendor(32)   [44:76] product id(32)   [76:88] firmware(12)
```

The previous offset of 11 put the protocol byte on the front of the vendor string
and shifted every field after it, leaving firmware empty. **That is why no
standalone firmware version was ever displayed.** Byte [11] read `0x03` on every
unit; its meaning is not established, so it is carried raw and never interpreted.

**Full Configuration** (158-byte body). Peer slots span `[14:56]` — seven
six-byte slots, matching `HOST_PEER_LIMIT`:

```
[0:6] unverified   [6:12] local MAC   [12:14] unverified   [14:56] seven peer slots
[56:58] unverified [58:62] IPv4  [62:66] mask  [66:70] gateway  [70:74] DHCP server
[74:82] unverified [82:114] vendor(32)  [114:138] product id(24)
[138:146] firmware(8)  [146:154] secondary version(8)  [154:158] unverified
```

An all-zero slot is an unused entry, never a device. The unverified regions are
preserved as raw hex under `unknown` and are not interpreted.

`MAX_PACKET_LEN` was raised from 136 to 512: the 168-byte Full Configuration
response was being rejected as malformed by the old bound. Every parser still
validates its own minimum length and field bounds.

**Topology is optional and answers in two forms.** One unit returned a 10-byte
`0x0308` with no payload; another returned a 42-byte `0x0305` carrying data whose
content changed between reads. Both mean "request processed". Neither is offline,
and topology never gates discovery.

`0x33` (Extended Configuration) is not answered by this firmware on any unit and
is never transmitted. Product-ID write is not implemented: the one experiment
that tested it produced no response and no change.

## The Atlona standalone model discriminator

Extended Device Information byte[10] is the model discriminator for a **confirmed
standalone** unit:

| byte[10] | Model | USB role |
| --- | --- | --- |
| `0x00` | AT-OMNI-311 | host / LEX |
| `0x01` | AT-OMNI-324 | device / REX |

This was re-established from the hardware after an uncontrolled reboot, and the
implementation already matched it — **no code correction was required.**

A proposed inversion (`0x01 -> 311`) was assessed and rejected, because the
captures it rested on were labelled the wrong way round. Three independent lines
of evidence fix the mapping:

1. Basic Device Information reports a unit's local MAC together with that unit's
   own IP address, which anchors identity without relying on any label. The unit
   at `.128` is `00:1B:13:04:E9:6E`; the unit at `.127` is `00:1B:13:04:6A:EA`.
2. `.128` answers byte[10] `0x00` and lists **two** peers. Only a host carries
   several peers (`HOST_PEER_LIMIT` 7 against `DEVICE_PEER_LIMIT` 1), and nothing
   has two local MACs — so the trailing six-byte groups are peers, and `.128` is
   the host.
3. The integrated D4511 at `.32`, which is a REX, names `00:1B:13:04:E9:6E` as
   its peer. A REX's peer is a host, so that MAC is a host, so `0x00` is the host.

A third physical unit (`.134`, byte[10] `0x01`, one host peer) agrees. Product ID
is identical on every unit (`USB Over Network`) and can never select a model.

This discriminator applies only to confirmed standalone units. An endpoint whose
canonical USB MAC maps to an E4521/D4511 parent stays `INTEGRATED`; the parent
supplies product identity and the UDP role byte remains diagnostic only.

## Four separate questions about one endpoint

These are answered by different providers, on different clocks, and none of them
may be derived from another:

| Question | Field | Established by |
| --- | --- | --- |
| Can OmniSuite reach it? | `online`, `liveness_source` | parent `usb_icron`, or a directed UDP Query |
| What peers are configured? | `paired_macs`, `pairing_source` | `usb_icron`, or UDP Advanced Query |
| Is the extender link up? | `link_state`, `link_states`, `link_source` | UDP Link Status, both families |
| Does USB actually work across it? | `data_plane_verified` | an operator with hardware |

Paired is not linked. Linked is not a working peripheral. Online is not paired.
And a failure to refresh one of them is not a change in its value.

### Every fact names its own provider

Each endpoint carries a `field_authority` map — `liveness`, `pairing`, `network`,
`link`, `firmware` — instead of one `source` that stands for all of them.

The defect that produced this: an integrated endpoint's Status alternated between
`Parent` and `UDP` while nothing about it changed. One `liveness_source` field
was written by whichever provider ran most recently — the parent's `usb_icron`
read set it to `usb_icron`, a UDP Query for the same endpoint set it to
`udp_query` — and the view then overwrote it only while the parent observation
was still inside its 45 s TTL. With only Configure open, that TTL kept lapsing
and returning.

Liveness authority is now decided by fixed precedence rather than by write order:
for an integrated endpoint the parent owns it and wins whenever its observation is
current; `udp_query` is reported only when the endpoint itself answered and the
parent has not been read recently, which is a real and different observation.
`liveness_sources` lists every provider currently attesting, for diagnostics.

Measured after the change: 16 samples at 5 s intervals, Configure only, one
source value per endpoint and no alternation.

### The operator sees status, not provider names

`Online / Seen 11 s ago`, with the provenance of every field on the row's
tooltip. Which backend refreshed a value last is a diagnostic; it was never a
status, and presenting it as one made a stable endpoint look unstable.

## Link Status freshness: verified, ageing, then unknown

Three numbers, and the ordering between them is the whole point:

```
LINK_REFRESH_AGE (25 s)  <  LINK_TTL (60 s)  <  LINK_RETAIN_TTL (300 s)
```

| Field | Meaning |
| --- | --- |
| `link_last_read` | last successful Link Status response |
| `link_last_attempt` | last time one was sent |
| `link_consecutive_failures` | misses since the last success (budget `LINK_MAX_MISSES = 4`) |
| `link_state_fresh` | read within the TTL, budget not exhausted |
| `link_state_retained` | past the TTL, still inside the retain window: shown, marked ageing |
| `link_known` | either of the above |

**Why a stable link used to flicker to Unknown.** The sweep skipped anything read
within `LINK_TTL` — the same 60 s at which freshness expired — and ran at most
every 30 s. So a device read at *t* was not eligible for re-reading until *t+60*,
by which point its state had already stopped counting as current, and the next
sweep could be another 30 s away. The window in which a perfectly healthy link
read `Unknown` was structural, not a device fault. Refreshing at 25 s closes it:
even a sweep arriving a full interval late is still inside the TTL.

A failed read now costs a miss, never the state. One dropped datagram leaves
`Linked` exactly as it was. Only exhausting the budget, or ageing past the retain
window, makes it `Unknown` — and a state past its TTL but inside the window reads
`Linked · seen 92 s ago` rather than being thrown away.

Measured after the change: 19 samples over 95 s with both pages polling at 5 s —
`Linked / 5 peers` in every one, no oscillation, no endpoint reading `Unknown`.

### One sweep, both surfaces

The Matrix and Configure both call the same manager, which has in-flight
suppression and a 30 s minimum interval, so opening a second page does not double
the traffic. Measured with both open at 5 s: **74.5 UDP packets/min in total
across 12 endpoints** — 51.1 liveness Query, 22.7 Link Status, 0.6 Advanced
Query. About 6 packets per minute per endpoint.

## Integrated endpoints answer Link Status

Configure showed `N/A` in the Link column for every E4521/D4511 USB endpoint,
because the sweep excluded anything with an OmniStream parent on the grounds that
no Link Status semantics were established for it. Directed read-only probes
settled that:

| Endpoint | Address | Response | Decoded |
| --- | --- | --- | --- |
| E4521 integrated LEX | `192.168.100.246` | 0x0310, 50-byte body | no peer slot in use |
| D4511 integrated REX | `192.168.100.248` | 0x0310, 50-byte body | `LINKED` to `00:1B:13:05:50:50` |
| D4511 integrated REX | `192.168.100.108` | 0x0310, 50-byte body | `LINKED` to `00:1B:13:05:50:50` |
| E4521 LEX, routed subnet | `192.168.200.62` | 0x0310, 50-byte body | `NOT_LINKED` to `B8:98:B0:07:85:87` |
| D4511 REX, routed subnet | `192.168.200.66` | 0x0310, 50-byte body | `NOT_LINKED` to `B8:98:B0:07:85:E7` |

Every one answered, with the same seven-slot layout the existing parser already
decodes, and each identified itself with the expected MAC on the preceding Query.

**Control ownership and read authority are different things.** An integrated
endpoint is paired through `usb_icron`, addressed for network configuration
through the parent `net` API, and rebooted through the parent — and it is still
an Icron endpoint with its own MAC and address that answers a read. A safe read
is not made unavailable by the fact that writes go somewhere else.

## Link Status summarises per peer

A host may hold several peers and they need not agree, so one state byte can never
speak for all of them:

| Condition | Label | Detail |
| --- | --- | --- |
| every configured peer `LINKED` | `Linked` | `5 peers` when more than one |
| some linked | `Partial` | `3/5 linked` |
| none linked | `Not linked` | `0/5 linked` |
| device reports no peer slot in use | `Not paired` | — |
| verified but ageing | as above | `· seen 92 s ago` |
| unknown, or budget exhausted | `Unknown` | why, when known |

The five-peer AT-OMNI-311 reports `01 01 01 01 01 00 00` with its five peer MACs,
so it renders `Linked / 5 peers` — never one global value standing in for five
independent states. Per-peer detail is on the tooltip.

### The original clocks

| Concept | Source | Fields | TTL |
| --- | --- | --- | --- |
| Configured pairing | Advanced Query / `usb_icron` | `paired_macs`, `pairing_last_read` | 120 s |
| Current link | Link Status | `link_state`, `link_states`, `link_last_read` | 60 s fresh, 300 s retained |

**Link Status byte[0] carries the link state**, with two values observed on
hardware: `0x01` while the peers were live, and `0x02` on an AT-OMNI-324 whose
configured host was powered off. Any other value is reported `UNKNOWN` rather
than folded into "not linked" — a value never observed is not evidence.

**The peer list in Link Status is the configured set, not the live link.** The
not-linked unit still listed its configured host. So the peer list answers "who
is this device configured for", and byte[0] answers "is the link up".

A query that did not answer is `UNKNOWN`, never `Not linked`: a timeout and a
confirmed absence of link are different facts, and the UI states them
differently (`Linked` / `Not linked` / `Unknown`).

### Multi-peer Link Status

The AT-OMNI-311 at `.128` held two configured peers (a standalone AT-OMNI-324 and
an integrated D4511). Its Link Status listed **both**, matching the configured set
exactly:

```
0101 000000000000 001B13046AEA B898B00785C7 0000...
```

So Link Status reports multiple peer slots and does **not** single out an active
peer. Byte[1] differed between units (`0x00` on `.127`, `0x01` on `.128`) but did
not track the Extended role byte and is carried raw. Whether any field
distinguishes an active peer among several remains **unresolved**, and nothing in
the UI assumes one.

## One inventory row per physical USB endpoint

The Matrix inventory tables rendered from `matrix_lex`/`matrix_rex` — which
already carry both families for the routing grid — and then appended
`standalone_lex`/`standalone_rex`, so every standalone endpoint appeared twice.
The same cause produced the second symptom: the duplicate went through the
integrated row template and inherited a Host Port selector, a Device Filtering
selector and a Type selector that an AT-OMNI-311/324 does not support.

The fix is server-side. `_usb_inventory()` merges both families and deduplicates
by **canonical USB MAC**, and `_usb_inventory_row()` states each row's own
capabilities:

| Row | Type | Host Port | Device Filtering |
| --- | --- | --- | --- |
| Integrated LEX | selector | selector | selector |
| Integrated REX | selector | `N/A` | selector |
| Standalone AT-OMNI-311 | `LEX`, fixed | `Fixed / N/A` | `Not supported` |
| Standalone AT-OMNI-324 | `REX`, fixed | `N/A` | `Not supported` |

Same MAC at a new address is the same device with a new address; the same address
with a different MAC is two devices. A row with no canonical MAC is dropped rather
than risking a duplicate under a display key. The routing axes are untouched: the
standalone host remains one LEX axis entry and the standalone device one REX axis
entry, and every route cell survives.

Verified live: 5 LEX and 7 REX inventory rows, every canonical MAC unique, axis
order stable across repeated polls, zero console errors on the page.

## Firmware

`Revision` meant different things per family, so it was audited before renaming:

| Family | Value | Source |
| --- | --- | --- |
| Standalone AT-OMNI-311/324 | `1.9.4` | live Basic Device Information / Full Configuration |
| Integrated E4521/D4511 | `2.1.2` | the parent's OmniStream firmware |

Both are the firmware of the unit that owns the USB endpoint, so one **Firmware**
column serves both and the rename is justified. The Icron module revision that
`usb_icron` reports is a different fact and is preserved separately as
`icron_revision` rather than being conflated with firmware.

## Live device state outranks persisted state

A successful live read wins over anything persisted, for model, MAC, address,
mask, gateway, network mode, firmware, vendor, product, pairing and link. Persisted
values are fallback and history only, so a Device Info scan makes the UI follow
the hardware without removing and re-adding a device. A failed read erases
nothing; it only stops the last known values being presented as current.

Where Basic and Full Configuration both report a property they are compared. A
disagreement is recorded on the record rather than silently resolved, and a Full
Configuration response naming a different MAC is not merged at all — identity
safety outranks richness.

## Measured cost

Bench of 17 OmniStream units and 4 standalone endpoints:

| Measurement | Result |
| --- | --- |
| `/api/scan` median | **1.44 s** (1.41–1.46), unchanged from 1.40 s before this pass |
| USB enrichment on the `/api/scan` request path | **none** |
| Liveness sweep | 4 Query datagrams |
| Link Status sweep | 3 datagrams |
| Full Configuration sweep | 3 datagrams |
| Icron network sweep | 8 parents in 0.20 s (WebSocket) |
| Six back-to-back page polls | **0 datagrams** — every throttle held |

Cadences: liveness 4 s floor / 45 s TTL, pairing 20 s / 120 s, Link Status 30 s /
60 s, Icron network 30 s. Link Status uses 6 workers and a 0.6 s timeout.

## A degraded bench unit, recorded so it is not mistaken for a defect

After the uncontrolled reboot the AT-OMNI-311 at `.128` was measured at **60% ICMP
packet loss** while `.127` measured 0% on the same subnet. Its UDP query success
followed: Basic 0/12, Extended 5/12, Link 7/12, Topology 12/12, against 12/12 on
every command for `.127` and `.134`.

This was measured through a raw socket, bypassing the application entirely, so it
is a link or unit condition and not a software fault. A bounded retry was measured
before being considered and rejected: it recovered 3/12 to only 4/12, so it would
have added traffic for almost nothing.

The application behaves correctly under it — a route transaction against `.128`
returns `UNVERIFIABLE` and transmits nothing, which is the intended fail-closed
result rather than a bug. No mutating operation was performed against `.128` while
it was in this state.

## Discovery, polling and routed management

Broadcast does not cross a router. An architecture that depends on it makes
inventory a function of Layer-2 adjacency, which is how an endpoint came to be
present in one surface and absent from another.

| Mechanism | Purpose | Scope |
| --- | --- | --- |
| Local UDP broadcast | find **unknown** endpoints | the directly attached L2 network only |
| Directed unicast UDP | poll and control **known** standalone endpoints | anywhere IP routing reaches |
| Configured range scan | find unknown endpoints at **known addresses** | directed per candidate address |
| Parent `usb_icron` / `net` | discover and refresh **integrated** endpoints | anywhere the parent is reachable |

**Directed unicast UDP is routable, and was measured to be.** From a host whose
only interface is on 192.168.100.0/24, Basic, Extended, Full Configuration and
Link Status all answered from Icron endpoints on 192.168.200.0/24 in 1.5-3 ms.
The transport limitation was broadcast scope, never UDP. **No verified TCP control
transport exists for the AT-OMNI-311/324**, and none was invented: unicast UDP
already crosses subnets, so speculative TCP would add risk and prove nothing.

### One logical inventory

`_usb_extender_view()` previously iterated only the UDP-discovered set, so an
integrated endpoint reached Configure only if its Icron interface had answered a
UDP query. It now also derives an endpoint from any USB-capable parent that
normal OmniStream discovery already knows, marking it `discovery_source:
parent_derived`. Configure, the USB Matrix and the Device Info count draw from
that one population; the surfaces differ in what they display, never in which
endpoints exist.

A discovered record always wins over a derived one for the same canonical MAC, so
one endpoint is still one row. Liveness for a derived endpoint comes from the
parent observation (`liveness_source: usb_icron`), exactly as it does for a
discovered one.

Verified live on a cold backend: the first request after a restart was to
Configure, and it returned all three endpoints behind routed 192.168.200.x
parents. The Matrix and Configure canonical MAC sets agreed exactly, 12 and 12,
with nothing missing in either direction. **Opening a page is not required to
repair backend state**; a startup task refreshes known endpoints by directed
polling and parent enumeration.

### Online means answered, not broadcast

`ONLINE` is a recent authoritative response: a directed Query for a standalone
endpoint, or a `usb_icron` observation for an integrated one. A reachable endpoint
on another subnet is therefore online. `OFF_NET` describes **where an endpoint
sits relative to the discovering interface** and is not a liveness state; it does
not imply unusable, and it is distinct from `OFFLINE` and from `UNCONFIRMED`.

### Pairing eligibility is a property of the two endpoints

The controller's own subnet is irrelevant to whether two USB endpoints can talk.
Per-endpoint eligibility now means only that the endpoint has a usable address.
Whether a *pair* is compatible is computed from the endpoints' own addresses and
masks by `endpoints_same_network()`, using real IPv4 arithmetic rather than an
assumed /24 -- a /16 pair spanning third octets is compatible, and the same
addresses under /24 masks are not. An indeterminate answer is `None` and never
becomes a refusal.

For a mixed route the Icron interface's own address is used, not the parent's
management address: the USB link lives on the Icron network.

A source address is bound only when the discovering interface is on the
destination's network, which is what makes that choice meaningful on a multi-NIC
host. For a routed destination no such local address exists, so binding one would
make a reachable endpoint unreachable; the OS routing table selects the source
instead. Local broadcast still binds deliberately, because choosing the interface
is the whole point of broadcasting.

`subnet_mask` on a record is the mask of the **interface the endpoint was
discovered from**. The device's own mask, learned from Full Configuration, is
`device_subnet_mask`. Conflating them would silently corrupt `network_relation`
wherever the two networks differ.

### Measured polling cost

Six Matrix-plus-Configure poll cycles produced **15 unicast datagrams, zero
broadcasts and zero range scans**. One forced full cycle sent four Query
datagrams, one to each known standalone address. Range scans are discovery
operations and never run from normal polling. `/api/scan` measured 1.26-1.44 s
throughout, with no USB enrichment on its request path.

## Standalone pairing does not survive a power cycle

The three standalone units were power-cycled between measurements. All three lost
their pairing tables entirely, while the integrated D4511 they were routed to
retained its own, because integrated pairing is persistent parent configuration.
The D4511 therefore named a host that no longer named it back, and reported
`linked: false` for it.

This is a real operating consequence: **after a standalone AT-OMNI-311/324 loses
power its routes must be re-established**, and the peer that survives will
disagree with it until they are.

### Converging a half-established route

That state is `INCONSISTENT`, and Pair previously refused it outright -- so the
one situation an operator most needs to fix could not be fixed from the Matrix.

Pair now converges a route that exists on one endpoint only, because that is
exactly the end state being asked for; Unpair does the mirror. Both still verify
**both** endpoints afterwards, and a device owned by a *different* host remains
`CONFLICT` and transmits nothing, so convergence can never become a way to steal
a REX. Convergence requires both reads to be authoritative.

**A device rejects a command that re-asserts a peer it already holds.** The first
convergence attempt on hardware NACKed for that reason, and its rollback correctly
reversed the endpoint that had accepted. So convergence addresses only the
endpoint whose table lacks the relationship; a skipped endpoint is recorded with
`ack: null` and is never rolled back, because the transaction did not change it.
With that, the mixed route re-established on the first attempt.

## What Scan actually does, and what it cannot do

Discovery has four independent sources. Only one of them can find a device whose
address is not already known **and** not covered by a configured network.

| Situation | Mechanism |
| --- | --- |
| Unknown device, attached L2 network | local broadcast |
| Unknown device, routed network | **configured directed range scan** |
| Known device, anywhere | directed unicast poll of its recorded address |
| Integrated 45xx endpoint | parent-derived enumeration |

**There is no generic mechanism for an unknown device at an arbitrary routed
address.** If OmniSuite knows neither the device's address nor a network
containing it, no broadcast, scan or protocol can find it, because there is
nothing to address. That is a property of IP, not a gap to be papered over:
OmniSuite does not sweep RFC1918 space and does not invent a TCP transport.

Configured discovery networks are therefore the operator's way of saying where to
look. They are edited in Configure &rsaquo; USB under **USB Discovery Networks**,
persist with the USB inventory, and are scanned by directed unicast whenever
Device Info &rsaquo; Scan runs. Before this pass they existed only in the API:
nothing in the UI referenced them, and the Scan path ignored them entirely,
expanding only the ad-hoc Targets field.

Scan now dispatches, all asynchronously and none of it on the `/api/scan` request
path:

1. directed re-probe of **every** known standalone endpoint, including ones
   currently offline -- a device that stopped answering may simply have moved,
   and its recorded address is the only lead available
2. parent-derived enumeration of every known 45xx parent
3. local broadcast for unknown devices on the attached network
4. directed range scan of the Targets field and the configured networks

Measured with two /24 networks configured (508 candidate addresses): `/api/scan`
returned encoder and decoder results in **2.15 s**, and the USB dispatch call
returned in **0.02 s**. A single /24 scan addressed 254 candidates with 24
workers in **4.0 s**, sent 261 datagrams and **zero broadcasts**, with the first
useful response at **0.72 s**. A network with no IP path costs the same bounded
4.0 s and yields nothing, which is the correct outcome rather than an error.

### Range scanning finds it once; unicast polling keeps it

A device is discovered by a range scan and maintained afterwards by directed
polling of its address. Six ordinary page polls produced **8 datagrams, zero
broadcasts and zero range scans**. Range scanning is a discovery operation
triggered by Scan, never part of liveness.

### A worked example: a moved endpoint

An AT-OMNI-324 was physically moved to another network. Scan had not found it,
for three compounding reasons: `192.168.150.0/24` was not configured; the Scan
path would not have scanned it if it were; and the device was persisted by MAC at
its old address, which nothing re-probed once it went quiet.

With the network configured, the directed range scan found it in **0.36 s** --
at `192.168.150.46`, not the address it was believed to be at, because it uses
DHCP. It reconciled onto the existing record by canonical MAC: one row, the new
address, no duplicate, and no stale row at `192.168.100.127`. Its model and REX
role were unchanged by the move, it reported `network_relation: OFF_NET` while
`online: true` and `pairing_eligible: true`, and after an OmniSuite restart with
no range scan at all it came back online through directed known-device polling.

**Moved again, to an unknown network, it would be lost the same way** -- known
polling would mark its old address stale, and it would return only through local
broadcast if it moved locally, a configured range that covers its new network, or
being told its address directly.

### Routed management is not routed USB pairing

Directed unicast proves OmniSuite can *manage* an endpoint across a router. It
says nothing about whether two USB endpoints on different IP networks can carry
USB traffic between themselves. Pairing eligibility is still computed from the
two endpoints' own addresses and masks.

## The inventory filter searches only what is displayed

Filtering `-311` in Configure &rsaquo; USB also matched every HW-OMNI-E4521 row.
The filter searched `device_type`, which for an integrated endpoint holds the
**UDP-reported USB role name**: an E4521's Icron endpoint reports `AT-OMNI-311`
and a D4511's reports `AT-OMNI-324`. That value is diagnostic, is deliberately
never displayed, and had no business being searchable.

The searchable set is now explicit and user-facing: model, role, device IP,
hostname, USB IP, USB MAC, firmware, network mode, status and link. Model
comparison ignores punctuation, so `311`, `-311`, `omni-311` and `AT-OMNI-311`
all select the AT-OMNI-311 rows, while that loose matching is applied to the
model field only so a bare number cannot sweep in unrelated columns. Terms
separated by spaces must all match, and optional `field:value` prefixes
(`model:`, `ip:`, `mac:`, `role:`, `status:` and the rest) scope a single term.

Filtering is presentation only: it never changes the inventory, the USB count,
route state, polling or pairing eligibility, and clearing it restores every row.

## The Scan interface is remembered

The adapter selector was rebuilt from `/api/adapters` on every load and nothing
was ever saved, so the choice was lost on refresh, on navigation and on restart.

It is now persisted server-side in `scan_preferences.json` and **identified by the
adapter's address**, never by its position: a reordered list, a renamed adapter or
a new one appearing first cannot silently change what the operator chose. A saved
adapter that no longer exists is reported and the normal default is used, so a
dead adapter is never left selected.

Three operator choices, three separate stores, none able to clear another: the
credential config (whose POST rewrites every field it owns), the USB discovery
networks (which live with the USB inventory), and this. The change handler is
bound as soon as the options exist rather than after the saved value loads, so a
selection made while the preference is still loading is still saved.

Verified in a browser: choosing an adapter saved it, and it survived a reload, a
navigation to another page and back, and a server restart.

## Clicking a supported route runs it

The Matrix asked for confirmation before creating a mixed route, explaining that
its data plane was unvalidated. That made the data-plane flag a precondition for
the very testing that would set it, and it interrupted an operator who had
already expressed intent by clicking the cell. The confirmation is gone.

Nothing else was relaxed. The transaction still resolves canonical identity,
selects the provider, checks eligibility, verifies both endpoints, rolls back a
partial failure and reports errors through the ordinary toast. `enabled` governs
whether a cell can be clicked; `data_plane_verified` remains separate diagnostic
information and never gates a route.

## Measured: one host, five simultaneous REX peers

Validated on hardware through the production Matrix endpoints, added one at a
time and verified from both sides after every addition.

| | Endpoint | Type |
| --- | --- | --- |
| LEX | `00:1B:13:05:50:50` (AT-OMNI-311) | standalone host |
| REX 1 | `00:1B:13:04:6A:EA` | standalone AT-OMNI-324 |
| REX 2 | `B8:98:B0:07:85:C7` | integrated D4511 |
| REX 3 | `00:1B:13:05:17:E9` | standalone AT-OMNI-324 |
| REX 4 | `B8:98:B0:07:85:87` | integrated D4511 |
| REX 5 | `B8:98:B0:07:85:C1` | integrated D4511 |

All five were held simultaneously, each REX independently named the host, no
addition displaced an earlier peer, and the membership is **mixed**: adding or
removing an integrated peer left the standalone peers untouched and vice versa.

Removing one route removed exactly that peer and left the other four; re-adding
it restored all five. An application restart reconstructed the five-peer state
from the hardware with no manual scan.

The host peer collection is `paired_macs`, a list bounded by the documented
`HOST_PEER_LIMIT` of 7, not a single `paired_mac`. Five is what this pass
validated, not a limit: nothing treats the fifth peer specially.

**The integrated E4521's capacity is measured separately and is not assumed to
match.** Its `usb_icron` `paired_devices` is a map keyed by peer MAC, so the data
model expresses 0..N, and one E4521 was observed holding **two** peers
simultaneously in this pass's baseline. A five-peer E4521 test was not run: the
compatible REX endpoints on that network are the ones now held by the 311, and
dismantling that topology would have destroyed the result the operator needs for
the physical test. **No E4521 capacity beyond two is claimed.**

### Pairing capacity is not USB device capacity

Five configured REX peers is a statement about pairing records. It says nothing
about how many USB peripherals can be used, or whether any peripheral works at
all. Those remain separate questions, and the data plane for this topology is
still unverified.

## Link Status carries a per-peer state array

The five-peer topology resolved what byte [1] was. Four captures line up exactly
when bytes [0:7] are read as one state byte per peer slot, aligned with the peer
MACs at [8:]:

```
1 peer,  linked      01 00 00 00 00 00 00
2 peers, linked      01 01 00 00 00 00 00
1 peer,  host off    02 00 00 00 00 00 00
5 peers, linked      01 01 01 01 01 00 00
```

Seven slots matches `HOST_PEER_LIMIT` and the seven peer slots in Full
Configuration. `0x01` and `0x02` are the only values observed; anything else is
reported `UNKNOWN` per peer rather than folded into "not linked". The endpoint's
overall state is derived: linked when any configured peer reports a link.

With five peers configured, Link Status and Full Configuration both listed all
five, and the Link Status peer set equalled the configured peer set exactly.

## An AT-OMNI-311 stops answering under sustained management traffic

Both standalone 311s on this bench became unresponsive to Basic and Advanced
Query after a burst of operations and recovered after a pause, while every
AT-OMNI-324 and every integrated Icron endpoint answered 6/6 or better
throughout. One 311 measured 100% ICMP loss and 2/20 UDP in one window and 10/10
minutes later.

This is a property of the hardware, not of the application, and it is not
concealed: the route gate refuses with `offline` rather than transmitting into a
device that is not listening, which is the correct fail-closed result. The
multi-peer test therefore waits for the host to answer several probes in a row
before each step rather than retrying blindly.

## Device Filtering on a standalone unit is not established

Configure and the Matrix inventory reported `Not supported` for Device Filtering
on every AT-OMNI-311/324 row. That was a stronger claim than the evidence
supports, so it now reads **Not established**, with the reason on the row.

What the audit found:

| Evidence | Result |
| --- | --- |
| The OmniStream device's own web app (`artifacts/device-150146-atlona.js`) | `usbfiltering` is a field of the `usb_icron` configuration object, read and written over the parent's WebSocket API. The six policy values come from `usbfilteringlist` in that app, and the control renders only when the device reports the field. |
| TCP 80 / 443 / 8080 / 3000 on three standalone units (two AT-OMNI-324, one AT-OMNI-311, firmware 1.9.4) | No listener on any port. That API does not exist on them. |
| Full Configuration (0x030D) from the same three units | Every unattributed byte is identical across both roles and all three units. Nothing there encodes a per-unit policy. |
| The verified UDP opcode set | No filtering command. |
| Full Configuration from integrated endpoints, for comparison | Unattributed bytes match the standalone units. All endpoints are at the default `Allow_All`, so no differential is available, and no production filtering policy was changed to manufacture one. |

None of that proves the hardware cannot filter — only that no way to read or set
it has been established. **Absence of evidence is not a capability statement**, so
the field says the capability is unknown rather than asserting the product lacks
it.

## One definition of what to search

Device Info &rsaquo; Scan takes a **Targets** expression, and it already fed both
protocols: `/api/scan` addresses those hosts over the OmniStream WebSocket, and
the same expression is handed to directed USB discovery. Configure &rsaquo; USB
also carried a **USB Discovery Networks** panel holding a second list of routed
subnets.

The two were never different search spaces. The only thing the second panel added
was **persistence** — Targets was ad-hoc and had to be retyped, so a routed
network had to be entered twice to be scanned reliably.

Device Info now remembers Targets, in the same `scan_preferences` store that
remembers the chosen adapter, and saving it writes through to the USB range store
the directed scanner already reads. One input, one persisted definition, both
protocols. The duplicate panel is gone; `/api/usb_extenders/ranges` remains as the
store behind it, with Device Info as its input.

Verified end to end on hardware, entering the network only once:

```
scan_targets = "192.168.200.60-192.168.200.70"   saved in Device Info
  -> /api/usb_extenders/ranges  ["192.168.200.60-192.168.200.70"]
  -> Scan dispatch with no USB-specific body: started ["local","ranges"], 11 targets
  -> directed unicast answered by  .62 B8:98:B0:07:85:E7
                                   .63 B8:98:B0:07:85:C5
                                   .66 B8:98:B0:07:85:79
  -> all three recorded, discovery_source udp_discovery, relation Routed
```

A target expression that cannot be parsed as a range is reported back
(`usb_ranges_error`) rather than silently dropped; the rest of the preference
still saves.

### UDP transport and UDP broadcast are different things

Reducing broadcast is possible. Removing UDP is not.

| Situation | What is required |
| --- | --- |
| Standalone AT-OMNI-311/324 management, query and control | Directed unicast UDP. It is the only verified transport; these units expose no TCP listener at all. |
| An unknown endpoint on the attached L2 network | Broadcast, which is the only thing that finds something with no address on record |
| An unknown endpoint on a routed network | A directed range scan of a configured target; broadcast cannot cross a router |
| A known endpoint, anywhere | Directed unicast polling of its recorded address |
| Integrated 45xx parent-owned configuration | The parent's OmniStream APIs |
| Integrated Icron diagnostics — Link Status | Directed unicast UDP, verified above |

Broadcast could become optional if configured targets ever covered every
discovery requirement. Unicast UDP could not.

## LOCAL and ROUTED, not OFF_NET

`network_relation` is an internal enum consumed by the source-binding logic:
`LOCAL` means the endpoint shares the selected interface's subnet, `OFF_NET`
means it does not, `UNKNOWN` means no interface information exists. It decides
whether to bind a source address, and it keeps those names in JSON, logs and
tests.

`OFF_NET` was also shown to the operator, next to endpoints that were online,
reachable, manageable and routable. It reads as a fault and is not one. The row
now renders `network_relation_label` — **Local** / **Routed** / **Unknown** — with
"Reached through a router, outside the selected interface subnet" as the detail.

## An older state read may not overwrite a newer route change

`/api/usb_state` performs liveness, pairing and link refreshes, so a poll can
easily take longer than a route change issued after it. Nothing ordered the
responses: the Matrix applied whichever arrived last. A poll that started before
a route move and finished after it put the old owner back on screen, and the only
way out was reloading the page — which is exactly the "Matrix feedback gets stuck"
report.

Two counters settle it, and neither reloads anything:

| Counter | Effect |
| --- | --- |
| `usbStateSeq` / `usbStateApplied` | a response overtaken by a newer one is discarded |
| `usbStateEpoch` | every completed route operation bumps it; a response already in flight at that moment is discarded whatever its sequence number |

After any route operation — success or failure — the grid re-reads immediately
and renders unconditionally, rather than waiting for the next poll or deferring
because a dropdown happens to hold focus. The pending marker is held in a small
registry keyed by the pair, so a re-render landing mid-request cannot lose it and
a rebuilt cell picks it back up.

**Measured on the live page**, twice, with no reload: one click moved an
integrated D4511 from the five-peer AT-OMNI-311 to the other one and the grid
converged in about 1 s — clicked cell out of pending, old owner inactive, new
owner active. One click moved it back, same result. The page then sat open for
60 s: no stuck pending cells, no console errors, no failed requests, and the
five-peer topology intact with all five peers `LINKED`.

## The multi-switch notice belongs to the inventory, not to a scan

Devices report their LLDP neighbour, so the discovered inventory knows which
switch each one is attached to. When more than one switch is represented, traffic
between devices crosses an uplink, and uplink capacity is worth checking *if*
performance problems appear. That is the entire claim: **multiple switches are
not presented as a fault.**

### Where the chassis comes from

The scan already holds a WebSocket conversation with every device it finds, so it
asks for `lldp` there and records `lldp_chassis_id` on the unit. Topology is
derived from the inventory, so it has to be recorded when the inventory is built;
a sweep of its own would be traffic the scan has already paid for. Nothing polls
for it, and rendering the banner touches no device at all.

Hardware returns `neighbors.interface` in two shapes -- a map of interface name
to neighbour, and a list of single-key maps (`[{"lan0": {...}}]`) -- and both are
flattened before reading. The bench returns the list form, which the hover panel
had been quietly rendering as a row of empty fields.

### Grouping

`_normalize_chassis_id` canonicalises a chassis ID so that punctuation cannot
split one switch into two: a MAC loses its separators and case, and anything else
(an address, a locally assigned string) keeps its shape, because stripping
separators there would fabricate equality between different switches.

Only devices with a valid chassis ID are compared. **A device with no LLDP
contributes nothing** -- missing information is not a second switch, and an
inventory where half the units are silent produces no warning.

**A neighbour is not necessarily a switch.** An OmniStream unit has a two-port
bridge, so a daisy-chained device reports the unit *in front of it* as its
chassis. This appeared on the bench the moment LLDP tables aged: the E4521 at
`.141` stopped naming the Catalyst and started naming the E4521 at `.143`, and a
naive reading turned a single-switch network into "2 switches, 1 device on
another switch" -- a false alarm of exactly the kind this feature must not raise.

A neighbour is recognised as one of our own devices only by exact identity: its
advertised management address matching a discovered unit's address, or its
chassis ID matching a discovered unit's MAC. Never by name similarity -- the
neighbour called itself `hw-omni-e4521-856c` while the unit is
`hw-omni-e4521-0856c`, and the MACs differ by one because they are different
interfaces on the same box. The chain is then followed to whatever that device
reports, so a chained unit is attributed to the switch it actually hangs off. A
chain that cannot be resolved -- the device in front was never discovered, or the
links form a loop -- leaves the upstream switch genuinely unknown, and unknown is
not a switch.

The dominant switch is the one with *strictly* the most devices. Everything else
is the minority. A tie has no dominant switch and therefore no minority: with
equal counts, designating one group as the odd one out would be an invention, so
nothing is designated and no device is marked.

### The acknowledgement lifecycle

```
inventory exists
  -> multiple chassis IDs present
  -> banner shown
  -> operator dismisses it
  -> suppressed for the rest of that inventory's lifetime
  -> discovered units cleared
  -> acknowledgement reset
  -> the next inventory may warn again
```

The flag lives in `topology_ack.json`, beside the discovered-device store whose
lifetime it shares. It is functional state: it is written by the dismiss control
and read from the server, and it is **never** inferred from whether the banner
element happens to be visible.

| Operation | Resets the acknowledgement? |
| --- | --- |
| Scan / Discover, however many times | **No** |
| Scanning a different network or subnet | **No** |
| Changing the Device Info Targets | **No** |
| Discovering additional devices | **No** |
| Discovering a third switch | **No** |
| Refreshing LLDP, or reading the topology | **No** |
| Background polling, or navigating between pages | **No** |
| **Clearing the discovered units** | **Yes** |

Exactly one line of server code calls `_topology_ack_reset()`, in
`/api/clear_units`, and a test asserts it never acquires a second caller. A
malformed acknowledgement file degrades to *unacknowledged*: showing the notice
once more is better than hiding a topology the operator never saw.

### Highlighting is not the acknowledgement

Dismissing the banner silences the banner. It says nothing about the network, so
it does not touch the minority-switch indication, which keeps describing whatever
the current topology is:

* warning dismissed -> stays dismissed;
* minority highlighting -> stays visible while the topology warrants it;
* topology changes -> the highlighting follows it, both ways;
* units cleared -> the highlighting goes because the inventory did, and the
  acknowledgement resets with it.

On a minority device's row, the LLDP/MAC cell carries a subtle shade and a
`Different switch: <name>` line, and its existing LLDP hover panel leads with
"LLDP indicates this device is connected through a different switch than the
majority of discovered devices."

### Semantic states, not colours

Two states are declared as tokens, defined for light and dark alike:

| State | Meaning |
| --- | --- |
| `--multi-switch-warning-*` | advisory. Deliberately does not borrow `--danger`: this is information worth noticing, not a failure. |
| `--alternate-switch-*` | a device reached through a different switch. A fact about the network, not a fault with the device. |

Nothing in either treatment names a colour directly, so any palette that
redefines the token set gets an appropriate treatment without touching this code.
Measured in the running page: amber on dark (`rgb(253,230,138)` on
`rgba(180,132,32,.22)`) and on light (`rgb(113,63,18)` on `rgba(250,204,21,.16)`),
with the minority cell indigo in both, distinct from the warning and from the
error red.

The banner is `position: static`, so it never covers the table, and it carries a
close control.

### Measured on the bench

15 discovered devices across two subnets, `192.168.100.0/24` and
`192.168.200.0/24`, all resolving to the same Cisco Catalyst chassis
`50:1c:b0:d5:86:80` -- 14 attached directly and one (`192.168.100.141`)
daisy-chained behind another OmniStream unit. One switch, so **no warning is
shown**: the correct negative case, and neither the routed subnet nor the daisy
chain implies a second switch.
The banner, both palettes, the dismiss control and the minority treatment were
exercised in the running page against a synthetic two-switch topology; no
discovered-device state was fabricated to produce it.

## An address is where a device is, not what it is

The reported defect: a unit moved to another subnet kept its old address in the
inventory until the operator ran Clear Units and rescanned -- for a device that
had answered, correctly, at its new address seconds earlier.

**Cause.** The scan's merge iterated the cache before the fresh results:

```python
for u in existing + units:          # cache first, scan second
    if mac in by_mac: continue      # the cached record claims the identity
```

The cached row was appended first, took the MAC, and the freshly discovered
record was dropped by the very check meant to prevent duplicates. Two more
address-keyed merges made it worse: `_load_cache` folded `scan_results.json` in
by address, so the row left behind at the old address came back as an *extra*
device, and `_units_for_export` did the same for the CSV.

**The rule.** `_reconcile_discovered_units` reconciles identity first:

| Situation | Result |
| --- | --- |
| a rediscovered MAC | the fresh record wins, filled in from the cached one; one row, new address |
| a cached device that did not answer | kept as it was |
| a cached row whose address now answers for a different MAC | dropped; that address has moved on |
| one MAC answering at two addresses in one scan | first response kept, the collision logged, never two rows |

Fresh data wins for everything it establishes and erases nothing it does not
carry, so a timezone, a learned USB association or a link speed survives the
move. The two address-keyed merges now check identity before re-adding a row.

Verified through the production `/api/scan`, with the inventory put into exactly
the state a move leaves it in and no device touched: the stale address was
replaced by the true one, the old address disappeared, the row count was
unchanged, one row carried that MAC, the store held the new address, and it was
still there after an OmniSuite restart. **Clear Units was never called.**

## The support dump carries what was learned

`schema_version` is now `2`. Everything the earlier dump contained is unchanged;
four sections are added, all derived from state already held, so generating a
dump sends nothing to any device.

| Section | Contents |
| --- | --- |
| `usb_endpoints` | one entry per canonical USB MAC: model, role, addresses, firmware and Icron revision, network mode and relation, liveness and its source, classification and parent identity, configured peers, per-peer link states with their clocks and miss counts, and the `field_authority` map naming the provider behind each fact |
| `usb_routes` | each configured relationship, both endpoints identified, with provider, pairing source, current link state and both `control_plane_verified` and `data_plane_verified` |
| `usb_discovery` | scan interface and targets, directed ranges, per-endpoint discovery and freshness timestamps, the freshness policy constants, and the UDP packet rate by opcode. A summary, deliberately: a dump carrying every packet answers no question a summary does not |
| `lldp_topology` | see below |

Credentials keep the existing masking. The new sections are attached *after* the
pre-existing sanitiser runs, because that sanitiser drops empty values -- right
for the device settings it was written for, wrong here, where "we do not know
this" is exactly what a support engineer needs to see. They contain no
credentials: every field is an address, an identity, a timestamp or a derived
state. A failure building one section leaves the rest of the dump intact and
records the failure by name.

## The inventory download is a workbook

`/api/download_inventory` returns an `.xlsx` with two sheets. The CSV endpoints
are untouched, so anything that consumed them still works, and a build without
`openpyxl` falls back to the CSV rather than failing.

* **Devices** -- the same 30 columns the CSV has always had, now defined once and
  shared by all three writers instead of being duplicated per export.
* **USB** -- exactly nine columns, in this order: Model, USB Role, Device IP,
  Hostname, USB IP, USB MAC, Firmware, Network Mode, Network.

The USB sheet is the operator's view of Configure &rsaquo; USB and nothing else.
Peers, link state, status, actions and every diagnostic source belong in the
support dump; putting them here would turn a clean inventory into a second
troubleshooting export.

It is built from the same canonical inventory the page renders, never rebuilt for
Excel -- reconstructing it is how one physical endpoint ends up listed twice, once
from standalone discovery and once from its parent. One endpoint, one row.
Addresses and MACs are written as text so Excel cannot reformat them; both sheets
freeze the header row, carry an autofilter, and size their columns to content.

## Three layouts, two appearance modes

| Template | Intent |
| --- | --- |
| **Classic** | the layout OmniSuite has always had, declared explicitly so it is a choice rather than the absence of one |
| **Compact** | more rows on screen and quieter chrome: the data is the interface, and controls stay out of its way until wanted |
| **Modern** | grouped into cards with a visible hierarchy, so the eye finds the boundary between one concern and the next |

Light and dark remain the independent dimension, on the mechanism they already
used (`body.light`). Three layouts times two modes -- not six templates.

A template is spacing, density and grouping. There is one behaviour layer, and
`ui/ui-template.js` never touches routing, discovery or device state; a test
asserts no `usb-classic.js` / `usb-compact.js` / `usb-modern.js` exists and that
the module names no device endpoint.

**Persistence has two tiers on purpose.** `localStorage` is read synchronously
before paint, so the chosen layout is already correct on the first frame and no
page flashes the wrong one; the server (`/api/ui_preferences`) is the authority,
so the choice survives a different browser, a cleared cache and an OmniSuite
restart. Another tab changing it is followed through the `storage` event.

Colours are named by meaning -- surface, edge, text, action, the four states,
selection, pending, and the two topology states the LLDP work introduced -- so a
template restyles without hunting component rules. Measured across all three
templates, both modes and four pages: 24 combinations, every one applying the
requested layout with every token defined, and no console errors. Configure's
table cells measure 3px / 6px / 9px of vertical padding and its controls 25px /
30px / 33px across Compact, Classic and Modern.

**Reset Appearance** restores the default look and nothing else. Discovered
units, routes, scan settings, USB state and topology acknowledgements are not UI
preferences and are never touched by it.

## A daisy chain is a path, not a fault

The LLDP work resolves a chained device to its upstream switch so grouping stays
correct. Resolution must not erase the path: which switch a device sits under and
how it gets there are two different facts, and the second one is where a shared
1 Gb link lives.

Every device now carries both:

| Field | Meaning |
| --- | --- |
| `immediate_neighbor` | what the device actually reported: chassis ID, name, management address, port |
| `resolved_upstream_switch` | the switch that resolves to, after following any chain |
| `is_daisy_chained` | the immediate neighbour is another discovered device |
| `daisy_chain_via_device` / `_ip` / `_mac` | the device directly in front, by exact identity |
| `daisy_chain_hops` | every hop, nearest first |
| `resolution` | `direct`, `resolved_through_device`, `unresolved`, or `none` |

Traversal is bounded at `LLDP_MAX_CHAIN_HOPS` (8) and loop-protected. A chain
that runs past the bound, loops, or leads to a device that was never discovered
leaves the upstream switch honestly `unresolved` rather than guessed. The switch
port is only reported for a device attached straight to it: through a chain, that
port belongs to the device at the far end.

### The advisory

A daisy chain gets its own banner, its own teal treatment and its own
acknowledgement -- separate from the multi-switch amber, and separate again from
any error state, because this is a supported way to cable a system:

> Daisy-chained network connection detected. A device is connected through
> another OmniStream device rather than directly to a switch. Traffic for
> downstream devices shares the upstream link. If multiple high-bandwidth streams
> are used, verify that link has sufficient capacity.

When a chained **decoder** is subscribed to more than one stream, the wording is
elevated to name the specific risk. Subscriptions and link speed are already
collected by the scan; **actual bitrate is not**. No device reports its stream
bitrate to OmniSuite, `bitrate_data_available` says so in the dump, and nothing
claims a link is congested, saturated or overloaded -- only that streams may
share a path worth checking.

### Two advisories, two acknowledgements

They describe different risks -- an uplink between switches, and a link shared
with the devices in front of you -- so dismissing one never silences the other.
Both live in the same store with distinct keys and both reset only when the
discovered units are cleared.

The daisy acknowledgement additionally records *which devices* it covered, by
MAC. A chained device that merely moves address is the same risk already
accepted; a device that becomes chained for the first time is a new condition and
is worth saying once. Fewer chained devices never re-notifies.

### Measured on the bench

`192.168.100.141` reports `192.168.100.143` as its LLDP neighbour -- one
OmniStream unit behind another -- and resolves to Catalyst `50:1c:b0:d5:86:80`.
The page showed the advisory, marked that row's MAC cell "Daisy chained via" the
unit in front, and its hover read "Daisy chained through &lt;unit&gt; (address)"
above "Upstream switch: Switch". Dismissing hid the banner, stored the
acknowledgement and **left the highlighting in place**; a rescan did not bring it
back; Clear Units reset it and the next discovery showed it again. Switch count
stayed 1 throughout: a chain is not a second switch.

## A USB route never crosses a subnet

Management is routed. USB is not. Directed unicast reaches an endpoint through a
router perfectly well -- Query, Advanced Query, Full Configuration, Link Status,
network configuration, identify and reboot all work across one, and none of that
changes. **A USB pairing between endpoints on different subnets does not work,
and OmniSuite will not create one.**

### Why a cross-subnet cell was offered

Neither Matrix axis builder carried a subnet mask. `_usb_matrix_axes` did not set
one for standalone entries, and the integrated entries built from `usb_icron`
did not either -- so every eligibility question answered "cannot say", and the
gate refused only on a definite mismatch. The mask was known all along, in the
derived view, and simply never reached the entry.

Three separate holes, all now closed:

| Hole | Fix |
| --- | --- |
| the axes carried no mask | `_usb_attach_endpoint_networks` stamps each entry's own address and mask from the derived view, once per grid rather than per cell |
| unknown meant permitted | the gate now refuses unless compatibility is *proven* |
| the check sat inside the standalone branch | it applies to every combination, `E4521 -> D4511` over `usb_icron` included |

A fourth was found while testing: `/api/usb_pair` had its own guard comparing the
first three octets of the two **parent management** addresses. Both halves of
that were wrong -- the USB link lives on the Icron network, not the management
one, and a /16 legitimately spans third octets -- so it is gone, replaced by the
endpoint check, which still runs before anything is written.

### The authority

| Endpoint | Address and mask from |
| --- | --- |
| standalone AT-OMNI-311/324 | the unit's own reported address and mask (UDP Full Configuration) |
| integrated E4521/D4511 | the **Icron interface** as the parent `net` API reports it |

An E4521 managed on `192.168.100.x` whose Icron endpoint sits on
`192.168.200.0/24` belongs, for pairing purposes, to `192.168.200.0/24`. The
parent's management address is never used for this.

Masks are used as given; nothing assumes /24. Both endpoints must resolve to the
same network *and* each must fall inside the other's, so two endpoints whose
masks disagree are not authorised: disagreement is not proof.

### Fail closed

```
    OK                          proven to share a subnet   -> offered
    NETWORK_MISMATCH            proven not to              -> refused
    NETWORK_RELATION_UNKNOWN    not proven either way      -> refused
```

Unknown refuses. Authorising a route on the strength of missing information is
what produced this defect in the first place. Existing hardware routes are never
torn down because telemetry went incomplete -- they stay visible and diagnosable;
only *creation* is refused.

### The backend is the safety boundary

Every mutating route path validates independently, immediately before it acts:
`/api/usb_route/pair`, `/api/usb_route/unpair`, the desired-state reassignment
inside them, and the `usb_icron` `/api/usb_pair`. `/api/usb_route/resolve` shares
the same guard, so nothing can describe a route as available that the pair
endpoint would refuse.

**Reassignment checks before it releases.** A REX belonging to LEX A, clicked
onto an incompatible LEX B, is refused while still paired to A. Nothing is
unpaired to discover afterwards that the destination was illegal.

### What the operator sees

An incompatible intersection is disabled and reads **Different USB subnet**; an
indeterminate one is disabled and reads **USB network information unavailable**.
Both keep the addresses and masks they judged, for diagnosis. Routed endpoints
stay on both axes -- only their intersection closes, which is itself useful
topology information.

**Measured on the bench:** 13 cross-subnet intersections, every one disabled and
labelled; 20 same-subnet intersections, every one still available. A direct
`/api/usb_route/pair` for a cross-subnet pair returned 409 `NETWORK_MISMATCH`
with both addresses, and the peer tables were byte-identical before and after.
The `usb_icron` endpoint refused its own cross-subnet pair the same way, from the
Icron addresses rather than the parents'.

## USB firmware is the USB endpoint's firmware

An OmniStream unit and the Icron USB module inside it are versioned separately.
Measured on hardware:

| | E4521 at `.141` | D4511 at `.32` |
| --- | --- | --- |
| parent OmniStream firmware | 2.1.2 | 2.1.2 |
| Icron USB endpoint | **2.0.9** | **2.0.9** |

The Icron version comes from the endpoint's own Basic Query `product_revision`,
corroborated by Full Configuration `secondary_version`. (On integrated endpoints
Full Configuration's `firmware` field is empty and `secondary_version` carries
the value; on a standalone unit both read the same.)

So `usb_firmware` is a field of its own, and every USB surface uses it: the
Configure &rsaquo; USB Firmware column and the workbook's USB sheet. The parent's
firmware stays on the Devices sheet, where it belongs.

**An unread Icron version stays unknown.** It is never filled in from the parent
to avoid a blank cell -- that would state a version the module does not have. The
workbook writes `N/A`; the field itself stays empty.

Bench values in the exported workbook: integrated endpoints `2.0.9`, standalone
`1.9.4`, one endpoint that has never answered `N/A`, and one D4511 carrying an
older `1.9.4` Icron module -- real per-endpoint variation the parent's firmware
would have hidden.

## Appearance: a layout, a palette, and one colour

| Control | Values |
| --- | --- |
| Theme | Dark, Light |
| Layout | Classic, Compact, Modern, Workspace |
| Colour preset | ten palettes |
| Light background | one colour picker |

That is the whole surface. An earlier build exposed nine individual semantic
colours -- accent, selection, online, warning, critical, information, daisy
chain, other switch, panel accent -- which asked the operator to make nine
decisions to arrive at a palette. **The token architecture underneath them
stays**, because it is what lets one choice reach every layout; what went away is
choosing them one at a time.

### Presets are the palette

Ten: Default, Slate, Cool, Ocean, Forest, Warm, Plum, Rose, Soft and High
contrast. Each defines the **complete** set of semantic roles, not just an
accent -- a preset that set only one would leave the states looking like whichever
palette came before it, and a test asserts every preset defines every role.

Semantic meaning is held constant across all ten: critical always reads as
critical, online always reads as healthy. A Rose or Plum palette changes how the
application looks, never what a colour means.

The softer options are named for their colours. A palette is not a demographic,
and nothing in the product says who should choose one. Hovering a preset previews
it; leaving restores the selection.

### One picker: the light background

The only colour set by hand is the light-mode application background. It drives
`--light-bg`, and the light palette derives its panel, header and border tones
from it with `colour-mix`, so surfaces keep their separation instead of the page
becoming one flat colour. Browsers without `colour-mix` fall back to the default
tones.

It is a light-mode value and reaches nothing else: dark declares its own tokens
and never reads `--light-bg`. Verified live -- a custom `#eef2f5` drove the light
shell with panels and headers derived from it, switching to dark returned the
standard dark surface with the custom value still stored, and switching back
restored it.

### Obsolete overrides

An installation upgraded from the previous build may hold per-role colours in
`ui_preferences.json` or in browser storage. Those controls are gone, so the
values are **ignored on read and dropped on the next save** in both places --
leaving them applied would tint the UI from a control nobody can see. Everything
else in the file is preserved, and a malformed file still degrades to defaults.

### Appearance reset

Restores Classic, the Default preset, the default light background and dark mode,
and clears any obsolete override. It is immediate and needs no confirmation
because it is visual and reversible. It touches no credential, port, timeout,
concurrency, firmware path, scan interface or target, USB discovery range,
route, LLDP acknowledgement, cached device or device configuration -- asserted by
test and verified live.

## Workspace is a constrained OmniSuite

Workspace originally carried a navigation rail, a top bar and a context
inspector. The rail duplicated navigation the application already had, and the
inspector restated what the row beside it was already showing. Both are gone,
along with the JavaScript that built them.

What remains is the part that was worth keeping: **a centred work area with a
maximum width**, so a wide display does not stretch every table across the whole
viewport. Measured: at 2560px the content is 1560px wide and centred, at 1920px
the same, and below about 1440px it uses the full width. The USB Matrix is the
exception -- on its own page it is the priority content and keeps the width it
needs.

### The raw-text artifact

The symptom was unstyled text -- an application title, four run-together
navigation links, a page heading and the word "Inspector" -- appearing as plain
content. The cause was **node relocation**: the old `workspace.js` moved the
page's existing children into a new shell it had created. Anything rendered
before it ran, or any moment where the grid rules were not yet in force, showed
those nodes with no layout at all, and a second navigation existed by
construction.

The fix is structural rather than cosmetic: **nothing is relocated any more.**
The layout is CSS applied to the page exactly as the server rendered it, and the
only thing set from script is a `data-page` attribute so a layout can give the
right content the width it needs. There is therefore exactly one application
title, one primary navigation, one page heading and one copy of the content, at
every instant, in every layout.

Verified: six consecutive layout switches and five page navigations, each ending
with exactly four navigation links in one container, one `h1`, zero rails and
zero inspector nodes.

## Semantic tokens must follow the palette

A confirmed defect, and the reason Workspace stayed dark around a light page.

`--surface: var(--bg)` was declared once on `:root`. A custom property resolves
where it is declared, so `--surface` froze at the dark value. Light mode
redefines `--bg` on `<body>`, so rules written against `--bg` went light and
rules written against `--surface`, `--text` or `--edge` did not. Classic and
Compact mostly use the raw variables and looked fine; Workspace is built entirely
on the semantic ones and stayed dark.

The light palette now restates every alias -- surface, raised, elevated, text,
muted text, edge, the action and state colours and both topology states -- and
`html` and `body` are painted from `--surface`, so no strip of the viewport is
left in the other palette. Layout CSS names no colour directly, which a test
enforces by scanning the Workspace block for literals.

Verified across 32 combinations (four layouts x light and dark x four pages),
inspecting every painted surface wider than 220px: **zero surfaces in the wrong
palette**, and no console errors.

## The USB Matrix is desired-state routing

A Matrix cell states a relationship the operator wants, not a protocol opcode
they are asking to send. The transaction layer reconciles the hardware to that
request.

| The operator does | It means |
| --- | --- |
| click an empty cell | route this REX to this LEX |
| click an active cell | remove this route |
| click another LEX's cell for an owned REX | **move** this REX to that LEX |

There is no step where the operator must clear the old route first, and no
message telling them to. A REX that permits one host and belongs to another is
reassigned by the backend: it captures the authoritative state, releases only the
conflicting relationship with a specific Unpair naming that peer, establishes the
requested one, and verifies both sides. **Unpair All is never used for ordinary
route movement**, and Force Pair does not exist.

Because a request is a desired end state, the transaction also handles the states
hardware is actually found in: already routed (nothing is sent), half-established
on one endpoint only (it converges, commanding only the endpoint that lacks the
peer, because a device rejects a command re-asserting what it already holds), or
owned by a different host (it moves).

**A multi-peer host keeps everything else.** Moving one REX off a host with five
peers leaves the other four exactly as they were.

### When a move fails

Removing a working relationship before establishing a new one carries more risk
than routing a free endpoint, so the previous state is captured first and the
result distinguishes what actually happened:

| Status | Meaning |
| --- | --- |
| `VERIFIED_SUCCESS` | the requested route exists and both endpoints agree; `reassigned_from` names the previous host when one was displaced |
| `REASSIGN_RELEASE_FAILED` | the endpoint could not be released, so nothing was changed and nothing was transmitted to it |
| `FAILED_ROLLED_BACK` | the new route failed and the previous one was restored and verified |
| `FAILED_ROLLBACK_UNVERIFIED` | the new route failed and the restoration could not be confirmed |

An ACK never produces any of the success states; every one rests on a fresh
read of both endpoints.

The cell renders as pending while the transaction runs and only shows a finished
state once the verified read-back says so, so an intermediate state is never
displayed as though it were the operator's completed action. Failures surface in
the ordinary toast. There is no confirmation dialog and no native browser dialog.

All four families are covered. The UDP transaction reassigns for
`311 -> 324`, `311 -> D4511` and `E4521 -> 324`; `E4521 -> D4511` keeps its
established `usb_icron` path, which already replaces an existing owner in one
action.

### Measured on hardware

Starting from the AT-OMNI-311 at `.135` holding four peers, one route action
moved an integrated D4511 to a second AT-OMNI-311 and reported
`reassigned_from`. The old host lost exactly that peer and kept its other three;
the new host gained it; the REX named its new host. One action moved it back,
restoring the original set. A third action moved another D4511 back from the
second host, returning the five-peer topology -- each time with a single request
and no manual unpair.

## Matrix structure versus live state

The Matrix has two independent concerns and they are refreshed differently.

**Structure** is axis membership and identity: which LEX and REX endpoints exist,
their labels, and each cell's capability. **Live state** is everything else:
online/offline, active route, freshness, conflict.

Axis order is deterministic and computed server-side by `_usb_matrix_sort_key()`:
integrated units first (preserving the established convention that OmniStream
units lead), then **numeric** IPv4, then canonical MAC as the final tie-break. The
previous build appended units in `as_completed()` completion order, so every poll
produced a different order and the grid appeared to animate. Numeric ordering also
matters on its own: as strings, `.141` sorts before `.32`. The client re-applies
the identical rule as defence in depth.

`usb.js` computes a membership signature from the axis keys, addresses, MACs and
labels. When the signature is unchanged it updates the existing cells in place —
classes, `data-*` attributes and tooltips — instead of replacing the table, so a
state-only refresh moves nothing. Structure is rebuilt only when membership or
identity actually changes, with a rebuild fallback if the shapes ever disagree.

## USB live-state architecture

One backend mechanism serves the USB Matrix, Configure > USB, and the Device Info
count, so two open pages never multiply device traffic. `_usb_live_refresh()`
carries in-flight suppression and a `USB_LIVE_MIN_INTERVAL` (4 s) floor: page
polls arriving inside that window return immediately without touching a device.

**Standalone endpoints** are refreshed with a single targeted UDP `Query` to the
address on record — never a broadcast, never a range scan, and deliberately never
an Advanced Query, because proving a unit answers does not require reading its
pairing table. Advanced Query stays reserved for pairing state. The responding MAC
must match, so another device at that address never marks this one online.
Timeout is 0.30 s, concurrency 8, so one unreachable endpoint cannot hold up the
others.

**Integrated endpoints are never polled over UDP.** Their liveness comes from the
`usb_icron` read the application already performs against the parent, recorded by
`_record_parent_live()` and valid for `USB_LIVE_PARENT_TTL` (45 s). No duplicate
traffic is introduced. `liveness_source` reports `usb_icron` or `udp_query` so the
UI never implies USB-endpoint reachability it did not measure.

Measured cost per cycle on the bench: **2 standalone UDP queries, 0 integrated
queries, 0.021 s**; five further page polls inside the interval produced **0**
extra queries.

## Liveness semantics

`DISCOVERED` (in inventory), `ONLINE` (answered within the live window),
`STALE` (in inventory, not recently confirmed), `OFFLINE` (targeted polling
determined it unavailable), `UNCONFIRMED` (classification incomplete), and
`OFF_NET` (different subnet from the management interface) are distinct.

`OFF_NET` is a network relation, not a liveness state: an off-net endpoint may be
online. A single dropped datagram does not flap a device — `LIVE_MISS_TOLERANCE`
(2) consecutive misses are tolerated. Exceeding it, or a different device
answering at the address, is an authoritative determination of OFFLINE that
overrides an older sighting from this process.

Critically, an endpoint discovered by a **previous** process is no longer stuck
offline: persisted history alone still does not authorize anything, but a
successful live Query supersedes it. Opening the USB Matrix after a restart brings
known standalone units online without a Device Info Scan.

**Liveness is convenience; the transaction is authority.** Matrix cell enablement
uses live liveness, but a Pair or Unpair still performs its own authoritative
fresh read of both endpoints, sends the per-endpoint commands, and verifies both.
That gate is unchanged. After a verified transaction the UI refreshes authoritative
state immediately rather than waiting for the next poll, and never paints a route
from a click, an HTTP 200, or an ACK.

## USB dialogs

Configure > USB uses no native browser dialogs. `alert()`, `confirm()` and
`prompt()` are absent from `usb-extenders.js`; a test asserts this. The Network
and Reboot flows use the application's existing dialog structure and classes
(`encoder-output-modal` / `-card` / `-head` / `-body` / `-actions`).

The network flow offers three explicit choices — **DHCP**, **Static**, **Cancel**
— so Cancel means cancel rather than being overloaded as "static". DHCP confirms
with the device identified by model, address and MAC, and warns that the address
may change (identity remains MAC-based). Static opens a form with IP, mask and
gateway, prepopulated where authoritative values exist, validated for IPv4 syntax,
a contiguous mask, and a gateway inside the same subnet before anything is sent.
Cancel, the close button, the backdrop and Escape all abort without transmitting.

An integrated endpoint never opens the standalone network dialog; it shows an
explanatory modal stating that its network is managed by the parent OmniStream
device and is not available from OmniSuite.

## Pairing freshness is separate from liveness

Two independent clocks, never overloaded onto one field:

| Concept | Refreshed by | Timestamp | TTL |
| --- | --- | --- | --- |
| Liveness | targeted UDP `Query` | `last_seen` / `live_seen` | `LIVE_TTL` 45 s |
| Pairing state | `Advanced Query` | `pairing_last_read` | `PAIRING_TTL` 120 s |

ONLINE does not imply the pairing table was re-read, and fresh pairing does not
imply the endpoint is online now. `pairing_state_fresh` and `pairing_age` are
derived from `pairing_last_read` alone. Unlike liveness, pairing freshness is not
session scoped: an Advanced Query that actually completed is authoritative
whichever process ran it.

`_usb_pairing_refresh()` runs the Advanced Query sweep on its own slow cadence
(`USB_PAIRING_MIN_INTERVAL` 20 s) with in-flight suppression, and
`refresh_pairing()` additionally skips any endpoint already fresh, so repeated
page polls never repeat the physical read. It is reset to run immediately after a
verified Pair or Unpair (the transaction already re-read both endpoints) and when
a previously offline endpoint returns online with stale pairing.

Measured with the Matrix (5 s) and Configure (10 s) both open for 60 s:
**13 Query and 1 Advanced Query per standalone endpoint per minute, 0 added UDP
packets for integrated endpoints**, no broadcast, no range scan, no `/api/scan`.

Stale pairing never disables Identify, Network or Reboot — liveness is sufficient
for those. Only routing needs fresh pairing, and the transaction performs its own
authoritative read regardless.

## USB UI text and tooltips

Matrix cells carry no native `title`. The previous build put the capability enum
and its full explanation into a `title` attribute, which the browser rendered as a
single very wide tooltip across the grid. Cells now carry concise
`data-tip` / `data-tip-detail` values and a small OmniSuite-styled tooltip renders
them: bounded at 320 px, wrapped, clamped inside the viewport, positioned adjacent
to the cell, and driven by both hover and keyboard focus.

Backend enums remain in the JSON, tests and logs; they never reach user-facing
text. `USB_CELL_TEXT` maps each state to a short label and a secondary detail:

| State | Label |
| --- | --- |
| `SUPPORTED_USB_ICRON` / `SUPPORTED_STANDALONE_UDP` | Available |
| `SUPPORTED_MIXED_EXPERIMENTAL` | Mixed USB route — data transport not yet validated |
| `IDENTITY_UNAVAILABLE` | USB endpoint identity unavailable |
| `OFFLINE` | Device offline |
| `UNCONFIRMED` | USB ownership not confirmed |
| `NOT_ELIGIBLE` | Not routable on current network |
| `CONFLICT` | Already paired to another host |
| `STALE` | Pairing state stale |

Configure > USB shows standalone pairing as peer identity plus an explicit age
("Paired devices: 1 / AT-OMNI-324 — 192.168.100.127 / Updated just now"), or
"Pairing state stale — Last confirmed 2 min ago" when it has aged out, so a stale
peer is never presented as current. Integrated endpoints stay authoritative from
`usb_icron` and show its current peer count.

## 45xx integrated USB (Icron) network configuration

Established from the device's own web application and confirmed against live
E4521 and D4511 hardware. This supersedes the earlier conclusion that no parent
network workflow existed: the earlier search looked for a *device* network
endpoint and missed that the USB endpoint is an interface inside the parent's
`net` configuration.

**Evidence.** `artifacts/device-150146-atlona.js` (the device UI bundle):
`NetworkController` sets `vm.icronmodes = ['broadcast','dhcp','static','disabled']`
and `save(network)` calls `SocketService.set('net', network, ...)`. `SocketService.set`
builds `{"id": name+"-set", "username", "password", "config_set": {"name": name,
"config": single ? config : [config]}}` — the controller passes no `single`, so the
payload is the interface object wrapped in a one-element list. The template
(`artifacts/device-150146-atlona.templates.js`) binds `network.dhcpmode` to
`networkCtrl.icronmodes` under `ng-if="network.type == 'icron'"`, and shows
`ipaddress`/`subnetmask`/`gateway` editable only when `dhcpmode == 'static'`.

**Read path.** `config_get: "net"` returns a list of interfaces. The USB endpoint
is the entry with `type == "icron"`:

```json
{"name": "icron", "type": "icron", "dhcpmode": "dhcp",
 "ipaddress": "<USB IP>", "subnetmask": "...", "gateway": "...",
 "macaddress": "<USB MAC>"}
```

Confirmed present on both an E4521 and a D4511, with values matching the parent
web UI exactly, so the mechanism applies across the 45xx family.

**Write path.** WebSocket, same transport and shape the application already uses
for `usb_icron`:

```json
{"id": "net-set", "username": "...", "password": "...",
 "config_set": {"name": "net", "config": [ <the icron interface object, mutated> ]}}
```

The whole interface entry is sent back with `dhcpmode` changed, exactly as the
device UI does.

**Modes.** `broadcast`, `dhcp`, `static`, `disabled` (lower case, property
`dhcpmode`). `ipaddress`/`subnetmask`/`gateway` are only meaningful for `static`;
for every other mode the device UI renders them read-only. **Uncertain and
labelled as such:** the device-side meaning of `broadcast`, and exactly what
`disabled` turns off, are not established by the sources inspected — only that
they are the accepted values. `disabled` is treated as potentially making the USB
endpoint unreachable, so it is confirmed with that consequence stated and marked
as a destructive action. Whether the endpoint restarts or how quickly a new
address appears after Apply was **not** determined; no mode change was performed
on live hardware beyond re-applying a device's existing value.

**Ownership and safety.** The parent is resolved server-side from the USB MAC via
`_usb_parent_context()`; a client-supplied parent is ignored. The USB MAC must
match the `macaddress` of the resolved parent's icron interface, otherwise the
request is refused with `mac_mismatch` and nothing is written. An ACK is not
success: the parent is read back and the result reports
`configuration_verified` or `command_accepted_unverified`. Identity remains the
canonical USB MAC, so an address change updates the existing record rather than
creating a second endpoint.

The standalone AT-OMNI-311/324 UDP commands (`IP_DHCP` 0x0306, `IP_STATIC` 0x0307)
are **never** sent to an integrated endpoint, and the parent `net` API is never
used for a standalone extender. Tests assert both directions.

## Integrated network configuration is read from the device

A 45xx integrated endpoint reported `DHCP` in OmniSuite while its parent's web UI
showed `static`. The stale value came from the standalone UDP Query mode byte,
which is meaningful for an AT-OMNI-311/324 but not for an Icron endpoint behind an
OmniStream parent. The fix reads the device rather than making the device agree
with the cache.

**Authority rule.** For an `INTEGRATED` endpoint the parent's `net` configuration
(the interface whose `type` is `icron`) is authoritative for mode, address, mask
and gateway. The UDP mode byte never wins there, and when no authoritative read
has succeeded yet the field is left empty rather than filled with a value known to
be unreliable. For a `STANDALONE` extender the UDP value remains authoritative.
Both cases carry `network_config_source` so the origin is visible.

`_ingest_icron_network_config()` is the single ingest for every path — discovery
enrichment, the Configure refresh, and the read-back after a write — so no path can
interpret a mode differently. It preserves `dhcpmode` verbatim as `mode_raw`; an
unrecognised value is surfaced as `Unknown (<value>)` rather than normalised into a
valid-looking mode.

Refresh is bounded and never on the request path:

| Property | Value |
| --- | --- |
| TTL | 30s (`USB_NET_CONFIG_TTL`) |
| Concurrency | 6 workers |
| Per-parent timeout | 3s |
| Deduplication | in-flight set; a parent read within the TTL is skipped |
| Failure | keeps the last known values, clears `network_config_fresh` |

Measured on the 17-unit bench: `/api/scan` median **1.40s** (min 1.38, max 2.33)
with **no** USB enrichment on the request path; the enrichment reads **8 parents in
0.22s** on the background executor. An immediate unforced repeat performs zero
reads. A single unreachable parent costs its own 3.02s timeout and affects no
other parent.

Verified against live hardware: before enrichment the endpoint showed no mode; after
enrichment it showed `STATIC` with `192.168.100.246 / 255.255.255.0 / 192.168.100.1`,
`network_config_source=parent_net`, matching the parent exactly. A change made in the
parent web UI is picked up on the next refresh with no rediscovery or restart.

## Control ownership

| Operation | Standalone AT-OMNI-311/324 | Integrated E4521/D4511 endpoint |
| --- | --- | --- |
| Identify | UDP Blink | `_omnistream_identify()` — the same helper `/api/blink` uses |
| Reboot | UDP Reboot | `_omnistream_reboot()` — the same helper `/api/reboot` uses |
| Network | UDP DHCP/static | **Not available.** See below |
| Pairing | fresh Advanced Query | `usb_icron` |

Identify and Reboot are single implementations shared by Device Info and the USB
surfaces; neither is reimplemented. The parent is resolved server-side from the
USB MAC, so a crafted request cannot redirect the action to another device.

**Network configuration for an integrated endpoint is not offered, because
OmniSuite has no OmniStream network-configuration operation to reuse.** The only
device-network endpoints are `/api/hostname` and `/api/set_ntp`; there is no
DHCP/static workflow on Device Info. Rather than invent one or aim the standalone
extender command at an Icron USB IP, the control is disabled and points the
operator at the unit's own web interface.

## Tests never reach the physical network

The fixtures use the real bench addresses, so a test that unexpectedly passes a
gate could drive a real device. Widening the route gate for mixed routing exposed
exactly that: a gating test that had always been refused before any transmission
began reaching the transaction.

`ServerTestBase` now replaces the transport for every test. Reads time out
harmlessly, because liveness reads are a normal part of these tests. Any mutating
command — Pair, Unpair, Unpair All, either IP command, Reboot, Blink — fails the
test instead of being delivered, asserted both at the call and in cleanup, since
the service converts transport errors into failure results and would otherwise
absorb the assertion. Tests that exercise the transaction install a scripted
`FakeUdp`, which replaces the guard.

## Regression policy

Unless explicitly required, preserve the normal scan endpoint and timing, adapter behavior, cache shapes consumed by matrix pages, A/V route behavior, encoder/decoder polling, firmware workflows, WebSocket credentials, E4521/D4511 `usb_icron` pairing APIs, USB Matrix UI behavior, and existing configuration/export APIs. New persistence should be additive and backward compatible. New network responses must be validated before state is written, and errors must be observable without exposing credentials.

## Bench validation API for AT-OMNI-311/324 routing

Three diagnostic endpoints exist so the standalone Pair/Unpair direction and
Advanced Query propagation can be measured against physical hardware **before**
any Matrix routing is enabled. They are bench instrumentation, not a product
feature: the USB Matrix exposes no route cell for standalone units, nothing calls
them automatically, and they are the only code paths permitted to invoke
`get_route_state` / `pair_route` / `unpair_route`.

| Endpoint | Sends |
| --- | --- |
| `POST /api/diagnostics/usb_route/state` | Query + Advanced Query only (read-only) |
| `POST /api/diagnostics/usb_route/pair` | Normal Pair (`0x0302`) |
| `POST /api/diagnostics/usb_route/unpair` | Specific Unpair (`0x0303`) |

Guards:

- Both mutating endpoints require an explicit `"confirm": "PAIR"` / `"UNPAIR"`
  token; without it nothing is transmitted.
- Both endpoints must classify as `STANDALONE`. An integrated or unconfirmed
  endpoint is refused, so no mixed E4521-to-OMNI-324 or OMNI-311-to-D4511
  combination can be attempted.
- `host_mac` must report `AT-OMNI-311` and `device_mac` must report
  `AT-OMNI-324`; reversed roles are refused.
- Force Pair and Unpair All are never sent.

Each response reports the operation, both endpoint identities, the pre-operation
Advanced Query state of both, the command target and peer MAC, the ACK/NACK or
timeout, the post-operation Advanced Query state of both, the verification
outcome, the number of verification attempts used, and elapsed time.
`VERIFIED_SUCCESS` still requires a fresh read from **both** endpoints to agree.

Propagation timing is unknown, so verification supports a bounded read-back
retry: `verify_attempts` (default `USB_BENCH_VERIFY_ATTEMPTS`, max
`USB_BENCH_MAX_ATTEMPTS`) and `verify_delay` seconds (default
`USB_BENCH_VERIFY_DELAY`, max `USB_BENCH_MAX_DELAY`). The reported
`verification_attempts` shows how many read-backs were needed, which is the
measurement that determines whether propagation is immediate or delayed. These
values are bench parameters and are **not** production constants; production
`pair_route`/`unpair_route` still default to a single immediate read.

## Test isolation from hardware

The suite runs against fixtures that carry the real bench addresses, because
those values were observed on real hardware and the records document what the
devices actually reported. That is only safe because nothing in the suite can
reach the network.

Two fences, both installed once at module import in `tests/test_usb_integration.py`
rather than per test:

- `srv._ws_send_recv` is replaced by `_fenced_ws_send_recv`. A payload carrying
  `config_set` is recorded and raises; the assertion is repeated in cleanup
  because the device handlers catch transport errors and would otherwise absorb
  it. A `config_get` is answered from the per-test `ws_read` hook, or, with no
  hook, behaves as an unreachable device.
- `srv.websocket.create_connection` is replaced outright. `_ws_send_recv` is not
  the only way out: config import, firmware upload, identify/blink and reboot
  each open their own socket, so fencing the shared transport alone still left
  four mutating paths able to reach a live device.

They are process-wide and never restored, because the scan dispatch tests start
background threads that outlive the test that started them. A per-test restore
handed the real socket back to those threads while they were still running, and
they went on to read live hardware after their test had finished.

The UDP transport keeps its existing per-test stub in `ServerTestBase`, which
records every datagram and fails the test on any mutating opcode.

Verified by running the whole suite with `websocket.create_connection` and
`socket.socket.sendto` replaced by spies that raise: 756 tests pass, and the
spies record zero WebSocket connections and zero UDP datagrams. Before this,
the same measurement recorded 163 WebSocket connections to five bench devices
and 2 UDP datagrams, and three tests failed when the bench was unreachable --
they had been passing only because the hardware answered.

`ws_read` is the supported way for a test to need a readable parent. The write
refusal is deliberately not overridable.

## Reporting a device that does not answer

An unreachable device is an upstream condition and is reported as `502`. `500`
is reserved for a fault in this application. `/api/usb_pair`, `/api/usb_unpair`,
`/api/usb_set_type` and `/api/blink` previously answered `500` when a unit was
simply offline, which put a routine condition into the same bucket as a bug;
`/api/hostname` already had this right and is the pattern the others follow.

## Mutation audit record

Every route that writes to a device carries `@_audited(...)`, which records the
requested operation, the filtered request fields, and the outcome. Before it, a
pair and an unpair were logged but a reboot, hostname, codec, host port, filter
or identify were not, so the only evidence a device had been reconfigured was
the device's own behaviour.

The record names the fields it logs and filters `_AUDIT_SECRET_FIELDS`. Two
endpoints accept credentials in the request body, so a verbatim dump -- which is
what `/api/route` used to do -- would write an operator's device password into
the log file. `HardwareIsolationTests` asserts both halves: every mutating
endpoint produces an entry with an outcome, and no credential appears in it.

Idle polling does not log at INFO. `/api/usb_state` is polled every five
seconds and logged two lines per call per device; an idle minute produced about
48 records saying nothing had changed. Those are now DEBUG, and an idle minute
produces none.

## One lock for every route mutation

Route mutation used to be serialised by two different locks. The integrated
path (`/api/usb_pair`, `/api/usb_unpair`) took the server's `_usb_route_lock`;
the desired-state path (`/api/usb_route/pair`, `/api/usb_route/unpair`) took
only the extender service's own `_route_lock`, and the bench diagnostics took
neither. Because those locks did not exclude one another, a mixed route and an
integrated route could reconfigure the same endpoint at the same time -- and
`_reassign_route` performs a release-then-acquire sequence that must not
interleave with anything.

All six route-mutating endpoints now pass through `_with_usb_route_lock`. The
service's `_route_lock` is still taken inside it, so the acquisition order is
always server then service. There is exactly one site that acquires
`_usb_route_lock`, and the service never acquires it, so the ordering cannot
invert.

The lock is deliberately global rather than per device. Serialising every route
change costs a little concurrency on an operation an operator performs one at a
time, and device reliability is worth more than that concurrency.

`_with_usb_route_lock` is defined beside the lock it uses, near the top of the
file, because routes far above its former position now use it.

## One Settings dialog, shared by every page

The Configuration dialog, the folder chooser it opens, and all of their wiring
lived inside `ui/index.html`. Device Info was therefore the only page that had
Settings at all, and the obvious way to put it on the other three -- copying the
markup -- would have produced four dialogs to keep in step.

`ui/settings.js` owns the dialog markup, the folder-browser markup and the
`initConfig()` wiring, all moved verbatim out of the page. It injects the markup
where it is loaded (guarded, so a second call cannot produce two copies of every
`#cfg_` id) and starts itself on `DOMContentLoaded`. `ui/settings.css` holds the
styling the dialog needs, because the rules also lived only in `index.html` and
the same dialog would otherwise render unstyled on three pages. `index.html`
still uses both files, so `.modal`, `.modal-row` and `.btn.secondary` continue to
serve its other dialogs from one definition.

All four pages carry the same header: logo, `h1`, the four tabs, then the gear.
Measured in a browser, `#header_gear` occupies the identical box on every page:

    /                  x=1228 y=22 w=36 h=36
    /matrix/configure  x=1228 y=22 w=36 h=36
    /matrix            x=1228 y=22 w=36 h=36
    /matrix/usb        x=1228 y=22 w=36 h=36

with exactly one navigation container, four tabs, one `h1` and zero rails or
inspectors on each. Settings state is the server's: the dialog reads and writes
`/api/config` and `/api/ui_preferences`, so there is no page-local copy that
could disagree.

## The firmware path is a real directory, chosen server-side

A browser file input cannot return a directory path, and the implementation does
not pretend otherwise. The "Choose Folder" button opens the application's own
folder browser, which lists directories through `/api/list_dir` on the machine
running the server, and the chosen path is stored in `config.json`.

Verified end to end: saving a path returns it from `/api/config`, `/api/files`
then enumerates that directory (listing only `.vpup2` files -- a `.txt` in the
same folder is not offered), and both survive a restart, because the value is
read back from `config.json` rather than held in memory.

## Running the tests

`python run_tests.py` is the single entry point. It runs the Python suite and
every JS suite, and exits non-zero if any of them fails.

It also enforces the isolation guarantee rather than assuming it: before any
test module is imported it replaces `websocket.create_connection` and
`socket.socket` with spies that record the attempt and raise, so a test cannot
open a real connection whichever path it takes. Connections to the RFC 5737
documentation ranges are reported but do not fail the gate -- those addresses
are not routed, so a datagram sent to one cannot reach a device, and one test
deliberately probes an unreachable address.

CI already existed (`.github/workflows/build-cross-platform.yml`) but ran no
tests. It now has a `test` job that runs `run_tests.py`, and the `build` job
declares `needs: test`, so a red suite cannot produce a release artifact. The
job needs no hardware and, by construction, cannot reach any.

## Device Info: identity stays put while the table scrolls

The leading columns are, in order, the selection checkbox, IP, Hostname, then
MAC. Hostname used to follow MAC; it belongs beside the address, so the operator
reads select, address, name, hardware identity in that order.

The first three columns are frozen. MAC and everything after it scroll beneath
them, so a row read at the far right is still attributable to a device.

Nothing about that is hard-coded. `ui/sticky-columns.js` measures the rendered
width of each leading column and publishes `--sticky-col2-offset` and
`--sticky-col3-offset` on the table; the stylesheet decides which columns are
sticky and consumes those offsets. A table says how many columns it wants frozen
with `data-sticky-cols`, defaulting to the two that `table.sticky-identity` has
always meant, so one measurement path serves Device Info's three columns and the
Encoder, Decoder and USB tables' two. Measured at 1280x900: col2 36px, col3
148px, 294px of frozen width.

## Device Info: a command area that stays on screen

The shell header, the scan bar and the firmware bar are sticky; the table
scrolls beneath them. Only those compact command surfaces are pinned -- the
advisory banners scroll away with the rows, because a permanently pinned
advisory costs the height the operator came for.

The stacked heights are measured at runtime and published as CSS variables
(`--shell-header-h`, `--scan-bar-h`, `--firmware-bar-h`, `--table-head-h` and
the rest), and every sticky `top` is expressed in terms of them. There is no
collection of unrelated magic offsets: `grep -nE "top:\s*[0-9]+px"` over
`ui/device-table.css` returns nothing but the two `top: 0` anchors. The
measurements are recomputed from `ResizeObserver`, `MutationObserver`,
`window.resize`, `visualViewport.resize` and `document.fonts.ready` -- never
from a timer.

The sticky table header and the frozen columns intersect at the top-left corner
cells, which therefore carry the highest stacking order of the three. Verified
by hit-testing each corner's own centre at three horizontal positions while
scrolled to the bottom: `elementFromPoint` returned the corner cell or a
descendant every time.

## The mirrored horizontal scrollbar

A horizontal scrollbar sits directly below the locked command area and above the
table, so reaching it no longer means scrolling to the bottom of a long
inventory. It scrolls the same container the table does.

Its track is sized from the wrapper's real `scrollWidth`, and the strip itself is
given the wrapper's measured *client* width and left edge. That last part
matters: the wrapper loses two pixels to its border and about fifteen more when a
vertical scrollbar appears, so a full-width mirror had a larger maximum
`scrollLeft` than the table and its right-hand end left the table short --
measured 158 against 160 before it was corrected.

Synchronisation is one `follow()` helper in both directions. Assigning
`scrollLeft` makes the receiver fire its own scroll event, which would assign
straight back, so a `syncOwner` guard lets only the element that began the
exchange drive it and releases ownership on the next animation frame. The
table's native scrollbar, the wheel, the trackpad and a programmatic scroll all
reach the mirror through that one path.

The mirror is shown only when the table actually overflows. Measured across six
widths at two zoom levels, it appeared exactly when `scrollWidth > clientWidth`
and was hidden otherwise, and its `scrollWidth`, `clientWidth` and maximum
`scrollLeft` matched the table's in every case where it was shown. Hiding a
column while scrolled to the far right clamps `scrollLeft` back to 0 and hides
the mirror without an exception.

## Where the shell header is pinned, and where it is not

Every page's shell header -- logo, navigation, Settings -- is sticky.
`ui/matrix/matrix.css` has pinned it for the three matrix pages for a long time,
and `ui/device-table.css` pins it, with the two command bars, for Device Info.
Only Device Info pins anything below the header, because only Device Info has a
long inventory to traverse.

A `body > .header` rule was briefly added to `ui/templates.css` to cover the
matrix pages and then removed: it was redundant, and being more specific than
matrix.css's `.header` it won the cascade and quietly dropped the header's
stacking order from 100 to 40.

Note for anyone reading matrix.css: it pins `.header` unscoped, so the
Encoders/Decoders/USB section headings are sticky as well. That is long-standing
behaviour and was left alone.

## The five-peer topology, read only

Recorded by a probe that blocks every mutating opcode at the transport, so the
reading cannot alter what it is reading. Eight USB endpoints answered:

    host  00:1B:13:05:50:50  192.168.100.135  fw 1.9.4   5 peers
      peer 00:1B:13:04:6A:EA  192.168.100.138  fw 1.9.4   (standalone)
      peer 00:1B:13:05:17:E9  192.168.100.134  fw 1.9.4   (standalone)
      peer B8:98:B0:07:85:87  192.168.100.250  fw 1.9.4   (integrated)
      peer B8:98:B0:07:85:C1  192.168.100.248  fw 2.0.9   (integrated)
      peer B8:98:B0:07:85:C7  192.168.100.108  fw 2.0.9   (integrated)

    unpaired  00:1B:13:04:E9:6E  192.168.100.128  fw 1.9.4
    unpaired  B8:98:B0:07:85:ED  192.168.100.246  fw 2.0.9

Each of the five peers reports the host back, so the tables are reciprocal and
consistent. Zero mutating commands were attempted by the probe, and the
automated suite opens no sockets at all, so nothing in this pass could have
changed it.

Two differences from the constants used as test fixtures are worth recording,
because the fixtures were written from an earlier reading and the hardware has
since moved: the host of the five-peer set is `00:1B:13:05:50:50` at .135, not
`00:1B:13:04:E9:6E`, and the standalone AT-OMNI-324 now answers at .138 rather
than .127. The fixtures are internally consistent and the suite does not depend
on these addresses being current, so they were left as they are rather than
edited to chase DHCP.

## The "Page Unresponsive" cause: observers re-armed without disconnecting

A Chrome "Page Unresponsive" dialog was reported on Configure. The cause was
found, measured, fixed and measured again.

`ui/sticky-columns.js` kept a `MutationObserver` on each sticky table so it
could re-measure the frozen-column offsets after a re-render. Its callback
cleared the guard flag and called `watch()` again -- and `watch()` created a new
`MutationObserver` and a new `ResizeObserver` without disconnecting the previous
ones. Every re-render therefore left the old observers attached to the same
table, and because each surviving callback re-armed once more, the number of
observers doubled per render.

Measured in the browser, callbacks delivered per batch of ten table re-renders:

    renders so far   10   20   30   40   50   60   70   80
    callbacks         2    4    8   16   32   64  128  256

Each callback calls `apply()`, which reads `getBoundingClientRect()` and so
forces a synchronous layout. The cost of one render therefore grew
exponentially with how long the page had been open. Configure rebuilds its
encoder and decoder tables with `innerHTML` every five seconds and its USB table
every ten, so a page left open accumulates renders steadily. That is a thread
that eventually stops answering, and it explains why the dialog appeared during
use rather than at load.

The fix keeps one `MutationObserver` per table for the life of the table -- the
table element is never replaced, only its rows are -- and replaces the
`ResizeObserver` on each re-render, disconnecting the previous one first,
because that one watches the leading cells and a re-render detaches them.

After the fix, the same measurement is flat at one callback per batch,
regardless of how many renders have happened.

`tests/sticky_observers_test.js` runs the module against a DOM stub that
implements enough `MutationObserver` and `ResizeObserver` semantics to count
instances and deliveries, and asserts the counts stay flat across 25 renders.
With the defect deliberately restored, that test does not merely fail: it
exhausts Node's 4 GB heap and aborts, which is the clearest available statement
of how steep the growth was.

No other observer in the application had this shape. The four in
`ui/index.html` and the one in `ui/appearance.js` are created once, on elements
that persist, and are frame-batched.

## Development diagnostics

`ui/diagnostics.js` is loaded by all four pages and is inert unless asked for:
`?diag=1` on any page, or `localStorage.setItem('omniDiag','1')`. Disabled, every
entry point is a function that returns immediately, nothing is patched and
nothing is logged.

Enabled, `omniDiag.report()` answers: which polling loops are alive and their
periods, total and outstanding requests with the concurrency high-water mark and
a per-endpoint breakdown, long-task count and duration bucketed at 100 ms /
250 ms / 1 s with the percentage of wall time the main thread was blocked,
observer callback counts by type, live interval timers and their periods, DOM
node count and heap size. It never writes to the console on its own; `report()`
and `log()` are the only output.

Note for anyone profiling this application from a headless harness: the browser
automation tool evaluates in an isolated world, so page globals such as
`omniDiag` are invisible to `page.evaluate`. Reaching them requires injecting a
`<script>` element, which the page runs in the main world, and returning the
result through a `data-` attribute. An earlier profiling attempt that read
`window.__diag` directly silently returned an empty object, and its reassuring
zeros meant nothing.

## Single-flight polling

Polling loops that sweep hardware now refuse to start a second copy of
themselves while one is still running. `/api/usb_extenders` performs live,
pairing and Icron network reads and can outlast its own ten-second interval, so
two loops needed the guard they did not have: Configure's USB table refresh, and
Device Info's USB endpoint count. Both call that same endpoint.

Discarding a stale *answer* is not the same as not asking the question twice.
Those two loops already had sequence counters that dropped an overtaken
response, which protected correctness but did nothing about the duplicated
sweep. The A/V Matrix decoder poll and the encoder settings poll already had
proper guards, and the Device Info unit poll has one; those were left alone.

## The application version has one source

`VERSION` at the repository root is authoritative. The server reads it in
`_app_version()` (an `OMNI_VERSION` environment variable overrides it, which is
how a build stamps a release), the launcher reads it in `resolve_version()`, and
the release workflow reads it with `cat VERSION`. `/api/config` exposes it as
`app_version`, and the support dump reports the same value.

Both displays now ask the server for it. They previously carried literals and
had drifted apart: the Device Info footer said V1.0.1 and the Settings dialog
V1.0.4 while the build was V1.0.6. `ui/settings.js` fetches `/api/config` once
per page and fills in `#cfg_app_version` and `#app_version_footer`; neither has
a version written into it, and a test asserts that no page or script contains a
version literal at all.

Current version: V1.0.7.

## Workspace is a constrained work area

The Settings layout hint described Workspace as "Navigation rail and context
inspector". Both were removed some time ago; the hint went on advertising them,
so the dialog offered two features the layout did not have. It now reads
"Constrained, centred work area", which is what the CSS actually does: a maximum
width, centred, with slightly tighter spacing.

The explanation of why the rail and inspector were removed stays in
`ui/templates.css` and in this document. That is history, and it is written as
history; only the text presented to the operator as current behaviour was wrong.

## The logo follows the theme

`ui/companylogo.png` is designed for a dark ground and is unchanged.
`ui/companylogo-light.png` is the black wordmark, used only in light mode. One
CSS rule in `ui/templates.css` does the swap:

    html.light .logo, body.light .logo { content: url("/ui/companylogo-light.png"); }

`content` replaces the rendered image of the existing `<img>`, so only the asset
actually in use is fetched, the `alt` text is untouched, and a browser that
ignores the property falls back to the dark asset already in the markup. Being a
CSS rule, it follows a theme change immediately, and no layout or colour preset
takes part in the decision. The two images share an aspect ratio (3.844 against
3.848), so the header geometry is unchanged.

This required a fix to how the theme is applied. The three matrix pages ran
their pre-paint theme script in `<head>`, where `document.body` is still null, so
the script deferred to `DOMContentLoaded` -- and the first paint used the dark
palette and the dark logo even when light was selected. The script now sets the
class on `document.documentElement`, which exists at that point, and still
applies it to the body once there is one.

Verified on all four pages in both modes: the light asset is used only when the
light class is present, the dark asset otherwise, both render at 185x48, and the
swap happens without a reload.

## USB Matrix: the REX identity column is frozen

The REX identity column (`th.row-head.rex-label`) is pinned to the left of the
grid so a route cell read at the far right can still be attributed to a device.
Route cells scroll underneath it.

`ui/matrix/matrix.css` already froze that column, but only under
`.matrix.sticky-enabled`, which the "Lock Headers" toggle writes and which
defaults to off -- so row identity was an opt-in the operator had to find first.
`ui/matrix/usb-matrix.css` makes the frozen column unconditional and settles the
three stacking orders that have to agree in one place: corner 40, LEX header row
30, frozen column 20. The header row itself stays toggle-gated, because that is
what the toggle is for.

The frozen column is the first column of every row, so its offset is `left: 0`.
No measured offset is needed and `ui/sticky-columns.js` is deliberately not
used here: it describes two or three leading columns and would publish a value
nothing consumes. The one dimension that is measured is `--usb-frozen-col-w`,
the rendered width of the column, taken from `getBoundingClientRect()` and
re-measured from one `ResizeObserver` on the corner cell plus `MutationObserver`s
for structural and layout changes. Measured 103px at 100% zoom and 129px at 125%.

A real defect was found while doing this. `.matrix-wrap` is the scrollport and
carries 8px of padding. A sticky cell pins to the scrollport's content edge, but
the container clips at its padding edge, so an 8px ribbon of route cells scrolled
visibly to the left of the identity column -- measured pinning at x=37 while
clipping began at x=29. Each frozen cell now plates that strip with a `::before`
expressed as "everything to my left, as far as the container allows", so it is
correct at any padding, template and zoom.

Verified with the grid widened to overflow: at maximum scroll the identity
column holds x=37..140 inside the wrapper, its background is opaque in both
modes (`rgb(29,35,42)` dark, `rgb(247,248,251)` light), a route cell is
underneath it, the topmost element at its right edge is the frozen cell, and the
corner wins its own hit test at scroll positions 0, mid and maximum in both
axes.

## Checking for a newer release

OmniSuite knows where its official releases live and can tell an operator that a
newer one exists. It is deliberately not a dependency: the application
discovers, scans, routes and upgrades with no Internet at all, and every failure
in this path is reported as "unable to check" and affects nothing else.

    repository   Hall-Research-Technologies/omniSuite
    endpoint     https://api.github.com/repos/.../releases/latest
    releases     https://github.com/Hall-Research-Technologies/omniSuite/releases

`/releases/latest` is used rather than the full list because GitHub already
excludes drafts and prereleases from it, so "the latest normal release" is the
endpoint's own definition rather than something reimplemented here. The release
TAG is what is compared; asset filenames are never used to decide a version.

The comparison is numeric, not lexical. `_version_tuple()` accepts an optional
leading `V` or `v`, pads to three components so `1.0` equals `1.0.0`, and
returns None for anything it cannot parse -- which is treated as "no update"
rather than as an update. `1.0.10` is newer than `1.0.9`, which a string
comparison gets backwards.

The request is made server-side. That keeps CORS out of it, puts the comparison
and the cache in one place, and means all four pages see the same answer.

Never a polling path. The result is cached for six hours; a page asks once when
Settings is wired, and again only when the operator presses Check Now. Startup
does not wait on it, Scan does not wait on it, and Settings renders before the
answer arrives -- a test asserts the dialog does not `await` the check. The
request carries a four-second timeout and no credential of any kind: this reads
a public endpoint, and a token is neither needed nor wanted.

A failure falls back to the last good answer, marked `stale`, so the dialog can
say "Up to date (last known)" rather than implying it just confirmed it. With no
previous answer it reports "Unable to check for updates". The Releases link is
always present and always usable, including when the check failed.

Only this repository's own URLs are ever handed to the browser.
`_is_official_release_url()` requires https, a host of github.com or
objects.githubusercontent.com, and for github.com a path under this owner and
repository. An unexpected `html_url` in a response is discarded in favour of the
known releases page, so the endpoint cannot be turned into an open redirect.

Platform assets are matched only when the platform is identified confidently --
windows, linux, macOS arm64, macOS x86_64 -- and the suffix is matched on a
token boundary so `x86_64` cannot be satisfied by `arm64`. An unidentified
platform offers no asset, because sending someone to the releases page is better
than handing them the wrong build. Nothing is ever downloaded or installed; a
test asserts the update-check code contains no download, unpack or subprocess
call.

Verified live against the real repository: with V1.0.7 installed and V1.0.6 the
latest published release, the check reports up to date. With the installed
version temporarily set to V1.0.5, it reports "New version available: V1.0.6",
links the validated release URL, and matches this machine's asset
(`OmniSuite-V1.0.6-windows.zip`) from the real release.

`UpdateCheckTests` covers 31 cases with the HTTP boundary stubbed -- same
version, newer patch/minor/major, installed newer than published, optional V
prefix, numeric ordering, malformed tag, draft, prerelease, no releases,
timeout, network failure, HTTP error, rate limiting, malformed JSON, missing
assets, all four platform selections, unknown platform, hostile asset and
release URLs, caching, forced refresh, cache expiry and stale fallback. Nothing
in the suite contacts GitHub, and run_tests.py's transport fences would block it
if it tried.

## Synchronized top and bottom horizontal scrolling

Application rule: if a section has a bottom horizontal scrollbar because its
content overflows, that same section also has a synchronized scrollbar at the
top, immediately above the content it controls.

`ui/mirror-scroll.js` is the single implementation. A scroll container opts in
with `data-mirror-scroll` -- explicit, never "every div that overflows" -- and
may also name an existing strip to adopt (`data-mirror-strip`), a class for a
created one (`data-mirror-class`), and a custom property to publish its height
to (`data-mirror-height-var`). Device Info uses all three, which is how it keeps
its own placement inside the locked command area while using the shared code.

Sections covered, measured at the widths where each overflows:

    /                  units_table_wrapper   998 / 1158, max 160
    /matrix/configure  Encoders             1222 / 1752, max 530
                       Decoders             1222 / 2713, max 1491
                       USB                  1245 / 1476, max 231
    /matrix/usb        LEX inventory         974 / 1124, max 150
                       REX inventory         974 / 1067, max 93
    /matrix/usb        route grid           hidden -- does not overflow at this device count
    /matrix            route grid           hidden -- does not overflow at this device count

Every shown mirror's `clientWidth`, `scrollWidth` and maximum `scrollLeft` equal
its container's exactly; a section that does not overflow shows no strip at all.

Each section is independent, with its own guard rather than a module-wide one --
a shared guard would let one section swallow another's scroll in the same frame.
Verified on Configure: driving the first section from its mirror moved only that
section (300/300 while the others stayed at 0/0), then driving the second from
its container moved only that one (700/700), and both were unchanged 400ms later.

Observers are created once per container, on the container itself, which is page
markup and is never replaced -- only its rows are. There are no timers; resize,
`visualViewport.resize`, `document.fonts.ready` and `load` cover the rest. This
matters because the mirror is geometry code added immediately after an
exponential observer-growth defect was found in the other geometry module, so
`tests/mirror_scroll_test.js` asserts the same invariant: 50 re-renders leave the
observer count unchanged and each batch of ten costs a constant number of
measurements.

## A rescan refreshes a known device; Clear Units is not a refresh

Reported live: an integrated HW-OMNI-E4521 USB endpoint showed Firmware N/A in
Configure although the device's own GUI reported a version, and scanning did not
fill it. Clear Units was deliberately not used, which is the correct
expectation: it is not a refresh mechanism.

Two defects, both found by investigating the hardware before touching code.

**The firmware was being read and discarded.** Every `usb_icron` reply carries
`revision`, the Icron module's own firmware -- queried directly on the affected
parent, it returns `2.0.9`, while the parent's OmniStream firmware is `2.1.2`.
`_record_usb_association()` took five fields from that reply and dropped it. The
only other source for USB firmware was the UDP Basic Query, and this endpoint
does not answer UDP at all: a directed `discover_ip` to `192.168.100.194` times
out, while its sibling at `.246` answers with the same `2.0.9`. With neither
source the field stayed empty, and the inventory correctly refused to substitute
the parent's version rather than state one the module does not have.

**An already-known device was never asked again.** `_refresh_usb_parent_map()`
returned immediately unless `pending` was non-empty, and `pending` only ever
holds units with no association at all. A device stopped being looked at the
moment it became known, so nothing a later scan could learn ever reached it --
and emptying the association, which is what Clear Units does, was the only way
to make the application ask. Worse, `/api/scan` never called it: the only
callers were startup and the USB discover endpoint.

Both are fixed. The association now carries `icron_revision` with the same
precedence as every other field it holds -- a fresh known value wins, otherwise
the remembered one survives -- so a read that fails or omits the field cannot
erase a version already verified. An explicit Scan dispatches a forced refresh
on the background executor, so discovery stays responsive.

A forced refresh re-reads **every** USB-capable parent, not only the ones with
something missing. Selecting only incomplete records was the obvious economy and
it was wrong: once a field had been learned the parent was never asked again, so
a value that CHANGED on the device could never arrive. An explicit Scan means
"converge on what the hardware says now", which includes replacing a value
already held; the merge rule keeps that safe. Background polling keeps the
pending-only behaviour, so this is a sweep the operator asked for and never a
per-poll one.

Merge precedence, applied consistently:

    fresh known value  >  cached known value  >  missing fresh value

The device-cache side already worked this way -- `_merge_unit_records` takes a
fresh field only when it is non-empty, and `_reconcile_discovered_units`
replaces a rediscovered MAC rather than keeping a second row -- so the gap was
specific to the USB association path.

Verified on the bench, without Clear Units. Before: all five integrated
endpoints reported an empty USB firmware. After one ordinary Scan: every one
reported its own version with source `usb_icron` -- `2.0.9` for four of them and
`1.9.4` for `B8:98:B0:07:85:87`, which matches what that endpoint reports over
UDP, so these are per-device values and not a constant. The parent's `2.1.2` is
untouched on every row.

Configure picks it up on its own poll: with the page already open and showing
N/A, a Scan replaced both cells with `2.0.9` within six seconds and
`performance.getEntriesByType("navigation").length` stayed at 1 -- the page was
never reloaded. The workbook's USB sheet, the extender view and the support dump
all report the same value, and the dump now carries `usb_firmware`,
`usb_firmware_source` and a `field_authority.usb_firmware` entry beside the
parent's own firmware. The value survives a restart, and a second scan still
leaves exactly one record per canonical USB MAC.

No storm. In that run one parent-map refresh was logged, labelled
"(operator scan)", asking the two parents once, while nine `/api/usb_extenders`
polls in the same window produced no additional device reads.

## HW-OMNI is the name, AT-OMNI is the protocol identity

A standalone extender answers the role byte in its UDP reply with the strings
`AT-OMNI-311` (host) and `AT-OMNI-324` (device). That value is the device's
internal identity inside OmniSuite: `_usb_matrix_axes` sorts LEX from REX by
comparing against `omni_usb_extender.HOST_MODEL` / `DEVICE_MODEL`, the bench and
route guards refuse a reversed pair by the same comparison, and
`_usb_standalone_inventory` chooses which list a row belongs in the same way.
Rewriting that string would not produce a rename; it would produce a silent
mis-classification, because every one of those comparisons would simply stop
matching.

The product is sold as HW-OMNI. So `USB_DISPLAY_MODELS` maps the two protocol
identities to `HW-OMNI-311` and `HW-OMNI-324`, `_display_model()` is the only
thing that applies it, and every rendered surface goes through `display_model` /
`display_hostname`:

| Surface | Field |
|---|---|
| Configure > USB inventory | `display_model`, `display_hostname` |
| USB Matrix axes and row labels | `model`, `host` on the standalone entries |
| Inventory workbook, USB sheet | `Model`, `Hostname` |
| Support dump | `model`, `hostname`, beside `udp_reported_type` |
| Route capability notes, refusal messages, user guide | literal HW-OMNI text |

`device_type` and `udp_reported_type` keep the protocol name everywhere,
including in the support dump, so a report can still be matched against what the
hardware actually said.

**The Hostname column was never a hostname.** These units expose no hostname
over UDP, so that column has always held OmniSuite's own label, and
`_display_model` of the device type is what it now holds. A name the operator
has assigned wins over it and is never overwritten -- no UI sets one today, and
`_display_hostname` is written so that when one does, the generated label gets
out of the way.

Verified live on the bench with the real five-peer topology attached: every
standalone row on Configure > USB and the USB Matrix reads HW-OMNI-311/324, and
no page contains the string `AT-OMNI-311` or `AT-OMNI-324` anywhere in its
rendered text, while `/api/usb_extenders` still reports `udp_reported_type`
`AT-OMNI-311` / `AT-OMNI-324`, the roles are still `USB Host / LEX` and
`USB Device / REX`, and the five peers on `00:1B:13:04:E9:6E` are unchanged.

The naive change is caught. Globally replacing `AT-OMNI-311` / `AT-OMNI-324`
through `omni_usb_extender.py` and the server -- 35 occurrences -- fails five
tests across three layers: the protocol decode test, parent-association
override, standalone correlation, the identity assertion and the support dump.
Making `_display_model` a no-op fails eight, covering Configure, the Matrix, the
workbook and the dump.

## The Settings icon alerts once per release

`/api/update_check` answers with one of three explicit states, and the UI
switches on the state rather than parsing the text:

| `status` | Meaning | Settings shows |
|---|---|---|
| `UPDATE_AVAILABLE` | A published release is strictly newer | `UPDATE AVAILABLE -- V1.0.8`, on the warning token |
| `CURRENT` | Nothing newer is published | `No update available -- V1.0.7 is current.` |
| `UNABLE_TO_CHECK` | The request failed, or the tag could not be parsed | `Unable to check for updates.` |

`CHECKING` exists only in the browser, shown while a request it started is in
flight; the server never returns it, because a server that is answering has
finished checking. A build ahead of the newest published release -- V1.0.7
installed against V1.0.6 published -- is `CURRENT`, `ok: true`, with no error.

Separately from the status, `alert` says whether the gear should draw attention.
It is true only for an `UPDATE_AVAILABLE` whose version has not been
acknowledged. Opening Settings posts `/api/update_ack`, which records the
version in `notice_ack.json` under `acknowledged_update`. Because the mark is a
version and not a flag, re-checking the same release stays quiet, a restart
stays quiet, and a newer release announces itself; `/api/update_ack` also
refuses an unparseable version and never moves the mark backwards, so a late
acknowledgement of an older release cannot re-arm a newer one.

The acknowledgement lives in `notice_ack.json` rather than in `UI_PREFS`
precisely because Reset Appearance deletes that file wholesale -- an icon that
started flashing again because someone changed a colour would be nonsense. Clear
Units does not touch it either.

The pulse is drawn entirely with `box-shadow` so the icon's own box never
changes size; animating width or padding would reflow the navigation twice a
second for as long as the update went unacknowledged. Under
`prefers-reduced-motion` the animation is replaced by a static ring rather than
removed -- the point is still to be noticed.

One check runs at startup, on a daemon thread nothing joins, and its result is
cached for six hours, so opening four pages asks GitHub nothing. Exactly two
call sites exist -- the endpoint and that startup prime -- and a test asserts
it, so no polling path can acquire one.

Verified live end to end against the real GitHub API: V1.0.7 installed against
the published V1.0.6 returns `CURRENT`, rendered in the browser as
"No update available -- V1.0.7 is current.", with the gear carrying no alert
class and zero console errors. With the provider temporarily stubbed at V1.0.8,
the gear rendered `gear-btn update-alert` running `omni-update-pulse` and
announced itself as "Settings -- update available V1.0.8"; opening Settings
stopped it and showed `UPDATE AVAILABLE -- V1.0.8`; navigating Configure, A/V
Matrix, USB Matrix and Device Info did not bring it back, nor did restarting the
application; stubbing V1.0.9 alerted again with the acknowledgement still
reading V1.0.8. The stub and the acknowledgement file were then removed and the
server source restored byte-for-byte (MD5 match against a pristine copy taken
beforehand), and the live check returns `CURRENT` again. No release was
published and no release asset was created.

## The Compact layout reached one page out of four

Found by measuring the shell across sections rather than across reloads. Each
page was pixel-identical to itself over five loads, but the header was 46px tall
on Device Info and 57px on the three Matrix pages, so the Settings icon moved
5px every time the operator changed section.

`data-template="compact"` was correctly set on the root of all four pages. Only
Device Info carried `body.compact` -- and the density rules are written against
that class: 42 of them in `matrix/matrix.css` and `matrix/usb.html`, plus 21
more inside `index.html`. On the Matrix pages every one of those rules was
inert, which is why their logo stayed at its full 48px while Device Info's was
38px.

The cause was five copies of one bridge, four of them reading a preference that
no longer exists. `viewDensity` was retired when the layout templates replaced
the old density switch; `appearance.js` still reads it once to migrate an
existing choice and deletes it on reset. Left behind were:

| Location | What it did |
|---|---|
| `index.html` `initDensity()` | mirrored the template onto the body -- the only copy that was still correct |
| `matrix/matrix.js` `initDensity()` | applied `viewDensity`, and drove a `density_toggle` control that no longer exists in any page's markup |
| `matrix/usb.js` `initDensity()` | applied `viewDensity` |
| the pre-paint block in each of the four pages and the user guide | applied `viewDensity` before first paint |

Because the retired key reads as absent, the last four all resolved to "not
compact" and cleared the class `appearance.js` had set. Whether Compact survived
on a given page came down to which `DOMContentLoaded` handler ran last -- which
is why Device Info, whose copy was the correct one, was the page that worked.

`appearance.js` now owns density: `applyAppearance` toggles `body.compact` from
the template alongside the attribute it already sets, and it already re-runs on
`DOMContentLoaded` and on the cross-tab storage event. The other four copies are
gone, and the pre-paint blocks keep only their light/dark half -- that part has
to be settled before the first frame, whereas density cannot be, because
`body.compact` rules need a body and by the time there is one `appearance.js`
has already applied the template.

Verified live on all four pages: `data-template` and `body.compact` agree
everywhere, the logo is 38px everywhere, and the Settings icon is at exactly the
same coordinates on every page across five loads each. Switching the stored
template to `classic` through `/api/ui_preferences` clears `body.compact` on all
four and restores the 48px logo; switching back to `compact` restores it, and
the operator's stored template, preset and light background were compared
before and after and are identical. A residual 3px of header height remains
between Device Info and the Matrix pages -- the Matrix header is `position:
sticky` with a `border-bottom` and `padding-bottom`, which Device Info's is not.
That is a deliberate difference in those pages' design, and it does not move any
control.

## Device debug logs: what get_debug_info actually does

Determined on the bench rather than assumed. The WebSocket method

```
{"id": "get_debug_info-method", "username": ..., "password": ...,
 "method": {"get_debug_info": {}}}
```

answers, on every generation present -- at-omni-111, at-omni-121, hw-omni-e4111,
hw-omni-e4111-wp, hw-omni-d4111, hw-omni-d4511 and hw-omni-e4521 -- with a
**path, not a log**:

```
{"error": false, "id": "get_debug_info-method",
 "reply": {"path": "/debug/<hostname>-debug.vdf"}}
```

The bundle is then fetched over plain HTTP from that same device. Four facts
shaped the implementation:

| Observation | Consequence |
|---|---|
| `Last-Modified` is the moment of the call | the device builds the bundle on demand; there is nothing to cache, and two clicks mean two builds |
| 9.9-12.3 MB across seven devices | streamed rather than buffered, with a size bound |
| The file begins `Salted__` | it is OpenSSL-encrypted by the manufacturer; OmniSuite passes it through and cannot inspect it |
| The HTTP GET needs no authentication | the WebSocket method is the authenticated step; the fetch is a plain GET against an address we already trust |

It is observational. Baselined before and after on a bench encoder, no
configuration field changed and uptime advanced only by the elapsed time. Fan
speed and die temperature moved while the bundle was built, which is the device
working rather than a state change.

`GET /api/device_log?ip=<address>` performs both steps server-side and streams
the result to the browser with a `Content-Disposition` filename of
`hostname_IP_timestamp.vdf`. The browser sends an address and receives bytes; it
never sees a credential, and `_device_credentials` supplies the same
primary-then-fallback ladder discovery uses, through the shared
`_ws_send_recv_with_fallback` rather than a second implementation.

**The path is untrusted input.** `_safe_device_log_path` refuses a scheme, a
host, a protocol-relative `//`, any `..`, a backslash, a control character,
anything over 512 characters and anything outside an explicit character set --
refuses rather than repairs. The URL is then built as `http://{ip}{path}` using
the address the operator asked for, so nothing the device says can retarget the
request. The download filename is built entirely from data OmniSuite already
holds; `_device_log_filename` strips everything outside `[A-Za-z0-9._-]` and
renames the Windows reserved device names, which are refused with any extension.

Strictly operator initiated. Exactly one call site exists, and a test asserts
the list; further tests assert that `api_scan`, `start_background_startup_tasks`,
`api_ts_export`, `_inventory_workbook`, `api_poll` and `_usb_extender_view`
contain no reference to it.

## What the Device Info USB column was counting

`found_count` was `len(usb_icron["found_devices"])`, rendered as `LEX \u00b7 8`.
Read from eight live endpoints, `found_devices` is the endpoint's **USB-over-IP
neighbour table**:

| Endpoint | USB subnet | found | paired | linked | contains itself |
|---|---|---|---|---|---|
| E4521 (LEX) | first bench subnet | 8 | 1 | 0 | no |
| E4521 (LEX) | first bench subnet | 8 | 0 | 0 | no |
| D4511 (REX) x3 | first bench subnet | 8 | 1 | 0 | no |
| E4521 (LEX) | second bench subnet | 2 | 3 | 2 | no |
| D4511 (REX) x2 | second bench subnet | 2 | 1 | 1 | no |

Four things follow, and each rules out an interpretation:

* It **never contains the endpoint itself**, so it is not an endpoint count
  including self.
* It is **identical for every endpoint on a segment** -- 8 on one bench subnet,
  2 on the other -- so it describes the network, not the device. Two E4521s of
  the same model report 8 and 2 depending only on where they sit, so it is not
  capacity, not a port count and not a protocol version.
* A LEX's list contains other LEX units and a REX's contains other REX units, so
  it is not a list of pairable peers.
* It can be **smaller than that endpoint's own paired count** -- one bench LEX
  reported 2 found against 3 paired, the third peer being on another subnet --
  so it is not a superset of pairing either.

It is a well-defined value with no per-device meaning, so the column now shows
the **role alone**. The counts that are about the device came from the same
reply and cost nothing: `paired_count`, `linked_count`, `usb_ip` and
`host_port` are now on the poll payload and appear in the cell's tooltip, each
named, alongside the neighbour count described as what it is. Role, configured
peers, live links, capacity, ports and revision stay six separate fields.

## One control system for four pages

Measured before changing anything: with the Rose preset active, three families
of control were painted `rgba(30, 144, 255, ...)` -- the active tab, every
toggle switch, the row-hover tint, the pending-write ring, the video-wall
selection, the USB route cells, the folder browser and the two firmware icon
buttons, whose blue was in a `style` attribute where no stylesheet could reach
it. The `.tab` rules existed in four copies, one per page, and `toast()` existed
twice with Device Info having none at all and falling back to `alert()`.

`ui/templates.css` now owns the control system. Every colour is derived from the
nine semantic roles a preset sets:

| Token | Derived from | Used for |
|---|---|---|
| `--selected-fill` | `--state-selection` | the fill of a selected control |
| `--selected-tint` / `--selected-tint-strong` | `--state-selection` | selected backgrounds and row hover |
| `--selected-on` | `--on-selection` | a label on a selected fill |
| `--focus-ring` | `--action-primary` + `--text` | the keyboard focus ring, visible on a bare surface and on an accent fill alike |
| `--danger-ring` | `--state-error` | the ring that makes a destructive action destructive |
| `--control-hover-veil` / `--control-press-veil` | `--text` / black | hover and pressed |

Two rules hold throughout. Nothing names a hue, so all ten presets and both
palettes are covered by construction. And no state changes a control's box:
hover, press, focus and selection alter background, colour, box-shadow and
outline only.

**Destructive actions are distinguished by form, not only by hue.** Measured as
CIELAB deltaE between each preset's accent and its critical colour:

```
contrast 133  default 109  ocean 108  cool 97  forest 80
slate 57  plum 51  soft 40  warm 29  rose 11
```

Rose -- the preset in use -- puts a destructive button 11 units from a primary
one, which is not a difference an operator can act on. Editing the palette was
the wrong fix; `.btn.danger` now carries an inset ring derived from the critical
colour that no other button has, so the distinction survives any palette.

### What the 80-combination sweep measured

Ten presets x four pages x light and dark, sampled through a main-world bridge
because the harness evaluates in an isolated world and `applyAppearance` is a
page global. Presets were applied with the same non-persisting call the preset
chooser uses on hover, so the operator's stored choice was never written.

Three defects were found by measurement and fixed:

| Defect | Before | After |
|---|---|---|
| Active tab label painted in the accent on a pale tint | 1.90:1 in light/Default | 5.38:1 worst case |
| Active tab fill at 20% of the selection colour | deltaE 3.5 worst, 17.5 median | deltaE 7.9 worst, 40.6 median |
| Every button on the three Matrix pages labelled in `--text` on an accent fill | 2.78:1 in dark/Default | 4.55:1 worst case |

Final figures across all 80: destructive label contrast at least 4.51:1;
selected-versus-unselected deltaE at least 7.9 for tabs (which also carry a
full-strength 2px underline and a coloured edge), 33.2 for the routing mode
selector and 25.4 for the toggles; the destructive ring present in all 20
combinations where a destructive button exists. The `switch` label figure is not
meaningful -- a toggle contains no text.

Interaction states were measured by forcing the pseudo-class through the
debugging protocol. A synthetic mouse move does not set `:hover` in this
headless context, and the first attempt read `matches(':hover') === false` and
would have reported working rules as absent. Across nine control families --
primary, destructive, secondary, selected and unselected tabs, the gear, the Log
button, and the selected and unselected mode buttons -- hover applies the veil,
pressed applies an inset shadow, focus-visible draws a preset-derived ring, and
**the box is identical in all four states**. Disabled reads opacity 0.45,
`cursor: not-allowed` and `saturate(0.4)`, at the same size.

Moving a selection changes no geometry at all: toggling the tab, mode and switch
selections on each of the four pages and re-measuring 44 elements -- the header,
every tab, every mode button, every switch and the first table -- produced **zero
changes**.

### Accessibility

Every toggle switch's real `<input type="checkbox">` was `display:none`, which
removes it from the tab order **and** from the accessibility tree: none of Poll
Units, Hide Device Details, Lock Headers, Preview, Auto-Refresh or the eleven
Configure section filters could be reached or announced without a mouse. They
are now visually clipped and focusable, with the focus ring drawn on the visible
switch through `input:focus-visible ~ .switch`.

The page tabs carried no state at all and now use `aria-current="page"`, which
is the native mechanism for a link to the current page. The A/V routing mode
selector is a `role="radiogroup"` of three `role="radio"` buttons, and `setMode`
sets `aria-checked` alongside the class -- painting the class alone left a
screen reader saying nothing was selected while the screen showed one clearly
was.

### The shared toast

`toast()` lived in `matrix.js` and `usb.js` as byte-identical copies, styled in
a fixed near-black in `matrix.css`, and Device Info had neither. It is now
`ui/toast.js`, loaded by all four pages, with its styling in `templates.css` so
it follows the preset: neutral for information, the success colour for a
confirmation, the critical colour plus the destructive ring for a failure. It
announces itself politely, or assertively for an error, and renders with
`textContent` because these messages routinely carry a hostname or an error
string that came from a device.

## The engineering notice on a device log

A `.vdf` bundle is encrypted by the manufacturer, so an operator who downloads
one and expects to open it has wasted the trip -- and may forward it somewhere
that cannot use it. Clicking **Log** therefore asks first:

> Device logs are encrypted and can only be decrypted and read by Engineering.

**OK** proceeds; **Cancel**, Escape and the backdrop all mean no request. Cancel
holds both the focus and the primary treatment, so an accidental Enter cannot
start a ~10 MB download -- and the dialog does not look like OK is the obvious
choice while Enter does the opposite. The notice is shown every time and is
never remembered: it explains a property of the file, not an event to
acknowledge, and that property is as true on the tenth download as on the first.

It uses the shared `omniConfirm`, which gained two opt-in options. Neither
changes any existing caller, and a live check confirmed Reboot Selected and
Remove Selected still focus OK with the danger treatment:

| Option | Effect |
|---|---|
| `defaultAction: 'cancel'` | Cancel takes the focus and the primary styling; OK becomes secondary |
| `requireDialog: true` | If the dialog markup is missing, resolve `false` instead of falling back to `window.confirm` -- a browser string box would not carry the notice, and the notice is the point |

The flow moved from an inline handler in `index.html` into `ui/device-log.js`.
That is what makes it testable: `tests/device_log_test.js` runs the real module
against a scripted dialog and a recording `fetch`, with `window.confirm`,
`alert` and `prompt` installed as throwing stubs, so thirteen checks measure
behaviour rather than reading source. The single-flight lock is taken *before*
the dialog opens -- two notices stacked on one device would each go on to ask
for a bundle, and the device builds a fresh one per request.

Verified live against the real dialog, recording every backend request:

| Action | Requests | Result |
|---|---|---|
| Click Log | 0 | dialog shown, `role="dialog"`, `aria-modal="true"`, focus on Cancel |
| Cancel | 0 | dialog closed, button back to "Log", enabled |
| Escape | 0 | dialog closed |
| OK | **1** | one download, `hw-omni-d4511-085c6_<ip>_<stamp>.vdf` |
| Six clicks during the download | still 1 | button `...`, disabled, `aria-busy`, **44 px in both states** |
| Click Log again | -- | the notice is shown again |

Zero native dialogs were raised and zero console errors occurred. Six mutations
were made and all six were caught: skipping the notice, ignoring Cancel, falling
back to a native confirm, taking the lock too late, showing the notice only once
per session, and removing the pending feedback.

## V1.0.7 release state

### Control hierarchy

Every actionable control on the four pages and in the dialogs was classified by
what it does. Before this, Device Info showed seven accent-filled buttons across
its command bar and footer, and the three Matrix pages filled every bare
`<button>` with the accent because `.btn, button` in `matrix.css` makes that the
default.

| Role | Controls |
|---|---|
| Primary | Scan, Upgrade Selected, and the principal action of each dialog (Sync, Apply, Upload, Apply, Continue, the confirmation's OK) |
| Secondary | Clear Units, Sync all Passwords, Set NTP, Slate Manager, Inventory, Check Now, Releases, TS Dump, User Guide, Close, Cancel, Refresh, Reload Cache, the filter Clears, the config exports, the reveal toggles, the folder-browser helpers, Log |
| Danger | Reboot Selected, Remove Selected, Apply and Reboot, Delete slate |
| Icon / utility | Settings gear, firmware refresh and folder, Blink |

The Matrix pages have no principal action, so nothing on them is primary. Their
controls opt into `button.secondary` rather than the bare-button default being
changed, because the generated encoder and decoder panels rely on that default
for their own Save buttons; those panels' Close and Refresh are named in the
shared sheet instead.

Measured across all ten presets in light and dark: primary against secondary is
at worst deltaE 28.5; destructive against primary is as little as deltaE 11.0 in
Rose, which is why `.btn.danger` also carries an inset ring derived from the
critical colour -- present in every one of the eighty combinations. Every label
contrast is at least 4.51:1, and selected-versus-unselected is at worst deltaE
14.7 for tabs, 33.2 for the routing mode selector and 25.4 for the toggles.

A second, partial `.btn.secondary` was found in `settings.css` setting only the
background. It loaded after `templates.css` on every page, so it won, and a
secondary control kept the primary label colour. Removed.

### Native dialogs

Fifteen `alert()` calls remained on Device Info. Each was classified rather than
swapped:

* **Twelve became toasts** -- codec update failure, scan validation, clear
  failure, firmware folder opened/failed, export failure, firmware and selection
  validation, reboot failure, remove failure, thumbnail toggle failure.
* **Two became an acknowledgement modal**: the incompatible-firmware list and
  the upgrade rejection, both of which name devices the operator has to read
  before acting. A list that scrolls away after two seconds is not something
  anyone can act on.
* **None became a toast that had been a required confirmation.** Reboot Selected
  and Remove Selected keep their confirmations unchanged.

`omniConfirm` gained a third opt-in option, `acknowledge`, which hides Cancel --
a message has one answer -- and restores it for the next caller. It now also
records the element that opened it and returns focus there on close.

The one native call that remains is `omniConfirm`'s own fallback for missing
dialog markup. It is unreachable while `#confirm_backdrop` exists, and any
caller that must not degrade passes `requireDialog: true`. Both are asserted.

### The User Guide

It carried its own palette, its own light/dark script and four hard-coded blues,
so it was the single surface that ignored the chosen preset. It now loads
`templates.css` and `appearance.js` and declares only what is specific to a
document: reading measure, print rules and callouts.

Colour is deliberately sparse -- this is documentation. Links use the
information role rather than the accent, and the table header is a quiet surface
rather than a tint. Measured across all ten presets in light and dark, every
text contrast is at least 9.37:1.

The three callouts kept their distinct meanings, but the tints alone converge to
deltaE 2.8 in Warm, where warning and critical sit close. Each now carries a 4px
left rule in its role colour at full strength, where the same pair is deltaE
21.1 apart.

### Polling log volume

Per-poll INFO tracing survived the earlier idle-logging pass because polling is
operator-enabled rather than idle. Measured during the release soak with polling
on: about ninety INFO lines a minute from fifteen devices on a five-second
cadence -- enough to bury a mutation, a refusal, or a device that stopped
answering. Nine per-poll traces are now DEBUG, and the one line that reports a
change -- a firmware version that actually differs from the cached one -- stays
at INFO and has moved inside the branch that establishes there was a change.

### Known issues deliberately left alone

| Item | Measurement | Decision |
|---|---|---|
| Header height differs between sections | 48px on Device Info, 57px on the three Matrix pages; the difference is exactly their sticky header's `border-bottom: 1px` plus `padding-bottom: 8px`. The Settings gear is at identical coordinates on all four pages. | Left. The border is the boundary content scrolls under on a sticky header; removing it to equalise a number would be a visual regression. |
| Configure USB filter said to scroll with the table | Does not reproduce. The filter input is not inside a horizontal scroller and its x-coordinate is 348 both before and after scrolling the tables fully right. | No change. |
| A/V Matrix Lock Headers versus frozen identity columns | Lock Headers is an operator option on the routing grid; the frozen identity column is a Configure and USB Matrix feature. No defect observed. | Left. |
| Mirrored scrollbar costs ~12px per overflowing section | Strips measure 12px and are hidden when a section does not overflow. | Left. Recovering it needs overlay or geometry-polling complexity the brief rules out. |
| Retired appearance code | `Inspector`, `rail`, `density_switch`, `density_toggle` and the granular colour controls have no remaining references; the only matches are comments recording that they were removed, and `viewDensity` survives solely as the one-time migration read and its removal on reset. | Nothing dead to remove. |
| `config.json` is tracked with `password` / `fallback_password` | The values were the documented defaults; the file is not in the PyInstaller `--add-data` list, and the server reads `DATA_DIR/config.json`, not the repository copy. | **RESOLVED.** Untracked with `git rm --cached`, added to `.gitignore`, and replaced by a tracked `config.example.json` holding `CHANGE_ME` placeholders and an empty firmware path. The local file is untouched, so nothing about a running installation changed. `TrackedCredentialTests` keeps it out. |
| `build/windows/omni_version_runtime_hook.py` contains `V1.0.1` | `build/` is gitignored and `tools/build_release.py` regenerates the hook from `VERSION` on every build. | Stale local artefact, not a packaging defect. |

## V1.0.8 backlog

Identified during the V1.0.7 wrap-up and deliberately not implemented. None of
these blocks the release.

| Item | Why it was deferred |
|---|---|
| Untrack `units_cache.json`, `scan_results.json` and `units_view.csv` | They are runtime data rather than source, and a checkout starts with one machine's device list. They hold no credential, so this is tidiness rather than safety, and it changes what a fresh clone looks like. |
| Give the generated encoder/decoder panels a full hierarchy pass | Their Close and Refresh buttons are now secondary, but the panels were not otherwise audited. Their markup is built in `matrix.js`, so the change is larger than a class on a tag. |
| Equalise the header height across sections | 9px, caused by the Matrix header's sticky border and padding. Purely cosmetic; the Settings gear is already pixel-identical everywhere. Worth revisiting only alongside a deliberate shell-layout change. |
| Recover the mirrored scrollbar's 12px | Needs overlay or geometry-observation complexity that is not worth 12px. |
| Move the remaining `[POLL]` DEBUG traces behind the diagnostics flag | They are DEBUG now, so they cost nothing at the default level; folding them into `ui/diagnostics.js` would put all instrumentation in one place. |
| A keyboard focus trap inside modals | Focus enters the dialog and returns to the invoking control, and Escape closes it. Tab can still leave the dialog for the page behind it. |

## User Guide: final V1.0.7 accuracy review

Reviewed section by section against the shipping build. The guide's structure,
appearance integration and existing content were preserved; four inaccuracies
were corrected.

| Found | Correction |
|---|---|
| The USB-column section explained, at length, the withdrawn `LEX · 8` presentation -- and reproduced it | Replaced with two operator tables: what LEX and REX mean in practice, and what each tooltip line tells you. A `warn` callout now states plainly that *paired* is configuration and *linked* is a live connection, and that a configured route is not evidence USB traffic is flowing. |
| Settings quoted update states the application no longer uses -- *Up to date* and *New version available* | Corrected to the three strings the build actually shows, with the note that a build newer than the latest published release is current rather than an error. The Settings-gear pulse and its once-per-release acknowledgement are now documented. |
| `notice_ack.json` was missing from "Files Created on Launch" | Added, with what it holds and what happens if it is deleted. |
| The Log section explained OmniSuite's internal credential handling | Trimmed to what an operator needs: the file is encrypted, not for local reading, and no device password is written into it. The troubleshooting note now says the Primary and Fallback passwords must match the unit, without describing the mechanism. |

Verified on the rendered page: 12 sections, 19 tables, 12 contents links, **zero
broken anchors**, the logo loads, zero console errors and zero failed requests.
No table has an inconsistent column count and **no table cell clips**; the
document never scrolls horizontally. Across five presets in light and dark,
every element measures at least **10.22:1** contrast, and the three callouts stay
**deltaE 21.1** apart at worst. Print mode renders dark text on white with the
navigation hidden.

The guide contains no reference to the retired Inspector, navigation rail,
granular colour pickers or density controls, no `AT-OMNI-3xx` name, and `V1.0.7`
is the only version literal in it.

**There are no screenshots in the User Guide** -- it describes controls by name
and location in prose and tables -- so there is no image to go stale. The single
image is the header logo, which is theme-aware and loads.

## V1.0.7 release engineering

### The release matrix as it now stands

| Target | Runner | PyInstaller | Output | Artifact |
|---|---|---|---|---|
| Windows x86-64 | `windows-latest` | `--onefile --windowed` | `OmniSuite.exe` | `OmniSuite-{v}-Windows-x86_64.zip` |
| macOS arm64 | `macos-latest` | `--onedir --windowed --target-architecture arm64` | `OmniSuite.app` | `OmniSuite-{v}-macOS-arm64.zip` |
| macOS x86-64 | `macos-15-intel` | `--onedir --windowed --target-architecture x86_64` | `OmniSuite.app` | `OmniSuite-{v}-macOS-x86_64.zip` |
| Ubuntu x86-64 | `ubuntu-latest` | `--onedir --windowed` | `dist/linux/OmniSuite/` | `OmniSuite-{v}-Ubuntu-x86_64.tar.gz` |

Intel macOS is built on its own Intel runner rather than cross-compiled, because
`--target-architecture` can only produce what the interpreter supports.

### What changed

* **The firmware folder is no longer bundled.** It is an operator-chosen path
  set in Settings, and packaging it produced a 981 MB executable on a machine
  with images present -- 908 MB of it the manufacturer's firmware. The same
  build is 29.7 MB without it. CI never saw this because `firmware/` is
  gitignored.
* **Artifacts state their architecture**, and say Ubuntu rather than Linux.
  `_platform_asset_key` changed with them: the keys are now
  `windows-x86_64`, `macos-arm64`, `macos-x86_64` and `ubuntu-x86_64`. Without
  that change a Windows machine would have matched `-x86_64.zip` and been
  offered the macOS Intel build. `ArtifactNamingTests` pins the build script and
  the matcher together, and an ARM Linux machine is now offered nothing rather
  than an x86-64 binary.
* **The full suite runs on all four targets**, not only on Ubuntu.
* **Every artifact is smoke-tested after it is built.** `tools/smoke_release.py`
  starts the packaged executable, reads the port the launcher bound from its own
  log, then checks `/api/config` reports the expected version and that twelve
  pages and assets return content. Linux runs it under Xvfb because the launcher
  is a graphical window.
* **Architecture is verified, not assumed** -- `file` on the built binary
  checked against what the matrix row claims, and a PE machine-type check on
  Windows.
* **SHA-256 per artifact**, concatenated into `SHA256SUMS.txt` at publish time
  and re-verified with `sha256sum -c` before the release is created.
* **Publication requires all four artifacts.** `needs: build` already meant one
  failed leg stopped the release; the publish job now also refuses if any
  declared artifact is missing, so a partial set cannot ship.
* **Release notes come from `RELEASE_NOTES_V1.0.7.md`** rather than being
  generated from commit messages.

### The launcher needs a display

`main()` creates a Tk window unconditionally; there is no headless mode. That is
why the Linux smoke test runs under Xvfb, and why the README says Ubuntu needs a
desktop session. `OMNI_SMOKE=1` suppresses only the automatic browser launch, so
a build runner does not open one; nothing else reads it.

### Repository hygiene

`units_cache.json`, `scan_results.json` and `units_view.csv` were tracked and
carried the bench's addresses, MACs, hostnames and stored device passwords. The
committed values were the documented default password, so nothing secret was
published -- but the next scan against a fleet with a real password would have
committed it. They are untracked and ignored, like `config.json` before them.

### V1.0.7 repository hygiene pass

The public repository now carries 62 files: application source, the launcher,
the protocol implementation, the build and smoke tooling, the test suite, the
CI workflow, the README with its screenshots, the User Guide and the release
notes. Everything else was proven unreferenced before it moved.

| Issue | Evidence | Status |
| --- | --- | --- |
| `ui/archive/` tracked, and inside `--add-data ui`, so fifteen superseded pages shipped in the executable | The build's own `PKG-00.toc` listed all fifteen | **RESOLVED.** Moved to `archive/ui-superseded/`, untracked, and `assert_package_contents` now reads the TOC back and refuses the build if any forbidden directory reappears. |
| 16 one-off `fix_*` / `add_*` / `debug_*` scripts tracked | No import, subprocess call, workflow step or documentation link referenced any of them; the files they patched have since been rewritten | **RESOLVED.** Moved to `archive/dev-scripts/`. |
| `units_cache - Copy.json` tracked, holding 13 real bench MAC addresses | Unreferenced; the server reads `DATA_DIR/units_cache.json` | **RESOLVED.** Moved to `archive/fixtures/` and untracked. |
| `ui/matrix/omnimatrix.ico` shipped as a byte-identical duplicate of the root icon | `resolve_asset()` searches `ui/<name>`, never `ui/matrix/`, so the copy was unreachable | **RESOLVED.** Archived; 162 KB no longer in the binary. |
| The application's own source was largely untracked -- `run_tests.py`, `omni_usb_extender.py`, `tools/smoke_release.py`, every `tests/` file and most of `ui/` | A clean clone could not test or smoke the build | **RESOLVED.** Staged for publication; the clean-clone test now installs, tests and builds from the publication set alone. |
| `artifacts/` (4.8 GB) untracked but not ignored | A `git add .` would have committed bench QA baselines and the device manufacturer's own web-app bundles | **RESOLVED.** Ignored. |

`RepositoryHygieneTests` keeps all of this from regressing: firmware, the
archive, the tests and the README screenshots are each proven to be refused by
the packaging guard, and the tracked-file assertions run in CI where the
checkout is real.

### The first-launch contract

A new installation starts with no firmware folder and no devices, and nothing
about it may be inherited from the machine it happens to run on. Two paths broke
that and were fixed for V1.0.7.

| Issue | Evidence | Status |
| --- | --- | --- |
| `/api/files` listed the working directory whenever no firmware path was set | A fresh install started beside any `.vpup2` file appeared to arrive with firmware already in it | **RESOLVED.** With no path configured the endpoint returns the `Select Firmware` placeholder alone. A decoy image beside the data directory is proven not to be adopted. |
| `/api/open_firmware_folder` guessed `./firmware`, `./ui/firmware` and an absolute path on the original development machine | The last is one developer machine's absolute path; it was in the tracked source and shipped inside the executable, and the failure message returned the whole list of guesses to the browser | **RESOLVED.** Only the operator's chosen folder is opened; with none chosen the endpoint returns a 400 naming Settings. No path is guessed and none is disclosed. |
| The startup log rendered an unset firmware path as `(current directory)` | It described a fallback that should not exist | **RESOLVED.** Logged as `(not set)`. |
| `ServerTestBase` did not isolate the standalone-extender store | `_usb_extenders` binds its state file at import, so the suite read the operator's real `usb_extenders.json` -- three bench extenders answered a test asking what a clean install contains, and a persist would have written synthetic devices into it | **RESOLVED.** The store is redirected into the per-test temp directory and restored afterwards. |

`FirstLaunchContractTests` and `CleanInstallLifecycleTests` hold the contract.
They run in a child process with its own data directory, because the shared test
server is seeded with fixtures at import and cannot answer what a new
installation looks like. Between them they walk the whole lifecycle: empty on
first start, still empty after a restart with no scan, persisting normally once
a scan has run, and keeping the operator's chosen firmware folder across a
restart -- while a second installation inherits none of it.
The local files are untouched; the server reads them from `DATA_DIR`.

## Multiview

The Multiview page (`/matrix/multiview`) configures an OmniStream decoder to
composite up to four sources onto one HDMI output. It is additive: nothing in
Scan, Device Info, Configure, the A/V Matrix, the USB Matrix, LLDP, firmware or
Settings changed to accommodate it.

Phase 1's live findings are in `docs/MULTIVIEW_DISCOVERY.md` and are the
authority for device behaviour. Three of them decide the whole architecture:

- **There is no `layout` field.** The device stores explicit subframe geometry
  and silently discards a layout name. Layouts are an OmniSuite abstraction.
- **A subframe has no size.** A window is as large as the stream arriving on its
  ip_input, which is set by the *encoder's* scaler. Resizing a window means
  reconfiguring a device other than the one being edited.
- **An invalid write is accepted and reported as success.** A nonexistent input,
  a `0x0` canvas and a `delete: true` flag all return `error: false`.

### One layout engine

`omni_multiview.py` is pure computation -- no Flask, no network, no persisted
state. It holds the eleven layout templates recovered from the decoder's own web
application, the canvas snapping rule, the encoder scaler tables, the encoder
selection rule, the ip_input allocator, the scaler-ownership classifier and the
planner.

`GET /api/multiview/layouts` returns that geometry precomputed, and the page
renders the preview from it. The browser derives nothing: the numbers drawn on
screen are the numbers written to the decoder, so the preview cannot disagree
with the hardware. A JS test asserts the page contains no rounding, no slice
arithmetic and no layout table.

Canvas presets carry an `exposed` flag. 3840x2160 and 1920x1080 are offered;
2560x1440 exists in the table but is not, because six of its window sizes
(`896x480`, in the two `1+3-horizontal` layouts) have no encoder scaler format.
Enabling it later is a flag, not a redesign.

### OmniSuite owns the layout, the device owns the geometry

`multiview_meta.json` records, per decoder and per Multiview object, the layout
name, the friendly name, the requested and actual canvas, and each window's
geometry, scaler format, encoder, session, ip_input and source. It is keyed by
hardware MAC, so the record follows a device that changes address, exactly as
USB associations do.

It is a cache and never the authority. On load the geometry is read from the
decoder and reconciled: the stored layout is used only where the hardware still
matches it, otherwise the layout is re-inferred from geometry, and otherwise the
Multiview is reported as Custom / Unknown. A Multiview built in the device's own
web UI therefore still shows a sensible layout name, and one edited elsewhere is
never described by a name it no longer matches.

### The resource planner

Every encoder, session, scaler, ip_input and multicast decision is made in
`plan_multiview` and nowhere else. The UI consumes its result and the apply
transaction executes it, so the confirmation summary and the work performed
cannot describe different things.

Encoder selection is a single rule: a window larger than 1920x1080 must come from
Encoder 1, because Encoder 2's scaler tops out there and has no pass-through.
Everything at or below uses Encoder 2, which leaves Encoder 1's native feed
alone. At a 4K canvas nine of the eleven layouts need Encoder 1 for their main
window; at 1920x1080 none do, which is why that canvas is the least disruptive.

ip_inputs are allocated from the decoder's own current state. The roles are
discovered -- which input the HDMI output takes video, audio and aux from, and
which are claimed by another Multiview -- never assumed from a numbering
convention. An input already carrying the wanted address and port is reused
rather than reconfigured, so an unchanged window is not torn down on every apply.

Multicast addresses are read from the encoder's sessions. OmniSuite has no
allocator and does not need one: the devices generate deterministic,
per-device-unique destinations themselves. A session with no destination is
reported as an error rather than filled in.

### Shared scaler ownership

A vc2 scaler belongs to the encoder, so retuning one changes what every decoder
consuming that session sees. Each window's scaler change is classified:

| Status | Meaning |
|---|---|
| `safe` | already correct, or Encoder 1 already passing that size through |
| `change` | must change, and every Multiview OmniSuite knows of wants the same size |
| `conflict` | another known Multiview depends on it at a different size |
| `unknown_external` | must change, with no known consumer -- which is not proof there is none |

`change` and `unknown_external` warn before Apply. `conflict` refuses: the
operator changes the design rather than the last layout silently winning.
OmniSuite cannot see a Multiview built in a device's own web UI, and the UI says
so rather than implying the check is exhaustive.

### The apply transaction

Rebuilt server-side from freshly read device state rather than trusted from the
page, so what is applied reflects the hardware's current condition.

Stages run in a fixed order: disable the inputs that are changing, then encoder
scalers, then bitrate, then sessions, then decoder ip_inputs, then remove any
subframes the new layout does not have, then the Multiview object, then SAP
Input, then the HDMI output. Inputs come down first so a window never shows torn
video mid-change, and the output moves last.

**Every stage is read back and compared against the desired state.** This is not
belt and braces: it is the only thing that distinguishes a write that worked from
one the device accepted and ignored. Only the fields the mutation named are
compared, because the device adds read-only status everywhere.

Before the first write, every field the plan may touch is snapshotted. On a
failure the remaining stages are abandoned and the applied ones are reversed in
order, each restore read back. The result is reported as `VERIFIED`,
`FAILED — ROLLED BACK` or `FAILED — ROLLBACK INCOMPLETE`; rollback is
best-effort by nature, since the devices offer no transaction, and the third
state exists so that is never glossed over.

The prune step is reversible too: a removed subframe is re-added from the
snapshot, and idempotently, because restoring the Multiview object itself merges
the captured subframe list back in first.

Deleting a Multiview moves the HDMI output onto another input first, verified,
and uses `del_multiview` -- the only mechanism the device has. Shared encoder
streams are deliberately left configured.

### Source eligibility

Exactly one model may not feed a Multiview window: **AT-OMNI-111-WP**, matched as
a whole normalised identity in `EXCLUDED_SOURCE_MODELS`.

This is deliberately not a wall-plate rule. **HW-OMNI-E4111-WP is eligible** --
verified on the bench at .253, where it reports the same two vc2 encoders, six
sessions and device-generated multicast as any other encoder, and has driven a
live Multiview window. An earlier `endswith("-WP")` rule excluded it, which was
wrong; a `111` substring rule would take the plain AT-OMNI-111, which is also
eligible. Three regression tests name all three models.

The exclusion applies to the Multiview source list only. Discovery, Device Info,
the matrices and routing are untouched.

### Many saved Multiviews, one active

A decoder holds as many saved Multiviews as an operator wants. Only one of them
is on the output at a time, and `/api/multiview/state` lists them all with their
names and layouts; New Multiview is never withdrawn because others exist.

This replaced the Phase 5 one-per-canvas rule. Two saved layouts will routinely
want the same Encoder 2 at two different sizes, and one scaler has one value --
so requiring every saved Multiview's resources to coexist is not a stricter
design, it is an impossible one.

A Multiview built in the device's own web UI is listed as Custom / Unknown,
never overwritten, and reserves nothing. A metadata record stops counting the
moment its object is no longer on the device.

### Save, then Show

Two operations, deliberately separate:

| Action | Endpoint | Effect |
|---|---|---|
| Save Multiview | `POST /api/multiview/apply` | configures the Multiview, encoders, sessions and ip_inputs. Changes nothing on screen. |
| Show on Display | `POST /api/multiview/show` | turns off automatic source selection (SAP), then selects the Multiview. The one moment the picture changes. |

Nobody should have to accept a picture change to store a layout, and on a bench
that separation is what makes experimenting safe. `Show` carries the same
snapshot, read-back and rollback as `Save`, and refuses a Multiview the decoder
is not yet offering as a video input.

Wording follows the action, not the device field: "Show on Display", "Currently
shown on display", "Display is currently showing". `hdmi_output.video.input`
appears only in the engineering detail.

### Workflow and page states

One state model drives every control's visibility; nothing is inferred from
whether a field happens to hold a value.

```
NO_DECODER -> LOADING -> DECODER_SELECTED_EMPTY | DECODER_SELECTED
                      -> UNREACHABLE | UNSUPPORTED
DECODER_SELECTED -> CREATE | EDIT -> SAVING -> (VERIFIED | ERROR)
```

The page opens showing one control: the decoder picker. Choosing a decoder
reveals the Multiview selector and `+ New Multiview`; the creation controls and
the workspace appear only once creation or editing has started. Creation is an
action, never an entry in the dropdown pretending to be a Multiview that exists.

Loading, unreachable and unsupported are three distinct banners. None of them may
read as "no Multiviews configured", which is a fourth, different condition.

The name defaults to the layout's friendly label and follows it until the
operator types their own, and never afterwards.

### ip_input allocation

Roles are read from the decoder's own state, never assumed from a numbering
convention. An input already carrying the exact address and port a window wants
is **shared** — whatever else it is doing — and nothing is written to it: a
subframe only references an input, and the hardware refuses to open one
address:port on two inputs at once. Anything else gets the lowest-numbered input
with no role and nothing enabled. Audio and aux are never repurposed for a
different stream.

An input **is** reclaimed when a layout change or a delete stops needing it, but
only where ownership is provable: OmniSuite owns an input it had to switch on for
a Multiview it manages. Using one that was already enabled and already carrying
the stream is sharing, and shared is never owned — that input may also be the
decoder's video or audio source. Release requires all of: OmniSuite claimed it,
the new configuration no longer needs it, no other Multiview references it, it
carries no other role, and it still holds the address it was given. Anything else
is left alone with the reason reported. Reclamation is cleanup and never fails the
save that ran it.

Measured on the bench: four consecutive create/change/delete cycles each returned
the decoder to exactly its starting set of enabled inputs.

### The output resolution follows the canvas

`hdmi_output[0].video.output.resolution` is set from the Multiview canvas when the
Multiview is **shown**, never when it is saved — changing it changes the
operator's picture. The preset is recovered from the canvas width, since most
layouts snap the height. The configured value is verified by read-back; the
resolution the display negotiated is reported separately and never used as a pass
condition, because it belongs to the sink rather than to the write.

### Loading

Nothing Multiview-related runs during a scan, and a test asserts the scan
endpoint references neither the nodes nor the helpers. Measured on the bench with
a 31-address scan: 1.36s median both with and without Multiview present (+0.2%).

| Data | When |
|---|---|
| decoder role, model, hostname, address | already in the scan; reused |
| `config_get multiview` capability | once per device, cached for an hour, never polled |
| `multiview`, `ip_input`, `hdmi_output` | on selecting a decoder -- three reads |
| encoder `vc2`, `sessions`, `hdmi_input` | only for a source actually assigned, when a plan is built |

Opening the page costs nothing until a decoder is chosen. Dragging, dropping and
clearing a window reach no device at all; they change a local desired-state model
and schedule a debounced plan preview, which is skipped entirely when the desired
state has not changed. There is no polling and no observer.

## Multiview: the 1080p / Encoder-2 architecture

Phase 5 replaced the resolution-driven design with a fixed one. The sections
above describe how the subsystem is built; this describes what it is now allowed
to do, and the whole of it is validated on hardware in
`docs/MULTIVIEW_DISCOVERY.md` section W.

### One canvas

`omni_multiview.ACTIVE_CANVAS` is `1920x1080`, and `plan_multiview` refuses
anything else before it looks at a layout, a source or a decoder. The 4K and
2560x1440 presets, the 4K scaler table, `encoder_for_window_size` and
`output_resolution_for_canvas`'s general form all remain, marked unexposed, so
the capability is a flag change rather than a redesign. Nothing in the active
path reaches them; `ActiveCanvasOnlyTests` runs every layout the product offers
and asserts no window ever asks for a size only Encoder 1 can make.

4K went dormant because nine of the eleven layouts need a main window larger than
1920x1080 at that canvas, and only Encoder 1 can produce one.

### One encoder

| | |
|---|---|
| video | `vc2_encoder2` -> `session2` -> reserved decoder input |
| audio | the main window's source, `session1`, unchanged |

`required_encoder()` returns 2 without consulting the size. Encoder 1's scaler is
never written; its bitrate is written only when the source's own 900 Mb/s budget
leaves no room for the window's floor, and then it is a planned, displayed,
verified, rollback-covered mutation like any other.

Preparation per source, all read back: Encoder 2's physical input, its scaler,
its bitrate, Session 2's encoder assignment, its video enable, its generated
multicast destination, and its SAP announcement turned **off**.

### Encoder 2's physical input

Two of four bench sources shipped with `vc2_encoder2.input = ""` -- the web
application's "Not used". Session 2 can then be fully configured and verified
while no picture is ever produced.

| Encoder 2 input | Action |
|---|---|
| same as Encoder 1's | nothing written |
| `""` (Not used) | set to Encoder 1's input, read back, announced before Apply |
| a different active input | refused as a conflict, never overwritten |
| Encoder 1 has none either | an error; there is nothing to follow |

A controlled A/B could not distinguish the two states over the API: same packet
rate, same subframe activity, same decoder Input status, and 426 encoder fields
differing only in the input field itself. The encoder emits its slate, which is a
valid stream with no picture in it. The fix is preventive because detection is
not available.

### The reserved decoder input pool

```
W1 -> ip_input2    W2 -> ip_input4    W3 -> ip_input6    W4 -> ip_input8
```

Odd inputs belong to the decoder's own roles. A pool input carrying anything
OmniSuite cannot account for -- an HDMI role, another Multiview's reference, or an
enabled stream it did not configure -- is refused with the reason, never taken.
Two exceptions, and only two: windows sharing one stream share one input, because
the decoder cannot open one address and port twice; and a stream already open on
an enabled input outside the pool is refused rather than duplicated.

The allocator and `reclaimable_inputs` apply the same ownership rule. They did
not, briefly, and the asymmetry leaked one input per create/delete cycle while
feeding our stream into another Multiview's window.

### Bandwidth

| Budget | Covers | Value |
|---|---|---|
| source | `vc2_encoder1.bitrate + vc2_encoder2.bitrate` | 900 Mb/s |
| decoder | the sum of the **unique** streams for one Multiview | 900 Mb/s |

```
floor        = 150 Mb/s per unique stream          (verified end to end)
remainder    = 900 - 150 x streams
extra(s)     = floor_to_10( remainder x area(s) / total_area )
allocated(s) = min(150 + extra(s), 900)
bitrate(s)   = allocated, or the source's headroom, or 150 with Encoder 1
               reduced to 750 -- in that order of preference
```

`area(s)` is the largest window that source feeds, because one encoder serves all
of them. `vc2_encoder2` accepts 20 to 900 Mb/s on this firmware and refuses
anything outside it explicitly.

### Interlocks

| Condition | Effect | Evidence |
|---|---|---|
| Video Wall enabled | Multiview refused on any decoder | live, two D4511s |
| Fast Switching on a 1xx decoder | Multiview refused | live, an AT-OMNI-121 |
| Fast Switching on a 4xxx decoder | allowed | live, all eleven layouts |
| Fast Switching, family unrecognised | refused | model coverage |

Neither feature is ever disabled automatically. The family is derived from the
model identity alone: the last segment of the normalised model with a leading `d`
stripped, four digits beginning `4` for 4xxx and three beginning `1` for 1xx.

### Show owns everything the operator can perceive

Save configures the Multiview and the sources and writes nothing at all to
`hdmi_output`. Show performs, in order, each verified and all rolled back
together:

1. output resolution to `1920x1080`
2. SAP Input off
3. the audio ip_input pointed at the main window's Session 1 audio
4. the Multiview selected as the video input
5. that ip_input selected as the audio input

and then reads the decoder's own **Input status**. A Show whose writes all
verified but whose Input status never goes active is `NO ACTIVE VIDEO — ROLLED
BACK`, with the whole chain captured before the rollback. That fired on a
`hw-omni-d4111` which received all four streams and composited none of them.

### The page

Toolbar: Decoder, Multiview, + New Multiview, Layout, Name. There is no canvas
selector, because there is nothing to select. The canvas states `1920x1080`
beside its heading and the caption separates the display output from the snapped
compositor canvas. The configuration table is Window / Source / Scaler / Encoder /
Bitrate / Decoder input, with **Audio: Session 1** under the main window, one line
of bandwidth summary, and an Engineering detail panel -- closed by default --
holding the ordered device writes, the per-source bandwidth arithmetic, the audio
plan and the decoder's own health fields.

Measured in a browser against live hardware: 8 requests on load, 1 per decoder
selection, **0 in 60 seconds idle**, no console errors. `/api/scan` is unchanged
at 10.415 s against HEAD's 10.395 s for 254 addresses, paired on the same bench.

## Multiview: saved configurations and recall

Phase 6 separated what a Multiview *is* from what it *costs*. The sections above
describe the 1080p / Encoder-2 architecture, which is unchanged; this describes
how many of them a decoder holds and when their resources are actually claimed.
It is validated on hardware in `docs/MULTIVIEW_DISCOVERY.md` section X.

### Save stores; recall configures

| | |
|---|---|
| **Save** | `add_multiview` / `config_set`, subframe pruning, OmniSuite metadata |
| **Recall** | Encoder 2's input, scaler and bitrate; Encoder 1's bitrate where the budget forces it; Session 2 and its announcement; the decoder inputs; the subframe rebind; the release of pool inputs no longer needed; output resolution; SAP; audio; the selection |

Saving writes nothing to a source and nothing to `hdmi_output`. Recall reads
every source again first, because another saved Multiview has usually been
recalled in between and left the encoder somewhere else entirely.

`plan_multiview` returns the two sets separately -- `mutations` for save,
`activation` for recall -- so the page can say which action does what, and
neither can reach into the other's territory.

### Allocation is by stream

`WINDOW_IP_INPUTS` is four slots, handed to distinct streams in first-use order:

```
W1=A W2=B W3=C W4=D  ->  2, 4, 6, 8
W1=A W2=A W3=B W4=C  ->  2, 2, 4, 6      (ip_input8 untouched)
W1=A W2=A W3=B W4=B  ->  2, 2, 4, 4
```

`stream_identity` is the multicast address and UDP port, never the hostname. An
input anywhere on the decoder already carrying the wanted stream is used where it
stands, because the hardware will not open one address and port twice; and an
input this plan has already given to a different stream is never a candidate,
which is the bookkeeping a live recall matrix caught missing.

### The two-window limit

One decoder ip_input drives at most **two** subframes. Measured: three or four
windows on one stream left exactly two showing, silently. `MAX_WINDOWS_PER_STREAM`
is 2 and `check_stream_window_limit` refuses a layout that exceeds it, naming the
windows that would be black.

### Ownership and cleanup

A pool input is OmniSuite's to reconfigure when a Multiview it manages on that
decoder references it. Everything else in the pool is judged on what it is
carrying **right now**: an HDMI role or a stream OmniSuite did not configure is a
collision; another saved Multiview merely mentioning it is not.

Cleanup follows the active configuration. `_releasable_pool_inputs` is given the
ownership set explicitly when the object that proves it has already been deleted,
because reading it afterwards would make every input the deleted Multiview
configured look like somebody else's and leak it permanently.

### Verification reaches each window

`_await_input_status` checks the composite; `_await_window_lock` checks each
subframe. A recall whose writes all verified and whose composite is live but
which left a window dark returns `VERIFIED — WINDOW NOT LOCKED` with that
window named and the full chain diagnostics attached -- and is not rolled back,
because the rest of the picture is working.

### Audio

The display's audio follows the main window's source over its ordinary Session 1
path, applied at recall. The insertion point is the input the display's audio
already uses, repointed -- `ip_input3` on every bench decoder, verified with each
of the four sources as the main window, with the decoder reporting active LPCM
each time. It is never taken from the window pool and never from Session 2.


## Multiview: switching a Multiview that is on the display

Phase 6 made a saved Multiview a description and a recall the act of making it
true. Phase 7 covers what happens *while* one is on the display. It is validated
on hardware in `docs/MULTIVIEW_DISCOVERY.md` section Y.

The distinction everything else follows from: an **inactive** Multiview's canvas
is a form, and the **active** one's canvas is the display. Dragging a source onto
a window means two different things depending on which is open.

| | inactive | active |
|---|---|---|
| a drop | edits the preset | switches the display, verified |
| the display | unchanged until *Show* | changes immediately |
| what is saved | when *Save* is pressed | when the switch is verified |
| what the canvas shows | the saved assignment | the decoder's live subscriptions |

### A switch is not a recall

Re-running a recall to change one window would tear down and rebuild windows
nobody asked about, and each of those is a visible glitch on a display someone
is watching. `/api/multiview/switch` plans the **whole** Multiview as it would
be after the change -- so the scaler rule, the two-window limit, both budgets and
the pool are all applied to the result rather than to one window in isolation --
and then writes only what the new source and the changed window's decoder input
require, rebinds that one subframe, releases a pool input the Multiview no longer
needs, and moves the audio only if the window that changed owns it.

Verification covers the window that changed *and* the windows that did not. A
switch that lights the window it was asked about while dropping another is
invisible otherwise, so regressed windows and unlocked windows are reported
separately -- they have different causes.

A switch that is verified becomes the saved preset. A switch that fails is rolled
back and the preset is untouched: a display showing one thing while its own
preset restores another is a trap that only surfaces days later.

A switch aimed at an inactive Multiview is refused with `NOT ACTIVE` rather than
reinterpreted as an edit. Those are two operations with two different contracts.

### The canvas shows subscriptions

A saved Multiview says what was wanted; the decoder's `ip_input` entries say what
is arriving. After a recall, or after someone switches a window from the front
panel, those can disagree, and the canvas shows the second because that is what
is on the screen. Each window reports one of four origins:

| `source_origin` | meaning |
|---|---|
| `subscription` | the arriving stream resolves to a known encoder |
| `unknown` | a stream is arriving that no discovered encoder claims |
| `saved` | nothing is arriving; only the preset's record remains |
| `none` | no stream and no record |

`unknown` is deliberately not collapsed into "empty". Recall reads each source's
session live, so a re-addressed encoder is subscribed correctly; *naming* the
encoder behind a window is answered from the discovery cache, because reading
every encoder when the page opens is exactly the cost the lazy Multiview
architecture exists to avoid. Between a re-address and the next scan the window
is therefore correct and reported as unknown -- never named as the wrong device --
and the saved source is kept as the record of intent.

### Source and decoder eligibility

| state | meaning | offered |
|---|---|---|
| READY | usable now | yes, draggable |
| CONFIGURATION REQUIRED | the device is fine; a field on it must be set | yes, with the reason, not draggable |
| INELIGIBLE | offline, excluded model, or no supported encoder | no; counted and explained |

Two states would hide the only one an operator can act on: an encoder whose
Session 2 has no multicast address needs one field set, and omitting it from the
list sends someone looking for a network fault.

Reachability is one bounded, concurrent TCP probe when the page loads. It is not
on the scan path and is never polled. Decoders are filtered the same way, with
one deliberate exception -- a decoder that answers but is barred by Video Wall or
Fast Switching is still offered, disabled, with the reason, because those are
switches the operator can turn off and an empty list would never say so.

### Who owns the object

Geometry belongs to Save; resources belong to Recall. A subframe added on the
device outside OmniSuite survives a recall and is reported as a window that never
locked, rather than silently deleted -- removing it would be OmniSuite overruling
a change it did not make. Saving over the object is what prunes it.

### The UI

The active canvas carries a banner reading *"LIVE -- changes made on this canvas
are applied immediately"* and the stage is outlined; an inactive canvas says the
opposite in words. Neither is left to be inferred from which buttons are enabled.
On the active canvas both a drop and a window's clear control go through the
confirmed live-switch path, and *Save* is relabelled because by the time it is
reachable the sources have already been applied.


## Multiview: source preview

Hovering a source tile shows what is on that encoder, so an operator can tell
two of them apart without routing one into a window to find out. It is
validated on hardware in `docs/MULTIVIEW_DISCOVERY.md` section Z.

It is an aid and nothing more. It reads, it never writes, and it has no say in
whether a source can be used.

### What it uses

| | |
|---|---|
| image | `http://<ip>/thumbnail/thumbnail1.jpg`, 320x180 JPEG, unauthenticated on port 80 |
| fetched by | the browser, directly from the encoder -- the same way the A/V Matrix page has always shown it |
| enabled state | `vc2_encoder1.thumbnail.enable`, read by `GET /api/multiview/preview?ip=` |
| generator | 5 fps on the encoder |

The thumbnail belongs to **Encoder 1**. `vc2_encoder2` has no thumbnail fields,
and Encoder 2 is required to take the same physical input as Encoder 1, so
Encoder 1's thumbnail is a picture of what the Multiview window will show.

### Why there is a backend call at all

Because the image cannot answer the question. With the thumbnail disabled the
device still returns HTTP 200 and a placeholder JPEG, so the browser sees a
successful load either way. `/api/multiview/preview` reads the encoder's own
configuration and returns one of three states:

| state | the card shows |
|---|---|
| `available` | the picture |
| `disabled` | "Preview disabled", and no image is fetched |
| `unavailable` | "Preview unavailable", with the reason |

The endpoint returns no credential and puts nothing in the image URL. It refuses
anything that is not a discovered encoder, so an offline or excluded source
generates no preview traffic at all.

### Lifecycle

```
pointer enters a tile
  -> 300 ms debounce          (cancelled if the pointer leaves first)
  -> one read of that encoder (abortable; a late answer for a tile the pointer
                               has left is discarded)
  -> the card opens and the browser fetches one frame
pointer leaves, Escape, a drag, a scroll, a re-render
  -> the card closes and the image's src is dropped, not just hidden
```

One frame per hover, and no repeating timer: the Multiview page is held to
"nothing polls", and moving off and back on is what fetches a newer frame.

### What it must not do

The card takes no pointer events, so it cannot swallow a drop. A drag dismisses
it first. It never changes eligibility: a source whose preview is off, broken or
unreachable is still perfectly routable, and a source that only needs its
multicast configured is still previewable -- which is the case where seeing the
picture helps most.


## Multiview: Phase 7B — shared state, previews and fewer dialogs

Validated on hardware in `docs/MULTIVIEW_DISCOVERY.md` section AA.

### Writing only what differs

Every Multiview transaction now reads the devices, diffs the plan against them,
and writes the difference:

```
READ current state  ->  PLAN desired state  ->  DIFF  ->  WRITE differences
                                                      ->  VERIFY
```

This matters because encoder configuration is **shared**. Encoder 1 may be
feeding decoders that have nothing to do with Multiview, and a `config_set` is
an instruction the device acts on rather than a comparison it makes.

Mutations are grouped by target and judged by the state they jointly ask for.
The decoder-input pair — disable while the source changes, then point and enable
— is kept or dropped as one, because judging them separately keeps the disable
and drops the enable, leaving the input off.

Every transaction reports `planned_fields`, `already_correct`,
`writes_required` and `writes_performed`.

### What actually disturbs a decoder

Measured on the bench with a second decoder watching the shared encoder's
Session 1 and sampled three times a second:

| write | effect on a decoder watching Session 1 |
|---|---|
| `session1.video.stream.destination_address`, **same value** | ~0.5 s blackout, then relock |
| `session1.video.stream.enabled`, **same value** | ~0.5 s blackout, then relock |
| `vc2_encoder1.bitrate`, **changed** | ~0.5 s blackout, then relock |
| `vc2_encoder1.bitrate`, same value | nothing |
| `session2.video.stream.enabled`, **changed** | ~0.5 s blackout, then relock |
| everything on `vc2_encoder2`, changed or not | nothing |
| SAP on either session, same value | nothing |

Multiview never writes Session 1. Its only writes that can disturb an unrelated
subscriber are a genuine Encoder 1 bitrate reduction, which cannot be avoided,
and genuinely starting Session 2, which also cannot. Everything else is now
skipped when it is already correct.

### The operator notice

Shown when the page is newly opened, unless suppressed for the running version.
The suppression key is `multiview_notice_acknowledged_version` and holds the
version string, so a new release shows the notice again without any migration.
It is raised once from page start, never from `render()`, and costs nothing.

Copy and Save produce the same complete plain text, carrying the version, the
title and a timestamp, and no credential.

### Previews in the windows

A populated window whose source has preview enabled shows that encoder's
thumbnail, with the source, address, encoder, decoder input and health as an
overlay on a scrim. A disabled preview keeps the ordinary appearance and says so
quietly; the device's placeholder JPEG is never displayed.

Visible thumbnails refresh about every five seconds — one timer for the page,
one image per unique visible source, stopped when the canvas is not on screen
or the page is hidden. This is the only repeating timer the page has.

### Dialogs

A drop on an active canvas, and Show on Display, both apply immediately. The
canvas already carries the LIVE banner and the button already carries the
intent; the transaction behind each is unchanged. Save and Delete still confirm.


## Multiview: Phase 8B — workflow, copying, and synchronized groups

Choosing a decoder now opens whatever it is **actually showing**, populated from
its live subscriptions. A remembered selection is a bookmark, used only when
nothing is on the display.

Creating and editing are visibly different operations. **+ New Multiview**
enters its own creation state and leaves the selected preset alone; the editing
area is headed with which Multiview is open and whether it has unsaved changes.
**Save** is offered only when something persistable has changed -- name, layout
or window assignments -- and never because a packet counter moved or a source
went quiet. A failed save keeps what the operator typed, because the transaction
changed nothing.

The Save confirmation can be turned off, **for this version**. The stored
preference names the version it was given against, so an upgrade asks once more
without anyone clearing browser storage. That is about consent rather than
tidiness: a warning describes what an operation does to shared equipment, and a
release can change it.

**Copy to Decoder** carries a Multiview's definition -- the layout and the source
in each window -- to another decoder and saves it there. It carries no
resources, because those belong to one decoder at one moment and are worked out
again at Show, and it changes no display. A name already in use is never
overwritten silently, and a source that happens to be switched off is a warning
rather than a reason to throw the window away.

**Decoder groups** are the larger idea. A group is several decoders meant to
show the same thing, and it persists in OmniSuite's runtime state. Copying a
Multiview to a group saves it on every member and changes no picture; showing it
on the group changes all of them and asks first.

The reason a group is a first-class idea rather than a convenience is that the
encoders are shared. Window geometry decides what a source's Encoder 2 must
scale to, and one Encoder 2 produces one size, so the same source as a large
window on one decoder and a small window on another is not a configuration that
can exist. OmniSuite plans the entire group before writing anything, refuses the
whole operation when that conflict is present, and names the source and the
decoders that disagree. Configuring members one at a time would discover it with
half the room already changed.

Decoders that share a source subscribe to the same stream; that source's
bandwidth is charged once and each decoder's own input budget is evaluated
separately. Audio stays per decoder. Showing on a group verifies each member and
restores the ones already changed if a later one fails, reporting whether that
restoration succeeded -- best effort across independent network endpoints, and
described as such.

While a group is in context the page says so, and a source change applies to the
whole group. Leaving that context returns to single-decoder work. A grouped
decoder routed normally from the A/V Matrix leaves Multiview by itself and is
then reported as DRIFTED; OmniSuite never silently puts it back, because it
cannot know the operator did not mean it.

## Multiview: Phase 8B corrections — the notice, and groups as targets

The introductory notice reappeared on every page load because closing it
recorded nothing unless the "do not show again" box was ticked: one level of
suppression where an operator reasonably expects two. Continue now means "I have
read it" and lasts the session; the checkbox is a preference and lasts the
version. Both are version-scoped, so a release that changes what Multiview
touches asks again without anyone clearing storage, and a missing or unusable
store shows the notice rather than assuming consent.

The notice also still said OmniSuite might adjust Encoder 1's bitrate, which
Phase 7C made untrue. It now says what the code does, and the promise not to
reduce a source's primary stream sits with the other things OmniSuite will never
do on its own.

The Groups dialog was unreadable over a populated canvas, and not because it was
styled to be translucent: it asked for `var(--panel)`, a token defined in no
stylesheet, so its background resolved to nothing. CSS fails silently like that,
so the stylesheet's tokens are now checked against the tokens that exist.

The larger correction is that a group is a target rather than a second
interface. Persisted groups appear in the same selector as the decoders, in
their own section, and selecting one scopes the ordinary workflow: the same
canvas, the same layouts and sources, the same drag and drop, Save and Show.
What changes is said out loud -- a context bar naming the group, and buttons that
read "Show on 6 displays" rather than "Show on Display". The Groups panel is now
only for deciding which decoders are in a group.

## Multiview: Phase 8C — what may be retried, and what a refusal means

The reported failure -- "Encoder hw-omni-e4521-00002 did not answer" -- was
accurate. That encoder answers none of five TCP probes; it is switched off. What
was wrong was the shape of the answer: a device that was not there came back as
400, the same code as a request that cannot be built, so the page could only say
BAD REQUEST. A device that did not answer is now 503, names the unit and the
attempt count, and says that nothing was changed. Live conflicts stay 409 and
genuinely invalid requests stay 400.

Reads get three bounded attempts with short pauses, because a read changes
nothing and every read opens its own connection -- there is no pooled socket to
go stale, which is also why the transport needed no rewrite. Writes are the
opposite: a `config_set` whose reply was lost may have arrived, so the device is
read before anything is repeated, and only then is one retry allowed. A `method`
is never repeated at all. Deterministic refusals are never retried; they fail
fast, before any write.

Measured cost: a Show with nothing going wrong is unchanged at about 4 ms of
OmniSuite's own work, a read that misses once is caught transparently, and an
absent device adds at most 0.45s of pause before a clear refusal.

The harder finding is a window that stays black after certain transitions --
every field correct, packets arriving, and no picture. It is intermittent, about
four occurrences in thirty attempts, and it does not clear itself in a minute.
OmniSuite already detected and reported it rather than claiming success. What it
did next was the problem: showing the same Multiview again performed **zero**
writes, because everything already matched, so the only obvious remedy could not
work. A window seen failing to lock is now remembered and its input
re-established on the next attempt instead of being skipped. Showing a different
Multiview and returning also clears it, and the guide says so.

## The test fence belongs to the tests

Phase 8 found the hardware fence living in `run_tests.py`, which meant a plain
`python -m unittest`, an IDE runner or a mutation harness ran the same suite
with nothing between it and the bench. Nothing had gone wrong -- measured, the
suite only ever addressed documentation-range addresses -- but the protection
was a convention rather than a fence.

It now lives in `tests/_fence.py`, installed by the test package and by every
test module, so it is present however the suite is entered. It refuses
WebSocket, TCP and UDP to anything that is not loopback, which covers HTTP,
thumbnails and the device transports. `tests/test_fence.py` proves it, including
from a fresh interpreter started the way a mutation harness starts one.

## Phase 8D — a flake with a cause, and a source that was never usable

Two things stood between the suite and a publication gate anyone could trust.

### The flake: background work landing in a stranger's test

`test_it_stops_asking_once_every_mask_is_known` failed occasionally and for no
visible reason. It was not a timing weakness in the test.

`start_background_startup_tasks()` starts three plain daemon threads.
`_startup_usb_refresh` slept two seconds, then called `_refresh_usb_parent_map`,
`_usb_live_refresh` and `_refresh_icron_network_config` — all through the module
globals, so all resolved **at the moment they fired**, against whatever the test
running two seconds later had patched in. `ServerTestBase.inline_background()`
could not reach them: it inlines executor submissions, and these are not
submissions.

The victim is decided by arithmetic. `MatrixNetworkReadinessTests` replaces
`_refresh_icron_network_config` with a recorder and then asserts the recorder is
empty. If the leaked thread fires during that test's last few milliseconds, the
recorder holds one entry that the test never caused, and it fails with
`[True] != []` on *"a known mask must not be re-read on every Matrix poll"*.

Measured rather than argued. Arming the trigger and sweeping the start offset
across the target reproduced it in **7 of 30** aimed attempts; running the real
contaminating sequence — the arming test, then the target across the moment the
thread fires — reproduced it in **4 of 45**, and **0 of 45** with the fix, same
harness and same offsets.

The fix is ownership, not patience:

- `start_background_startup_tasks()` records the threads it starts in
  `_startup_threads`, and `await_startup_tasks(timeout)` waits for them. Startup
  itself still waits for nothing — that is what a daemon thread is for — but a
  caller that is about to rebind the globals those threads resolve now has a way
  to let the work finish first. Shutdown gains the same handle.
- The two-second settle became `STARTUP_USB_SETTLE`, so a caller with nothing to
  settle waits for the *work* rather than for the delay.
- `ServerTestBase` registers the wait **last in `setUp`, so it runs first in
  teardown**, with the test's own stubs still installed. The work both belongs
  to the test that armed it and is observable by it. Anything still running
  fails that test, by name.

`StartupTaskIsolationTests` proves all of it, including that a deliberate leak is
reported as a failure of the test that leaked. Four mutations of the fix — the
contract not waiting, the wait always claiming success, the threads going
untracked, the settle coming back — were all caught.

### The source that was advertised and then refused

An encoder was listed **Ready** and then refused by the planner. Internally
consistent, externally baffling: the operator assigned it to a window and only
then learned it could not be used.

The cause was that the scan cache does not carry Encoder 1's bitrate, so the
list had no way to ask the question the planner asks. A source has one 900 Mb/s
budget shared by both encoders; if Encoder 1 holds all of it, there is nothing
left for a Multiview window.

`multiview_headroom()` in `omni_multiview.py` is now the single rule, and the
list and the planner both use it. A source with less than `ENCODER2_MIN_BITRATE`
of headroom is **CONFIGURATION REQUIRED**, with the number it is short by and a
plain statement that OmniSuite will not lower Encoder 1 to make room. An
Encoder 1 bitrate that could not be read is *unknown*, not *no*: the source stays
Ready and the planner finds out before it writes.

The read is one `config_get` per reachable encoder, concurrent, and only on
`/api/multiview/sources` where eligibility is deliberately being evaluated.
`probe=0` remains the cheap call and reads nothing. Nothing was added to the
scan path or to idle.

**Encoder 1 is read. It is never written.** That was true before and is asserted
now.

## Phase 8E — Display output: what is actually on the screen

The Multiview page could say a great deal about the composition an operator had
open and almost nothing about what the decoder was showing. The canvas already
distinguished LIVE from not-on-air, but the only way out of Multiview was to go
to the A/V Matrix and route from there.

### The panel

A small **Display output** panel sits above the canvas. It reports one of three
states, derived from the decoder's own `hdmi_output.video.input` and from
nothing else:

| state | shown as |
|---|---|
| compositing a Multiview | **MULTIVIEW · ACTIVE**, named |
| an ip_input carrying a stream | **LIVE**, with the encoder that stream belongs to |
| selected but carrying nothing | **NO VIDEO** |

It is carried on `/api/multiview/state` and computed from the three reads that
endpoint already performs, so it adds no device traffic and no timer. A stream
that is arriving but belongs to no discovered encoder is still reported as on
the screen, with no source named: something is plainly playing.

The canvas beneath it now carries **LIVE** or **INACTIVE** explicitly, so a
preset being edited can never be mistaken for a picture someone is watching.

### The drop

Dragging a source onto Display Output routes it the ordinary way. The request is
`POST /api/route` with `exit_multiview` — the same request the A/V Matrix
sends — so the Multiview exit, the Session 1 route, the semantic verification
and the rollback are the established ones and exist in one place. The warning,
its session acknowledgement and its permanent preference live in
`ui/confirm.js` under the keys the A/V Matrix already wrote, so answering it on
one page answers it on both.

**Session 1, and only Session 1.** A conventional route carries the encoder's
Session 1 video and Session 1 audio. Encoder 2 is not prepared, scaled, re-rated
or enabled for it, however conveniently its Session 2 stream is already running.

### Two eligibilities, not one

Adding the panel exposed a real defect. The page made a source tile draggable
only when it was Multiview-ready, so an encoder whose primary stream uses its
whole budget — refused for a window, and correctly so — could not be
dragged anywhere at all, including to the display output, where the reason it
was refused does not apply.

`normal_route_eligibility()` is now a separate rule beside `classify_source()`,
and the source list carries both answers. A window needs Encoder 2, a Session 2
multicast and headroom; an ordinary route needs the Session 1 stream that is
already running. The Multiview-only model exclusion does not bar a conventional
route either: it is a statement about a model's second encoder.

Windows refuse what they cannot carry, at the window rather than at the tile.

### Groups

A group's Display Output reports the group state — **MULTIVIEW — ACTIVE**
with the number of synchronized displays, or **DRIFTED** — and accepts no
drops. One drag must never route a room full of screens, and this phase did not
invent group-wide conventional routing.

### Two defects the bench found

**A derived field outliving its source.** `/api/state` derives `multiview_active`
from the cached `video_input` and then overlays the live `video_input` on top.
On the bench one record read `video_input='multiview13HorizontalBottom'` and
`multiview_active=False` at the same time. The verdict is now recomputed after
the overlay, from the value that survived it.

**A rollback that did not say what it had done.** Leaving Multiview records the
input the display moved to; putting it back after a failed route did not record
anything, so the A/V Matrix went on showing a conventional route that had been
undone. `_restore_multiview_after_failed_route` now records the restored display.

Fixing the first exposed a third, in the tests: `omni_matrix_logic._decoders` is
process-global and `/api/state` overlays it, so one test's decoder was already
deciding what a later test's `video_input` said. Three tests had been asserting
on `multiview_name` while the contradictory `video_input` sat beside it. The
Multiview test base now isolates those globals.

## Multiview: Phase 8 — the A/V Matrix knows

A decoder showing a Multiview is marked **MULTIVIEW** in the A/V Matrix and has
no video crosspoint, because its picture is a composition rather than a route
from one encoder. Its audio crosspoint stays, and is true: Multiview sound comes
from one source over its ordinary audio path.

This is decided from what the decoder's display is actually selecting, which the
scan already reads. Saved Multiview objects, a remembered page selection and
OmniSuite's own metadata are all explicitly not treated as evidence, and nothing
was added to the scan hot path.

Routing a conventional source to such a decoder still works. It warns first —
once per browser session, with a "do not show this again" option — and the
operator's answer travels with the request, so a route that does not carry it is
refused rather than quietly changing what is on a display. Cancel writes
nothing at all.

Continuing is one transaction: move the display off the composition, release the
reserved inputs that Multiview owned, then apply the route, each step verified.
If the route fails, the Multiview is restored and the failure says whether that
restoration succeeded. The saved Multiview is never deleted — it can be shown
again at any time — and the matrix redraws from the route response rather than
waiting for a rescan.

## Multiview: Phase 7C — bitrate policy and remembered selection

Validated on hardware in `docs/MULTIVIEW_DISCOVERY.md` section AB.

### The Encoder 2 bitrate policy

Targets are stated by the role a window plays, decided from the geometry:

| role | when | target |
|---|---|---|
| equal | every window in the layout is the same size | 200 Mb/s |
| main | the largest window of a mixed layout | 300 Mb/s |
| small | every other window | 150 Mb/s |

What the device is actually given is

```
headroom = 900 - current Encoder 1 bitrate
actual   = min(layout target, headroom)
```

**Encoder 1 is read and never written.** If a source is running Encoder 1 at
750, its Multiview window gets 150 rather than 200, and Encoder 1 stays at 750.
Reducing it would black out every decoder watching that source for about half a
second — measured in Phase 7B — which is a much worse outcome than a slightly
softer Multiview window.

A source with less than the 20 Mb/s Encoder 2 needs is refused by name. The
primary feed is not trimmed to make a Multiview fit.

| layout | windows | roles | targets (Mb/s) | total |
|---|---|---|---|---|
| `side-by-side` | 2 | equal / equal | 200 / 200 | 400 |
| `2x2` | 4 | equal / equal / equal / equal | 200 / 200 / 200 / 200 | 800 |
| `pip-top-left` | 2 | main / small | 300 / 150 | 450 |
| `pip-top-right` | 2 | main / small | 300 / 150 | 450 |
| `pip-bottom-left` | 2 | main / small | 300 / 150 | 450 |
| `pip-bottom-right` | 2 | main / small | 300 / 150 | 450 |
| `1+3-horizontal-bottom` | 4 | main / small / small / small | 300 / 150 / 150 / 150 | 750 |
| `1+3-horizontal-top` | 4 | main / small / small / small | 300 / 150 / 150 / 150 | 750 |
| `1+3-vertical-right` | 4 | main / small / small / small | 300 / 150 / 150 / 150 | 750 |
| `1+3-vertical-left` | 4 | main / small / small / small | 300 / 150 / 150 / 150 | 750 |
| `4-split` | 4 | main / small / small / small | 300 / 150 / 150 / 150 | 750 |

The totals are deliberate: 900 is a ceiling, not something to spend.

### Target and actual are different numbers

The Configuration table shows the configured bitrate, and — only where it falls
short — the target underneath it. The engineering panel shows the arithmetic in
the order it happens: layout target, Encoder 1, headroom, Encoder 2.

The previous algorithm shared the decoder's 900 Mb/s out by window area, which
made two identical Side-by-Side windows "entitled" to 450 Mb/s each and
explained every real number as a reduction from a figure that meant nothing on
its own.

### Remembering the selection

The page stores two identifiers — the selected decoder and the selected
Multiview — and restores them when it next opens, through a refresh or a trip to
another page. Live state is loaded first and decides: an offline, undiscovered
or no-longer-capable decoder is dropped, and a deleted Multiview clears only
itself. Nothing about the devices is cached and nothing is written to a device
to remember a view.


## Build and publish gate

A build intended for publication to Git is **not publication-ready** until every
item below is satisfied. This is a gate, not a checklist to fill in afterwards.

```
[ ] Settings-page User Guide reviewed
[ ] User-visible changes documented in the guide
[ ] Screenshots / UI references still match the current controls
[ ] The guide opens correctly from Settings
[ ] The guide is included in the packaged application
[ ] No credentials, tokens or secrets anywhere in the guide
[ ] The guide corresponds to the version being published
```

If a build needs no documentation change, record it explicitly:

> User Guide reviewed — no update required.

Do not skip the review silently. A build whose guide describes behaviour the
build does not have is a defect in that build, and it is the one defect the
operator meets first.

### Where the guide lives

| | |
|---|---|
| source | `ui/user-guide.html`, a single static page |
| reached from | Settings → **User Guide**, and the first-launch notice |
| served at | `/help`, with the running version substituted in |
| packaged by | `ui/` being bundled wholesale; `tools/build_release.py` allows it |
| covered by | `UserGuideTests` — presence, Settings link, serving, version, packaging, structure, and absence of secrets |

The guide carries `{{OMNI_VERSION}}` rather than a typed version string. A
version bump therefore cannot leave it claiming the wrong release.

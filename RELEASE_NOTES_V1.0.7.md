# OmniSuite V1.0.7

A consolidation release. USB support is the centre of it — standalone extenders
and the endpoints built into OmniStream units are now one inventory with one
routing surface — alongside a reworked Device Info page, encrypted device-log
collection for Engineering, and a substantial pass over appearance,
accessibility and responsiveness.

---

## Highlights

- **One discovery console.** Device Info owns the adapter, the targets, Scan and
  Clear Units. Every other page reads what discovery found.
- **Standalone HW-OMNI-311 / HW-OMNI-324 extenders** are discovered, polled,
  configured and routed alongside the Icron endpoints inside E4521 and D4511
  units.
- **One-click USB routing.** A cell states the relationship you want; OmniSuite
  reconciles the hardware to it.
- **Device logs** can be collected for manufacturer Engineering, with a clear
  notice about what the file is.
- **Appearance** — ten colour presets, light and dark, four layouts, applied
  consistently across every page.

---

## Device management

- Discovery, configuration, firmware upgrade, reboot, removal, NTP and password
  synchronisation continue to work from Device Info.
- **Rescan updates what it already knows.** Devices are matched by MAC, so a unit
  that changed address, hostname, firmware or status is corrected on its
  existing row. **Clear Units is not a refresh** — it empties the inventory
  deliberately, and you should not need it to bring information up to date.
- **Frozen identity columns.** The checkbox, IP and Hostname stay in place while
  a wide table scrolls sideways, so a value read at the far right is still
  attributable to a device.
- **The command area stays put.** The header, scan bar and firmware bar remain on
  screen while the table scrolls.
- **A mirrored horizontal scrollbar** sits above every wide table as well as
  below it, so reaching it does not mean scrolling past every row. It appears
  only when a section actually overflows.
- **Inventory export** produces a two-sheet XLSX workbook: *Devices* for the
  OmniStream units, *USB* for the endpoints.

## USB and Icron

- **Two kinds of endpoint, one inventory.** Standalone extenders are found over
  their own UDP protocol; integrated endpoints come from their parent OmniStream
  unit. Each physical endpoint appears exactly once, correlated by hardware
  identity rather than by address or name.
- **LEX and REX**, shown plainly. On Device Info the USB column states the
  unit's USB role; the details — USB MAC, USB IP, host port, configured peers,
  live links, and the endpoints visible on its subnet — are on the tooltip, each
  one named.
- **Configured pairing and live link are separate facts** everywhere they are
  shown. A configured route is never presented as proof that USB traffic is
  flowing.
- **USB firmware is the USB module's own.** For an integrated endpoint it is
  read from the Icron endpoint, never substituted from the parent OmniStream
  unit — on the bench, units running OmniStream 2.1.2 carried Icron 2.0.9 and
  1.9.4.
- **Multi-peer.** One host can hold several remote peers at once; adding or
  removing one leaves the rest untouched.

## A/V and USB Matrix

- **A/V Matrix** routes encoders to decoders with separate **AV**, **Video** and
  **Audio** modes.
- **USB Matrix** routes LEX hosts to REX devices. Clicking another host for a
  peer that already belongs to one **moves it** — there is no unpair-then-pair
  step for the operator to perform, and no message asking for one.
- **A route is only shown as done once a fresh read of both endpoints agrees.**
  A cell is drawn as pending while the transaction runs; a device
  acknowledgement alone is never painted as a finished route.
- **Same-subnet safety.** Both endpoints of a USB route must resolve to the same
  network, computed from their own addresses and masks. A cell that cannot be
  proven valid is disabled and says which case it is — different subnet, or
  network information unavailable. Checking happens **before** anything is
  released, so a working route is never torn down to discover the destination
  was not allowed.
- **Failures are distinguished**: nothing changed; the previous route was
  restored and verified; or the restoration could not be confirmed.

## Discovery and networking

- **Targets is the single definition of where to look.** Saving it also becomes
  the configured USB discovery network, so a routed subnet is entered once.
- **Broadcast finds unknown devices on the attached network; directed polling
  keeps known ones visible, including across routers.** Broadcast does not cross
  a router, and OmniSuite says so rather than appearing to fail.
- **Adapter and targets are remembered** on the server, identified by address
  rather than list position.
- **LLDP topology.** Each unit is asked for its neighbour during the scan.
  OmniSuite advises when the fleet spans **more than one switch**, and when a
  unit is **daisy chained** behind another. Both are supported configurations,
  not faults, and each advisory is acknowledged separately. No device reports
  its bitrate, so nothing claims a link is congested — the daisy-chain advisory
  states a risk from topology, not a measurement.

## Diagnostics and support

- **Device logs.** The **Log** button on a Device Info row collects the
  manufacturer's diagnostic bundle. It first shows a notice — *Device logs are
  encrypted and can only be decrypted and read by Engineering* — with **Cancel**
  as the default. Nothing is requested until you choose OK. The file downloads
  as `hostname_IP_timestamp.vdf`, is encrypted by the manufacturer, and is
  intended to be sent to Engineering. Nothing else in OmniSuite ever requests
  one.
- **TS Dump** produces a single JSON file for support with passwords masked,
  carrying USB endpoints, USB routes, discovery state and LLDP topology. It is
  built from state OmniSuite already holds, so generating it contacts no device.
  **It does not contain device logs.**
- **First-run network notice** explains that OmniSuite talks to devices directly
  and may need to be permitted through the firewall, without ever suggesting the
  firewall be turned off.

## Appearance and usability

- **Ten colour presets** — Default, Slate, Cool, Ocean, Forest, Warm, Plum,
  Rose, Soft and High contrast — in **light and dark**, across four layouts.
  Meaning is held constant: critical always reads as critical.
- **Button hierarchy.** The action a page exists for is filled with the accent
  colour; everyday utilities are quieter but plainly clickable; destructive
  actions are filled in the critical colour *and* carry a ring no other button
  has, so they stay recognisable even in a palette whose accent and critical
  colours sit close together.
- **Selected controls are filled, not outlined** — tabs, the routing mode
  selector, the layout and preset choosers and every toggle.
- **Settings is one dialog** with the same state on every page, in the same
  position.
- **Accessibility.** Every toggle is reachable with Tab and operated with Space;
  keyboard focus is visible on every control; the routing mode selector
  announces its state; dialogs return focus to the control that opened them.
- **The User Guide** now follows the same theme and preset as the application.

## Reliability and performance

- **A responsiveness defect that could make a page stop responding was fixed** —
  observers were being re-armed from inside their own callbacks, so their cost
  grew the longer a page stayed open.
- **Every poll loop is single-flight**, so a slow read cannot stack another on
  top of it, and extra open pages do not multiply device traffic.
- **Update notification.** OmniSuite checks once at startup and caches the
  result for hours; it is never on a polling path and works normally with no
  Internet. A newer release makes the Settings gear pulse until you open
  Settings. **Nothing is downloaded or installed for you.** A build newer than
  the latest published release reports as current, not as an error.

---

## Platform downloads

| Platform | Artifact |
|---|---|
| Windows x86-64 | `OmniSuite-V1.0.7-Windows-x86_64.zip` |
| macOS Apple Silicon | `OmniSuite-V1.0.7-macOS-arm64.zip` |
| macOS Intel | `OmniSuite-V1.0.7-macOS-x86_64.zip` |
| Ubuntu Linux x86-64 | `OmniSuite-V1.0.7-Ubuntu-x86_64.tar.gz` |

`SHA256SUMS.txt` accompanies the release. Verify with `sha256sum -c
SHA256SUMS.txt`, or `Get-FileHash -Algorithm SHA256` on Windows.

---

## Known limitations

- **Physical hardware validation has been performed on Windows only.** The
  macOS and Ubuntu artifacts are built, started and exercised through their
  interface automatically, and the full test suite runs on each; they have not
  been used to manage real devices.
- **No code signing.** The Windows executable is not Authenticode signed, so
  SmartScreen will warn on first run. The macOS app is not signed or notarised,
  so Gatekeeper requires right-click → Open the first time.
- **Ubuntu needs a desktop session.** The launcher is a graphical window; there
  is no headless mode. Only the GitHub `ubuntu-latest` image is tested.
- **USB data-plane verification is not claimed for every route combination.**
  Pairing and unpairing are verified on hardware for all four combinations, and
  USB peripherals were physically run across standalone→standalone,
  standalone→integrated and the established integrated→integrated path. The
  **E4521 → HW-OMNI-324** combination is control-plane verified only; no
  peripheral test has been performed for it, and it is labelled as such in the
  Matrix.
- **Five peers on one host is what was tested**, not a discovered maximum. The
  documented model for an HW-OMNI-311 is seven. No capacity beyond two is
  claimed for an integrated E4521.
- **Standalone extenders lose their pairing table on a power cycle.** Routes
  have to be re-established afterwards.
- Adapter enumeration on macOS and Linux depends on OS interface data; if the
  list looks wrong, enter the subnet in Targets manually.

---

## Upgrade notes

- **No migration is required.** OmniSuite reads the same data directory as
  before — `%LOCALAPPDATA%\OmniSuite`, `~/Library/Application Support/OmniSuite`
  or `~/.omnisuite` — and existing settings, caches and USB associations are
  picked up as they are.
- **Nothing on your devices is changed by upgrading.** Starting OmniSuite
  performs read-only discovery: it does not pair, unpair, reboot, change an
  address or alter USB configuration.
- **If a USB row looks stale, scan again.** Clear Units is not needed to refresh
  information and should be reserved for deliberately starting over.
- The Windows build is portable — extract and run; there is nothing to
  uninstall. Replace the previous `OmniSuite.exe` with the new one.

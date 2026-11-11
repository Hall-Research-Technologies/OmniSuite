# Changelog

## V1.2.0

Optional secure remote access with administrator and user accounts, per-user routing, Display Groups,
a fixed local port, backup and restore, firmware availability, and clearer scanning and first-launch
behaviour on macOS. See RELEASE_NOTES_V1.2.0.md for the user-facing summary.

### Remote Access

- Added Remote Access: off by default on new installations and on upgrades, bound to a network adapter an
  administrator chooses, and restored safely across a restart. Updating never turns it on.
- A first-time setup starts on HTTP, labelled as not encrypted, and turning it on needs an explicit
  acknowledgement. HTTPS is the recommended transport once a certificate exists and is one choice away. Once a
  transport has been saved that choice is honoured; an HTTPS choice whose certificate cannot be used refuses to
  start and says why. There is no fallback to plain HTTP, and no HTTP listener at all while HTTPS is active.
- Added HTTPS with either a certificate OmniSuite generates or one the site supplies. A supplied certificate
  and key stay in use across restarts and are never altered; a generated certificate is checked against the
  address it will be served on and is never silently reissued.
- Generated certificates carry the computer's Windows name as the common name, organisation Hall Research,
  unit OmniSuite, subject alternative names for the computer name and the chosen adapter's address, and three
  calendar years of validity. Earlier certificates keep the name and validity they were made with.
- Settings describes the certificate that actually exists: certificate active, custom certificate active, or
  certificate required. It never says "trusted".
- The adapter OmniSuite starts on is a physical Ethernet port in preference to Wi-Fi and to virtual adapters.
  A saved choice is never changed.
- Opening the remote address in a browser shows the sign-in page. Browser security headers are sent. Sign out
  is available on every screen reached through Remote Access.
- Remote responses carry no device passwords; the local listener answers only to localhost / 127.0.0.1; a
  second copy on the same data folder is refused; remote requests may only name devices OmniSuite already
  knows (control) or non-public addresses (scan).

### Accounts, permissions and routing

- Added administrator and user accounts. The first administrator is created at the OmniSuite computer; there
  is no default credential. Passwords are stored only as one-way hashes.
- Added per-user permissions for features (A/V, USB), sources, displays and Display Groups, enforced on the
  server for every request and again immediately before the device write.
- Added a restricted screen for phones and tablets with Standard routing, the User Matrix (AV, Video, Audio),
  or both, chosen per account. Sources can be dragged onto a display or group.
- Added Display Groups: a named set of displays that receive one source, routed one display at a time with
  each verified. A failed display does not stop the rest and nothing is rolled back. A group is its own
  permission.
- Added optional direct access links: a passwordless address for one non-administrator account, off by default.
- Added friendly device names (kept by OmniSuite, never written to equipment), with duplicates distinguished by
  the Location the unit reports.
- Added an Activity history of sign-ins, refusals, routing and administrative changes, for administrators only
  (30 days / 5 MB by default, configurable).
- Operations on one display are serialized, and extra browsers do not multiply device traffic.
- Routing a source to a display that is showing a Multiview simply does it: the display leaves the Multiview
  in the same verified step and the saved Multiview is kept; a failed route puts it back. Multiview is not
  offered to User accounts or direct access links.
- Source pictures for Users come from the encoder's thumbnail, served without exposing device addresses; they
  never block routing.
- Clear Units and Remove Selected keep units that Users or Display Groups depend on while Remote Access is on.

### Local application port

- Added an optional fixed local application port (Settings > Local Application Port). The default is
  unchanged: OmniSuite chooses 8080 or the next free port. Save and Restart restarts OmniSuite on the chosen
  port; a busy port is never silently replaced and the launcher offers Retry, Start on Alternate Port,
  Change Fixed Port and Exit. A damaged setting never stops OmniSuite starting.

### Backup and restore

- Added encrypted Configuration Backup (Export and Restore, at the OmniSuite computer). A restore is
  authoritative for the sections the backup covers, is applied as a unit, puts everything back on failure and
  says so if it cannot. Sessions of accounts the restore changes or removes are ended; unchanged accounts stay
  signed in.

### Firmware availability

- Added a firmware availability check (once after start-up, then every seven days) shown as Up to Date,
  Available or Unknown. It only reports.

### Device Info, A/V Matrix and the interface

- The A/V Matrix draws from the inventory before decoders answer and updates crosspoints in place.
- Device Info sorts addresses, numbers and versions numerically, has keyboard-operable headers, shows uptime,
  and lists standalone HW-OMNI-311/324 extenders as their own rows (never upgrade targets).
- The Upgrade Manager is now the Device Manager. Settings sections were reordered and button styles unified.

### Scanning and macOS

- A scan that the operating system blocks is reported as a failed scan with a reason instead of "0 found",
  and leaves the device list untouched. A target range that expands to no addresses is refused with an
  explanation.
- On macOS a notice under Scan explains Local Network access; the download includes READ-ME-FIRST-macOS.txt and
  docs/INSTALL_MACOS.md explains approving an unsigned, un-notarized app.
- Fixed the Intel macOS package, which failed on `import cryptography`.

### Updates, releases and packaging

- The in-app update check offers the newest release by version number and reports "unable to check" when none
  is found. It contacts GitHub at start-up (cached for six hours) and when you press Check Now, and never
  downloads anything.
- Release identity is checked before anything is built. Installed V1.1.0 applications keep being offered
  updates.
- Dependencies are locked with a pinned PyInstaller; third-party licence texts ship with the application and
  in every archive.

### Fixes

- A destructive request that says nothing no longer acts: Clear Units and the USB inventory clear now refuse a
  missing or malformed request instead of clearing.
- Malformed requests anywhere in the application are answered with a plain message instead of an internal
  error, and never act as an instruction.
- The Remote Access port is validated (1024-65535) before anything is changed.
- A device address can no longer be mistaken for a hardware address in Display Groups or account grants.
- A restricted User is no longer shown a stale Display Group member's hardware address.
- A backup with out-of-range encryption parameters is refused as damaged.
- A verification pass that began before Clear Units could write stale results back; it now discards them.
- Restricted-user routing no longer fails on a display that was showing a Multiview.
- Saved settings, the device-list cache and the restore path are protected against overlapping writes, and a
  damaged cache is set aside rather than stopping discovery.
- Passwords are no longer written to the logs when a command is sent to a device.
- Text a device reports is escaped wherever it is shown.
- Many presentation fixes (confirmation dialog styling, matrix select-all with Lock Headers, plain-text
  warning no longer shown over an HTTPS selection).

## V1.1.0 - 2026-09-17

Multiview: a decoder can show several encoders at once as a saved, editable
layout, with a display-output view that reports what the decoder is actually
showing. See [RELEASE_NOTES_V1.1.0.md](RELEASE_NOTES_V1.1.0.md) for the
user-facing summary.

- Added Multiview: eleven standard layouts, saved presets, empty and partial
  layouts, live source switching on an active Multiview, synchronized decoder
  groups, copy to another decoder, and source previews.
- Separated presets from execution: creating, installing, editing, copying and
  saving a Multiview configures no encoder and changes no display. Encoder 2,
  Session 2 and the decoder's inputs are prepared only when a Multiview is
  shown or changed live.
- Made Show on Display save the current editor state first and then show what
  it saved, so an operator never has to press Save first. A preset that saves
  but cannot be shown is kept, and the reason is reported.
- Added Display Output: what the decoder is actually displaying, derived from
  its own output selection, with a drop target that routes a source normally
  over Session 1 and exits Multiview first when needed.
- Made the A/V Matrix aware of Multiview, including exiting it for a
  conventional route, with rollback if that route then fails.
- Added bandwidth planning across the shared 900 Mb/s encoder budget, with a
  20 Mb/s minimum for the Multiview stream. Encoder 1 is read and never
  reduced.
- Added cross-decoder scaler protection: two displays may share a source at the
  same size, and a request for an incompatible size is refused before any write
  rather than disturbing the display already using it.
- Added a source filter and an independently scrolling source list, so the
  canvas stays visible while a long inventory is searched.
- Replaced the licence. OmniSuite is now published under the Hall Research
  Technologies Source-Available Software License, which permits personal,
  business and commercial use free of charge, including while doing paid work,
  and permits modification for personal or internal business use. Selling
  OmniSuite and distributing modified versions remain prohibited. This
  supersedes the PolyForm Noncommercial License 1.0.0 used up to V1.0.7, which
  did not permit the business use Hall intends to allow.
- Added an About area in Settings naming the publisher and linking to the
  licence, which the application now serves from the build it was made from.
- Added `THIRD_PARTY_NOTICES.md`, listing the bundled third-party components
  and their licences, and shipped it with the application.
- Fixed a test-isolation defect where background startup work could land in a
  later test and be reported as that test's failure.
- Fixed the A/V Matrix reporting a decoder's Multiview state from a stale
  cached value while naming a Multiview as its current input.
- Fixed a failed-route rollback restoring the Multiview without recording it,
  which left the A/V Matrix showing a route that had been undone.

Also in this release, previously listed as unreleased:

- Fixed Settings/Appearance behaving differently from page to page. Light mode
  could not be selected from Configure, the A/V Matrix or the USB Matrix even
  though Device Info worked: the shared Settings dialog rendered the Theme
  control on every page, but each page was expected to wire it up itself, and
  two of the three did so against header controls that had been removed.
- Made light and dark part of the one shared appearance implementation, next to
  the layout, the colour preset and the light background, so every page uses the
  same source of truth and the three page-specific copies are gone.
- Removed the MutationObserver that watched `<body>` for a theme class change;
  the shared module sets the class rather than following it.
- Settings can now be closed with Escape consistently from every page.

## V1.0.7 - 2026-09-11

- Made Device Info the single discovery surface: it owns the network adapter picker, the target list, Scan and Clear Units, and every other page reads what discovery found.
- Added support for standalone HW-OMNI-311 and HW-OMNI-324 USB extenders over their binary UDP API, discovered by local broadcast and by bounded, configurable range scans that run in the background without delaying encoder/decoder discovery.
- Surfaced the Icron USB endpoints built into E4521 and D4511 units alongside the standalone extenders, as one USB inventory with one row per physical endpoint.
- Added routed USB discovery networks so extenders on a subnet other than the management PC's can be found by directed polling.
- Reconciled devices by MAC rather than by IP, so a unit that changes address is rediscovered and merged instead of being duplicated or lost.
- Added LLDP and topology awareness, including the switch and port a unit reports and daisy-chain relationships between endpoints.
- Reported USB and Icron firmware revisions separately from the parent OmniStream firmware.

- Added one-click USB routing: a Matrix cell states the desired relationship and OmniSuite reconciles the hardware to it, verifying both sides by read-back rather than trusting a command acknowledgement.
- Presented LEX and REX roles explicitly, with peer capacity, configured peers and live linked counts kept as distinct fields.
- Added multi-peer support for extenders that carry more than one simultaneous peer, including per-peer Link Status.
- Added desired-state reassignment, which unpairs a stale relationship before pairing the selected one and serialises rapid changes to the same endpoint.
- Enforced same-subnet USB routing in the backend as well as the UI, so a cross-subnet attempt is rejected rather than warned about and attempted.

- Added an XLSX inventory download with separate Devices and USB sheets.
- Improved TS Dump collection and contents.
- Added encrypted device-log collection for manufacturer Engineering, behind a confirmation that states what the file is; the log is encrypted and is not readable locally.

- Added ten colour presets with independent light and dark selection and four layouts, applied consistently across every page.
- Reworked dialogs, confirmations and toasts, including keyboard and focus handling, replacing native browser prompts with the shared OmniSuite components.
- Improved table responsiveness and rendering performance on large inventories, including sticky headers and synchronised scrolling.

- Added an update checker that distinguishes up to date, an available update and a build newer than the newest published release, and reports a failed check as a failure rather than as up to date.
- Improved the launcher window and tray behaviour, and added port fallback so OmniSuite serves on the next free port when 8080 is taken, showing and logging the address it chose.
- Fixed the launcher browser handoff so the Open Browser button, the clickable URL and the tray item always open the operator's default browser; CI-only suppression now applies to the automatic open alone.

- Replaced the licence with the PolyForm Noncommercial License 1.0.0 and identified OmniSuite as source-available software.
- Stopped packaging content the application does not run: superseded page iterations, duplicate assets and development history no longer enter the executable, and the build now verifies its own table of contents.
- Kept the operator-chosen firmware directory, runtime configuration and device caches out of both the repository and the package.
- Added cross-platform release automation that runs the full test suite, builds, verifies architecture and smoke-tests the packaged artifact on Windows x86-64, macOS arm64, macOS x86-64 and Ubuntu x86-64, publishing SHA-256 checksums for each.

## V1.0.6 - 2026-08-26

- Fixed Video Wall configurator writes so inches and millimeters are sent to decoders as physical values instead of being converted into grid coordinates.
- Fixed Video Wall configurator readback so physical values that are whole numbers are still displayed as configured dimensions, not mistaken for grid counts.
- Added one-time live Video Wall setting loads on Matrix page load and decoder selection without adding an auto-refresh loop.
- Updated the Video Wall Layout picker to open with the configured grid size and highlight the unit's current selected display.
- Normalized Video Wall edge compensation mode writes to the device-accepted `bezel compensation` spelling.
- Added regression tests for physical-unit video wall payloads, physical-unit readback, and edge mode normalization.

## V1.0.5 - 2026-08-24

- Rebuilt the OmniSuite release after V1.0.4 artifacts were not created.
- Included the launcher update that uses native Tk buttons on non-Windows platforms to avoid unsupported themed button options.

## V1.0.4 - 2026-08-24

- Hardened USB pairing so route changes unpair stale LEX relationships before pairing the selected LEX, verify both sides, and serialize rapid multi-REX updates.
- Enforced same-/24 USB pairing policy in both backend and UI so cross-subnet pair attempts are rejected instead of warning-and-continuing.
- Improved USB Matrix feedback by preserving active controls during polling, extending verification refreshes, and showing shared LEX usage counts.
- Improved cached matrix state by merging `units_cache.json` with `scan_results.json`, inferring encoder/decoder roles from device fields, and saving cache updates atomically.
- Improved Matrix and Configure pages so background refreshes defer while live controls, modals, codec selectors, or video wall controls are active.
- Added codec compatibility protection so incompatible encoder/decoder routes are blocked in the UI and codec mismatches are clearly surfaced.
- Tightened decoder input handling by hiding `input` resolution while Fast Switching or Video Wall is enabled and rejecting invalid `input` resolution writes on the backend.
- Improved decoder setting verification so acknowledged writes can return requested fields when a follow-up read fails instead of falsely reporting a failed update.
- Improved Device Manager startup status display so polling-enabled sessions show `N/A` instead of stale disconnected status while fresh polling is about to update rows.

## V1.0.3 - 2026-08-21

- Fixed macOS scan persistence for packaged OmniSuite builds.

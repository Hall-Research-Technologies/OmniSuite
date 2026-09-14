# Changelog

## Unreleased

Fixed on `main` and **not yet part of a published release**. The V1.0.7
binaries on the Releases page do not contain these changes.

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

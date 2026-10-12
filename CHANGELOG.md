# Changelog

> This changelog belongs to the OmniSuite Plus line (`omnisuite-plus`). The other product
> line keeps its own; see [DEVELOPMENT.md](DEVELOPMENT.md).

## V1.2.0 - unreleased

Optional secure remote access, with administrator and user accounts and
per-user routing permissions. See [RELEASE_NOTES_V1.2.0.md](RELEASE_NOTES_V1.2.0.md)
for the user-facing summary.

- Added Remote Access, off by default on new installations and on upgrades,
  bound to an adapter an administrator chooses and restored safely across a
  restart.
- Added HTTPS with either a locally generated certificate or one supplied by
  the site; a certificate that cannot be read stops remote access rather than
  falling back to an unencrypted connection.
- Added administrator and user accounts, created locally for the first
  administrator, with no default credential.
- Added per-user permissions for features, sources, displays and saved
  Multiview layouts, enforced on the server at the moment each operation runs.
- Added a restricted operating screen for A/V, USB and Multiview, built for a
  phone or tablet.
- Added friendly device names, kept by OmniSuite and never written to the
  equipment, with duplicates distinguished by the Location an OmniStream unit
  reports about itself.
- Added an Activity history of sign-ins, refusals, routing and administrative
  changes, visible to administrators only.
- Added per-resource serialization so two operators changing one display
  cannot interleave, and shared state observation so extra browsers do not
  multiply device traffic.
- Added optional per-user direct access links: a passwordless address that
  opens one restricted account's controls, off by default, enabled per account
  by an administrator, never available on an administrator account, and
  carrying no permissions of its own.
- Opening the remote address in a browser now presents the sign-in page.
  Previously an unauthenticated page request was answered with the API's JSON
  refusal, which a browser displayed as raw text. Page requests and API
  requests are now answered differently and API responses are unchanged.
- A generated certificate is now checked against the address it is about to
  be served on. Remote access over HTTPS refuses to start on an address the
  certificate does not name, rather than reporting normal secure operation
  that no browser will accept. A supplied certificate is reported and never
  altered.
- Fixed the plaintext warning being shown in Settings while HTTPS was
  selected: the class that styles it overrode the attribute that hid it.
  Settings now states what protects the connection in either mode.
- Added a favicon route, so a browser no longer makes a failed request on
  every page load.
- Added browser security headers (nosniff, frame denial, no referrer, and a
  permissions policy naming only long-standing features).
- Fixed every mutating action being refused for an administrator signed in
  from another device. The matrix, USB, Multiview and Settings pages predate
  remote access and did not present a CSRF token, so a remote administrator
  could read everything and change nothing. One shared module now attaches
  it, covering existing call sites and any added later.
- The firmware file list is no longer requested from a remote browser. It
  reads a folder on the OmniSuite computer, so it could only ever fail
  there; the picker now says where firmware upgrades are started from.
- Fixed restricted-user routing failing on any display that was showing a
  Multiview. The A/V engine refuses to take a display off a composition unless
  the caller says so, and the user endpoint never passed that answer through,
  so routing looked intermittent while actually being deterministic per
  display. The operator is now asked, through the same confirmation the A/V
  Matrix uses, and their answer travels with the request.
- Every refusal a restricted user can meet now carries a machine-readable
  reason, so a display that is busy and a display that is compositing are no
  longer both "409".
- Added source pictures to the restricted user's source cards, from the
  encoder thumbnail the A/V Matrix has always used. Served through an
  authorised endpoint addressed by hardware identity, so no device address
  reaches a restricted browser, and refused for a source the account may not
  use. A source with no picture is still fully routable.
- Added Sign out to every screen reached through Remote Access, including the
  full application. It was previously only on the restricted user screen, so
  an administrator signing in remotely had no way out but to clear cookies.
- Fixed the shared confirmation dialog rendering unstyled on pages that do not
  load the A/V Matrix stylesheet -- including account management, where a
  passwordless link is replaced. It now carries its own presentation.
- Fixed the copy-link confirmations never appearing: they called a toast
  helper that does not exist.
- Routing a source to a display that is showing a Multiview now simply does
  it. A normal A/V route means single view, so the display stops compositing
  as part of the same verified transaction -- no question, from the A/V
  Matrix, the Display Output drop, a password User or a Direct Access User
  alike. The saved Multiview is kept, and a route that fails still puts it
  back and still says whether the restoration was verified.
- Clear Units now keeps devices assigned to User accounts while Remote Access
  is on, and says how many it kept and for whom. Clearing one of those is done
  from Manage users, where the accounts are, rather than being what the
  ordinary Clear button silently does. With Remote Access off, Clear behaves
  exactly as it always has.
- Clear Units no longer hides a file it could not remove. They were removed
  under one shared `except: pass`, so a locked file stopped the rest of the
  clear and said nothing -- and because scan results are merged back in by the
  next read, devices reported as cleared would reappear.
- Removed a second Multiview rollback in the route path that could never run:
  the failure branch above it returns on every path. A rollback that looks
  like a safety net and is not is worse than none.
- Manage users can now reach the rest of the application, and every
  administrative page is guaranteed a way back to OmniSuite.
- Fixed a duplicated charset in the Content-Type of every served page.
- macOS discovery diagnostics: a scan that the operating system refused is now reported as a failed scan
  with a reason, instead of "0 found". Connection attempts are classified once
  per address (open, refused, timed out, or the local error such as EPERM,
  EACCES, EHOSTUNREACH, ENETUNREACH, EMFILE) and returned in `diagnostics`.
- A refused scan leaves the device cache and scan results untouched.
- Targets that expand to no addresses are refused with `no_valid_targets`
  rather than scanning zero addresses.
- The file-descriptor limit and open count are reported read-only. The limit is
  never raised automatically.
- Windows discovery is unchanged: same ports, order and timeouts.

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
